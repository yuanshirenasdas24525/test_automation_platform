from src.database.base import Base
from sqlalchemy import Column, Integer, String, DateTime, Float, func, Text


class TestStepReport(Base):
    __tablename__ = "test_step_reports"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, index=True)
    case_id = Column(Integer)
    step_name = Column(String(255))

    # 通用化映射
    action = Column(String(50))  # 代替 method
    target = Column(Text)  # 代替 url
    input_data = Column(Text)  # 代替 request_payload
    output_data = Column(Text)  # 代替 response_body

    status_code = Column(Integer)
    assertion_results = Column(Text)
    extract_values = Column(Text)

    # 新增 UI 字段
    screenshot_path = Column(String(500))
    page_info = Column(String(100))

    status = Column(String(20))
    error_message = Column(Text)
    duration = Column(Float)
    create_time = Column(DateTime, default=func.now())
