"""AI 生成 Web UI 自动化用例草稿。"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


UI_AUTO_DRAFT_PENDING = "pending"
UI_AUTO_DRAFT_ACCEPTED = "accepted"
UI_AUTO_DRAFT_REJECTED = "rejected"
ALL_UI_AUTO_DRAFT_STATUSES = {
    UI_AUTO_DRAFT_PENDING,
    UI_AUTO_DRAFT_ACCEPTED,
    UI_AUTO_DRAFT_REJECTED,
}


class UiAutomationCaseDraft(Base):
    """元素事实编译后的可执行 Web 用例草稿。"""

    __tablename__ = "ui_automation_case_drafts"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_id = Column(
        Integer,
        ForeignKey("modules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    functional_case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ai_run_id = Column(Integer, ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    batch_id = Column(String(64), nullable=False, index=True)
    model_label = Column(String(120), nullable=True)

    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(Integer, nullable=False, default=2, server_default="2")
    tags = Column(JSONType, nullable=False, default=list, server_default="[]")
    variables = Column(JSONType, nullable=False, default=dict, server_default="{}")
    steps = Column(JSONType, nullable=False, default=list, server_default="[]")
    evidence = Column(JSONType, nullable=False, default=dict, server_default="{}")
    warnings = Column(JSONType, nullable=False, default=list, server_default="[]")
    manual_reasons = Column(JSONType, nullable=False, default=list, server_default="[]")
    confidence = Column(Float, nullable=False, default=0.0, server_default="0")
    visual_assertion = Column(Boolean, nullable=False, default=False, server_default="false")

    status = Column(
        String(20),
        nullable=False,
        default=UI_AUTO_DRAFT_PENDING,
        server_default=UI_AUTO_DRAFT_PENDING,
        index=True,
    )
    committed_case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
    )
    reject_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("Project", foreign_keys=[project_id])
    module = relationship("Module", foreign_keys=[module_id])
    functional_case = relationship("TestCase", foreign_keys=[functional_case_id])
    ai_run = relationship("AiRun", foreign_keys=[ai_run_id])
    committed_case = relationship("TestCase", foreign_keys=[committed_case_id], post_update=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "module_id": self.module_id,
            "functional_case_id": self.functional_case_id,
            "ai_run_id": self.ai_run_id,
            "batch_id": self.batch_id,
            "model_label": self.model_label,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "tags": self.tags or [],
            "variables": self.variables or {},
            "steps": self.steps or [],
            "evidence": self.evidence or {},
            "warnings": self.warnings or [],
            "manual_reasons": self.manual_reasons or [],
            "confidence": float(self.confidence or 0),
            "visual_assertion": bool(self.visual_assertion),
            "status": self.status,
            "committed_case_id": self.committed_case_id,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
