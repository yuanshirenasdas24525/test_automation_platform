"""模块大纲按用例类型隔离。

功能与 API 用例共用模块树，但大纲必须相互独立。已有记录保留其 mode，迁移后允许
同一 module_id 分别保存 functional 和 interface 大纲。

Revision ID: outline_mode_scope_001
Revises: draft_review_signals_001
"""
from __future__ import annotations

from alembic import op


revision = "outline_mode_scope_001"
down_revision = "draft_review_signals_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_module_outlines_module_id", table_name="module_outlines")
    op.create_index(
        "ix_module_outlines_module_id",
        "module_outlines",
        ["module_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_module_outlines_module_mode",
        "module_outlines",
        ["module_id", "mode"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_module_outlines_module_mode",
        "module_outlines",
        type_="unique",
    )
    op.drop_index("ix_module_outlines_module_id", table_name="module_outlines")
    op.create_index(
        "ix_module_outlines_module_id",
        "module_outlines",
        ["module_id"],
        unique=True,
    )
