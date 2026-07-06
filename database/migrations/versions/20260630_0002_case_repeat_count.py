"""case repeat_count

test_cases 加 repeat_count 列：单用例重复执行次数，默认 1。

Revision ID: case_repeat_count_001
Revises: script_store_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "case_repeat_count_001"
down_revision = "script_store_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column(
            "repeat_count",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("test_cases", "repeat_count")
