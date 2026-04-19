# src/database/base.py
from sqlalchemy import JSON as _JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# 跨数据库的 JSON 列类型：
#   - PostgreSQL 用 JSONB（支持 GIN 索引、更丰富的查询操作符）
#   - 其他数据库（SQLite/MySQL）回退到通用 JSON 类型
# 使用方式：
#   from src.database.base import Base, JSONType
#   config = Column(JSONType, nullable=False)
JSONType = _JSON().with_variant(JSONB(), "postgresql")
