from __future__ import annotations

from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# case_type 枚举。functional 是"人工执行用例"（无自动化 step），
# 其它走自动化执行链路。app 已废，跑迁移脚本 app_to_android 后剩下：
#   api / web / android / ios / mixed / functional
CASE_TYPE_API = "api"
CASE_TYPE_ANDROID = "android"
CASE_TYPE_IOS = "ios"
CASE_TYPE_WEB = "web"
CASE_TYPE_MIXED = "mixed"
CASE_TYPE_FUNCTIONAL = "functional"
ALL_CASE_TYPES = {
    CASE_TYPE_API,
    CASE_TYPE_ANDROID,
    CASE_TYPE_IOS,
    CASE_TYPE_WEB,
    CASE_TYPE_MIXED,
    CASE_TYPE_FUNCTIONAL,
}
# 需要 Appium 设备的 case_type 集合 —— case_executor 据此决定是否拉 AppSession，
# 前端 ProjectsPage 也用它判断"要不要弹 device picker"。
APP_CASE_TYPES = {CASE_TYPE_ANDROID, CASE_TYPE_IOS}
# 走自动化执行链路（dispatcher / runner）的 case_type 集合
AUTOMATED_CASE_TYPES = {
    CASE_TYPE_API,
    CASE_TYPE_ANDROID,
    CASE_TYPE_IOS,
    CASE_TYPE_WEB,
    CASE_TYPE_MIXED,
}


class TestCase(Base):
    """
    测试用例（通用化）。
    一条 TestCase 是"用例元信息壳"，真正的执行步骤下沉到 TestStep。
    支持 API / Android / iOS / Web / Mixed / Functional 用例。
    """
    __tablename__ = "test_cases"

    # ============ 基础 ============
    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("modules.id"))
    name = Column(String, nullable=False)
    description = Column(Text)
    sort_order = Column(Integer, default=0)

    # ============ 通用用例元信息 ============
    case_type = Column(String(20), default="api", index=True)   # api | android | ios | web | mixed | functional
    tags = Column(JSONType)                                     # ["smoke","regression"]
    skip = Column(Boolean, default=False, nullable=False)
    priority = Column(Integer, default=2)                       # 0/1/2/3

    # 版本归属（M1 加）—— 资产沉淀回流：迭代结束后该版本新增/修改的用例打这个标
    version_id = Column(Integer, ForeignKey("project_versions.id"), nullable=True, index=True)

    # ============ 执行控制（v2 新增）============
    env_id = Column(Integer, ForeignKey("test_environments.id"), nullable=True)
    pre_hook = Column(JSONType)       # [{type:'sql'|'http'|'script', ...}]
    post_hook = Column(JSONType)
    variables = Column(JSONType)      # 用例级变量
    timeout = Column(Integer, default=60)
    retry = Column(Integer, default=0)
    # 单用例重复执行次数：填 N 则该用例连续执行 N 次（每次独立成报告条目）。
    # 默认 1（不重复）。展开发生在 pytest_generate_tests，不改执行链路。
    repeat_count = Column(Integer, default=1, server_default="1", nullable=False)

    # ============ v1 历史列（保留 nullable=True 占位）=========
    # 这些列在 v2 数据迁移 v2_cases_to_steps 之后已经全部转成 TestStep.config，
    # 代码层不再读写；保留列只是为了避免下游脚本在迁移窗口期崩。
    # 后续可加一条 alembic 迁移把它们 DROP 掉。
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
    # functional 用例的"勾结果"历史记录。case 删时 cascade 一并删掉，
    # 跟 FunctionalCaseRun.case 的 back_populates="functional_runs" 对接。
    functional_runs = relationship(
        "FunctionalCaseRun",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="FunctionalCaseRun.executed_at.desc()",
    )

    # functional 用例的"步骤 / 期望"自由文本结构（JSON）。
    # 仅 case_type='functional' 时使用；其它类型为 NULL。
    functional_spec = Column(JSONType, nullable=True)

    # ============ M7 AI 一键生成回填 ============
    # source = manual / ai_m7（后续 m8 自动化生成会扩展到 ai_m8_auto 等）
    source = Column(String(20), nullable=False, default="manual", server_default="manual", index=True)
    # AI 接口用例的可追溯信息：生成 run/model/prompt/契约 hash、编译器版本、
    # 静态门禁和探测结果。人工用例为 NULL，不把运行期大对象塞进普通字段。
    generation_metadata = Column(JSONType, nullable=True)
    # 回指 ai_case_drafts.id（来源草稿）；逻辑删 draft 时 SET NULL
    draft_id = Column(Integer, ForeignKey("ai_case_drafts.id", ondelete="SET NULL"), nullable=True)
    # 直接关联到需求（绕开 module 反查）
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # M8 衔接：结构化业务步骤 [{order, action_text, step_type_hint, needs_ui_detail}, ...]
    # commit 时由 ai_case_draft_service 从 draft.steps_text + step_template 合成
    business_steps = Column(JSONType, nullable=True)

    draft = relationship("AiCaseDraft", foreign_keys=[draft_id])
    requirement = relationship("Requirement", foreign_keys=[requirement_id])
