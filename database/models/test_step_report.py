from sqlalchemy import BigInteger, Column, Integer, String, DateTime, Float, ForeignKey, Text, func

from database.base import Base, JSONType


class TestStepReport(Base):
    """
    步骤级执行记录。设计为通用结构，同时兼容 API / App / Web 三类步骤。
    一条 TestStepReport 对应一个 TestStep 的一次执行结果。
    """
    __tablename__ = "test_step_reports"

    id = Column(Integer, primary_key=True, index=True)

    # ============ 关联 ============
    report_id = Column(Integer, index=True)               # 关联 test_reports.id（本次执行）
    case_id = Column(Integer, index=True)                 # 关联 test_cases.id
    case_execution_id = Column(Integer, index=True)       # v2 新增：单条用例本次执行的 id（便于多次重试/并发场景聚合）
    step_id = Column(Integer, index=True)                 # v2 新增：关联 test_steps.id（便于回溯到步骤定义）

    # ============ 基础信息 ============
    step_name = Column(String(255))
    step_type = Column(String(50))                        # v2 新增：http_request | app_tap | ...

    # ============ 通用化的执行载荷（跟 TestStep 对齐） ============
    action = Column(String(50))                           # 代替旧 method（HTTP method / app action / web action）
    target = Column(Text)                                 # 代替旧 url（URL / 元素 locator / 页面 url）
    input_data = Column(Text)                             # 代替旧 request_payload（请求体 / 输入值）
    output_data = Column(Text)                            # 代替旧 response_body（响应 / 页面文本）

    status_code = Column(Integer)                         # HTTP 状态码（API 专用）
    assertion_results = Column(Text)                      # 断言结果（JSON 序列化字符串）
    extract_values = Column(Text)                         # 提取的变量（JSON 序列化字符串）

    # ============ UI 相关（App/Web 专用） ============
    screenshot_path = Column(String(500))
    page_info = Column(String(100))

    # ============ v2 新增：附件/日志链接 ============
    # 格式：[{"name":"error.png","url":"minio://.../xxx.png","type":"image"}, ...]
    attachments = Column(JSONType)
    context_session_id = Column(
        BigInteger,
        ForeignKey("ui_context_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    context_event_from_seq = Column(BigInteger, nullable=True)
    context_event_to_seq = Column(BigInteger, nullable=True)

    # ============ 执行结果 ============
    status = Column(String(20))                           # passed | failed | broken | skipped
    error_message = Column(Text)
    duration = Column(Float)                              # 秒
    create_time = Column(DateTime, default=func.now())
