# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Companion Document

`AGENTS.md`(项目根) 已经包含完整的代码风格、命名约定、Python/TS import 顺序和错误处理规范。**修改代码前先读它**，本文件只补充 AGENTS.md 没讲清楚的"全局视角"和高频 trap。

## Common Commands

### Backend
```bash
# Run FastAPI (dev)，端口 54351
python server/main.py

# Celery worker（消费 run_test_task / probe_devices / ai_tasks）
celery -A celery_app worker --loglevel=info

# Celery beat（设备心跳，30s 一次）— 集群里只能起一个
celery -A celery_app beat --loglevel=info

# 同步调试模式：不走 Redis / worker，task.delay() 在当前进程里跑
CELERY_TASK_ALWAYS_EAGER=1 uvicorn server.main:app --port 54351

# DB migrations
alembic upgrade head
alembic revision --autogenerate -m "xxx"
```

### Frontend (in `frontend/`)
```bash
npm run dev         # Vite dev server，端口 5173，API 代理到 127.0.0.1:54351
npm run build       # tsc -b && vite build → frontend/dist
npm run typecheck   # tsc -b --noEmit
npm run lint        # eslint，--max-warnings 0（CI 严）
```

### Tests
没有传统单测；"跑测试" = 通过 v2 唯一入口 `tests/service_run_executor.py::TestService::test_case_runner` 端到端执行用例。一般通过 `POST /api/run_test` 触发，平台会自动拼 pytest 命令。手动调用见 AGENTS.md 的 Backend Test 段。

**No Python linter/formatter is configured** —— 没有 ruff / black / flake8。提交前自查可用 `python -m compileall .`。

### Docker
```bash
docker compose up -d              # Redis + Postgres + api + worker + beat
docker compose logs -f worker     # 看任务执行
```

## Big-Picture Architecture

### 唯一执行链路（v2）

所有 case_type（api / web / android / ios / mixed / functional）走**同一条管道**。这是 2026 重构的核心结论 —— v1 的 `test_api_runner`、CLI 路径下的 `api_runner.py / mobile_runner.py` 已删，**不要"再加一个 runner 入口"**。

```
POST /api/run_test  (server/api/runs.py)
  → 创建 TestReport，提交 Celery 任务
  → tasks/run_test_task.py
       └─ pytest.main([
            "-p config.pytest_config",                     # 全局钩子
            "tests/service_run_executor.py::TestService::test_case_runner",
            "--cases_data=<json>",
          ])
            └─ CaseExecutor.run(case, ctx)                  # 用例级编排（runners/case_executor.py）
                 └─ StepDispatcher.dispatch(step, ctx)      # 派发 + retry / wait_before / on_failure
                      ├─ http_request   → HttpRequestStepRunner       (runners/steps/http_request.py)
                      ├─ web_*          → build_web_runners            (runners/steps/web_actions.py)
                      ├─ app_*          → build_app_runners + generic  (runners/steps/app_actions.py)
                      ├─ sleep / assert → SleepStepRunner / AssertStepRunner
                 → CaseResult → Allure attachments + record_property
       └─ 任务收尾：sync_allure_to_db + finalize_report（database/data_sync.py）
       └─ 异常兜底：force_error_status，**绝不让报告卡在 "running"**
```

关键不变量：
- **Runner 永不 raise**：所有异常都包装为 `StepResult(status=FAILED|ERROR)`（见 `runners/protocol.py`）。`AssertionError` → FAILED，其他 → ERROR + traceback。
- **Runner 接的是字典 + ExecutionContext**，不依赖 SQLAlchemy ORM —— 这是为了将来能脱离平台单独跑。
- **重试 / wait_before / on_failure 在 dispatcher 实现一次**，不要在 Runner 内部再写一遍。
- **没有 steps 的老 API 用例需要先迁移**：`database/migrations/data_migrations/v2_cases_to_steps.py` 把 v1 的 method/path/headers 字段拆成 step 行；CaseExecutor 遇到没 steps 的会直接抛错。

### 服务层分层

| 层 | 目录 | 约束 |
|---|---|---|
| HTTP | `server/api/` | 一个 REST 资源一个文件；用 `db: DBDep` 注入 session（`server/api/deps.py`，自动 commit/rollback/close）。**路由内一般不要手动 commit**，留给 deps 兜底。响应统一 `{status: "success"|"error", data?, message?}`。 |
| 业务 | `server/services/` | 不碰 HTTP，可被路由 / Celery 任务复用 |
| ORM + Schema | `database/models/` + `database/schemas/` | SQLAlchemy 2.0 风格；JSON 列用 `database.base.JSONType`（PG → JSONB，其他 → JSON）；预加载用 `selectinload()` |
| 步骤执行 | `runners/` | 协议 `runners/protocol.py` → Dispatcher → Runner |
| 执行上下文 | `runners/context/` + `core/` | `ExecutionContext` 装变量 / 日志 / attachments / `record_property` 句柄 |
| 异步任务 | `tasks/` | `run_test_task` / `probe_devices`（30s 心跳）/ `ai_tasks` |
| AI 网关 | `ai_gateway/` | 多 provider（OpenAI / Anthropic / Ollama）+ 分析模式（quick/standard/deep/multi_model）；不做持久化，由 `tasks/ai_tasks.py` 落库 |

### Celery 模式与排查

- **EAGER 模式（`CELERY_TASK_ALWAYS_EAGER=1`）**：`.delay()` 在当前进程同步跑，所有 print 落 uvicorn 终端 —— 排查"提交了但库里没数据"的首选手段。
- 报告卡在 `running` 99% 是 worker 没起 / Redis 没连上。先 EAGER 验证链路本身是否通。
- worker 是长期存活的进程，`pytest_sessionstart` 会重置 `AppSessionRegistry`，避免上一轮 closed session 串到下一轮（`config/pytest_config.py`）。

### 前端

- React 19 + Vite + TS strict + Tailwind + shadcn/ui（Radix）
- API 调用走 `src/lib/api.ts` 的 `request<T>()`，业务错误抛 `ApiError`；用户提示用 `sonner` toast
- 数据获取统一用 `@tanstack/react-query`；表单 `react-hook-form` + `zod`
- 路径别名 `@/* → frontend/src/*`（`tsconfig.app.json` + `vite.config.ts` 双声明）
- 领域类型集中在 `src/types/domain.ts`；API 信封用泛型 `ApiEnvelope<T>`

## Project-Specific Traps

1. **包名是 `server/`，不是 `platform/`**。`platform` 会遮蔽 stdlib 的 `platform` 模块，SQLAlchemy import 期就会调 `platform.python_implementation()` 直接挂掉。任何文档 / 历史代码看到 `platform.xxx` 都该当作 `server.xxx` 读。
2. **`celery_app.py` 的 broker / backend 当前写死 `redis://127.0.0.1:6379`**。docker-compose 里有 `CELERY_BROKER_URL` env，但代码还没读 env。改前先确认是否真的要切换。
3. **Playwright 浏览器内核要单独装**：`pip install playwright` 后还得 `playwright install`。Dockerfile 默认注释了这一步以缩小镜像。
4. **路径锚点用 `_PROJECT_ROOT = Path(__file__).resolve().parent.parent`**，不要写 `Path.cwd()` 或硬编码 —— uvicorn 从不同 cwd 启动会让相对路径全错位。
5. **报告 / 静态资源**：`data/reports/<task_id>` 是 Allure HTML 产物（FastAPI 挂在 `/reports`）；`data/results/<task_id>` 是 allure 原始结果（pytest `--alluredir`）；`frontend/dist` 是 SPA 产物，没构建时 `/` 会返回 503 提示。
6. **`config/object_conf.ini` 是平台业务配置**（DB 连接、Appium、设备等），通过 `utils/read_conf.read_conf` 读取；不是 pytest / alembic 配置。
7. **Alembic DB URL 不在 `alembic.ini`**，在 `database/migrations/env.py` 里从 `object_conf.ini` 读，可用 `ALEMBIC_DB_URL` env 临时覆盖。
8. **没有 Python lint/format 配置**。代码质量纯靠 review；不要因为"项目没装 ruff"就在新代码里引入风格不一致的写法 —— 跟着已有文件走。

## When Modifying...

- **加新 step type**：在 `runners/steps/` 里写 Runner，声明 `step_types`，到 `StepDispatcher.default()` 注册。**不要**自己实现 retry。
- **加新 REST 资源**：在 `server/api/<name>.py` 写 router，到 `server/api/__init__.py` 导出，`server/main.py` 的 `for router in (...)` 循环里加进去（自动挂 `/api` 前缀）。
- **改数据库 schema**：编辑 `database/models/`，跑 `alembic revision --autogenerate -m "..."`，**review 自动生成的迁移**（autogenerate 经常漏 server_default / index 改动）。
- **加 Celery 任务**：在 `tasks/` 加文件，并在 `celery_app.py` 底部 `import tasks.xxx  # noqa: F401` 注册。
