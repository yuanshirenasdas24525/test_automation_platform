"""ai_case_drafts 评审信号埋点（P1 数据飞轮）：

  - original_payload：生成时原始快照（accept 时与当前字段比对得编辑相似度）
  - reject_reason：拒绝原因（回填 prompt 反例 + 统计）
  - edit_ratio：编辑相似度 0..1（1.0=原样采纳），accept 时计算

Revision ID: draft_review_signals_001
Revises: cfg_pid_not_null
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "draft_review_signals_001"
down_revision = "cfg_pid_not_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_case_drafts", sa.Column("original_payload", sa.JSON(), nullable=True))
    op.add_column("ai_case_drafts", sa.Column("reject_reason", sa.Text(), nullable=True))
    op.add_column("ai_case_drafts", sa.Column("edit_ratio", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_case_drafts", "edit_ratio")
    op.drop_column("ai_case_drafts", "reject_reason")
    op.drop_column("ai_case_drafts", "original_payload")
