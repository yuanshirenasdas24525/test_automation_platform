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
from .config_update_item import ConfigUpdateItem

# ============ Pydantic 请求模型（v1 遗留，v2 将迁移到 schemas/） ============
from .project_create import ProjectCreate
from .module_create import ModuleCreate
from .test_case_create import TestCaseCreate
from .run_test_request import RunTestRequest
from .response_model import ResponseModel
from .reorder_item import ReorderItem, ReorderRequest


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
