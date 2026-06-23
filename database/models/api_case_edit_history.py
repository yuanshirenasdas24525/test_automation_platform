"""ApiCaseEditHistory：API 用例的新建、修改、删除审计记录。"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


class ApiCaseEditHistory(Base):
    __tablename__ = "api_case_edit_history"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    module_id = Column(Integer, nullable=True, index=True)
    case_name = Column(String(255), nullable=True)
    action = Column(String(20), nullable=False)
    changes = Column(JSONType, nullable=True)
    session_id = Column(String(64), nullable=True, index=True)
    operator = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    case = relationship("TestCase", foreign_keys=[case_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "module_id": self.module_id,
            "case_name": self.case_name,
            "action": self.action,
            "changes": self.changes or [],
            "session_id": self.session_id,
            "operator": self.operator,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
