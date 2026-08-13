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

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# ---------------------------------------------------------------------------
# 日志：统一格式 + 级别从 env 读（LOG_LEVEL=DEBUG/INFO/WARNING，默认 INFO）。
# 业务代码一律 `logger = logging.getLogger(__name__)`，不要用 print()。
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
)

# 把项目根塞到 sys.path，让 `platform / core / database / runners` 这些顶层
# 包都能直接 import（无论是 `uvicorn platform.main:app` 还是 `python platform/main.py`
# 启动）。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class _DownloadStaticFiles(StaticFiles):
    """给用户上传的附件强制加安全响应头，杜绝内联渲染带来的存储型 XSS。

    - ``Content-Disposition: attachment`` → 浏览器下载而非内联执行（含 SVG/HTML）
    - ``X-Content-Type-Options: nosniff`` → 关闭 MIME 嗅探，防止 .png 里塞 HTML 被当页面
    - ``Content-Security-Policy: sandbox`` → 即便被打开也运行在受限沙箱
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Content-Disposition"] = "attachment"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
        return resp


class _FrontendAssetStaticFiles(StaticFiles):
    """为带内容哈希的前端静态资源设置长期缓存。"""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

from server.api import (
    ai_router,
    ai_case_generation_router,
    web_ui_case_generation_router,
    ai_dialogue_router,
    ai_models_router,
    ai_requirements_router,
    api_keys_router,
    app_packages_router,
    attachments_router,
    auth_router,
    bug_fix_router,
    cases_router,
    api_cases_router,
    change_adjust_router,
    config_router,
    content_router,
    knowledge_router,
    devices_router,
    functional_cases_router,
    modules_router,
    project_versions_router,
    projects_router,
    requirement_analysis_router,
    requirements_router,
    reports_router,
    roles_router,
    runs_router,
    scripts_router,
    system_router,
    tasks_router,
    tasks_overview_router,
    test_plans_router,
    ui_recordings_router,
    users_router,
    version_summaries_router,
)
from server.api.auth import get_current_user

# ---------------------------------------------------------------------------
# 路径常量
#
# 重构前 main.py 在仓库根，所以 BASE_DIR 就是仓库根；重构后 main.py 搬到
# platform/ 下面，但 reports/ 和 frontend/dist 仍在仓库根。这里统一走
# _PROJECT_ROOT，不要再用 Path(__file__).parent，避免路径退化。
# ---------------------------------------------------------------------------
BASE_DIR = _PROJECT_ROOT
REPORTS_DIR = BASE_DIR / "data" / "reports"
ATTACHMENTS_DIR = BASE_DIR / "data" / "attachments"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Lifespan：启动时保证 reports 目录存在
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
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
    # 默认收紧为空（不放开任何跨域来源）。需要跨域时显式设置：
    #   BACKEND_CORS_ORIGINS="https://foo.com,https://bar.com"
    # 开发期要全放开，显式设 BACKEND_CORS_ORIGINS="*"。
    # 之所以不再默认 "*"：一旦将来改用 cookie 认证，通配来源就是 CSRF/跨域读取口子。
    raw = os.getenv("BACKEND_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    if raw == "*":
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
    auth_router,
    users_router,
):
    app.include_router(router, prefix="/api")


for router in (
    projects_router,
    modules_router,
    project_versions_router,
    cases_router,
    api_cases_router,
    change_adjust_router,
    functional_cases_router,
    content_router,
    knowledge_router,
    runs_router,
    scripts_router,
    reports_router,
    roles_router,
    config_router,
    system_router,
    devices_router,
    app_packages_router,
    attachments_router,
    requirements_router,
    requirement_analysis_router,
    tasks_router,
    tasks_overview_router,
    test_plans_router,
    ui_recordings_router,
    ai_router,
    ai_case_generation_router,
    web_ui_case_generation_router,
    ai_dialogue_router,
    ai_models_router,
    ai_requirements_router,
    version_summaries_router,
    bug_fix_router,
    api_keys_router,
):
    app.include_router(router, prefix="/api", dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# 静态资源
# ---------------------------------------------------------------------------
# Allure 报告：Celery worker 跑完会把产物写到这里，前端拿 /reports/<task_id>/ 打开
# StaticFiles 要求 import 时目录就存在（早于 lifespan），全新部署时 data/reports
# 还没被任何一次跑测试创建，所以这里必须先 mkdir，否则模块导入即崩。
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")

# 需求附件：用户上传到 data/attachments/req_{id}/，前端用 /attachments/req_{id}/{name} 访问。
# StaticFiles 要求 import 时目录就存在，所以这里先 mkdir 一次（lifespan 里还会再 ensure）。
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/attachments",
    _DownloadStaticFiles(directory=str(ATTACHMENTS_DIR)),
    name="attachments",
)

# React 前端 dist：开发期可能还没 build，mount 前先判一下
if (FRONTEND_DIST / "assets").is_dir():
    app.mount(
        "/assets",
        _FrontendAssetStaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="frontend-assets",
    )


def _frontend_index_response() -> FileResponse:
    """入口 HTML 禁止缓存，确保刷新时拿到与当前哈希资源一致的版本。"""
    return FileResponse(
        FRONTEND_DIST / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
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
        candidate = (FRONTEND_DIST / full_path).resolve()
        dist_root = FRONTEND_DIST.resolve()
        # 防目录穿越：resolve() 后必须仍落在 FRONTEND_DIST 内，
        # 否则 ../ 或 ..%2f 之类可读到 dist 外的任意文件。
        if candidate.is_file() and (candidate == dist_root or dist_root in candidate.parents):
            if candidate == FRONTEND_DIST / "index.html":
                return _frontend_index_response()
            return FileResponse(candidate)

    return _frontend_index_response()


if __name__ == "__main__":
    import uvicorn

    # 注意：在 uvicorn 重载机制下需要传字符串形式，指向当前模块。
    # 包名不能叫 platform（会遮蔽 stdlib 的 platform.python_implementation
    # 导致 SQLAlchemy import 就崩），所以平台服务层包叫 server/。
    uvicorn.run("server.main:app", host="127.0.0.1", port=54351)
