from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# 平台元数据库统一 PostgreSQL，JSON 列直接使用 JSONB。
JSONType = JSONB


def embedding_column(dim: int):
    """RAG / 语义检索用的向量列。

    PostgreSQL 下用 ``pgvector`` 的 ``vector(dim)``，可走 HNSW / ivfflat 索引
    + ``<=>``/``<->``/``<#>`` 距离算子，性能最好。

    pgvector 库不存在时抛出明确错误，避免迁移或运行时悄悄退成普通 JSON。

    用法：
        from database.base import embedding_column
        embedding = Column(embedding_column(1536), nullable=True)
    """
    try:
        from pgvector.sqlalchemy import Vector  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺少 pgvector 依赖，平台元数据库需要 PostgreSQL + pgvector") from exc
    return Vector(dim)
