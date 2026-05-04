"""Role + UserRole —— 用户角色（多对多）。

设计：
  - 6 个固定 role code：admin / dev / test / pm / ui / ops
  - role 表本身只 seed 一次（migration 里 INSERT），运行期一般不增删
  - user_roles 是简单 m2m 关联表，无业务字段
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Table
from sqlalchemy.orm import relationship

from database.base import Base


# 6 个固定 role code
ROLE_ADMIN = "admin"
ROLE_DEV = "dev"
ROLE_TEST = "test"
ROLE_PM = "pm"
ROLE_UI = "ui"
ROLE_OPS = "ops"

ALL_ROLE_CODES = {
    ROLE_ADMIN, ROLE_DEV, ROLE_TEST, ROLE_PM, ROLE_UI, ROLE_OPS,
}

# m2m 关联表
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(50))
    description = Column(Text)

    users = relationship("User", secondary=user_roles, backref="roles")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
        }
