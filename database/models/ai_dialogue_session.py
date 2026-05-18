"""AiDialogueSession —— "AI 对话式写需求" 的会话主体（第 1 期 AI Studio）。

一个 session = 一次"PM 想写一条需求"的完整对话过程：
  - 用户先开 session 输入想法
  - 模型每轮反问 ≤3 个澄清问题，回写到 turns
  - 用户随时点"我说完了" → finalize → 产出 ai_requirement_drafts

字段语义见 database/migrations/versions/20260514_0001_ai_studio_tables.py。
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# 状态枚举
AI_DIALOGUE_STATUS_ACTIVE = "active"          # 进行中
AI_DIALOGUE_STATUS_FINALIZED = "finalized"    # 已 finalize，产出 draft
AI_DIALOGUE_STATUS_ABANDONED = "abandoned"    # 用户/超时放弃
ALL_AI_DIALOGUE_STATUSES = {
    AI_DIALOGUE_STATUS_ACTIVE,
    AI_DIALOGUE_STATUS_FINALIZED,
    AI_DIALOGUE_STATUS_ABANDONED,
}


class AiDialogueSession(Base):
    __tablename__ = "ai_dialogue_sessions"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(20),
        nullable=False,
        default=AI_DIALOGUE_STATUS_ACTIVE,
        server_default=AI_DIALOGUE_STATUS_ACTIVE,
        index=True,
    )

    # 完整对话 turns：[{role: "user"|"assistant", content: str, ts: int, asked_fields?: [...]}]
    turns = Column(JSONType, nullable=False, default=list)
    # 已澄清字段覆盖度：{title: true, user_story: true, ac: ["AC-1"], nfr: false}
    coverage = Column(JSONType, nullable=True)

    # 这次会话用的 LLM provider / 模型（审计 / 续跑）
    model_name = Column(String(80), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project")
    drafts = relationship(
        "AiRequirementDraft",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "status": self.status,
            "turns": self.turns or [],
            "coverage": self.coverage or {},
            "model_name": self.model_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
