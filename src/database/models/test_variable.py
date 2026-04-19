from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, func, UniqueConstraint

from src.database.base import Base


class TestVariable(Base):
    """
    通用变量表。支持多级作用域：
      global   : 全局变量，scope_id 为 NULL
      project  : 项目级，scope_id = project.id
      env      : 环境级，scope_id = test_environment.id
      case     : 用例级，scope_id = test_case.id（也可直接存 TestCase.variables 里）

    查找优先级（高 → 低）：case > env > project > global

    ⚠️ 说明：如果你觉得 TestEnvironment.variables / TestCase.variables 两处 JSON 字段已经够用，
    这张表可作为"跨用例共享变量 + 全局变量"的补充。初期可不立即使用。
    """
    __tablename__ = "test_variables"

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(20), nullable=False, index=True)   # global|project|env|case
    scope_id = Column(Integer, index=True)                   # scope=global 时为 NULL

    key = Column(String(128), nullable=False)
    value = Column(Text)
    secret = Column(Boolean, default=False, nullable=False)
    description = Column(String(255))

    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("scope", "scope_id", "key", name="uq_variable_scope_key"),
    )

    def __repr__(self):
        return f"<TestVariable scope={self.scope}:{self.scope_id} key={self.key}>"
