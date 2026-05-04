"""
Database models 统一导出入口。

⚠️ 顺序敏感：被关系引用的 model 必须先导入
    - Project 要在 TestEnvironment 之前
    - Module / TestCase / TestStep 要有明确的相互可见性
    - 所有 model 都必须在 Alembic autogenerate 扫描前被 import 一次
"""

# ============ 业务主体 ============
from .project import (
    Project,
    PROJECT_STACK_API,
    PROJECT_STACK_WEB,
    PROJECT_STACK_ANDROID,
    PROJECT_STACK_IOS,
    PROJECT_STACK_FUNCTIONAL,
    ALL_PROJECT_STACKS,
)
from .module import Module
from .user import User
from .role import (
    Role,
    user_roles,
    ROLE_ADMIN,
    ROLE_DEV,
    ROLE_TEST,
    ROLE_PM,
    ROLE_UI,
    ROLE_OPS,
    ALL_ROLE_CODES,
)

from .task import (
    Task,
    TASK_TYPE_DEV,
    TASK_TYPE_TEST,
    TASK_TYPE_UI_REVIEW,
    TASK_TYPE_BUG,
    ALL_TASK_TYPES,
    TASK_STATUS_PENDING,
    TASK_STATUS_DEV_DOING,
    TASK_STATUS_DEV_DONE,
    TASK_STATUS_TEST_DOING,
    TASK_STATUS_PASSED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CLOSED,
    ALL_TASK_STATUSES,
    BUG_SEVERITY_P0,
    BUG_SEVERITY_P1,
    BUG_SEVERITY_P2,
    BUG_SEVERITY_P3,
    ALL_BUG_SEVERITIES,
)

# ============ 用例 & 步骤（v2 核心） ============
from .test_case import (
    TestCase,
    CASE_TYPE_API,
    CASE_TYPE_ANDROID,
    CASE_TYPE_IOS,
    CASE_TYPE_WEB,
    CASE_TYPE_MIXED,
    CASE_TYPE_FUNCTIONAL,
    ALL_CASE_TYPES,
    APP_CASE_TYPES,
    AUTOMATED_CASE_TYPES,
)
from .functional_case_run import (
    FunctionalCaseRun,
    RUN_STATUS_PASSED,
    RUN_STATUS_FAILED,
    RUN_STATUS_BLOCKED,
    RUN_STATUS_NA,
    RUN_STATUS_PENDING,
    ALL_RUN_STATUSES,
)
from .test_step import (
    TestStep,
    # 步骤类型常量，方便业务代码直接引用
    STEP_TYPE_HTTP_REQUEST,
    STEP_TYPE_SQL_QUERY,
    STEP_TYPE_SCRIPT,
    STEP_TYPE_APP_LAUNCH,
    STEP_TYPE_APP_CLOSE,
    STEP_TYPE_APP_TAP,
    STEP_TYPE_APP_INPUT,
    STEP_TYPE_APP_SWIPE,
    STEP_TYPE_APP_PRESS,
    STEP_TYPE_APP_WAIT,
    STEP_TYPE_APP_SCREENSHOT,
    STEP_TYPE_APP_BACK,
    STEP_TYPE_WEB_GOTO,
    STEP_TYPE_WEB_CLICK,
    STEP_TYPE_WEB_INPUT,
    STEP_TYPE_WEB_SELECT,
    STEP_TYPE_WEB_WAIT,
    STEP_TYPE_ASSERT,
    STEP_TYPE_SLEEP,
    ALL_STEP_TYPES,
)

# ============ 环境 & 变量（v2 新增） ============
from .test_environment import TestEnvironment
from .test_variable import TestVariable

# ============ 设备（v2 新增，App 专用） ============
from .device import (
    Device,
    DEVICE_STATUS_IDLE,
    DEVICE_STATUS_BUSY,
    DEVICE_STATUS_OFFLINE,
)

# ============ App 安装包仓库（v2 新增，App 包管理 / 选择器用） ============
from .app_package import AppPackage

# ============ 报告 ============
from .test_report import TestReport
from .test_step_report import TestStepReport
# from .hook_test_report import HookTestReport
# from .hook_test_step_report import HookTestStepReport

# ============ 配置 ============
from .config_store import ConfigStore

# ============ AI 任务 / 需求（AI 模块）============
from .ai_run import (
    AiRun,
    AI_RUN_STATUS_PENDING,
    AI_RUN_STATUS_RUNNING,
    AI_RUN_STATUS_SUCCESS,
    AI_RUN_STATUS_FAILED,
    AI_RUN_STATUS_CANCELLED,
    ALL_AI_RUN_STATUSES,
    AI_FEATURE_REQUIREMENT_PARSE,
    AI_FEATURE_TEST_PLAN,
    AI_FEATURE_FUNCTIONAL_CASE_GEN,
    AI_FEATURE_FUNCTIONAL_CASE_REVIEW,
    AI_FEATURE_API_CASE_GEN,
    AI_FEATURE_REPORT_SUMMARY,
    AI_FEATURE_FUNCTIONAL_TO_AUTO,
    AI_FEATURE_LOAD_PLAN_GEN,
)
from .requirement import (
    Requirement,
    REQUIREMENT_STATUS_DRAFT,
    REQUIREMENT_STATUS_APPROVED,
    REQUIREMENT_STATUS_ARCHIVED,
    ALL_REQUIREMENT_STATUSES,
    REQUIREMENT_SOURCE_MANUAL,
    REQUIREMENT_SOURCE_AI,
)
from .test_plan import (
    TestPlan,
    TEST_PLAN_STATUS_DRAFT,
    TEST_PLAN_STATUS_PUBLISHED,
    TEST_PLAN_STATUS_ARCHIVED,
    ALL_TEST_PLAN_STATUSES,
    TEST_PLAN_SOURCE_MANUAL,
    TEST_PLAN_SOURCE_AI,
)
from .project_context import (
    ProjectContext,
    RequirementAnalysis,
    CONTEXT_TYPE_BUSINESS_RULE,
    CONTEXT_TYPE_DATA_MODEL,
    CONTEXT_TYPE_API_CONTRACT,
    CONTEXT_TYPE_ARCHITECTURE,
    CONTEXT_TYPE_TERM_DEFINITION,
    CONTEXT_TYPE_REQUIREMENT,
    CONTEXT_TYPE_CONSTRAINT,
    CONTEXT_TYPE_USER_SCENARIO,
    CONTEXT_TYPE_PROCESS_FLOW,
    CONTEXT_TYPE_DEPENDENCY,
    ALL_CONTEXT_TYPES,
    CONTEXT_SOURCE_DOCUMENT,
    CONTEXT_SOURCE_MANUAL,
    CONTEXT_SOURCE_API,
    CONTEXT_SOURCE_ANALYSIS,
    CONTEXT_SOURCE_PLATFORM,
)
from .project_version import (
    ProjectVersion,
    VERSION_STATUS_PLANNING,
    VERSION_STATUS_DEVELOPING,
    VERSION_STATUS_TESTING,
    VERSION_STATUS_RELEASED,
    VERSION_STATUS_ARCHIVED,
    ALL_VERSION_STATUSES,
)

# ============ Pydantic 请求模型 —— 实际放在 database/schemas/ 下 =========
# 这里只做 re-export，让历史 `from database.models import XxxCreate` 路径
# 不破。新代码请直接 `from database.schemas.xxx import ...`。
from database.schemas.config_update_item import ConfigUpdateItem
from database.schemas.project_create import ProjectCreate
from database.schemas.module_create import ModuleCreate
from database.schemas.test_case_create import TestCaseCreate
from database.schemas.run_test_request import RunTestRequest
from database.schemas.response_model import ResponseModel
from database.schemas.reorder_item import ReorderItem, ReorderRequest


__all__ = [
    # ORM models
    "Project", "Module",
    "TestCase", "TestStep",
    "FunctionalCaseRun",
    "TestEnvironment", "TestVariable",
    "Device",
    "AppPackage",
    "TestReport", "TestStepReport",
    "ConfigStore",
    "AiRun", "Requirement", "TestPlan",
    "ProjectContext", "RequirementAnalysis",
    "ProjectVersion", "User",
    "Role", "user_roles",
    "ROLE_ADMIN", "ROLE_DEV", "ROLE_TEST", "ROLE_PM", "ROLE_UI", "ROLE_OPS",
    "ALL_ROLE_CODES",
    "Task",
    "TASK_TYPE_DEV", "TASK_TYPE_TEST", "TASK_TYPE_UI_REVIEW", "TASK_TYPE_BUG",
    "ALL_TASK_TYPES",
    "TASK_STATUS_PENDING", "TASK_STATUS_DEV_DOING", "TASK_STATUS_DEV_DONE",
    "TASK_STATUS_TEST_DOING", "TASK_STATUS_PASSED", "TASK_STATUS_FAILED",
    "TASK_STATUS_CLOSED", "ALL_TASK_STATUSES",
    "BUG_SEVERITY_P0", "BUG_SEVERITY_P1", "BUG_SEVERITY_P2", "BUG_SEVERITY_P3",
    "ALL_BUG_SEVERITIES",
    # Project context constants
    "CONTEXT_TYPE_BUSINESS_RULE", "CONTEXT_TYPE_DATA_MODEL",
    "CONTEXT_TYPE_API_CONTRACT", "CONTEXT_TYPE_ARCHITECTURE",
    "CONTEXT_TYPE_TERM_DEFINITION", "CONTEXT_TYPE_REQUIREMENT",
    "CONTEXT_TYPE_CONSTRAINT", "CONTEXT_TYPE_USER_SCENARIO",
    "CONTEXT_TYPE_PROCESS_FLOW", "CONTEXT_TYPE_DEPENDENCY",
    "ALL_CONTEXT_TYPES",
    "CONTEXT_SOURCE_DOCUMENT", "CONTEXT_SOURCE_MANUAL",
    "CONTEXT_SOURCE_API", "CONTEXT_SOURCE_ANALYSIS",
    "CONTEXT_SOURCE_PLATFORM",
    # Test plan constants
    "TEST_PLAN_STATUS_DRAFT", "TEST_PLAN_STATUS_PUBLISHED",
    "TEST_PLAN_STATUS_ARCHIVED", "ALL_TEST_PLAN_STATUSES",
    "TEST_PLAN_SOURCE_MANUAL", "TEST_PLAN_SOURCE_AI",
    # AI run status constants
    "AI_RUN_STATUS_PENDING", "AI_RUN_STATUS_RUNNING", "AI_RUN_STATUS_SUCCESS",
    "AI_RUN_STATUS_FAILED", "AI_RUN_STATUS_CANCELLED", "ALL_AI_RUN_STATUSES",
    # AI feature names
    "AI_FEATURE_REQUIREMENT_PARSE", "AI_FEATURE_TEST_PLAN",
    "AI_FEATURE_FUNCTIONAL_CASE_GEN", "AI_FEATURE_FUNCTIONAL_CASE_REVIEW",
    "AI_FEATURE_API_CASE_GEN", "AI_FEATURE_REPORT_SUMMARY",
    "AI_FEATURE_FUNCTIONAL_TO_AUTO", "AI_FEATURE_LOAD_PLAN_GEN",
    # Requirement constants
    "REQUIREMENT_STATUS_DRAFT", "REQUIREMENT_STATUS_APPROVED",
    "REQUIREMENT_STATUS_ARCHIVED", "ALL_REQUIREMENT_STATUSES",
    "REQUIREMENT_SOURCE_MANUAL", "REQUIREMENT_SOURCE_AI",
    # Project stack constants
    "PROJECT_STACK_API", "PROJECT_STACK_WEB",
    "PROJECT_STACK_ANDROID", "PROJECT_STACK_IOS",
    "PROJECT_STACK_FUNCTIONAL",
    "ALL_PROJECT_STACKS",
    # Case type constants
    "CASE_TYPE_API", "CASE_TYPE_ANDROID", "CASE_TYPE_IOS",
    "CASE_TYPE_WEB",
    "CASE_TYPE_MIXED", "CASE_TYPE_FUNCTIONAL",
    "ALL_CASE_TYPES", "APP_CASE_TYPES", "AUTOMATED_CASE_TYPES",
    # Functional run status constants
    "RUN_STATUS_PASSED", "RUN_STATUS_FAILED", "RUN_STATUS_BLOCKED",
    "RUN_STATUS_NA", "RUN_STATUS_PENDING", "ALL_RUN_STATUSES",
    # Step type constants
    "STEP_TYPE_HTTP_REQUEST", "STEP_TYPE_SQL_QUERY", "STEP_TYPE_SCRIPT",
    "STEP_TYPE_APP_LAUNCH", "STEP_TYPE_APP_CLOSE",
    "STEP_TYPE_APP_TAP", "STEP_TYPE_APP_INPUT", "STEP_TYPE_APP_SWIPE",
    "STEP_TYPE_APP_PRESS", "STEP_TYPE_APP_WAIT",
    "STEP_TYPE_APP_SCREENSHOT", "STEP_TYPE_APP_BACK",
    "STEP_TYPE_WEB_GOTO", "STEP_TYPE_WEB_CLICK", "STEP_TYPE_WEB_INPUT",
    "STEP_TYPE_WEB_SELECT", "STEP_TYPE_WEB_WAIT",
    "STEP_TYPE_ASSERT", "STEP_TYPE_SLEEP",
    "ALL_STEP_TYPES",
    # Device status constants
    "DEVICE_STATUS_IDLE", "DEVICE_STATUS_BUSY", "DEVICE_STATUS_OFFLINE",
    # Pydantic (v1 遗留)
    "ProjectCreate", "ModuleCreate", "TestCaseCreate", "RunTestRequest",
    "ConfigUpdateItem", "ResponseModel", "ReorderItem", "ReorderRequest",
]
