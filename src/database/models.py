
from sqlalchemy import Column, Integer, String, ForeignKey, func, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base




Base = declarative_base()


class HookTestReport(Base):
    __tablename__ = "test_reports"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, index=True)
    total_count = Column(Integer, default=0)
    pass_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    status = Column(String)  # success / fail
    create_time = Column(DateTime, server_default=func.now())

class HookTestStepReport(Base):
    __tablename__ = "test_step_reports"
    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("test_reports.id")) # 关联到主报告
    step_name = Column(String)
    status = Column(String)
    url = Column(Text)
    response_body = Column(Text)



