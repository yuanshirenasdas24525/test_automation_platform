"""
Pydantic v2 schemas — 平台对外 API 的请求/响应体。

v2 设计原则：
- 所有新增/修改的接口都使用这里的 schema（而非 models/ 下的 *_create.py）
- *_create.py 是 v1 遗留，后续会被废弃
- schema 命名规范：XxxCreate（POST 请求）、XxxUpdate（PUT/PATCH 请求）、XxxRead（响应）
"""

from .test_step_schema import (
    TestStepCreate, TestStepUpdate, TestStepRead,
    ExtractRule, Assertion,
)
from .test_case_schema import (
    TestCaseCreateV2, TestCaseUpdateV2, TestCaseReadV2,
)
from .test_environment_schema import (
    TestEnvironmentCreate, TestEnvironmentUpdate, TestEnvironmentRead,
)
from .device_schema import (
    DeviceRead, DeviceHeartbeat, DeviceAcquireRequest, DeviceAcquireResponse,
)
from .ui_recording import (
    UiRecordingControlRequest,
    UiRecordingCreate,
    UiRecordingEventCreate,
    UiRecordingEventBatchCreate,
    UiRecordingLeaseRequest,
    UiRecordingMobileActionRequest,
    UiRecordingPickModeRequest,
    UiRecordingReplayRequest,
    UiRecordingRead,
    UiRecordingEventRead,
    UiPageSnapshotRead,
    UiElementLocatorRead,
    UiElementRead,
)

__all__ = [
    # Step
    "TestStepCreate", "TestStepUpdate", "TestStepRead",
    "ExtractRule", "Assertion",
    # Case (v2)
    "TestCaseCreateV2", "TestCaseUpdateV2", "TestCaseReadV2",
    # Environment
    "TestEnvironmentCreate", "TestEnvironmentUpdate", "TestEnvironmentRead",
    # Device
    "DeviceRead", "DeviceHeartbeat", "DeviceAcquireRequest", "DeviceAcquireResponse",
    # UI recording
    "UiRecordingCreate", "UiRecordingControlRequest", "UiRecordingLeaseRequest",
    "UiRecordingPickModeRequest", "UiRecordingMobileActionRequest",
    "UiRecordingEventCreate", "UiRecordingEventBatchCreate",
    "UiRecordingReplayRequest",
    "UiRecordingRead", "UiRecordingEventRead", "UiElementLocatorRead", "UiElementRead",
    "UiPageSnapshotRead",
]
