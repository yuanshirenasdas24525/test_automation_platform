"""test case description text

测试用例描述由 varchar 放宽为 text，避免 AI 生成的多步骤描述编辑后保存失败。

Revision ID: test_case_description_text_001
Revises: module_outline_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "test_case_description_text_001"
down_revision = "module_outline_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "test_cases",
        "description",
        existing_type=sa.String(),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "test_cases",
        "description",
        existing_type=sa.Text(),
        type_=sa.String(),
        existing_nullable=True,
    )
