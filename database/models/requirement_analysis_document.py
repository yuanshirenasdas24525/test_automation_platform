"""RequirementAnalysisDocument —— AI 需求分析产出的 Markdown 文档（M6）。

每次"AI 分析"产生一份独立文档；文档可被人工编辑，每次保存追加一条
RequirementAnalysisVersion 行（git-like 历史）。
"""
from sqlalchemy import (
    Boolean,
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

from database.base import Base


class RequirementAnalysisDocument(Base):
    __tablename__ = "requirement_analysis_documents"

    id = Column(Integer, primary_key=True, index=True)
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ai_run_id = Column(
        Integer, ForeignKey("ai_runs.id"), nullable=True, index=True
    )

    title = Column(String(200), nullable=False)
    current_markdown = Column(Text, nullable=False)
    current_version = Column(Integer, nullable=False, server_default="1")

    model_label = Column(String(100), nullable=True)

    created_by_id = Column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    versions = relationship(
        "RequirementAnalysisVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="RequirementAnalysisVersion.version_no",
    )
    created_by = relationship("User", foreign_keys=[created_by_id])

    def to_dict(self, include_markdown: bool = True) -> dict:
        d = {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "ai_run_id": self.ai_run_id,
            "title": self.title,
            "current_version": self.current_version,
            "model_label": self.model_label,
            "created_by_id": self.created_by_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_markdown:
            d["current_markdown"] = self.current_markdown
        return d


class RequirementAnalysisVersion(Base):
    __tablename__ = "requirement_analysis_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_no",
            name="uq_analysis_versions_document_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer,
        ForeignKey("requirement_analysis_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no = Column(Integer, nullable=False)
    markdown = Column(Text, nullable=False)
    change_summary = Column(String(500), nullable=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    is_ai_generated = Column(
        Boolean, nullable=False, server_default="0", default=False
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    document = relationship(
        "RequirementAnalysisDocument", back_populates="versions"
    )
    author = relationship("User", foreign_keys=[author_id])

    def to_dict(self, include_markdown: bool = True) -> dict:
        d = {
            "id": self.id,
            "document_id": self.document_id,
            "version_no": self.version_no,
            "change_summary": self.change_summary,
            "author_id": self.author_id,
            "is_ai_generated": bool(self.is_ai_generated),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_markdown:
            d["markdown"] = self.markdown
        return d
