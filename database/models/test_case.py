from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


class TestCase(Base):
    """
    测试用例（通用化）。
    一条 TestCase 是"用例元信息壳"，真正的执行步骤下沉到 TestStep。
    支持 API / App / Web / Mixed 四类用例。
    """
    __tablename__ = "test_cases"

    # ============ 基础 ============
    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("modules.id"))
    name = Column(String, nullable=False)
    description = Column(String)
    sort_order = Column(Integer, default=0)

    # ============ 通用用例元信息 ============
    case_type = Column(String(20), default="api", index=True)   # api | app | web | mixed
    tags = Column(JSONType)                                     # ["smoke","regression"]
    skip = Column(Boolean, default=False, nullable=False)
    priority = Column(Integer, default=2)                       # 0/1/2/3

    # ============ 执行控制（v2 新增）============
    env_id = Column(Integer, ForeignKey("test_environments.id"), nullable=True)
    pre_hook = Column(JSONType)       # [{type:'sql'|'http'|'script', ...}]
    post_hook = Column(JSONType)
    variables = Column(JSONType)      # 用例级变量
    timeout = Column(Integer, default=60)
    retry = Column(Integer, default=0)

    # ============ 兼容字段（v1 遗留，过渡期保留，半年后删除）============
    # 注意：这些字段由 v2 起改为 nullable=True；迁移脚本会把它们复制到 test_steps.config
    method = Column(String, nullable=True)
    path = Column(String, nullable=True)
    headers = Column(Text, nullable=True)
    data_type = Column(String, nullable=True)
    params = Column(Text, nullable=True)
    file_path = Column(String, nullable=True)
    extract_data = Column(Text, nullable=True)
    sql_query = Column(Text, nullable=True)
    assertion = Column(Text, nullable=True)
    wait_time = Column(Integer, default=0)

    # ============ 关系 ============
    module = relationship("Module", back_populates="test_cases")
    steps = relationship(
        "TestStep",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="TestStep.step_order",
    )
    environment = relationship("TestEnvironment", lazy="joined")
