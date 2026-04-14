from src.database.base import Base
from sqlalchemy import Column, Integer, String, ForeignKey, BOOLEAN
from sqlalchemy.orm import relationship



class TestCase(Base):
    __tablename__ = "test_cases"
    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("modules.id"))
    name = Column(String, nullable=False)
    description = Column(String)
    skip = Column(BOOLEAN, nullable=False)
    method = Column(String, nullable=False)
    path = Column(String)
    headers = Column(String)
    data_type = Column(String, nullable=False)
    params = Column(String)
    file_path = Column(String)
    extract_data = Column(String)
    sql_query = Column(String)
    assertion = Column(String)
    wait_time = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)
    module = relationship("Module", back_populates="test_cases")