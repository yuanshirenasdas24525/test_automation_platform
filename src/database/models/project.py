from src.database.base import Base
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    icon = Column(String)
    type = Column(String, nullable=False) # Mobile, API, Web
    sort_order = Column(Integer, default=0)
    modules = relationship("Module", back_populates="project", cascade="all, delete-orphan")