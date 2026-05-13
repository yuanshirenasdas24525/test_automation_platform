"""RequirementEditHistory —— 需求字段变更历史（追加模式，不更新）。"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


class RequirementEditHistory(Base):
    __tablename__ = "requirement_edit_history"

    id = Column(Integer, primary_key=True, index=True)
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edited_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    changes = Column(JSONType, nullable=False, comment="[{field, old, new}, ...]")
    change_summary = Column(String(512), nullable=True, comment="操作人可选填的变更摘要")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    requirement = relationship("Requirement", back_populates="edit_histories")
    edited_by = relationship("User", foreign_keys=[edited_by_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "edited_by_id": self.edited_by_id,
            "changes": self.changes,
            "change_summary": self.change_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
