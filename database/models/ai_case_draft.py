"""AiCaseDraft —— M7 AI 一键生成测试用例的"草稿"行。

每次 AI 触发产 N 条草稿，PM 在 ReviewDialog 勾选/编辑后 batch_commit 才写入
test_cases。草稿 ↔ 用例 1:1 关系：commit 后 committed_case_id 回写指向用例。

字段语义见 database/migrations/versions/20260513_0002_ai_case_drafts.py。
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# 状态枚举
AI_CASE_DRAFT_STATUS_PENDING = "pending"      # 刚生成，等 PM review
AI_CASE_DRAFT_STATUS_ACCEPTED = "accepted"    # batch_commit 后落库
AI_CASE_DRAFT_STATUS_REJECTED = "rejected"    # PM 弃用（逻辑删）
ALL_AI_CASE_DRAFT_STATUSES = {
    AI_CASE_DRAFT_STATUS_PENDING,
    AI_CASE_DRAFT_STATUS_ACCEPTED,
    AI_CASE_DRAFT_STATUS_REJECTED,
}


class AiCaseDraft(Base):
    __tablename__ = "ai_case_drafts"

    id = Column(Integer, primary_key=True, index=True)

    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_document_id = Column(
        Integer,
        ForeignKey("requirement_analysis_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    ai_run_id = Column(Integer, ForeignKey("ai_runs.id"), nullable=True)

    # 同一次"触发批次"的 uuid（一次触发 N 需求 × M 模型 → 多个 batch_id）
    batch_id = Column(String(64), nullable=False, index=True)
    model_label = Column(String(100), nullable=True)

    # 用例字段
    title = Column(String(200), nullable=False)
    preconditions = Column(Text, nullable=True)
    steps_text = Column(Text, nullable=True)
    expected = Column(Text, nullable=True)
    priority = Column(Integer, nullable=False, default=2, server_default="2")
    tags = Column(JSONType, nullable=True)

    # M8 衔接占位：[{step_type, hint, needs_ui_detail}, ...]
    step_template = Column(JSONType, nullable=True)

    # 是否标记"需 UI 细节"
    needs_ui_detail = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    # PM 上传作为上下文的 UI 截图 attachment.id 列表
    ui_image_refs = Column(JSONType, nullable=True)

    status = Column(
        String(20),
        nullable=False,
        default=AI_CASE_DRAFT_STATUS_PENDING,
        server_default=AI_CASE_DRAFT_STATUS_PENDING,
        index=True,
    )
    committed_case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    requirement = relationship("Requirement")
    analysis_document = relationship("RequirementAnalysisDocument")
    ai_run = relationship("AiRun")
    # 用 foreign_keys 显式指明，避免与 TestCase.draft_id 形成歧义
    committed_case = relationship(
        "TestCase",
        foreign_keys=[committed_case_id],
        post_update=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "analysis_document_id": self.analysis_document_id,
            "ai_run_id": self.ai_run_id,
            "batch_id": self.batch_id,
            "model_label": self.model_label,
            "title": self.title,
            "preconditions": self.preconditions,
            "steps_text": self.steps_text,
            "expected": self.expected,
            "priority": self.priority,
            "tags": self.tags or [],
            "step_template": self.step_template or [],
            "needs_ui_detail": bool(self.needs_ui_detail),
            "ui_image_refs": self.ui_image_refs or [],
            "status": self.status,
            "committed_case_id": self.committed_case_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
