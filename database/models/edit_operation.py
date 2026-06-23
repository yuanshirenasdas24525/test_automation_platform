"""EditOperation —— 可回滚编辑操作记录。

Batch 表示一次用户动作；Event 表示这次动作里某个实体的一条变更。
Event 不外键到业务实体，避免业务实体硬删后历史也被级联删除。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


EDIT_ACTION_CREATE = "create"
EDIT_ACTION_UPDATE = "update"
EDIT_ACTION_DELETE = "delete"
EDIT_ACTION_MIXED = "mixed"
ALL_EDIT_OPERATION_ACTIONS = {
    EDIT_ACTION_CREATE,
    EDIT_ACTION_UPDATE,
    EDIT_ACTION_DELETE,
    EDIT_ACTION_MIXED,
}

ROLLBACK_STATUS_NONE = "none"
ROLLBACK_STATUS_PARTIAL = "partial"
ROLLBACK_STATUS_FULL = "full"
ROLLBACK_STATUS_ROLLED_BACK = "rolled_back"
ALL_ROLLBACK_STATUSES = {
    ROLLBACK_STATUS_NONE,
    ROLLBACK_STATUS_PARTIAL,
    ROLLBACK_STATUS_FULL,
    ROLLBACK_STATUS_ROLLED_BACK,
}

ENTITY_TYPE_REQUIREMENT = "requirement"
ENTITY_TYPE_TEST_CASE = "test_case"


class EditOperationBatch(Base):
    __tablename__ = "edit_operation_batches"

    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String(64), nullable=False, index=True)
    action = Column(String(20), nullable=False, index=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    summary = Column(String(512), nullable=True)
    rollback_status = Column(
        String(20), nullable=False, default=ROLLBACK_STATUS_NONE, server_default=ROLLBACK_STATUS_NONE,
    )
    rollback_batch_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    events = relationship(
        "EditOperationEvent",
        cascade="all, delete-orphan",
        order_by="EditOperationEvent.id",
        back_populates="batch",
    )
    operator = relationship("User", foreign_keys=[operator_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "action": self.action,
            "operator_id": self.operator_id,
            "summary": self.summary,
            "rollback_status": self.rollback_status,
            "rollback_batch_id": self.rollback_batch_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class EditOperationEvent(Base):
    __tablename__ = "edit_operation_events"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(
        Integer,
        ForeignKey("edit_operation_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type = Column(String(64), nullable=False, index=True)
    entity_id = Column(Integer, nullable=True, index=True)
    entity_label = Column(String(255), nullable=True)
    action = Column(String(20), nullable=False, index=True)
    before_snapshot = Column(JSONType, nullable=True)
    after_snapshot = Column(JSONType, nullable=True)
    field_changes = Column(JSONType, nullable=True)
    rollback_status = Column(
        String(20), nullable=False, default=ROLLBACK_STATUS_NONE, server_default=ROLLBACK_STATUS_NONE,
    )
    rollback_event_id = Column(Integer, nullable=True, index=True)
    rollback_available = Column(Boolean, nullable=False, default=True, server_default="true")
    snapshot_expires_at = Column(DateTime, nullable=True, index=True)
    snapshot_purged_at = Column(DateTime, nullable=True)
    purge_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    batch = relationship("EditOperationBatch", back_populates="events")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_label": self.entity_label,
            "action": self.action,
            "before_snapshot": self.before_snapshot,
            "after_snapshot": self.after_snapshot,
            "field_changes": self.field_changes or [],
            "rollback_status": self.rollback_status,
            "rollback_event_id": self.rollback_event_id,
            "rollback_available": self.rollback_available,
            "snapshot_expires_at": (
                self.snapshot_expires_at.isoformat() if self.snapshot_expires_at else None
            ),
            "snapshot_purged_at": (
                self.snapshot_purged_at.isoformat() if self.snapshot_purged_at else None
            ),
            "purge_reason": self.purge_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
