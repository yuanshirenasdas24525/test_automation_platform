"""
FastAPI 应用入口。

重构后 main.py 只做"组装":
  - 生命周期（启动时建报告目录）
  - 中间件（CORS，来源从 env 读）
  - 路由注册（projects / modules / cases / content / runs / config）
  - 静态资源托管（Allure 报告 + React 前端 dist）
  - `/api/health` 健康检查
  - SPA fallback：非 /api/* 的请求都回退到 React 的 index.html，
    这样前端路由（react-router）可以自己处理 /projects 之类的子路径

业务逻辑全部下沉到 server/api/、server/services/、runners/。

关于目录名：架构方案里叫 platform/，但 `platform` 会遮蔽 Python stdlib 的
`platform` 模块，SQLAlchemy 等包 import 期就会调 platform.python_implementation()
直接挂掉。实际落地用 server/。
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 把项目根塞到 sys.path，让 `platform / core / database / runners` 这些顶层
# 包都能直接 import（无论是 `uvicorn platform.main:app` 还是 `python platform/main.py`
# 启动）。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.api import (
    ai_router,
    app_packages_router,
    cases_router,
    config_router,
    content_router,
    devices_router,
    functional_cases_router,
    modules_router,
    projects_router,
    requirements_router,
    reports_router,
    runs_router,
    system_router,
)

# ---------------------------------------------------------------------------
# 路径常量
#
# 重构前 main.py 在仓库根，所以 BASE_DIR 就是仓库根；重构后 main.py 搬到
# platform/ 下面，但 reports/ 和 frontend/dist 仍在仓库根。这里统一走
# _PROJECT_ROOT，不要再用 Path(__file__).parent，避免路径退化。
# ---------------------------------------------------------------------------
BASE_DIR = _PROJECT_ROOT
REPORTS_DIR = BASE_DIR / "data" / "reports"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Lifespan：启动时保证 reports 目录存在
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    # 配置中心走『推荐配置项面板』模式：启动时 *不* 写库，前端按
    # /api/config/schema/{category} 拉推荐项展示，用户决定要不要『一键填入』并保存。
    # （早期版本曾在这里 seed 五节『系统配置』并打 is_system=True 禁删，已彻底移除：
    #  utils/seed_system_configs.py 文件已删，is_system 列也通过 alembic drop 掉。）
    yield


# ---------------------------------------------------------------------------
# CORS：从 env 读，默认 "*"（开发期方便）。
# 生产环境设 BACKEND_CORS_ORIGINS="https://foo.com,https://bar.com"
# ---------------------------------------------------------------------------
def _cors_origins() -> list[str]:
    raw = os.getenv("BACKEND_CORS_ORIGINS", "*").strip()
    if raw == "*" or not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


# ---------------------------------------------------------------------------
# App 装配
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Automation Test Platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 健康检查：方便 k8s / 自动化脚本探活
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["meta"])
def health():
    return {
        "status": "success",
        "data": {
            "ok": True,
            "frontend_dist": FRONTEND_DIST.is_dir(),
        },
    }


# ---------------------------------------------------------------------------
# 业务路由：统一挂 /api
# ---------------------------------------------------------------------------
for router in (
    projects_router,
    modules_router,
    cases_router,
    functional_cases_router,
    content_router,
    runs_router,
    reports_router,
    config_router,
    system_router,
    devices_router,
    app_packages_router,
    requirements_router,
    ai_router,
):
    app.include_router(router, prefix="/api")


# ---------------------------------------------------------------------------
# 静态资源
# ---------------------------------------------------------------------------
# Allure 报告：Celery worker 跑完会把产物写到这里，前端拿 /reports/<task_id>/ 打开
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

# React 前端 dist：开发期可能还没 build，mount 前先判一下
if (FRONTEND_DIST / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="frontend-assets",
    )


# ---------------------------------------------------------------------------
# SPA fallback：catch-all 放在最后，前面所有路由没命中才走这里
#
# - /api/xxx 保持 404（上面的 include_router 没覆盖到就是真 404，别回退到 HTML）
# - /reports/xxx、/assets/xxx 已经被 mount 拦走了，不会进这里
# - 其它路径：有同名实体文件就返回，否则 fallback 到 index.html（React Router 自理）
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str = ""):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404)

    if not FRONTEND_DIST.is_dir():
        raise HTTPException(
            status_code=503,
            detail=(
                "前端 dist 还没构建。请先 `cd frontend && npm run build`，"
                "或在开发期直接用 Vite dev server (http://localhost:5173)。"
            ),
        )

    # 命中 dist 里的真实静态文件就直接返回（favicon.ico、manifest.json 之类）
    if full_path:
        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)

    return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    # 注意：在 uvicorn 重载机制下需要传字符串形式，指向当前模块。
    # 包名不能叫 platform（会遮蔽 stdlib 的 platform.python_implementation
    # 导致 SQLAlchemy import 就崩），所以平台服务层包叫 server/。
    uvicorn.run("server.main:app", host="127.0.0.1", port=54351)
