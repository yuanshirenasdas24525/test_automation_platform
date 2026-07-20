"""
REST API 路由层。每个领域一个子模块，暴露一个 `router`（APIRouter）。

main.py 只负责把它们 include 进来，业务逻辑继续往 src/services、
src/runners、src/database 分层下沉。
"""
from .ai import router as ai_router
from .ai_case_generation import router as ai_case_generation_router
from .api_keys import router as api_keys_router
from .ai_dialogue import router as ai_dialogue_router
from .ai_models import router as ai_models_router
from .ai_requirements import router as ai_requirements_router
from .app_packages import router as app_packages_router
from .attachments import router as attachments_router
from .auth import router as auth_router
from .bug_fix import router as bug_fix_router
from .cases import router as cases_router
from .api_cases import router as api_cases_router
from .config import router as config_router
from .content import router as content_router
from .devices import router as devices_router
from .functional_cases import router as functional_cases_router
from .modules import router as modules_router
from .project_versions import router as project_versions_router
from .projects import router as projects_router
from .reports import router as reports_router
from .requirement_analysis import router as requirement_analysis_router
from .requirements import router as requirements_router
from .runs import router as runs_router
from .scripts import router as scripts_router
from .roles import router as roles_router
from .tasks import router as tasks_router
from .tasks_overview import router as tasks_overview_router
from .users import router as users_router
from .system import router as system_router
from .test_plans import router as test_plans_router
from .version_summaries import router as version_summaries_router

__all__ = [
    "ai_router",
    "ai_case_generation_router",
    "ai_dialogue_router",
    "ai_models_router",
    "ai_requirements_router",
    "api_keys_router",
    "app_packages_router",
    "attachments_router",
    "auth_router",
    "bug_fix_router",
    "cases_router",
    "api_cases_router",
    "config_router",
    "content_router",
    "devices_router",
    "functional_cases_router",
    "modules_router",
    "project_versions_router",
    "projects_router",
    "reports_router",
    "requirement_analysis_router",
    "requirements_router",
    "runs_router",
    "scripts_router",
    "system_router",
    "tasks_router",
    "tasks_overview_router",
    "test_plans_router",
    "roles_router",
    "users_router",
    "version_summaries_router",
]
