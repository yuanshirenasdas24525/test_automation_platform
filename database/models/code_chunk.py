"""CodeChunk —— RAG 索引的最小单元（项目 repo 内一段代码 + embedding）。

第 3 批 RAG 链路才会真正写这张表；第 1 批先把表建出来 / 模型导出，避免后续
迁移叠加。

设计：
- 每个项目 1 个 git_url；索引时记录当时的 git_sha（commit hash），repo 更新后
  老 chunk 失效，按 (project_id, git_sha) 删旧建新
- file_path 是 repo 内相对路径；chunk_idx 是文件内分块序号
- embedding 列：PG → pgvector.Vector(N)（第 1 批先用 JSON 占位，第 3 批安装
  pgvector 后通过迁移转为 vector 列）
- 索引：(project_id, file_path, chunk_idx) 唯一；embedding 上的 HNSW 索引在
  第 3 批切到 pgvector 列后再加
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType

DEFAULT_EMBEDDING_DIM = 768


class CodeChunk(Base):
    __tablename__ = "code_chunks"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "git_sha",
            "file_path",
            "chunk_idx",
            name="uq_code_chunks_project_sha_path_idx",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 索引时的 commit SHA，便于"repo HEAD 跳了就废"
    git_sha = Column(String(40), nullable=False, index=True)

    file_path = Column(String(500), nullable=False)
    chunk_idx = Column(Integer, nullable=False)

    start_line = Column(Integer, nullable=True)
    end_line = Column(Integer, nullable=True)

    # 原文（拼 prompt 用）；用 Text 避免长度限制
    content = Column(Text, nullable=False)

    # 向量列 —— 使用 JSONType 存储任意维度的向量
    # pgvector 扩展在 PostgreSQL 中不可用时，retriever 走 JSON fallback 路径
    embedding = Column(JSONType, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    project = relationship("Project")
