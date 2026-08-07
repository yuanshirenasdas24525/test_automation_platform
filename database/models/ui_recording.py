"""UI 录制中心的控制面与元素事实模型。

录制器只负责产生纯字典事件和快照证据，持久化由服务层完成；正式用例执行
仍然走现有 v2 Runner。本文件仅保存结构化索引，大体积画面、视频、DOM/UI Tree
和离线资源包通过 URI 指向存储服务。
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


UI_PLATFORM_WEB = "web"
UI_PLATFORM_ANDROID = "android"
UI_PLATFORM_IOS = "ios"
ALL_UI_PLATFORMS = {UI_PLATFORM_WEB, UI_PLATFORM_ANDROID, UI_PLATFORM_IOS}

UI_RECORDING_DRAFT = "draft"
UI_RECORDING_STARTING = "starting"
UI_RECORDING_RECORDING = "recording"
UI_RECORDING_PAUSED = "paused"
UI_RECORDING_STOPPING = "stopping"
UI_RECORDING_PROCESSING = "processing"
UI_RECORDING_COMPLETED = "completed"
UI_RECORDING_FAILED = "failed"
UI_RECORDING_CANCELLED = "cancelled"
ALL_UI_RECORDING_STATUSES = {
    UI_RECORDING_DRAFT,
    UI_RECORDING_STARTING,
    UI_RECORDING_RECORDING,
    UI_RECORDING_PAUSED,
    UI_RECORDING_STOPPING,
    UI_RECORDING_PROCESSING,
    UI_RECORDING_COMPLETED,
    UI_RECORDING_FAILED,
    UI_RECORDING_CANCELLED,
}

UI_ELEMENT_PENDING = "pending"
UI_ELEMENT_VERIFIED = "verified"
UI_ELEMENT_STALE = "stale"
UI_ELEMENT_ARCHIVED = "archived"
ALL_UI_ELEMENT_STATUSES = {
    UI_ELEMENT_PENDING,
    UI_ELEMENT_VERIFIED,
    UI_ELEMENT_STALE,
    UI_ELEMENT_ARCHIVED,
}


class UiRecordingSession(Base):
    """一次 Web 或模拟器录制会话。"""

    __tablename__ = "ui_recording_sessions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform = Column(String(20), nullable=False, index=True)
    status = Column(
        String(20),
        nullable=False,
        default=UI_RECORDING_DRAFT,
        server_default=UI_RECORDING_DRAFT,
        index=True,
    )
    name = Column(String(200), nullable=False)

    environment_id = Column(
        Integer,
        ForeignKey("test_environments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    device_id = Column(
        Integer,
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    app_package_id = Column(
        Integer,
        ForeignKey("app_packages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    source_url = Column(Text, nullable=True)
    recorder_agent_id = Column(String(128), nullable=True, index=True)
    offline_level = Column(Integer, nullable=False, default=3, server_default="3")
    capture_config = Column(JSONType, nullable=False, default=dict, server_default="{}")
    capabilities = Column(JSONType, nullable=False, default=dict, server_default="{}")
    context_summary = Column(JSONType, nullable=False, default=dict, server_default="{}")
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    started_at = Column(DateTime, nullable=True)
    paused_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    project = relationship("Project")
    environment = relationship("TestEnvironment")
    device = relationship("Device")
    app_package = relationship("AppPackage")
    created_by = relationship("User")
    events = relationship(
        "UiRecordingEvent",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    snapshots = relationship(
        "UiPageSnapshot",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    mock_exchanges = relationship(
        "UiMockExchange",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UiRecordingEvent(Base):
    """统一时间线事件：用户动作、Console、Network、导航和设备日志。"""

    __tablename__ = "ui_recording_events"
    __table_args__ = (
        UniqueConstraint("session_id", "event_key", name="uq_ui_event_session_key"),
        UniqueConstraint("session_id", "sequence_no", name="uq_ui_event_session_sequence"),
        Index("ix_ui_event_session_occurred", "session_id", "occurred_at"),
    )

    id = Column(BigInteger, primary_key=True)
    session_id = Column(
        Integer,
        ForeignKey("ui_recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_key = Column(String(80), nullable=False)
    sequence_no = Column(BigInteger, nullable=False)
    event_type = Column(String(80), nullable=False, index=True)
    source = Column(String(40), nullable=False, index=True)
    severity = Column(String(20), nullable=False, default="info", server_default="info")
    page_key = Column(String(255), nullable=True, index=True)
    element_id = Column(
        Integer,
        ForeignKey("ui_elements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    snapshot_before_id = Column(BigInteger, nullable=True)
    snapshot_after_id = Column(BigInteger, nullable=True)
    occurred_at = Column(DateTime, nullable=False, index=True)
    monotonic_ms = Column(BigInteger, nullable=True)
    payload = Column(JSONType, nullable=False, default=dict, server_default="{}")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    session = relationship("UiRecordingSession", back_populates="events")
    element = relationship("UiElement", foreign_keys=[element_id])


class UiPageSnapshot(Base):
    """一个可追溯的 Web 页面或移动端场景版本。"""

    __tablename__ = "ui_page_snapshots"
    __table_args__ = (
        Index("ix_ui_snapshot_project_page", "project_id", "platform", "page_key"),
    )

    id = Column(BigInteger, primary_key=True)
    session_id = Column(
        Integer,
        ForeignKey("ui_recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform = Column(String(20), nullable=False, index=True)
    page_key = Column(String(255), nullable=False, index=True)
    page_name = Column(String(200), nullable=False)
    state_name = Column(String(120), nullable=True)
    url = Column(Text, nullable=True)
    route = Column(String(500), nullable=True)
    app_identifier = Column(String(255), nullable=True)
    app_version = Column(String(80), nullable=True)
    snapshot_version = Column(Integer, nullable=False, default=1, server_default="1")
    fingerprint = Column(String(64), nullable=False, index=True)
    screenshot_uri = Column(Text, nullable=True)
    document_uri = Column(Text, nullable=True)
    tree_uri = Column(Text, nullable=True)
    offline_package_uri = Column(Text, nullable=True)
    is_interactive = Column(Boolean, nullable=False, default=False, server_default="false")
    resource_manifest = Column(JSONType, nullable=False, default=dict, server_default="{}")
    environment = Column(JSONType, nullable=False, default=dict, server_default="{}")
    limitations = Column(JSONType, nullable=False, default=list, server_default="[]")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    session = relationship("UiRecordingSession", back_populates="snapshots")
    project = relationship("Project")


class UiElement(Base):
    """按项目、平台和页面组织的元素事实。"""

    __tablename__ = "ui_elements"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "platform",
            "page_key",
            "fingerprint",
            name="uq_ui_element_page_fingerprint",
        ),
        Index("ix_ui_element_project_page", "project_id", "platform", "page_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform = Column(String(20), nullable=False, index=True)
    page_key = Column(String(255), nullable=False, index=True)
    page_name = Column(String(200), nullable=False)
    semantic_name = Column(String(200), nullable=False)
    element_type = Column(String(100), nullable=False)
    status = Column(
        String(20),
        nullable=False,
        default=UI_ELEMENT_PENDING,
        server_default=UI_ELEMENT_PENDING,
        index=True,
    )
    fingerprint = Column(String(64), nullable=False)
    attributes = Column(JSONType, nullable=False, default=dict, server_default="{}")
    first_snapshot_id = Column(
        BigInteger,
        ForeignKey("ui_page_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_snapshot_id = Column(
        BigInteger,
        ForeignKey("ui_page_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    usage_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project")
    first_snapshot = relationship("UiPageSnapshot", foreign_keys=[first_snapshot_id])
    last_snapshot = relationship("UiPageSnapshot", foreign_keys=[last_snapshot_id])
    locators = relationship(
        "UiElementLocator",
        back_populates="element",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="UiElementLocator.score.desc()",
    )


class UiElementLocator(Base):
    """元素的定位器候选及验证结果。"""

    __tablename__ = "ui_element_locators"
    __table_args__ = (
        UniqueConstraint(
            "element_id",
            "strategy",
            "locator",
            name="uq_ui_locator_element_strategy_value",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    element_id = Column(
        Integer,
        ForeignKey("ui_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy = Column(String(40), nullable=False, index=True)
    locator = Column(Text, nullable=False)
    score = Column(Integer, nullable=False, default=0, server_default="0")
    is_primary = Column(Boolean, nullable=False, default=False, server_default="false")
    is_unique = Column(Boolean, nullable=True)
    match_count = Column(Integer, nullable=True)
    source = Column(String(40), nullable=False, default="recorder", server_default="recorder")
    last_verified_snapshot_id = Column(
        BigInteger,
        ForeignKey("ui_page_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    element = relationship("UiElement", back_populates="locators")
    last_verified_snapshot = relationship("UiPageSnapshot")


class UiMockExchange(Base):
    """Web 离线业务回放使用的脱敏 XHR/Fetch 请求响应。"""

    __tablename__ = "ui_mock_exchanges"
    __table_args__ = (
        UniqueConstraint("session_id", "exchange_key", name="uq_ui_mock_session_key"),
        Index("ix_ui_mock_session_request", "session_id", "method", "request_key"),
    )

    id = Column(BigInteger, primary_key=True)
    session_id = Column(
        Integer,
        ForeignKey("ui_recording_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_id = Column(
        BigInteger,
        ForeignKey("ui_page_snapshots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    exchange_key = Column(String(80), nullable=False)
    sequence_no = Column(BigInteger, nullable=False)
    method = Column(String(12), nullable=False)
    url = Column(Text, nullable=False)
    request_key = Column(String(64), nullable=False)
    request = Column(JSONType, nullable=False, default=dict, server_default="{}")
    response = Column(JSONType, nullable=False, default=dict, server_default="{}")
    match_rule = Column(JSONType, nullable=False, default=dict, server_default="{}")
    timing = Column(JSONType, nullable=False, default=dict, server_default="{}")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    session = relationship("UiRecordingSession", back_populates="mock_exchanges")
    snapshot = relationship("UiPageSnapshot")
