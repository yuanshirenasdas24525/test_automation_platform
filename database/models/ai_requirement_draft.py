"""AiRequirementDraft —— "对话 → 需求草稿" 的中间态。

一个 session 终止可产出一份 draft；PM 在前端 review / 编辑后 commit，
真正落 requirements 表（同步回填 requirement_id）。

设计参照 M7 的 AiCaseDraft 范式：draft 与最终业务实体分离，便于反悔 / 重生成。

字段语义见 database/migrations/versions/20260514_0001_ai_studio_tables.py。
"""
from sqlalchemy import (
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
AI_REQ_DRAFT_STATUS_PENDING = "pending_review"   # 刚 finalize，等 PM review
AI_REQ_DRAFT_STATUS_COMMITTED = "committed"      # 已入 requirements 表
AI_REQ_DRAFT_STATUS_REJECTED = "rejected"        # 弃用
ALL_AI_REQ_DRAFT_STATUSES = {
    AI_REQ_DRAFT_STATUS_PENDING,
    AI_REQ_DRAFT_STATUS_COMMITTED,
    AI_REQ_DRAFT_STATUS_REJECTED,
}


class AiRequirementDraft(Base):
    __tablename__ = "ai_requirement_drafts"

    id = Column(Integer, primary_key=True, index=True)

    session_id = Column(
        Integer,
        ForeignKey("ai_dialogue_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 给 PM 看的 markdown（前端直接 render）
    markdown = Column(Text, nullable=False)
    # 结构化 spec：{title, user_story, acceptance_criteria[], nfr, module_deps[], priority}
    # 下游 coding agent / RAG 吃这个
    spec_json = Column(JSONType, nullable=True)

    status = Column(
        String(20),
        nullable=False,
        default=AI_REQ_DRAFT_STATUS_PENDING,
        server_default=AI_REQ_DRAFT_STATUS_PENDING,
        index=True,
    )

    # commit 后回填，指向真正的 requirements.id
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 产出这份草稿的 finalize 调用对应的 ai_runs.id（便于追溯 prompt / 重生成）
    ai_run_id = Column(Integer, ForeignKey("ai_runs.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    session = relationship("AiDialogueSession", back_populates="drafts")
    requirement = relationship("Requirement")
    ai_run = relationship("AiRun")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "markdown": self.markdown,
            "spec_json": self.spec_json or {},
            "status": self.status,
            "requirement_id": self.requirement_id,
            "ai_run_id": self.ai_run_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
