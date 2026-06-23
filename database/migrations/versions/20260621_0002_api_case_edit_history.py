"""新增 API 用例编辑历史。

Revision ID: api_case_edit_history_001
Revises: task_case_set_null_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from database.base import JSONType


revision = "api_case_edit_history_001"
down_revision = "task_case_set_null_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_case_edit_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("case_name", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("changes", JSONType, nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_case_edit_history_id", "api_case_edit_history", ["id"])
    op.create_index("ix_api_case_edit_history_case_id", "api_case_edit_history", ["case_id"])
    op.create_index("ix_api_case_edit_history_module_id", "api_case_edit_history", ["module_id"])
    op.create_index("ix_api_case_edit_history_session_id", "api_case_edit_history", ["session_id"])
    op.create_index("ix_api_case_edit_history_created_at", "api_case_edit_history", ["created_at"])


def downgrade() -> None:
    op.drop_table("api_case_edit_history")
