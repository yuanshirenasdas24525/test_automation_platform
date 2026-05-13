"""attachments 表：需求附件（外链 OR 本地文件）

Revision ID: pm_000008
Revises: pm_000007
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000008"
down_revision: Union[str, None] = "pm_000007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.create_index(
        "ix_attachments_requirement_id", "attachments", ["requirement_id"]
    )
    op.create_index(
        "ix_attachments_requirement_created",
        "attachments",
        ["requirement_id", "created_at"],
    )
    op.create_index(
        "ix_attachments_uploaded_by_id", "attachments", ["uploaded_by_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_attachments_uploaded_by_id", table_name="attachments")
    op.drop_index("ix_attachments_requirement_created", table_name="attachments")
    op.drop_index("ix_attachments_requirement_id", table_name="attachments")
    op.drop_table("attachments")
