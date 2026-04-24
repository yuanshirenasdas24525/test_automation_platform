"""
REST API 路由层。每个领域一个子模块，暴露一个 `router`（APIRouter）。

main.py 只负责把它们 include 进来，业务逻辑继续往 src/services、
src/runners、src/database 分层下沉。
"""
from .cases import router as cases_router
from .config import router as config_router
from .content import router as content_router
from .devices import router as devices_router
from .modules import router as modules_router
from .projects import router as projects_router
from .reports import router as reports_router
from .runs import router as runs_router
from .system import router as system_router

__all__ = [
    "cases_router",
    "config_router",
    "content_router",
    "devices_router",
    "modules_router",
    "projects_router",
    "reports_router",
    "runs_router",
    "system_router",
]
