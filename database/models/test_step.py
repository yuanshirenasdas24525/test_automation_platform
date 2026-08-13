from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Float
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# step_type 枚举（不用 Python Enum 是为了方便数据库里直接看到可读字符串）
STEP_TYPE_HTTP_REQUEST = "http_request"
STEP_TYPE_SQL_QUERY = "sql_query"
STEP_TYPE_SCRIPT = "script"

# App 原生动作
STEP_TYPE_APP_LAUNCH = "app_launch"
STEP_TYPE_APP_CLOSE = "app_close"
STEP_TYPE_APP_TAP = "app_tap"
STEP_TYPE_APP_INPUT = "app_input"
STEP_TYPE_APP_SWIPE = "app_swipe"
STEP_TYPE_APP_PRESS = "app_press"
STEP_TYPE_APP_WAIT = "app_wait"
STEP_TYPE_APP_SCREENSHOT = "app_screenshot"
STEP_TYPE_APP_BACK = "app_back"

# Web 原生动作（P5 预留）
STEP_TYPE_WEB_GOTO = "web_goto"
STEP_TYPE_WEB_CLICK = "web_click"
STEP_TYPE_WEB_INPUT = "web_input"
STEP_TYPE_WEB_SELECT = "web_select"
STEP_TYPE_WEB_WAIT = "web_wait"
STEP_TYPE_WEB_SCREENSHOT = "web_screenshot"
STEP_TYPE_WEB_ASSERT_TEXT = "web_assert_text"
STEP_TYPE_WEB_ASSERT_VISUAL = "web_assert_visual"
STEP_TYPE_WEB_EVALUATE = "web_evaluate"

# 通用
STEP_TYPE_ASSERT = "assert"
STEP_TYPE_SLEEP = "sleep"

ALL_STEP_TYPES = {
    STEP_TYPE_HTTP_REQUEST, STEP_TYPE_SQL_QUERY, STEP_TYPE_SCRIPT,
    STEP_TYPE_APP_LAUNCH, STEP_TYPE_APP_CLOSE,
    STEP_TYPE_APP_TAP, STEP_TYPE_APP_INPUT, STEP_TYPE_APP_SWIPE,
    STEP_TYPE_APP_PRESS, STEP_TYPE_APP_WAIT,
    STEP_TYPE_APP_SCREENSHOT, STEP_TYPE_APP_BACK,
    STEP_TYPE_WEB_GOTO, STEP_TYPE_WEB_CLICK, STEP_TYPE_WEB_INPUT,
    STEP_TYPE_WEB_SELECT, STEP_TYPE_WEB_WAIT,
    STEP_TYPE_WEB_SCREENSHOT, STEP_TYPE_WEB_ASSERT_TEXT,
    STEP_TYPE_WEB_ASSERT_VISUAL, STEP_TYPE_WEB_EVALUATE,
    STEP_TYPE_ASSERT, STEP_TYPE_SLEEP,
}


class TestStep(Base):
    """
    测试步骤。一条 TestCase 包含 N 个有序 TestStep。
    不同类型（HTTP / App / Web / Assert）的具体参数全部放在 config JSON 里，
    由 Runner 根据 step_type 解析执行。
    """
    __tablename__ = "test_steps"

    # ============ 基础 ============
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("test_cases.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    step_order = Column(Integer, default=0, nullable=False)
    step_name = Column(String(255), nullable=False)

    # ============ 类型与载荷 ============
    # step_type 决定 config/extract/assertion 里的字段结构，也决定派发到哪个 Runner
    step_type = Column(String(50), nullable=False, index=True)
    skip = Column(Boolean, default=False, nullable=False)

    # 核心载荷，每种 step_type 对应不同的 JSON 结构：
    #   http_request : {method, path, headers, data_type, params, file_path}
    #   sql_query    : {sql, params, target_db}
    #   app_tap      : {by, locator, sliding_location}
    #   app_input    : {by, locator, value, clear_first}
    #   app_swipe    : {direction|start|end, distance}
    #   app_launch   : {appPackage, appActivity}
    #   assert       : {by, locator, type, target, expected}
    config = Column(JSONType, nullable=False)

    # ============ 变量相关 ============
    # [{name, from:'response.body'|'text'|'attr', jsonpath:'...'}]
    extract = Column(JSONType)
    # [{type:'equal'|'contains'|'jsonpath'|'text_equal', target, expected}]
    assertion = Column(JSONType)

    # ============ 控制 ============
    wait_before = Column(Float, default=0)
    timeout = Column(Integer, default=30)
    retry = Column(Integer, default=0)
    on_failure = Column(String(20), default="stop")  # stop | continue | retry

    # ============ 关系 ============
    case = relationship("TestCase", back_populates="steps")

    def to_dict(self) -> dict:
        """转成 Runner 可用的纯字典，避免带 ORM 实例泄露"""
        return {
            "id": self.id,
            "case_id": self.case_id,
            "step_order": self.step_order,
            "step_name": self.step_name,
            "step_type": self.step_type,
            "skip": self.skip,
            "config": self.config or {},
            "extract": self.extract or [],
            "assertion": self.assertion or [],
            "wait_before": self.wait_before or 0,
            "timeout": self.timeout or 30,
            "retry": self.retry or 0,
            "on_failure": self.on_failure or "stop",
        }

    def __repr__(self):
        return f"<TestStep id={self.id} case={self.case_id} "\
               f"order={self.step_order} type={self.step_type}>"
