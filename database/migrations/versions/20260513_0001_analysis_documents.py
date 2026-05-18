"""requirement_analysis_documents + requirement_analysis_versions

AI 需求分析 M6：每次 AI 分析产出一份 Markdown 文档；文档支持人工编辑，
每次保存追加一条 version 行（git-like 历史），diff 由前端实时计算。

Revision ID: ai_m6_001
Revises: pm_000008
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "ai_m6_001"
down_revision: Union[str, None] = "pm_000008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    return name in insp.get_table_names()


def _index_exists(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    try:
        indexes = insp.get_indexes(table)
    except Exception:
        return False
    return any(i["name"] == index_name for i in indexes)


def upgrade() -> None:
    if not _table_exists("requirement_analysis_documents"):
        op.create_table(
            "requirement_analysis_documents",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "requirement_id",
                sa.Integer(),
                sa.ForeignKey("requirements.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "ai_run_id",
                sa.Integer(),
                sa.ForeignKey("ai_runs.id"),
                nullable=True,
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("current_markdown", sa.Text(), nullable=False),
            sa.Column(
                "current_version",
                sa.Integer(),
                server_default="1",
                nullable=False,
            ),
            sa.Column("model_label", sa.String(length=100), nullable=True),
            sa.Column(
                "created_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    if not _index_exists("requirement_analysis_documents", "ix_analysis_documents_requirement_id"):
        op.create_index(
            "ix_analysis_documents_requirement_id",
            "requirement_analysis_documents",
            ["requirement_id"],
        )
    if not _index_exists("requirement_analysis_documents", "ix_analysis_documents_requirement_created"):
        op.create_index(
            "ix_analysis_documents_requirement_created",
            "requirement_analysis_documents",
            ["requirement_id", "created_at"],
        )
    if not _index_exists("requirement_analysis_documents", "ix_analysis_documents_ai_run_id"):
        op.create_index(
            "ix_analysis_documents_ai_run_id",
            "requirement_analysis_documents",
            ["ai_run_id"],
        )

    if not _table_exists("requirement_analysis_versions"):
        op.create_table(
            "requirement_analysis_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "document_id",
                sa.Integer(),
                sa.ForeignKey(
                    "requirement_analysis_documents.id", ondelete="CASCADE"
                ),
                nullable=False,
            ),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("markdown", sa.Text(), nullable=False),
            sa.Column("change_summary", sa.String(length=500), nullable=True),
            sa.Column(
                "author_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column(
                "is_ai_generated",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "document_id",
                "version_no",
                name="uq_analysis_versions_document_version",
            ),
        )
    if not _index_exists("requirement_analysis_versions", "ix_analysis_versions_document_id"):
        op.create_index(
            "ix_analysis_versions_document_id",
            "requirement_analysis_versions",
            ["document_id"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_analysis_versions_document_id",
        table_name="requirement_analysis_versions",
    )
    op.drop_table("requirement_analysis_versions")

    op.drop_index(
        "ix_analysis_documents_ai_run_id",
        table_name="requirement_analysis_documents",
    )
    op.drop_index(
        "ix_analysis_documents_requirement_created",
        table_name="requirement_analysis_documents",
    )
    op.drop_index(
        "ix_analysis_documents_requirement_id",
        table_name="requirement_analysis_documents",
    )
    op.drop_table("requirement_analysis_documents")
