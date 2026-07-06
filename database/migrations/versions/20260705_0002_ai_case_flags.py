"""ai_case_flags：AI 诊断标记 + 用户清除反馈

设计见 docs/ai_case_flags_design.md。
JSON 列类型与 database.base.JSONType 一致（PG → JSONB，其他 → JSON），
迁移里按 sa.JSON 建（PG 上 alembic 会落 JSON；如需 JSONB 可后续单独迁移，读写兼容）。

Revision ID: ai_case_flags_001
Revises: test_case_description_text_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ai_case_flags_001"
down_revision = "test_case_description_text_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_case_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("flag_type", sa.String(length=30), nullable=False),
        sa.Column("classification", sa.String(length=20), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("fix_rounds", sa.Integer(), nullable=True),
        sa.Column("source_ai_run_id", sa.Integer(), nullable=True),
        sa.Column("source_report_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("cleared_at", sa.DateTime(), nullable=True),
        sa.Column("cleared_by_id", sa.Integer(), nullable=True),
        sa.Column("cleared_reason", sa.String(length=30), nullable=True),
        sa.Column("corrected_classification", sa.String(length=20), nullable=True),
        sa.Column("cleared_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_case_flags_case_id", "ai_case_flags", ["case_id"])
    op.create_index("ix_ai_case_flags_module_id", "ai_case_flags", ["module_id"])
    op.create_index("ix_ai_case_flags_status", "ai_case_flags", ["status"])
    op.create_index("ix_ai_case_flags_source_ai_run_id", "ai_case_flags", ["source_ai_run_id"])
    op.create_index("ix_ai_case_flags_case_status", "ai_case_flags", ["case_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_ai_case_flags_case_status", table_name="ai_case_flags")
    op.drop_index("ix_ai_case_flags_source_ai_run_id", table_name="ai_case_flags")
    op.drop_index("ix_ai_case_flags_status", table_name="ai_case_flags")
    op.drop_index("ix_ai_case_flags_module_id", table_name="ai_case_flags")
    op.drop_index("ix_ai_case_flags_case_id", table_name="ai_case_flags")
    op.drop_table("ai_case_flags")
