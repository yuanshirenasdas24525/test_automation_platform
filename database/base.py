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


def embedding_column(dim: int):
    """RAG / 语义检索用的向量列。

    PostgreSQL 下用 ``pgvector`` 的 ``vector(dim)``，可走 HNSW / ivfflat 索引
    + ``<=>``/``<->``/``<#>`` 距离算子，性能最好。

    pgvector 库不存在时（比如 import 期 model 文件被加载、但环境没装这个包）
    回退到 JSON，**仅作 import 兼容**，迁移 / 实际查询仍按 PG + pgvector 设计。

    用法：
        from database.base import embedding_column
        embedding = Column(embedding_column(1536), nullable=True)
    """
    try:
        from pgvector.sqlalchemy import Vector  # type: ignore
        return Vector(dim)
    except Exception:  # noqa: BLE001 — pgvector 没装也不阻断 import
        return _JSON()
