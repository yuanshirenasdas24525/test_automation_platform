from database.base import Base
from sqlalchemy import Column, Integer, String, ForeignKey, Text



class HookTestStepReport(Base):
    __tablename__ = "test_step_reports"
    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("test_reports.id")) # 关联到主报告
    step_name = Column(String)
    status = Column(String)
    url = Column(Text)
    response_body = Column(Text)