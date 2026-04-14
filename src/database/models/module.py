from sqlalchemy import Column, Integer, String, ForeignKey
from src.database.base import Base
from sqlalchemy.orm import relationship


class Module(Base):
    __tablename__ = "modules"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    parent_id = Column(Integer, ForeignKey("modules.id"), nullable=True)
    name = Column(String, nullable=False)
    sort_order = Column(Integer, default=0)
    project = relationship("Project", back_populates="modules")
    # 模块删除时，也自动删除下的用例
    test_cases = relationship("TestCase", back_populates="module", cascade="all, delete-orphan")