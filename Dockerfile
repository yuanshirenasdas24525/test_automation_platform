# =============================================================================
# 测试自动化平台 —— 多阶段构建
#
# 一份镜像同时支撑三种角色（由 docker-compose 用不同 command 启动）：
#   1) FastAPI         uvicorn server.main:app   （API + 静态前端）
#   2) Celery worker   celery -A celery_app worker
#   3) Celery beat     celery -A celery_app beat   （probe_devices 定时任务）
#
# 为什么不拆三个镜像？三种角色都要完整 Python 环境 + 项目源码，
# 唯一区别是启动命令；拆开会把 200MB 镜像翻三倍，且要维护三份 build 缓存。
#
# 构建：
#   docker build -t tap:latest .
#
# 运行（推荐用 docker-compose）：
#   docker compose up -d
# =============================================================================


# -----------------------------------------------------------------------------
# Stage 1: 前端构建（Node 20）
# 单独一段是为了把 node_modules / vite 依赖跟 runtime 隔离，
# 最终镜像只有 dist/ 静态产物，不留 node。
# -----------------------------------------------------------------------------
FROM node:20-bookworm AS frontend-build

WORKDIR /frontend

# 换淘宝镜像（解决 npm 官方源连接不稳定）
RUN npm config set registry https://registry.npmmirror.com

# 先 copy lock 文件单独装依赖 —— 源码改动不会让 npm ci 重跑
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# 再 copy 源码 + 构建
COPY frontend/ ./
RUN npm run build && ls -la dist/


# -----------------------------------------------------------------------------
# Stage 2: Python runtime（python 3.13 + Java 17 + Allure CLI）
# -----------------------------------------------------------------------------
FROM python:3.13-bookworm AS runtime

# Python 行为收紧：不缓存 .pyc、stdout 不缓冲、pip 不缓存
# 时区默认上海；如果你的环境是 UTC，docker run 时用 -e TZ=UTC 覆盖
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# 系统依赖：
#   - openjdk-17-jre-headless：Allure 报告生成需要 JVM
#   - libpq5：psycopg2-binary 运行时（编译版需要 -dev 包，binary 包只要 .so）
#   - default-libmysqlclient-dev + pkg-config：保险起见装个 mysql 头文件，
#     PyMySQL 是纯 Python 的可以不要，但有些环境会附带其它 mysql 库
#   - netcat-openbsd：entrypoint 探活 redis/postgres 用
#   - fonts-noto-cjk：截图 / Allure 报告里的中文字符不变方块
#   - tzdata：时区数据
#   - curl/wget/unzip/ca-certificates：拉 Allure CLI
#   - libgl1-mesa-glx：Playwright 浏览器渲染依赖
#   - tesseract-ocr + tesseract-ocr-chi-sim：OCR 验证码识别
#   - android-tools-adb：Android 设备探测（adb devices）
#   - libimobiledevice-utils：iOS 真机探测（idevice_id -l）
# 切国内镜像源（解决 deb.debian.org 连接不稳定问题）
RUN sed -i "s@http://deb.debian.org@https://mirrors.aliyun.com@g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i "s@http://deb.debian.org@https://mirrors.aliyun.com@g" /etc/apt/sources.list 2>/dev/null; \
    sed -i "s@http://security.debian.org@https://mirrors.aliyun.com@g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i "s@http://security.debian.org@https://mirrors.aliyun.com@g" /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        libpq5 \
        default-libmysqlclient-dev pkg-config \
        netcat-openbsd \
        fonts-noto-cjk \
        tzdata \
        curl wget unzip ca-certificates \
        libgl1-mesa-glx \
        tesseract-ocr tesseract-ocr-chi-sim \
        android-tools-adb \
        libimobiledevice-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# JAVA_HOME：跨架构兼容（amd64 / arm64 都能用）
ARG TARGETARCH
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-${TARGETARCH}
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# Allure CLI（HTML 报告生成 — Celery worker 跑完 pytest 调用）
# 国内连不上 repo.maven.apache.org，用阿里 Maven 镜像
ARG ALLURE_VERSION=2.29.0
RUN curl -fsSL --retry 3 --retry-delay 5 \
        "https://maven.aliyun.com/repository/public/io/qameta/allure/allure-commandline/${ALLURE_VERSION}/allure-commandline-${ALLURE_VERSION}.tgz" \
        -o /tmp/allure.tgz \
    && tar -zxf /tmp/allure.tgz -C /opt/ \
    && rm /tmp/allure.tgz \
    && ln -s /opt/allure-${ALLURE_VERSION}/bin/allure /usr/local/bin/allure \
    && allure --version

WORKDIR /app

# Python 依赖：单独 copy requirements.txt 装一遍，最大化层缓存
# 源码改动不会让 pip install 重跑
COPY requirements.txt ./
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --upgrade pip \
    && pip install -r requirements.txt

# Playwright 浏览器内核（Web 用例需要）
# 国内网络连不上 Azure CDN，用 npmmirror 镜像
RUN apt-get update \
    && PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
    python -m playwright install --with-deps chromium

# 拷贝项目源码（.dockerignore 会过滤掉 venv / __pycache__ / data / .git 等）
COPY . /app

# 把前端 build 产物拷进 server 期望的位置：/app/frontend/dist
# 先删 stage1 之前可能 COPY . 进来的 frontend 目录，再 mount stage1 的 dist
RUN rm -rf /app/frontend
COPY --from=frontend-build /frontend/dist /app/frontend/dist

# 数据 / 报告 / 日志目录占位（外部 volume 会盖在上面，这里建是给"裸跑"兜底）
RUN mkdir -p \
        /app/data/log \
        /app/data/reports \
        /app/data/results \
        /app/data/app_packages \
        /app/data/screenshots \
        /app/data/db

# 入口脚本（带迁移 / 启动检查）
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# FastAPI 默认监听 8000；docker-compose / docker run 时映射出去
EXPOSE 8000

# 健康检查（针对 FastAPI 角色；celery 角色 docker-compose 里 healthcheck 关掉）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# 默认角色 = FastAPI
# celery worker / beat 由 compose / docker run 用 command 覆盖
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
