"""module outline

模块级测试点大纲长期保存：module_outlines + module_outline_points。
设计见 docs/module_outline_design.md。

Revision ID: module_outline_001
Revises: case_repeat_count_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "module_outline_001"
down_revision = "case_repeat_count_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "module_outlines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("module_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="functional"),
        sa.Column("digest", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("last_aligned_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_module_outlines_id", "module_outlines", ["id"])
    op.create_index("ix_module_outlines_module_id", "module_outlines", ["module_id"], unique=True)

    op.create_table(
        "module_outline_points",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outline_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("linked_case_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="gap"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="ai"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["outline_id"], ["module_outlines.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linked_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_module_outline_points_id", "module_outline_points", ["id"])
    op.create_index("ix_module_outline_points_outline_id", "module_outline_points", ["outline_id"])
    op.create_index("ix_module_outline_points_linked_case_id", "module_outline_points", ["linked_case_id"])


def downgrade() -> None:
    op.drop_index("ix_module_outline_points_linked_case_id", table_name="module_outline_points")
    op.drop_index("ix_module_outline_points_outline_id", table_name="module_outline_points")
    op.drop_index("ix_module_outline_points_id", table_name="module_outline_points")
    op.drop_table("module_outline_points")
    op.drop_index("ix_module_outlines_module_id", table_name="module_outlines")
    op.drop_index("ix_module_outlines_id", table_name="module_outlines")
    op.drop_table("module_outlines")
