from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from src.database.base import Base, JSONType


class TestEnvironment(Base):
    """
    测试环境。项目级，支持 API host / App 设备池 / Web 浏览器配置。
    一条用例通过 env_id 关联到具体环境；运行时优先读环境里的变量，再读用例变量。
    """
    __tablename__ = "test_environments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"),
                        nullable=False, index=True)

    name = Column(String(64), nullable=False)          # dev / staging / prod / qa-01
    category = Column(String(20))                       # api | app | web | mixed
    description = Column(String(255))

    # 环境级配置
    host = Column(String(255))                          # API base url
    device_pool = Column(String(64))                    # App 设备池标签，对应 devices.pool
    browser_config = Column(JSONType)                   # {browser:'chromium', headless:true, ...}

    # 变量 & 敏感信息
    variables = Column(JSONType)                       # {"key":"value"} 普通变量
    secrets = Column(JSONType)                         # 敏感变量（加密存储，建议后续接 pgcrypto/Vault）

    # 元信息
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(), onupdate=func.now())

    project = relationship("Project")

    def __repr__(self):
        return f"<TestEnvironment id={self.id} project={self.project_id} name={self.name}>"
