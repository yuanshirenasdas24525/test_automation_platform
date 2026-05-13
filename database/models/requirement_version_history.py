"""RequirementVersionHistory —— 需求迭代关联变更历史（仅追加）。

需求的 `version_id` 是单值（当前迭代）。每次 plan/move/remove 写一条 history，
带操作人、原因、from/to 版本，前端 Timeline 用。
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from database.base import Base


REQUIREMENT_VERSION_ACTION_PLAN = "plan"      # 第一次入迭代
REQUIREMENT_VERSION_ACTION_MOVE = "move"      # 从一个迭代挪到另一个
REQUIREMENT_VERSION_ACTION_REMOVE = "remove"  # 移出迭代回池
ALL_REQUIREMENT_VERSION_ACTIONS = {
    REQUIREMENT_VERSION_ACTION_PLAN,
    REQUIREMENT_VERSION_ACTION_MOVE,
    REQUIREMENT_VERSION_ACTION_REMOVE,
}


class RequirementVersionHistory(Base):
    __tablename__ = "requirement_version_history"

    id = Column(Integer, primary_key=True, index=True)
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_version_id = Column(
        Integer, ForeignKey("project_versions.id"), nullable=True,
    )
    to_version_id = Column(
        Integer, ForeignKey("project_versions.id"), nullable=True,
    )
    action = Column(String(20), nullable=False)
    reason = Column(Text, nullable=True)
    operator_id = Column(
        Integer, ForeignKey("users.id"), nullable=True,
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    requirement = relationship("Requirement")
    from_version = relationship("ProjectVersion", foreign_keys=[from_version_id])
    to_version = relationship("ProjectVersion", foreign_keys=[to_version_id])
    operator = relationship("User", foreign_keys=[operator_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "from_version_id": self.from_version_id,
            "to_version_id": self.to_version_id,
            "action": self.action,
            "reason": self.reason,
            "operator_id": self.operator_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
