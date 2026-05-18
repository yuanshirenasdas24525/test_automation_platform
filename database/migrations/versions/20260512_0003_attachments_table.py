"""attachments 表：需求附件（外链 OR 本地文件）

Revision ID: pm_000008
Revises: pm_000007
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "pm_000008"
down_revision: Union[str, None] = "pm_000007"
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
    if not _table_exists("attachments"):
        op.create_table(
            "attachments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "requirement_id",
                sa.Integer(),
                sa.ForeignKey("requirements.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("kind", sa.String(length=10), nullable=False),
            sa.Column("url", sa.String(length=500), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column(
                "uploaded_by_id",
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
        )
    if not _index_exists("attachments", "ix_attachments_requirement_id"):
        op.create_index(
            "ix_attachments_requirement_id", "attachments", ["requirement_id"]
        )
    if not _index_exists("attachments", "ix_attachments_requirement_created"):
        op.create_index(
            "ix_attachments_requirement_created",
            "attachments",
            ["requirement_id", "created_at"],
        )
    if not _index_exists("attachments", "ix_attachments_uploaded_by_id"):
        op.create_index(
            "ix_attachments_uploaded_by_id", "attachments", ["uploaded_by_id"]
        )


def downgrade() -> None:
    op.drop_index("ix_attachments_uploaded_by_id", table_name="attachments")
    op.drop_index("ix_attachments_requirement_created", table_name="attachments")
    op.drop_index("ix_attachments_requirement_id", table_name="attachments")
    op.drop_table("attachments")
