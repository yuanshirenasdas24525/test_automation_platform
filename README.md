# Automation Test Platform

AI 驱动的全栈测试平台，覆盖项目管理、需求管理、测试用例、自动化执行、报告分析、AI 需求分析、AI 生成用例、AI Studio 编码协作，以及 Bug 一键修复。

后端使用 FastAPI + SQLAlchemy + Celery，前端使用 React + Vite + shadcn/ui，数据库统一使用 PostgreSQL，异步队列使用 Redis。

## 主要能力

### 项目与需求管理

- 项目、版本、模块、需求、任务、Bug 的完整管理。
- 需求支持父子结构、编辑历史、附件、负责人、版本化记录。
- 功能用例、API/Web/App/Mixed 自动化用例、测试计划与执行报告统一管理。

### 自动化测试执行

- API、Web、Android、iOS、Mixed 用例走同一条 v2 执行链路。
- 执行入口统一为 `POST /api/run_test`。
- Pytest + Celery 执行任务，Allure 产物自动同步入库。
- Web 自动化支持 Playwright，并保留 Selenium 兼容能力。
- App 自动化基于 Appium，支持设备探活、包管理、设备动作执行。

### AI 能力

- M6 需求分析：从需求文本生成测试维度、可测性、行业/市场补充等分析文档。
- M7 AI 生成测试用例：根据需求上下文生成用例草稿，审核后批量入库。
- AI Studio M1：对话式写需求，生成结构化需求草稿，并支持后续编码任务。
- Bug Fix：基于 LLM Agent 或 CLI Agent 自动拉代码、修复、提交、推送，并回写 Bug 状态。
- RAG：代码仓库索引、向量检索、上下文拼接，支持 AI 编码和修复链路。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2.0、Pydantic 2、Alembic |
| 异步任务 | Celery、Redis |
| 前端 | TypeScript、React 19、Vite 5、Tailwind CSS、shadcn/ui、TanStack Query |
| 数据库 | PostgreSQL、pgvector |
| 测试执行 | pytest、allure-pytest、Playwright、Selenium、Appium |
| AI 网关 | OpenAI、Anthropic、Ollama、智谱等 provider 抽象 |
| 部署 | Docker、docker compose |

## 快速开始

### 1. 前置依赖

本地开发建议准备：

- Python 3.10+，推荐 Python 3.12。
- Node.js 18+。
- Docker Desktop 或等价 Docker 环境。
- PostgreSQL 和 Redis 可由 `docker compose` 自动拉起。

首次拉下代码后安装依赖：

```bash
make venv
make setup
```

`make setup` 会安装后端依赖到 `venv/`，并在 `frontend/` 下执行 `npm install`。

如果公司网络存在 SSL 证书拦截，可临时使用：

```bash
make setup PIP_TRUSTED=1
```

### 2. 启动本地开发环境

```bash
make dev
```

默认会启动：

- Redis + PostgreSQL：通过 `docker compose up -d redis postgres`。
- Alembic 迁移：`alembic upgrade head`。
- FastAPI：`http://127.0.0.1:54351`。
- Celery worker：执行测试、AI 任务、设备探活等异步任务。
- Celery beat：定时任务调度。
- Vite dev server：`http://localhost:5173`，前端 `/api` 代理到后端。

日志输出在：

```text
data/logs/dev/
```

停止开发环境：

```bash
make stop
```

如需连同 Redis/PostgreSQL 一起停止：

```bash
STOP_INFRA=1 make stop
```

### 3. 按需裁剪启动项

```bash
START_INFRA=0 ./start-dev.sh                 # 已自行启动 Redis/PostgreSQL
START_WEB=0 ./start-dev.sh                   # 只启动后端和异步任务
START_WORKER=0 START_BEAT=0 ./start-dev.sh   # 只启动 API + 前端
API_RELOAD=0 ./start-dev.sh                  # 关闭 uvicorn 热重载
RUN_MIGRATIONS=0 ./start-dev.sh              # 跳过 Alembic 迁移
AUTO_INSTALL=1 ./start-dev.sh                # 缺后端依赖时自动 pip install
```

## Docker 启动

一键启动完整容器环境：

```bash
docker compose up -d
```

默认服务：

| 服务 | 说明 | 端口 |
|---|---|---|
| `api` | FastAPI + 已构建前端 SPA | `8000` |
| `worker` | Celery worker | 无 HTTP 端口 |
| `beat` | Celery beat | 无 HTTP 端口 |
| `postgres` | PostgreSQL | `127.0.0.1:5432` |
| `redis` | Redis | `127.0.0.1:6379` |
| `appium` | Appium Server | `4723` |

查看日志：

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f beat
```

重建镜像：

```bash
docker compose up -d --build
```

停止并移除容器：

```bash
docker compose down
```

运行期数据挂载在 `data/`，PostgreSQL、Redis、Playwright 缓存等使用 Docker volume 保存。

## 常用命令

| 命令 | 作用 |
|---|---|
| `make help` | 查看本地命令 |
| `make venv` | 使用 Python 3.10+ 重建 `venv/` |
| `make setup` | 安装后端和前端依赖 |
| `make dev` | 启动本地开发环境 |
| `make stop` | 停止本地开发环境 |
| `make migrate` | 执行 `alembic upgrade head` |
| `make lint` | 执行前端 ESLint |
| `make build` | 执行前端 TypeScript 检查和 Vite 构建 |
| `make backfill-flags` | 历史 AI 诊断结果回填为用例标记 |
| `make check-flags` | 只读检查 AI 标记链路 |

前端单独开发：

```bash
cd frontend
npm run dev
npm run typecheck
npm run lint
npm run build
```

后端单独启动：

```bash
source venv/bin/activate
uvicorn server.main:app --host 127.0.0.1 --port 54351 --reload
celery -A celery_app worker --loglevel=INFO
celery -A celery_app beat --loglevel=INFO
```

## 数据库与迁移

项目统一以 PostgreSQL 为标准数据库，不维护 SQLite/MySQL 兼容路径。

迁移：

```bash
make migrate
```

或：

```bash
source venv/bin/activate
alembic upgrade head
```

生成迁移：

```bash
alembic revision --autogenerate -m "描述本次变更"
```

注意：Alembic autogenerate 可能漏掉 `server_default`、索引、部分约束等改动，生成后必须人工 review。

数据库连接配置来自：

```text
config/object_conf.ini
```

也可以通过环境变量或 `ALEMBIC_DB_URL` 临时覆盖迁移连接。

## 自动化执行链路

平台当前只保留 v2 执行链路。所有自动化用例类型都走同一条管道：

```text
POST /api/run_test
  -> 创建 TestReport
  -> Celery run_test_task
  -> pytest.main(...)
  -> tests/service_run_executor.py::TestService::test_case_runner
  -> runners.case_executor.CaseExecutor
  -> runners.dispatcher.StepDispatcher
  -> 具体 StepRunner
  -> Allure attachments + TestStepReport 入库
  -> database.data_sync 同步报告并 finalize
```

核心约束：

- Runner 不直接抛异常，统一返回 `StepResult`。
- Retry、`wait_before`、`on_failure` 在 `StepDispatcher` 中统一处理。
- Runner 接收 `dict` 和 `ExecutionContext`，不依赖 ORM。
- 老的 v1 runner 入口已经移除，不要新增旁路执行入口。

手动调试用例执行可以参考：

```bash
pytest -s -v \
  -p config.pytest_config \
  --report_id=1 \
  --category=api \
  --alluredir=data/results/test_001 \
  tests/service_run_executor.py::TestService::test_case_runner \
  --cases_data='[{"id":1,"name":"登录","case_type":"api","steps":[...]}]'
```

排查 Celery / Redis 问题时，可以使用同步模式：

```bash
CELERY_TASK_ALWAYS_EAGER=1 uvicorn server.main:app --port 54351
```

此时 `.delay()` 会在当前进程内同步执行，便于直接看后端日志。

## 目录结构

```text
.
├── server/                 # FastAPI 入口、API 路由、服务层
├── database/               # SQLAlchemy 模型、Pydantic schema、Alembic 迁移
├── runners/                # v2 自动化执行引擎
├── tasks/                  # Celery 异步任务
├── ai_gateway/             # 多 provider AI 网关和 prompt
├── coding_agent/           # RAG、diff、patch、git 操作
├── frontend/               # React + Vite 前端
├── config/                 # pytest、平台配置、业务配置
├── utils/                  # 通用工具
├── tests/                  # 平台执行入口和测试相关代码
├── docs/                   # 设计文档和专题说明
├── data/                   # 运行期产物，不入仓
├── docker-compose.yaml     # 本地/服务器 compose 编排
├── Dockerfile              # 应用镜像构建
├── Makefile                # 常用命令入口
├── start-dev.sh            # 本地一键启动
└── stop-dev.sh             # 本地一键停止
```

`data/` 下常见目录：

```text
data/reports/       # Allure HTML 报告
data/results/       # Allure 原始结果
data/attachments/   # 需求附件
data/app_packages/  # APK/IPA 包
data/logs/          # 本地开发日志
```

## API 与前端入口

本地开发：

- 前端：`http://localhost:5173`
- 后端：`http://127.0.0.1:54351`
- 健康检查：`http://127.0.0.1:54351/api/health`

Docker：

- 平台入口：`http://localhost:8000`
- 健康检查：`http://localhost:8000/api/health`

FastAPI 会挂载：

- `/api/*`：后端 API。
- `/reports/*`：Allure HTML 报告。
- `/attachments/*`：需求附件。
- 非 `/api/*` 路径：回退到 React SPA。

## 配置说明

### 平台业务配置

```text
config/object_conf.ini
```

包含数据库连接、服务地址、Appium、设备相关配置等。

### 环境变量

| 变量 | 说明 |
|---|---|
| `BACKEND_CORS_ORIGINS` | CORS 允许来源，默认 `*` |
| `CELERY_TASK_ALWAYS_EAGER` | 设为 `1` 时 Celery 任务同步执行 |
| `CELERY_BROKER_URL` | Celery broker URL |
| `CELERY_RESULT_BACKEND` | Celery result backend URL |
| `PLATFORM_SECRET_KEY` | AES-256 主密钥，用于 Git 凭证等敏感信息加解密 |
| `PYTHONUNBUFFERED` | 容器日志实时输出 |
| `TZ` | 时区，默认建议 `Asia/Shanghai` |

注意：`celery_app.py` 当前仍有 Redis 默认连接配置，调整 Celery 连接前请先核对实现。

### AI Provider 配置

AI 模型和 provider 配置通过平台配置中心和 `ai_models` 等表管理。底层 provider 位于：

```text
ai_gateway/providers/
```

prompt 模板位于：

```text
ai_gateway/prompts/
```

## 开发约定

更完整的协作约定见：

- `AGENTS.md`：代码风格、模块边界、不变量。
- `CLAUDE.md`：高频 trap、常用操作命令。

关键约定摘要：

- Python 文件以 `from __future__ import annotations` 开头。
- Python import 顺序：标准库、第三方、项目内包。
- API 层使用 `server.api.deps.DBDep` 管理事务，路由内一般不要手动 `commit()`。
- JSON 列修改要整体重新赋值，避免 SQLAlchemy 脏检查失效。
- 路径锚点使用 `_PROJECT_ROOT = Path(__file__).resolve().parent.parent`，不要依赖 `Path.cwd()`。
- 不要创建名为 `platform` 的包，避免遮蔽 Python 标准库。
- 前端领域类型集中在 `frontend/src/types/domain.ts`。
- 前端 API 请求统一走 `frontend/src/lib/api.ts`。
- 新增 REST 资源后，需要在 `server/api/__init__.py` 导出，并在 `server/main.py` 注册路由。

## 测试与质量检查

当前项目没有传统 Python unit test 套件。平台里的“跑测试”通常指通过 v2 执行链路运行真实自动化用例。

提交前建议至少执行：

```bash
python -m compileall .
cd frontend && npm run typecheck
cd frontend && npm run lint
cd frontend && npm run build
```

如果修改了自动化执行链路，建议用 `CELERY_TASK_ALWAYS_EAGER=1` 先走一遍真实用例。

如果修改了前端交互，建议在 Vite dev 环境中手动验证关键页面和工作流。

## 常见问题

### 前端打不开，提示 dist 不存在

本地开发请访问 Vite 地址：

```text
http://localhost:5173
```

如果通过后端地址访问 SPA，需要先构建：

```bash
cd frontend && npm run build
```

### 报告一直卡在 running

优先检查：

- Celery worker 是否启动。
- Redis 是否可连接。
- `run_test_task` 是否报错。
- Allure 结果目录是否有写入权限。

本地排查建议使用：

```bash
CELERY_TASK_ALWAYS_EAGER=1 uvicorn server.main:app --port 54351
```

### Playwright 报浏览器内核不存在

本地首次安装后需要执行：

```bash
playwright install
```

Docker 环境会通过 volume 缓存浏览器内核。

### Alembic 迁移连接错数据库

检查：

- `config/object_conf.ini`
- `ALEMBIC_DB_URL`
- Docker compose 中的 `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`

### Appium 无法连接设备

检查：

- Appium 服务是否健康：`http://127.0.0.1:4723/status`
- Android 设备是否通过 adb 授权。
- 平台设备表里的 `agent_host`、`appium_port` 是否正确。
- iOS 自动化需要 macOS 宿主机和 Xcode，不能在普通 Linux 容器中完成。

## 参考文档

- `docs/architecture.md`：架构说明。
- `docs/DATABASE.md`：数据库说明。
- `docs/CI_CD.md`：CI/CD 说明。
- `docs/api_crypto_usage.md`：API 请求/响应加解密配置。
- `docs/ai_bug_fix_plan.md`：AI Bug Fix 设计。
- `docs/ai_case_generation_m7_plan.md`：AI 生成用例设计。
- `docs/ai_analysis_m6_plan.md`：AI 需求分析设计。
- `docs/ai_ui_automation_m8_plan.md`：AI UI 自动化规划。

## Git 忽略与运行期产物

以下内容不应提交：

- `data/`
- `venv/`
- `__pycache__/`
- `.pytest_cache/`
- `frontend/node_modules/`
- `frontend/dist/`
- `.env`

敏感信息请放到本地 `.env` 或部署环境变量中，尤其是 `PLATFORM_SECRET_KEY`、AI provider token、Git token 等。
