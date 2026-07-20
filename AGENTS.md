# AGENTS.md — Automation Test Platform

> 给 AI 协作 agent（Claude Code / Copilot / Cursor 等）和新加入的工程师看的"开工前必读"。
> 全局视角看：[`CLAUDE.md`](./CLAUDE.md) 补充了高频 trap 和操作命令，本文件聚焦"代码风格 + 模块边界 + 不变量"。

---

## Project Overview

全栈"AI 驱动 SDLC"测试平台。后端 FastAPI + Celery；前端 React + Vite + shadcn/ui。

覆盖的业务域：
- **PM 域**：项目 / 版本 / 模块 / 需求（含父子需求 + 编辑历史 + 附件）/ 任务 / Bug
- **测试域**：自动化用例（API / Web / Android / iOS / Mixed）+ 功能用例 + 测试计划 + 执行报告 + Allure 集成
- **AI 域**：
  - M6：需求分析（输入需求 → 输出测试维度文档）
  - M7：AI 一键生成测试用例（草稿态 → 批量入库）
  - M1（AI Studio，本期主推）：对话式写需求 + AI 下发编码（RAG + 单次 patch → UI diff 审核 → push）
  - **Bug Fix**：AI 一键修复 Bug（LLM Agent 主路径 + CLI Agent 可选），自动拉代码仓库 → 修复 → git commit/push → Bug 标记"已修复"

技术栈：
- **后端**：Python 3.12（Docker runtime）、FastAPI 0.135、SQLAlchemy 2.0、Pydantic 2.12、Celery 5.6、Alembic 1.13
- **前端**：TypeScript 5.6（strict）、React 19、Vite 5、Tailwind 3、shadcn/ui（Radix）、TanStack Query、react-hook-form + zod、sonner
- **数据库**：**PostgreSQL（统一标准）** + pgvector 向量列（RAG 用）；不维护 SQLite/MySQL 兼容路径
- **缓存 / 队列**：Redis（Celery broker + backend）
- **测试**：pytest 8.4 + allure-pytest 2.15；Playwright（Web UI）+ Selenium（兼容/降级）+ Appium（移动端）
- **AI 网关**：多 provider（OpenAI / Anthropic / Ollama，embedding 走同一抽象）

---

## Build / Lint / Test

### Backend (Python)

```bash
pip install -r requirements.txt
playwright install                 # 装 chromium 内核；本地需手动跑（Docker 镜像已内置 chromium）

python server/main.py              # FastAPI，127.0.0.1:54351
celery -A celery_app worker --loglevel=info
celery -A celery_app beat --loglevel=info    # probe_devices 30s 心跳，集群里只能起 1 个

alembic upgrade head
alembic revision --autogenerate -m "xxx"      # ⚠️ autogenerate 经常漏 server_default / index 改动，review 后再合
```

**同步调试**（不走 Redis / worker，`.delay()` 在当前进程跑）：

```bash
CELERY_TASK_ALWAYS_EAGER=1 uvicorn server.main:app --port 54351
```

**跑测试 = 跑用例**：没有传统 unit test 套，"跑测试"指通过 v2 唯一入口 `tests/service_run_executor.py::TestService::test_case_runner` 执行实际用例。一般经 `POST /api/run_test` 触发，平台自动拼 pytest 命令；手动调用示例：

```bash
pytest -s -v \
  -p config.pytest_config \
  --report_id=1 \
  --category=api \
  --alluredir=data/results/test_001 \
  tests/service_run_executor.py::TestService::test_case_runner \
  --cases_data='[{"id":1,"name":"登录","case_type":"api","steps":[...]}]'
```

**没有 Python lint / format**（无 ruff / black / pyproject.toml）。提交前最低自查：`python -m compileall .`。

### Frontend (TypeScript)

```bash
cd frontend
npm install                # 本地；CI/Docker 用 npm ci
npm run dev                # Vite，5173；API 代理 → 127.0.0.1:54351
npm run build              # tsc -b && vite build → frontend/dist
npm run typecheck          # tsc -b --noEmit
npm run lint               # eslint，--max-warnings 0
```

### Docker

```bash
docker compose up -d       # postgres + redis + api + worker + beat
docker compose logs -f worker
```

---

## Architecture

### 1. v2 唯一执行链路（自动化用例）

所有 case_type（api / web / android / ios / mixed）走**同一条管道**。v1 的 `test_api_runner`、CLI 路径下的 `api_runner.py / mobile_runner.py` 已删 ——**不要再加新的 runner 入口**。

```
POST /api/run_test  (server/api/runs.py)
  → 创建 TestReport(status=running)，提交 Celery
  → tasks/run_test_task.py
       └─ pytest.main([
            "-p config.pytest_config",                           # 全局钩子（动态参数化 / TestStepReport 入库）
            "tests/service_run_executor.py::TestService::test_case_runner",
            "--cases_data=<json>",
            "--alluredir=data/results/<task_id>",
          ])
            └─ CaseExecutor.run(case, ctx)                        # 用例级编排（runners/case_executor.py）
                 └─ StepDispatcher.dispatch(step, ctx)            # 派发 + retry / wait_before / on_failure 都在这一层
                      ├─ http_request    → HttpRequestStepRunner
                      ├─ web_*           → build_web_runners       (Playwright / Selenium 都用同一组 Runner)
                      ├─ app_*           → build_app_runners + 通用 device_action
                      ├─ sleep / assert  → SleepStepRunner / AssertStepRunner
                 → CaseResult → Allure attachments + record_property
       └─ 收尾：sync_allure_to_db + finalize_report（database/data_sync.py）
       └─ 异常兜底：force_error_status，**绝不让报告卡在 "running"**
```

**核心不变量**：
- **Runner 永不 raise**：所有异常包装为 `StepResult(status=FAILED|ERROR)`（见 `runners/protocol.py`）。`AssertionError` → FAILED，其他 → ERROR + traceback。
- **Runner 接 dict + ExecutionContext，不依赖 ORM** —— 这是为了将来能脱离平台单跑。
- **重试 / wait_before / on_failure 在 dispatcher 实现一次**，Runner 内部别再写。
- **没有 steps 的老 API 用例**先经 `database/migrations/data_migrations/v2_cases_to_steps.py` 迁移，CaseExecutor 遇到没 steps 的直接抛错。

### 2. AI Studio M1 链路（对话写需求 → 编码）

```
PmWorkspace → "AI 需求工作间"
  ├─ POST /api/ai/dialogue/sessions                        # 起 session + 立刻派第一轮（Celery）
  │   → tasks/ai_dialogue_task.py::run_dialogue_turn_task
  │       └─ coding_agent.prompt_templates.run_dialogue_turn
  │           └─ ai_gateway.chat_json("ai_studio_dialogue_turn", ...)
  ├─ POST /api/ai/dialogue/sessions/{id}/turns             # 用户每发一句话同上
  ├─ POST /api/ai/dialogue/sessions/{id}/finalize
  │   → tasks/ai_dialogue_task.py::finalize_dialogue_task
  │       └─ chat_json("ai_studio_finalize") → markdown + spec_json
  │       → AiRequirementDraft(status=pending_review)
  └─ POST /api/ai/requirement-drafts/{id}/commit           # 草稿 → requirements 表，source=ai_generated
```

编码链路（Batch 5+，schema 已就位）：

```
requirement (committed)
  → POST /api/ai/coding-tasks
  → tasks/ai_coding_task.py
       ├─ coding_agent.rag.indexer       # 首次扫码仓 → code_chunks（pgvector）
       ├─ coding_agent.rag.retriever     # cosine top-k
       ├─ coding_agent.prompt_templates  # coding prompt（含 RAG 片段）
       ├─ coding_agent.diff.parser/validator/applier
       └─ coding_agent.git_ops           # clone / temp branch / commit / push（AES-256 解密凭证）
  → coding_tasks.diff_blob → 前端 DiffViewerDrawer
  → POST /accept（按 hunks）→ /push
```

### 3. AI 一键修复 Bug 链路

```
Bug 列表 [AI修复] 按钮
  → BugFixDialog（选择智能体：LLM / OpenCode / Codex / Claude Code）
  → POST /api/tasks/{id}/ai-fix  { agent_name }
    → 创建 AiRun(status=pending, feature='bug_fix')
    → Celery: tasks/bug_fix_task.py::run_bug_fix_task
      ├─ 有 Git 配置 ──────────────────────────────
      │  ① GitOps.ensure_clone()  拉取仓库
      │  ② GitOps.temp_branch("fix/bug-{id}-{ts}")
      │  ③ 执行智能体修改代码
      │     LLM: RAG 检索 → chat_json("bug_fix") → diff → patch apply
      │     CLI: subprocess.run → 直接改文件
      │  ④ GitOps.commit_all("fix: {title}")
      │  ⑤ GitOps.push(branch)
      │  ⑥ Task.status=dev_done, 写入 fix_description/fix_commit_sha
      │
      └─ 无 Git 配置 ──────────────────────────────
         ① LLM 生成修复建议
         ② 写入 Task.metadata.fix_suggestion
  前端轮询 GET /api/ai/runs/{id}
  POST /api/tasks/{id}/ai-fix/rollback → 回滚（删远程分支 + 恢复状态）
```

智能体抽象：`server/services/bug_fix_service.py` 定义 `execute_fix(bug, agent_config, git_ops)`，LLM 与 CLI 实现同一接口。CLI Agent 通过 `which` 检测可用性，不可用时前端灰显。

**异步任务统一范式**（所有 AI 任务沿用）：
1. API 层创建 `AiRun(status=pending, feature, project_id, input_payload, operator)` → `db.commit()`
2. API 层 `task.delay(ai_run_id)` → 回写 `celery_task_id`
3. Task 层 load AiRun → 置 running → 干活 → 回写 `output_payload / meta / tokens / cost / status=success|failed`
4. 前端轮询 `GET /api/ai/runs/{id}` 拿结果

### 3. 服务层分层

| 层 | 目录 | 约束 |
|---|---|---|
| HTTP | `server/api/` | 一个 REST 资源一个文件；用 `db: DBDep` 注入 session（`server/api/deps.py` 自动 commit/rollback/close）。**路由内一般不要手动 commit**，留给 deps 兜底。响应统一 `{status: success\|error, data?, message?}`。 |
| 业务 | `server/services/` | 不碰 HTTP，可被路由 / Celery 任务复用（如 `git_config_service`、`requirement_context_builder`、`task_service`） |
| ORM + Schema | `database/models/` + `database/schemas/` | SQLAlchemy 2.0 风格；JSON 列用 `database.base.JSONType`（PG → JSONB；其他后端兜底 JSON，但项目只跑 PG）；预加载用 `selectinload()` |
| 步骤执行 | `runners/` | `runners/protocol.py` → `StepDispatcher` → `Runner`。不依赖 ORM。 |
| AI 网关 | `ai_gateway/` | `gateway.chat_json(feature, ...)` + `embeddings.py`；providers 在 `ai_gateway/providers/`（anthropic / openai / ollama）；prompt 模板在 `ai_gateway/prompts/`。**不做持久化**，由 `tasks/ai_tasks.py` / `tasks/ai_dialogue_task.py` 落库。 |
| 编码 agent | `coding_agent/` | `prompt_templates` + `rag/` (indexer / embedder / retriever) + `diff/` (parser / applier / validator) + `git_ops`。底层 LLM 调用复用 `ai_gateway.chat_json`，不在 ai_gateway 里塞业务逻辑。 |
| 执行上下文 | `runners/context/` + `runners/app/` + `utils/captcha/` | `ExecutionContext` 装变量 / 日志 / attachments / `record_property` 句柄；设备池在 `runners/app/`；captcha 在 `utils/captcha/` |
| 异步任务 | `tasks/` | `run_test_task` / `probe_devices`（30s 心跳）/ `ai_tasks` / `ai_dialogue_task` / `rag_index_task` / `bug_fix_task` |

### 4. 目录约定

| 目录 | 职责 |
|---|---|
| `server/` | FastAPI 入口 + API 路由 + 服务层。**包名不能叫 `platform/`**（会遮蔽 stdlib `platform`，SQLAlchemy import 期挂）。 |
| `database/` | SQLAlchemy ORM (`models/`) + Pydantic schemas (`schemas/`) + Alembic migrations |
| `runners/` | v2 步骤引擎（协议 → Dispatcher → Runner） |
| `ai_gateway/` | 多 provider AI 网关 + 通用 prompt 模板 |
| `coding_agent/` | RAG + diff + git_ops；AI 写代码闭环模块 |
| `tasks/` | Celery 异步任务 |
| `config/` | `pytest.ini`、`pytest_config.py`、`object_conf.ini`（业务配置） |
| `utils/` | 工具函数（Allure / 加密 / 日志 / read_conf 等） |
| `frontend/` | React SPA；产物落 `frontend/dist/`，由 FastAPI 兜底托管（SPA fallback） |
| `data/` | 运行期产物（不进仓）：`reports/`（Allure HTML）、`results/`（allure 原始）、`attachments/`（需求附件）、`app_packages/`（APK/IPA）、`db/`（仅本地 sqlite 残留） |

---

## Python 代码风格

### 文件头 & Import 顺序

每个 `.py` 文件以 `from __future__ import annotations` 开头。Import 顺序：

1. 标准库
2. 第三方
3. 项目内包

```python
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func as sa_func

from server.api.deps import DBDep, CurrentUserDep
from database.models import TestCase
```

### 命名约定

- **模块**：`snake_case`（`http_request.py`、`ai_dialogue_task.py`）
- **类**：`PascalCase`（`StepDispatcher`、`AiDialogueSession`）
- **函数/方法**：`snake_case`
- **私有函数/方法**：`_` 前缀（`_get_session_or_404`、`_normalize_spec`）
- **常量**：`UPPER_SNAKE`（`BASE_DIR`、`REPORTS_DIR`、`FEATURE_DIALOGUE_TURN`）

### 类型注解

- 用 Python 3.10+ `X | None` 语法（不用 `Optional[X]`）；项目目标 runtime 是 Python 3.12，本地若有 3.9 venv 自行升级或走 Docker
- 协议用 `typing.Protocol`，实现侧 duck typing 即可
- FastAPI 依赖用 `Annotated[X, Depends(...)]` 别名（见 `server/api/deps.py` 的 `DBDep` / `CurrentUserDep`）

### 错误处理

**Runner 层**：`StepRunner.execute()` **绝不 raise**：
- `AssertionError` → `StepStatus.FAILED`
- 其他 `Exception` → `StepStatus.ERROR`
- 统一带 traceback 字符串

**API 层**：用 `fastapi.HTTPException`：

```python
raise HTTPException(status_code=404, detail="用例不存在")
raise HTTPException(status_code=422, detail="steps[0].step_type 不能为空")
```

响应信封统一 `{status: "success"|"error", data?, message?}`，前端 `request<T>()` 解析为 `ApiError`。

### 数据库

- SQLAlchemy 2.0：`declarative_base()` + `sessionmaker`
- JSON 列用 `database.base.JSONType` → PG JSONB；项目**只跑 PG**，不为 SQLite/MySQL 写兼容代码
- pgvector 向量列：`from pgvector.sqlalchemy import Vector`；维度由 embedding 模型决定（在 `ai_models` 里记），换模型要重建索引
- 路由经 `db: DBDep` 注入，**不手动 commit**（deps 兜底）；写多表事务时 `db.session.flush()` 拿 id，最后由 deps commit
- 预加载关系用 `selectinload()`

### 其他

- Docstring、注释**全中文**
- 4 空格缩进
- noqa：`BLE001`（broad Exception）、`ARG001`（未用参数）按需
- 项目根：`_PROJECT_ROOT = Path(__file__).resolve().parent.parent`，不用 `Path.cwd()` 也不硬编码
- 不要遮蔽 stdlib 名字（`platform`、`types`、`io` 等）

---

## TypeScript 代码风格

### Import 顺序

1. React 相关（`react`、`react-router-dom`）
2. 第三方（`@tanstack/react-query`、`lucide-react`、`zod`、`sonner`）
3. UI 组件（`@/components/ui/...`）
4. 项目内部（`@/lib/...`、`@/types/...`、`@/pages/...`）

```tsx
import { useQuery, useMutation } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { aiDialogueApi } from "@/lib/api";
import type { AiDialogueSession } from "@/types/domain";
```

### 路径别名

`@/*` → `frontend/src/*`（`tsconfig.app.json` + `vite.config.ts` 双声明，改一处忘改另一处会 build 不出）。

### 命名

- 文件名：组件 PascalCase（`ProjectDetailPage.tsx`）；工具 / 类型 kebab/snake（`api.ts`、`domain.ts`）
- 组件：`PascalCase`；函数 / 变量：`camelCase`；类型 / 接口：`PascalCase`；常量：`UPPER_SNAKE`

### 类型

- strict 全开（`strict`、`noUnusedLocals`、`noUnusedParameters`）
- 领域类型集中 `src/types/domain.ts`
- API 响应用泛型 `ApiEnvelope<T>`

### 组件模式

- 函数组件 + Hooks（无 class component）
- 页面在 `src/pages/<feature>/`，复杂 feature 拆 `components/` `dialogs/` `viewers/` `tabs/` 子目录
- 数据获取统一 `@tanstack/react-query`（`useQuery` / `useMutation`），key 用 `[resource, id]` 数组
- 表单用 `react-hook-form` + `zod`
- 样式 Tailwind utility class，复杂合并用 `cn()` from `@/lib/utils`
- shadcn/ui 在 `src/components/ui/`（基于 Radix）

### 错误处理

- API 调用通过 `src/lib/api.ts` 的 `request<T>()`，业务错误抛 `ApiError(message, status, payload)`
- 上游 try/catch 或 TanStack Query `onError`，用户提示统一 `sonner` toast

---

## 配置

- **平台业务配置**：`config/object_conf.ini`（DB 连接、Host、Appium、设备参数）→ 由 `utils/read_conf.read_conf` 读
- **pytest 配置**：`config/pytest.ini` + `config/pytest_config.py`（动态参数化 + TestStepReport 入库）
- **Alembic**：`alembic.ini` + `database/migrations/env.py`。**DB URL 不在 alembic.ini**，env.py 从 `object_conf.ini` 读，可用 `ALEMBIC_DB_URL` env 临时覆盖
- **环境变量**：
  - `BACKEND_CORS_ORIGINS`（CORS，默认 `*`）
  - `CELERY_TASK_ALWAYS_EAGER=1`（同步调试）
  - `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`（Celery broker / backend，docker-compose 默认注入）
  - `PLATFORM_SECRET_KEY`（AES-256 主密钥，加解密 Git 凭证；docker-compose 通过 `.env` 注入；不入仓）
  - `PYTHONUNBUFFERED=1`、`TZ=Asia/Shanghai`

---

## 重点 trap（踩过坑的地方）

1. **包名 `server/` 不是 `platform/`**：`platform` 会遮蔽 stdlib，SQLAlchemy import 时 `platform.python_implementation()` 直接挂。文档 / 历史代码出现 `platform.xxx` 都按 `server.xxx` 读。
2. **Playwright 内核要单独装**：`pip install playwright` 后还得 `playwright install`（本地）。Docker 镜像已内置 chromium，容器内无需再装。
3. **Celery EAGER 是排查首选**：`CELERY_TASK_ALWAYS_EAGER=1` → `.delay()` 在当前进程同步跑，print 落 uvicorn 终端。报告卡在 running 99% 是 worker 没起 / Redis 没连上 —— 先 EAGER 验证链路本身。
4. **路径锚点用 `_PROJECT_ROOT`**：`Path.cwd()` 在 uvicorn 不同 cwd 启动时会全错位。
5. **Alembic autogenerate 经常漏迁移**：`server_default`、`index` 改动需要手动补。每次 `alembic revision --autogenerate` 后必须 review 文件。
6. **JSONType 列直接赋值不会触发脏检查**：改 `turns / coverage / spec_json / acceptance_criteria` 这类列时，把整个对象重新赋值（`obj.turns = [*obj.turns, new_turn]`），不要原地 `.append()`。
7. **AI 任务签名统一是 `(ai_run_id)`**：API 先建 `AiRun(pending)` + commit，再 `.delay(run.id)`，task 内自己 load。**不要**把业务 payload 当 Celery 参数传。
8. **数据库只跑 PG**：不要"为 SQLite 加 fallback"。`JSONB` / `Vector` / GIN 索引随便用。
9. **`AI Studio M1` 的 LLM 调用 stub 在哪**：测试时要同时 stub `ai_gateway.gateway.chat_json`、`ai_gateway.chat_json`、`coding_agent.prompt_templates.chat_json` 三处 —— `from ... import chat_json` 会在各模块产生独立绑定，只 patch 一处不够。

---

## 修改某类东西时该动哪儿

| 想做的事 | 改这些 |
|---|---|
| 加新 step type | `runners/steps/` 写 Runner → 在 `StepDispatcher.default()` 注册。**不要自己实现 retry**。 |
| 加新 REST 资源 | `server/api/<name>.py` 写 router → `server/api/__init__.py` 导出 → `server/main.py` 的 router 循环加进去（自动挂 `/api`） |
| 改 DB schema | 编辑 `database/models/` → `alembic revision --autogenerate -m "..."` → **review 迁移文件** → `alembic upgrade head` |
| 加 Celery 任务 | `tasks/` 加文件 → `celery_app.py` 底部 `import tasks.xxx  # noqa: F401` 注册 → `tasks/__init__.py` 按需 re-export |
| 加 AI feature | `ai_gateway/prompts/` 加 prompt 模板 → `coding_agent/prompt_templates.py`（或新 wrapper）封装 → `tasks/` 加 Celery 任务 → `server/api/` 加路由 → 前端 hooks |
| 加 Bug Fix 智能体 | `server/services/bug_fix_service.py` 加 Agent 类 → `config_store` 表注册配置（category=`bug_fix_agent`）→ CLI 类型自动检测 PATH 可用性 |
| 加 LLM provider | `ai_gateway/providers/` 实现 `chat` / `embed` 接口 → 在 `ai_gateway/gateway.py` 注册 → `ai_models` 表新增配置 |
| 加前端页面 | `src/pages/<feature>/` → 在 `src/routes.tsx` 注册 → 数据获取统一 react-query → 入口按钮挂到对应 Workspace |

---

## 不在仓库 / 运行期产物

- `data/`（reports / results / attachments / app_packages / db）：本地产物，`.gitignore`
- `venv/`、`__pycache__/`：忽略
- `frontend/node_modules/`、`frontend/dist/`：忽略
- `.env`：放 `PLATFORM_SECRET_KEY` 等敏感量，**绝不入仓**

## Imported Claude Cowork project instructions
