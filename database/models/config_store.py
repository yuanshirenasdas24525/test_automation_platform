from sqlalchemy import Column, Integer, String
from database.base import Base




class ConfigStore(Base):
    __tablename__ = "config_store"
    id = Column(Integer, primary_key=True)
    config_group = Column(String)
    config_key = Column(String)
    config_value = Column(String)
    category = Column(String)