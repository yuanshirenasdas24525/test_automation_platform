"""User —— 平台用户。

设计：
  - password_hash 存 bcrypt 哈希，nullable 兼容存量无密码用户
  - 角色通过 user_roles 关联 roles 表（多对多），不在 users 表内冗余 role 字段
  - is_active 用于离职 / 停用场景：保留历史记录但不出现在分配下拉
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import relationship

from database.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    full_name = Column(String(128))
    email = Column(String(255), unique=True, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    password_hash = Column(String(128), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # 关系：roles 通过 user_roles 关联（在 role.py 里定义 backref）
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    # tasks_as_dev / tasks_as_test 通过 task.py 里的 assignee_dev / assignee_test FK 反向

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "email": self.email,
            "is_active": self.is_active,
            "role_codes": [r.code for r in (self.roles or [])],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
