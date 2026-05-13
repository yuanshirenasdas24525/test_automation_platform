"""RequirementAssignee —— 需求级开发/测试人员关联（多对多）。

一个 Requirement 可关联多个 dev、多个 test；同一 (requirement, user, role) 唯一。
PM 维度仍走 Requirement.assignee_pm_id（单值，无需多对多）。
"""
from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, UniqueConstraint, func,
)
from sqlalchemy.orm import relationship

from database.base import Base


REQUIREMENT_ASSIGNEE_ROLE_DEV = "dev"
REQUIREMENT_ASSIGNEE_ROLE_TEST = "test"
REQUIREMENT_ASSIGNEE_ROLE_PM = "pm"
REQUIREMENT_ASSIGNEE_ROLE_UI = "ui"
ALL_REQUIREMENT_ASSIGNEE_ROLES = {
    REQUIREMENT_ASSIGNEE_ROLE_DEV,
    REQUIREMENT_ASSIGNEE_ROLE_TEST,
    REQUIREMENT_ASSIGNEE_ROLE_PM,
    REQUIREMENT_ASSIGNEE_ROLE_UI,
}


class RequirementAssignee(Base):
    __tablename__ = "requirement_assignees"
    __table_args__ = (
        UniqueConstraint(
            "requirement_id", "user_id", "role",
            name="uq_requirement_assignee_req_user_role",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True,
    )
    role = Column(String(10), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    requirement = relationship("Requirement", back_populates="assignees")
    user = relationship("User")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "user_id": self.user_id,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
