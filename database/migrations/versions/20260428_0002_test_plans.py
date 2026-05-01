"""test_plans 表

测试计划：项目下的 markdown 文档，可由 AI 生成 + 用户编辑。

Revision ID: ai_phase_000002
Revises: ai_phase_000001
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ai_phase_000002"
down_revision: Union[str, None] = "ai_phase_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("requirement_ids", sa.JSON(), nullable=True),
        sa.Column("module_ids", sa.JSON(), nullable=True),
        sa.Column("time_range_start", sa.DateTime(), nullable=True),
        sa.Column("time_range_end", sa.DateTime(), nullable=True),
        sa.Column("resource_notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "ai_run_id",
            sa.Integer(),
            sa.ForeignKey("ai_runs.id"),
            nullable=True,
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
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
    op.create_index("ix_test_plans_project_id", "test_plans", ["project_id"])
    op.create_index("ix_test_plans_status", "test_plans", ["status"])
    op.create_index("ix_test_plans_ai_run_id", "test_plans", ["ai_run_id"])


def downgrade() -> None:
    op.drop_index("ix_test_plans_ai_run_id", table_name="test_plans")
    op.drop_index("ix_test_plans_status", table_name="test_plans")
    op.drop_index("ix_test_plans_project_id", table_name="test_plans")
    op.drop_table("test_plans")
