FROM python:3.12-slim

WORKDIR /app
ENV DEBIAN_FRONTEND=noninteractive

# 安装系统依赖（MySQL 客户端等）
RUN apt-get update && apt-get install -y \
    default-mysql-client \
    curl \
    unzip \
    wget \
    gnupg \
    apt-transport-https \
    software-properties-common \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 升级 pip 并安装 Python 依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# 拷贝项目
COPY . /app

# 入口脚本
RUN chmod +x docker/entrypoint.sh
ENTRYPOINT ["docker/entrypoint.sh"]