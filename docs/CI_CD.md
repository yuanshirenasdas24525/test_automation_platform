# CI/CD 方案

## 整体架构

```
GitHub Push (main 分支)
    │
    ▼
GitHub Actions
    ├── Build Docker 镜像（多阶段：前端构建 + Python 环境）
    ├── Push 到 GitHub Container Registry (ghcr.io)
    └── SSH → 云服务器 → docker compose up -d
```

---

## 一、GitHub Secrets 配置

在 GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**，添加以下 3 个 Secret：

| Secret 名 | 说明 |
|-----------|------|
| `SSH_HOST` | 云服务器 IP 或域名 |
| `SSH_USER` | SSH 登录用户名（如 `root`） |
| `SSH_PRIVATE_KEY` | SSH 私钥完整内容（`-----BEGIN ... END-----`） |

> GHCR 认证无需额外 Secret —— `${{ secrets.GITHUB_TOKEN }}` 自动可用。

### 生成 SSH 密钥对（如未就绪）

```bash
# 本地生成
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_actions_deploy

# 公钥添加到服务器
ssh-copy-id -i ~/.ssh/github_actions_deploy.pub root@<服务器IP>

# 私钥内容作为 SSH_PRIVATE_KEY secret
cat ~/.ssh/github_actions_deploy
```

---

## 二、服务器端准备

在云服务器上执行：

```bash
# 1. 安装 Docker（如未安装）
curl -fsSL https://get.docker.com | bash

# 2. 创建项目目录
mkdir -p /opt/test_automation_platform && cd /opt/test_automation_platform

# 3. 首次手动放置配置文件（后续 CI 自动同步 compose 文件）
#    - config/object_conf.ini（数据库连接等业务配置）
#    - 确保 data/ 目录存在（volume 挂载点）

# 4. 首次手动登录 GHCR
echo "$GHCR_TOKEN" | docker login ghcr.io -u <GitHub用户名> --password-stdin
```

---

## 三、项目文件

### 3.1 GitHub Actions 工作流 `.github/workflows/deploy.yml`

```yaml
name: Build & Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:  # 允许手动触发

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  # ── 阶段 1：前端质量检查（可选） ──
  frontend-check:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        run: npm ci
        working-directory: frontend

      - name: TypeScript typecheck
        run: npm run typecheck
        working-directory: frontend

      - name: ESLint
        run: npm run lint
        working-directory: frontend

  # ── 阶段 2：构建并推送 Docker 镜像 ──
  build-and-push:
    needs: frontend-check
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Docker metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=sha,format=short
            type=ref,event=branch
            latest

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  # ── 阶段 3：部署到云服务器 ──
  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Sync compose file
        uses: appleboy/scp-action@v0
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          source: "docker-compose.prod.yaml"
          target: "/opt/test_automation_platform/docker-compose.yaml"

      - name: Deploy to server
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.SSH_HOST }}
          username: ${{ secrets.SSH_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            cd /opt/test_automation_platform
            echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            docker compose pull
            docker compose up -d --remove-orphans
            docker image prune -f
            sleep 10
            docker compose ps
```

### 3.2 生产版 Compose 文件 `docker-compose.prod.yaml`

```yaml
version: "3.9"

x-app-common: &app-common
  image: ghcr.io/yuanshirenasdas24525/test_automation_platform:latest
  environment: &app-env
    TZ: Asia/Shanghai
    PYTHONUNBUFFERED: "1"
    REDIS_HOST: redis
    REDIS_PORT: "6379"
    POSTGRES_HOST: postgres
    POSTGRES_PORT: "5432"
    CELERY_BROKER_URL: redis://redis:6379/0
    CELERY_RESULT_BACKEND: redis://redis:6379/1
    DB_HOST: postgres
    DB_PORT: "5432"
    DB_USER: tap
    DB_PASSWORD: tap_pass
    DB_NAME: tap
  volumes: &app-vols
    - ./data:/app/data
    - ./config:/app/config
  depends_on:
    redis:
      condition: service_healthy
    postgres:
      condition: service_healthy
  restart: unless-stopped

services:
  redis:
    image: redis:7-alpine
    container_name: tap_redis
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  postgres:
    image: postgres:15-alpine
    container_name: tap_postgres
    environment:
      POSTGRES_USER: tap
      POSTGRES_PASSWORD: tap_pass
      POSTGRES_DB: tap
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "tap"]
      interval: 10s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  api:
    <<: *app-common
    container_name: tap_api
    command:
      - uvicorn
      - server.main:app
      - --host
      - 0.0.0.0
      - --port
      - "8000"
      - --workers
      - "2"
    ports:
      - "8000:8000"

  worker:
    <<: *app-common
    container_name: tap_worker
    command:
      - celery
      - -A
      - celery_app
      - worker
      - --loglevel=INFO
      - -c
      - "4"
    healthcheck:
      disable: true

  beat:
    <<: *app-common
    container_name: tap_beat
    command:
      - celery
      - -A
      - celery_app
      - beat
      - --loglevel=INFO
      - --schedule
      - /app/data/celerybeat-schedule.db
    healthcheck:
      disable: true

volumes:
  redis_data:
  postgres_data:
```

> **与开发版区别**：`x-app-common` 中 `build:` 改为 `image: ghcr.io/...`，不再从源码构建，直接拉取 CI 已构建好的镜像。

---

## 四、文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `.github/workflows/deploy.yml` | **新建** | GitHub Actions 工作流定义 |
| `docker-compose.prod.yaml` | **新建** | 生产环境 compose（用 GHCR 镜像） |
| GitHub → Secrets | **配置** | `SSH_HOST`、`SSH_USER`、`SSH_PRIVATE_KEY` |
| 服务器 `/opt/test_automation_platform/` | **创建目录** | 存放 compose 文件和持久化数据 |

---

## 五、触发与执行流程

1. 开发者 `git push` 到 `main` 分支
2. GitHub Actions 自动触发：
   - **frontend-check**：TypeScript 类型检查 + ESLint（约 1 分钟）
   - **build-and-push**：多阶段 Docker 构建 + 推送到 GHCR（约 5-8 分钟）
   - **deploy**：
     - SCP 同步 `docker-compose.prod.yaml` → 服务器
     - SSH 登录 → `docker compose pull`（拉取新镜像）
     - `docker compose up -d`（滚动更新服务）
     - 清理旧镜像
3. `workflow_dispatch` 支持手动触发

---

## 六、镜像标签策略

| 标签 | 触发条件 | 示例 |
|------|---------|------|
| `latest` | 每次 push main | `ghcr.io/.../test_automation_platform:latest` |
| `main` | 每次 push main | `ghcr.io/.../test_automation_platform:main` |
| `sha-abc1234` | 每次 push main | `ghcr.io/.../test_automation_platform:sha-abc1234` |

---

## 七、注意事项

1. **GitHub Packages 权限**：首次推送后，在 GitHub 仓库 → Settings → Packages → 将镜像设为 **Public**（或在 Actions workflow permissions 中启用 "Allow GitHub Actions to create and approve pull requests"）。

2. **首次部署**：服务器上需要手动创建 `/opt/test_automation_platform/` 目录并放好 `config/object_conf.ini`（数据库连接、域名等业务配置）。这个文件通过 volume 挂载到容器内。

3. **数据持久化**：`data/` 目录（测试报告、日志、截图）和 PostgreSQL 数据均通过 Docker volume 持久化，`docker compose down` 不会丢失。

4. **现有 Jenkinsfile**：项目已有 `Jenkinsfile` 但引用部分不存在的文件（`config/settings.py`、`tests/test_api.py`），建议保留备用或删除。

5. **Dockerfile 路径**：CI 使用根目录的 `Dockerfile`（Jenkinsfile 中错误引用了 `docker/Dockerfile`，后者不存在）。

6. **Playwright 浏览器**：默认未安装浏览器内核（镜像约 250MB），需要 Web UI 自动化时取消 Dockerfile 中 `RUN python -m playwright install --with-deps chromium` 的注释（镜像会增至约 900MB）。

---

## 八、扩展建议

### 8.1 添加 Python 语法检查阶段

```yaml
  python-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python -m compileall . -q
```

### 8.2 添加自动化测试阶段

```yaml
  test:
    needs: python-check
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: |
          CELERY_TASK_ALWAYS_EAGER=1 pytest -s -v \
            -p config.pytest_config \
            --alluredir=data/results/ci_test \
            tests/
```

### 8.3 生产环境密码管理

建议将 `docker-compose.prod.yaml` 中的明文密码（`DB_PASSWORD` 等）改为环境变量注入：

```yaml
environment:
  DB_PASSWORD: ${DB_PASSWORD}
```

然后在服务器上创建 `.env` 文件存放真实密码（不要提交到 Git）。

### 8.4 多环境部署

如需区分 staging / production 环境，可创建分支对应的 workflow：

- `push: branches: [develop]` → 部署到 staging 服务器
- `push: branches: [main]` → 部署到 production 服务器
