"""requirement_edit_history

Revision ID: 565bb2dfd337
Revises: ai_m6_001
Create Date: 2026-05-12 22:35:59.448372+08:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "565bb2dfd337"
down_revision: Union[str, None] = "ai_m6_001"
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
    if not _table_exists("requirement_edit_history"):
        op.create_table(
            "requirement_edit_history",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("requirement_id", sa.Integer(), nullable=False),
            sa.Column("edited_by_id", sa.Integer(), nullable=True),
            sa.Column(
                "changes",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=False,
                comment="[{field, old, new}, ...]",
            ),
            sa.Column(
                "change_summary",
                sa.String(length=512),
                nullable=True,
                comment="操作人可选填的变更摘要",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["edited_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(
                ["requirement_id"], ["requirements.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists("requirement_edit_history", "ix_requirement_edit_history_created_at"):
        op.create_index(
            op.f("ix_requirement_edit_history_created_at"),
            "requirement_edit_history",
            ["created_at"],
            unique=False,
        )
    if not _index_exists("requirement_edit_history", "ix_requirement_edit_history_requirement_id"):
        op.create_index(
            op.f("ix_requirement_edit_history_requirement_id"),
            "requirement_edit_history",
            ["requirement_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_requirement_edit_history_requirement_id"),
        table_name="requirement_edit_history",
    )
    op.drop_index(
        op.f("ix_requirement_edit_history_created_at"),
        table_name="requirement_edit_history",
    )
    op.drop_table("requirement_edit_history")
