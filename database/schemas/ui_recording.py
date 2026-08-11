"""UI 录制中心对外 API Schema。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from database.models.ui_recording import ALL_UI_PLATFORMS


class UiRecordingCreate(BaseModel):
    project_id: int = Field(..., gt=0)
    platform: str
    name: str = Field(..., min_length=1, max_length=200)
    environment_id: int | None = Field(None, gt=0)
    device_id: int | None = Field(None, gt=0)
    app_package_id: int | None = Field(None, gt=0)
    source_url: str | None = Field(None, max_length=4000)
    capture_config: dict[str, Any] = Field(default_factory=dict)
    recording_role: str = Field("auto", pattern="^(auto|primary|supplement|history)$")
    baseline_session_id: int | None = Field(None, gt=0)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALL_UI_PLATFORMS:
            raise ValueError("platform 必须是 web/android/ios")
        return normalized


class UiRecordingBaselineUpdate(BaseModel):
    """维护补充会话是否进入主基线，或提升历史会话为新主基线。"""

    action: str = Field(..., pattern="^(include|exclude|promote)$")


class UiRecordingEventCreate(BaseModel):
    event_key: str = Field(..., min_length=1, max_length=80)
    sequence_no: int | None = Field(None, ge=1)
    event_type: str = Field(..., min_length=1, max_length=80)
    source: str = Field(..., min_length=1, max_length=40)
    severity: str = Field("info", min_length=1, max_length=20)
    page_key: str | None = Field(None, max_length=255)
    element_id: int | None = Field(None, gt=0)
    snapshot_before_id: int | None = Field(None, gt=0)
    snapshot_after_id: int | None = Field(None, gt=0)
    occurred_at: datetime
    monotonic_ms: int | None = Field(None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class UiRecordingEventBatchCreate(BaseModel):
    events: list[UiRecordingEventCreate] = Field(..., min_length=1, max_length=500)


class UiRecordingControlRequest(BaseModel):
    """录制控制端身份和命令幂等键。"""

    client_instance_id: str = Field(..., min_length=8, max_length=80)
    command_id: str = Field(..., min_length=8, max_length=80)
    takeover: bool = False


class UiRecordingLeaseRequest(BaseModel):
    """独立窗口和主窗口之间的短租约操作。"""

    client_instance_id: str = Field(..., min_length=8, max_length=80)
    action: str = Field("heartbeat", pattern="^(claim|heartbeat|takeover|release)$")


class UiRecordingPickModeRequest(BaseModel):
    """切换受控浏览器中的非破坏性元素拾取模式。"""

    client_instance_id: str = Field(..., min_length=8, max_length=80)
    command_id: str = Field(..., min_length=8, max_length=80)
    enabled: bool


class UiRecordingExplorationRequest(BaseModel):
    """Web AI 安全探索参数与控制端身份。"""

    client_instance_id: str = Field(..., min_length=8, max_length=80)
    command_id: str = Field(..., min_length=8, max_length=80)
    max_pages: int = Field(40, ge=1, le=200)
    max_depth: int = Field(4, ge=0, le=10)
    max_actions_per_page: int = Field(6, ge=0, le=20)
    timeout_seconds: int = Field(600, ge=30, le=3600)
    login_wait_seconds: int = Field(300, ge=0, le=1800)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=20)
    seed_urls: list[str] = Field(default_factory=list, max_length=200)


class UiRecordingReplayRequest(BaseModel):
    """启动严格离线的受控浏览器回放。"""

    browser: str = Field("chromium", pattern="^(chromium|firefox|webkit)$")
    headless: bool = False
    entry_url: str | None = Field(None, max_length=4000)
    page_fingerprint: str | None = Field(None, max_length=64)
    page_source_session_id: int | None = Field(None, gt=0)
    viewport: dict[str, int] = Field(default_factory=lambda: {"width": 1440, "height": 900})


class UiRecordingReplayActionRequest(BaseModel):
    """完成态离线画布转发到 Replay 浏览器的动作。"""

    action: str = Field(..., pattern="^(click|pick|input|scroll|back|refresh)$")
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
    text: str | None = Field(None, max_length=4000)
    delta_x: int = Field(0, ge=-10000, le=10000)
    delta_y: int = Field(0, ge=-10000, le=10000)


class UiPageSnapshotPickRequest(BaseModel):
    """在只读页面快照中按坐标拾取元素。"""

    x: int = Field(..., ge=0, le=10000)
    y: int = Field(..., ge=0, le=10000)


class UiRecordingWebActionRequest(BaseModel):
    """Web 录制预览画面的远程动作及控制身份。"""

    client_instance_id: str = Field(..., min_length=8, max_length=80)
    command_id: str = Field(..., min_length=8, max_length=80)
    action: str = Field(..., pattern="^(click|pick|input|scroll|back|refresh)$")
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
    text: str | None = Field(None, max_length=4000)
    delta_x: int = Field(0, ge=-10000, le=10000)
    delta_y: int = Field(0, ge=-10000, le=10000)


class UiRecordingMobileActionRequest(BaseModel):
    """移动端远程画面动作及录制控制身份。"""

    client_instance_id: str = Field(..., min_length=8, max_length=80)
    command_id: str = Field(..., min_length=8, max_length=80)
    action: str = Field(..., pattern="^(tap|input|swipe|back|refresh)$")
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
    end_x: int | None = Field(None, ge=0)
    end_y: int | None = Field(None, ge=0)
    duration_ms: int = Field(400, ge=100, le=5000)
    text: str | None = Field(None, max_length=4000)


class UiElementUpdate(BaseModel):
    """维护项目元素语义、别名和审核状态。"""

    semantic_name: str | None = Field(None, min_length=1, max_length=200)
    aliases: list[str] | None = Field(None, max_length=20)
    status: str | None = Field(None, pattern="^(pending|verified|stale|archived)$")


class UiElementLocatorCreate(BaseModel):
    strategy: str = Field(..., min_length=1, max_length=40)
    locator: str = Field(..., min_length=1, max_length=4000)
    score: int = Field(80, ge=0, le=100)
    is_primary: bool = False


class UiElementLocatorUpdate(BaseModel):
    strategy: str | None = Field(None, min_length=1, max_length=40)
    locator: str | None = Field(None, min_length=1, max_length=4000)
    score: int | None = Field(None, ge=0, le=100)
    is_primary: bool | None = None


class UiPageSnapshotUpdate(BaseModel):
    """人工维护逻辑页面和页面状态名称。"""

    page_name: str | None = Field(None, min_length=1, max_length=200)
    state_name: str | None = Field(None, min_length=1, max_length=120)
    apply_page_name_to_group: bool = True


class UiRecordedActionUpdate(BaseModel):
    """人工整理录制动作；ignored 动作不会进入用例草稿。"""

    name: str | None = Field(None, min_length=1, max_length=255)
    status: str | None = Field(None, pattern="^(captured|confirmed|ignored)$")
    sequence_no: int | None = Field(None, ge=1)
    payload: dict[str, Any] | None = None


class UiRecordingRead(BaseModel):
    id: int
    project_id: int
    platform: str
    status: str
    name: str
    recording_role: str
    baseline_session_id: int | None
    baseline_included: bool
    baseline_version: int
    merged_at: datetime | None
    environment_id: int | None
    device_id: int | None
    app_package_id: int | None
    created_by_id: int | None
    source_url: str | None
    recorder_agent_id: str | None
    offline_level: int
    capture_config: dict[str, Any]
    capabilities: dict[str, Any]
    context_summary: dict[str, Any]
    error: str | None
    event_count: int = 0
    snapshot_count: int = 0
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    paused_at: datetime | None
    ended_at: datetime | None


class UiRecordingEventRead(BaseModel):
    id: int
    session_id: int
    event_key: str
    sequence_no: int
    event_type: str
    source: str
    severity: str
    page_key: str | None
    element_id: int | None
    snapshot_before_id: int | None
    snapshot_after_id: int | None
    occurred_at: datetime
    monotonic_ms: int | None
    payload: dict[str, Any]
    created_at: datetime


class UiPageSnapshotRead(BaseModel):
    id: int
    session_id: int
    project_id: int
    platform: str
    page_key: str
    page_name: str
    state_name: str | None
    url: str | None
    snapshot_version: int
    fingerprint: str
    has_screenshot: bool
    has_document: bool
    has_offline_package: bool
    is_interactive: bool
    resource_manifest: dict[str, Any]
    environment: dict[str, Any]
    limitations: list[Any]
    created_at: datetime


class UiElementLocatorRead(BaseModel):
    id: int
    strategy: str
    locator: str
    score: int
    is_primary: bool
    is_unique: bool | None
    match_count: int | None
    source: str
    last_verified_snapshot_id: int | None
    last_verified_at: datetime | None


class UiElementRead(BaseModel):
    id: int
    project_id: int
    platform: str
    page_key: str
    page_name: str
    semantic_name: str
    element_type: str
    status: str
    fingerprint: str
    attributes: dict[str, Any]
    first_snapshot_id: int | None
    last_snapshot_id: int | None
    usage_count: int
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    locators: list[UiElementLocatorRead]
