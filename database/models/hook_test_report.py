from database.base import Base
from sqlalchemy import Column, Integer, String, DateTime, func


class HookTestReport(Base):
    __tablename__ = "test_reports"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, index=True)
    total_count = Column(Integer, default=0)
    pass_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    status = Column(String)  # success / fail
    create_time = Column(DateTime, server_default=func.now())