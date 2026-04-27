# AGENTS.md — Automation Test Platform

## Project Overview

全栈自动化测试平台：后端 FastAPI + Celery，前端 React + Vite + shadcn/ui。
支持 API / Web UI / Android / iOS / Functional 五种用例类型的编写、执行与报告。

- **后端**: Python 3.12, FastAPI 0.135, Celery 5.6, SQLAlchemy 2.0, Pydantic 2.12
- **前端**: TypeScript 5.6, React 19, Vite 5, Tailwind 3, shadcn/ui
- **数据库**: 默认 SQLite（开发期）；生产可切 PostgreSQL/MySQL
- **消息队列**: Redis (Celery broker + backend)
- **测试框架**: pytest 8.4 + allure-pytest 2.15

---

## Build / Lint / Test

### Backend (Python)

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 FastAPI (端口 54351)
python server/main.py
# 或
uvicorn server.main:app --host 127.0.0.1 --port 54351

# Celery worker
celery -A celery_app worker --loglevel=info

# Celery beat (定时任务：设备心跳探测 30s)
celery -A celery_app beat --loglevel=info

# 数据库迁移
alembic upgrade head
```

**测试**:

```bash
# 跑全部（几乎没有传统单测；核心是"用例级端到端跑"）
pytest -s -v \
  -p config.pytest_config \
  --report_id=1 \
  --category=api \
  --alluredir=data/results/test_001 \
  tests/service_run_executor.py::TestService::test_case_runner \
  --cases_data='[{"id":1,"name":"登录","case_type":"api","steps":[{"step_type":"http_request","config":{"method":"POST","url":"/login","body":{"username":"admin","password":"123456"}}}]}]'

# 本地同步调试（Celery eager 模式，不走 worker）
CELERY_TASK_ALWAYS_EAGER=1 uvicorn server.main:app --port 54351
```

**没有配置 Python linter/formatter**。项目依赖 `requirements.txt`，无 `pyproject.toml`/`ruff.toml`/`.flake8`。
建议提交前至少确保没有语法错误（`python -m compileall .`）。

### Frontend (TypeScript)

```bash
cd frontend

# 安装
npm ci          # Docker/prod
npm install     # 本地开发

# 开发服务器 (端口 5173，API 代理到 127.0.0.1:54351)
npm run dev

# 构建
npm run build       # tsc -b && vite build

# 类型检查
npm run typecheck   # tsc -b --noEmit

# Lint
npm run lint        # eslint src --ext ts,tsx --report-unused-disable-directives --max-warnings 0
```

### Docker

```bash
# 完整编排（Redis + Postgres + API + Worker + Beat）
docker compose up -d

# 单独构建镜像
docker build -t test_auto_platform .
```

---

## Architecture

### 执行链路

```
FastAPI: POST /api/run_test
  └─ server/api/runs.py:run_test()
      ├─ 从 DB 加载用例 → 创建 TestReport
      └─ Celery task: tasks/run_test_task.py
           └─ pytest.main([
                "tests/service_run_executor.py::TestService::test_case_runner",
                "--cases_data=...",
              ])
                └─ CaseExecutor().run(case, ctx)
                    └─ StepDispatcher.default().dispatch(step, ctx)
                        ├─ http_request  → HttpRequestStepRunner
                        ├─ web_click/... → WebStepRunners
                        ├─ app_tap/...   → AppStepRunners
                        ├─ sleep         → SleepStepRunner
                        └─ assert        → AssertStepRunner
```

### 目录约定

| 目录 | 职责 |
|------|------|
| `server/` | FastAPI 应用 + API 路由 + 服务层 |
| `server/api/` | 每个 REST 资源一个文件，注入 `DBDep` (Annotated 依赖) |
| `server/services/` | 可复用的业务逻辑（不直接碰 HTTP） |
| `database/` | SQLAlchemy ORM (`models/`) + Pydantic schemas (`schemas/`) + migrations |
| `runners/` | 步骤级执行引擎：协议层 → Dispatcher → 具体 Runner 实现 |
| `core/` | 核心设施：执行上下文、设备池、代理 mock、captcha |
| `tasks/` | Celery 异步任务 |
| `config/` | pytest.ini、pytest_config.py（全局钩子）、object_conf.ini（业务配置） |
| `utils/` | 工具函数（Allure、加密、日志、read_conf 等） |
| `frontend/` | React SPA（Vite 构建产物落 `frontend/dist/`） |

---

## Python 代码风格

### 文件头 & Import 顺序

每个 `.py` 文件以 `from __future__ import annotations` 开头。Import 顺序：
1. 标准库 (`import os, sys, ...`)
2. 第三方 (`from fastapi import ...`, `from sqlalchemy import ...`)
3. 项目内包 (`from server.api.deps import DBDep`, `from database.models import ...`)

```python
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from server.api.deps import DBDep
from database.models import TestCase
```

### 命名约定

- **模块**: `snake_case` (如 `test_case_schema.py`, `http_request.py`)
- **类**: `PascalCase` (`StepDispatcher`, `TestCase`, `BaseStepRunner`)
- **函数/方法**: `snake_case` (`create_case`, `_infer_case_type`)
- **私有函数/方法**: `_` 前缀 (`_replace_case_steps`, `_serialize_case`)
- **常量**: `UPPER_SNAKE` (`BASE_DIR`, `REPORTS_DIR`, `ALL_STEP_TYPES`)
- **变量**: `snake_case`

### 类型注解

- 使用 Python 3.12+ `X | None` 语法（不用 `Optional[X]`）
- 协议用 `typing.Protocol` (runtime_checkable)，实现类 duck typing 即可
- FastAPI 依赖用 `Annotated[X, Depends(...)]` 别名

### 错误处理

**Runner 层**: StepRunner.execute() **绝不要 raise 异常**。所有异常包装为 `StepResult`：
- `AssertionError` → `StepStatus.FAILED`
- 其他 `Exception` → `StepStatus.ERROR`
- 统一带 traceback 字符串

**API 层**: 用 `fastapi.HTTPException` 返回错误（前端统一解析 `ApiError`）：
```python
raise HTTPException(status_code=404, detail="用例不存在")
raise HTTPException(status_code=422, detail="steps[0].step_type 不能为空")
```

**API 响应信封**: 所有接口返回 `{status: "success"|"error", data?, message?}`。

### 数据库

- SQLAlchemy 2.0 风格：`declarative_base()` + `sessionmaker`
- JSON 列用 `database.base.JSONType`（自动区分 PostgreSQL JSONB / SQLite/MySQL JSON）
- 懒加载关系用 `selectinload()` 预加载
- ORM 模型在 `database/models/`，Pydantic schema 在 `database/schemas/`
- 路由通过 `db: DBDep` 注入 session（`deps.py` 自动 commit/rollback/close）

### 其他约定

- Docstring 和注释使用中文
- noqa 标签：`BLE001`（捕获 broad Exception）、`ARG001`（未用参数）
- 4 空格缩进
- 项目根路径用 `_PROJECT_ROOT = Path(__file__).resolve().parent.parent` 计算（不要硬编码）

---

## TypeScript 代码风格

### Import 顺序

1. React 相关 (`react`, `react-router-dom`)
2. 第三方库 (`@tanstack/react-query`, `lucide-react`, `zod`)
3. UI 组件 (`@/components/ui/...`)
4. 项目内部 (`@/lib/...`, `@/types/...`, `@/pages/...`)

```tsx
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { projectsApi } from "@/lib/api";
import type { Project } from "@/types/domain";
```

### 路径别名

- `@/*` 映射到 `frontend/src/*`（在 `tsconfig.app.json` 和 `vite.config.ts` 中配置）

### 命名约定

- **文件名**: kebab-case 或 PascalCase（组件文件 `ProjectDetailPage.tsx`、工具文件 `api.ts`、类型文件 `domain.ts`）
- **组件**: `PascalCase` (`AppLayout`, `ProjectsPage`)
- **函数/变量**: `camelCase` (`fetchReports`, `isFormData`)
- **类型/接口**: `PascalCase` (`ApiEnvelope<T>`, `TestCaseCreate`, `ContentNode`)
- **常量**: `UPPER_SNAKE` (`ALL_PROJECT_STACKS`, `AUTOMATED_CASE_TYPES`)
- **私有函数**: `_` 前缀不强制；内部 tool 函数随意

### 类型

- TypeScript strict 模式 (`strict: true`, `noUnusedLocals`, `noUnusedParameters`)
- 领域类型集中在 `src/types/domain.ts`
- API 响应用泛型 `ApiEnvelope<T>`
- `type` 优先于 `interface`（联合类型场景用 type，对象形状用 interface 均可）

### 组件模式

- 函数组件 + Hooks（不用 class component）
- 页面组件在 `src/pages/`，一个文件一个组件
- 全局数据获取用 `@tanstack/react-query` (`useQuery` / `useMutation`)
- 表单用 `react-hook-form` + `zod` 校验
- 样式用 Tailwind CSS utility class，复杂合并用 `cn()` from `@/lib/utils`
- shadcn/ui 组件在 `src/components/ui/`（基于 Radix UI）

### 错误处理

- API 调用通过 `src/lib/api.ts` 的 `request<T>()` 函数
- 业务错误抛 `ApiError(message, status, payload)`
- 上游用 try/catch 或 TanStack Query 的 error 回调处理
- 用户提示用 `sonner` toast

---

## 配置

- **平台业务配置**: `config/object_conf.ini`（数据库连接、host 域名、Appium、设备参数等）
- **pytest 配置**: `config/pytest.ini` + `config/pytest_config.py`（全局钩子：动态参数化、TestStepReport 入库）
- **Alembic**: `alembic.ini` + `database/migrations/`
- **环境变量**: `BACKEND_CORS_ORIGINS` (CORS)、`PYTHONUNBUFFERED=1`、`TZ=Asia/Shanghai`

## 注意事项

1. **包名 `server/` 而非 `platform/`**：`platform` 会遮蔽 Python stdlib 的 `platform` 模块，导致 SQLAlchemy import 时崩溃。
2. **Playwright 需要手动安装浏览器内核**：`pip install playwright` 后还需 `playwright install`。
3. **不存在 AGENTS.md / .cursorrules / .github/copilot-instructions.md**（本文件是首次创建）。
4. **没有 Python linter/formatter 配置**，代码质量依赖人工 review。
