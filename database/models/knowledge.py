"""知识库（Knowledge Base）独立数据模型。

阶段 0 起，知识库文档从寄生 ``project_contexts`` 迁到本组独立表：
  - KnowledgeFolder    多级目录树（替代原先借用的模块树）
  - KnowledgeDocument  文档主体（富文本 rich_text 或文件 file）
  - KnowledgeTag / KnowledgeDocumentTag  标签（多对多）
  - KnowledgeAttachment  文件附件（file 文档的主文件也是一条附件）
  - KnowledgeDocumentVersion  版本历史快照

与 AI 检索的关系：纳入检索的文档由 ``knowledge_service.sync_rag_projection``
单向投影一行到 ``project_contexts``（source_type='knowledge'），AI 用例生成侧
``context_service.retrieve_context`` 照旧消费，零改动。
"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, ForeignKey, DateTime, func, Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database.base import Base


# -- doc_type 枚举 -----------------------------------------------------------
KB_DOC_TYPE_RICH_TEXT = "rich_text"
KB_DOC_TYPE_FILE = "file"
ALL_KB_DOC_TYPES = {KB_DOC_TYPE_RICH_TEXT, KB_DOC_TYPE_FILE}


class KnowledgeFolder(Base):
    """知识库目录（多级，parent_id=NULL 为根级）。"""
    __tablename__ = "knowledge_folders"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("knowledge_folders.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class KnowledgeDocument(Base):
    """知识库文档主体。"""
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    folder_id = Column(Integer, ForeignKey("knowledge_folders.id"), nullable=True, index=True)
    # 兼容过渡：阶段 0 保留旧的模块归属，前端仍按 module_id 展示/过滤；阶段 1 改用 folder。
    module_id = Column(Integer, ForeignKey("modules.id"), nullable=True, index=True)

    doc_type = Column(String(20), nullable=False, default=KB_DOC_TYPE_RICH_TEXT)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False, default="")       # 去标签纯文本，供检索/投影
    content_html = Column(Text, nullable=True)               # 富文本原文
    context_type = Column(String(50), nullable=False, default="term_definition")  # 供 RAG 投影归类

    include_in_rag = Column(Boolean, nullable=False, default=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)

    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    editor_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    tags = relationship(
        "KnowledgeTag", secondary="knowledge_document_tags", backref="documents"
    )
    attachments = relationship(
        "KnowledgeAttachment", back_populates="document",
        cascade="all, delete-orphan",
    )
    versions = relationship(
        "KnowledgeDocumentVersion", back_populates="document",
        cascade="all, delete-orphan",
    )


class KnowledgeTag(Base):
    """项目内知识库标签。"""
    __tablename__ = "knowledge_tags"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_kb_tag_project_name"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    color = Column(String(16), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class KnowledgeDocumentTag(Base):
    """文档↔标签 多对多连接表。"""
    __tablename__ = "knowledge_document_tags"

    document_id = Column(
        Integer, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id = Column(
        Integer, ForeignKey("knowledge_tags.id", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeAttachment(Base):
    """文件附件；file 文档的主文件也存为一条。"""
    __tablename__ = "knowledge_attachments"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer, ForeignKey("knowledge_documents.id"), nullable=False, index=True
    )
    filename = Column(String(255), nullable=False)
    mime = Column(String(128), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    storage_path = Column(String(512), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    document = relationship("KnowledgeDocument", back_populates="attachments")


class KnowledgeDocumentVersion(Base):
    """文档版本快照（每次编辑保存前写一条）。"""
    __tablename__ = "knowledge_document_versions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer, ForeignKey("knowledge_documents.id"), nullable=False, index=True
    )
    title = Column(String(255), nullable=False)
    content_html = Column(Text, nullable=True)
    editor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    document = relationship("KnowledgeDocument", back_populates="versions")


Index("ix_knowledge_documents_project_folder", KnowledgeDocument.project_id, KnowledgeDocument.folder_id)
