"""test_cases 增加 AI 接口生成追踪元数据。

Revision ID: api_case_gen_meta_001
Revises: device_busy_since_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "api_case_gen_meta_001"
down_revision = "device_busy_since_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column("generation_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_cases", "generation_metadata")
