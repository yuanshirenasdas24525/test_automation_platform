from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from database.base import Base


SCRIPT_KIND_FUNCTION = "function"
SCRIPT_KIND_CRYPTO_REQUEST = "crypto_request"
SCRIPT_KIND_CRYPTO_RESPONSE = "crypto_response"
ALL_SCRIPT_KINDS = {
    SCRIPT_KIND_FUNCTION,
    SCRIPT_KIND_CRYPTO_REQUEST,
    SCRIPT_KIND_CRYPTO_RESPONSE,
}


class ScriptStore(Base):
    """页面可编辑脚本库。

    project_id 为空表示全项目通用；不为空表示项目专属脚本。
    脚本函数统一暴露 handler，外部用 name 作为 function:xxx 或 handler 名引用。
    """

    __tablename__ = "script_store"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    kind = Column(String(40), nullable=False, index=True)
    code = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true", index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("Project")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "code": self.code,
            "enabled": self.enabled,
            "project_id": self.project_id,
            "scope": "project" if self.project_id is not None else "global",
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
