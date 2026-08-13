"""新增 AI Web UI 自动化用例草稿。

Revision ID: ui_auto_case_draft_001
Revises: ui_knowledge_merge_001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from database.base import JSONType


revision = "ui_auto_case_draft_001"
down_revision = "ui_knowledge_merge_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_automation_case_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("functional_case_id", sa.Integer(), nullable=True),
        sa.Column("ai_run_id", sa.Integer(), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("model_label", sa.String(length=120), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="2", nullable=False),
        sa.Column("tags", JSONType(), server_default="[]", nullable=False),
        sa.Column("variables", JSONType(), server_default="{}", nullable=False),
        sa.Column("steps", JSONType(), server_default="[]", nullable=False),
        sa.Column("evidence", JSONType(), server_default="{}", nullable=False),
        sa.Column("warnings", JSONType(), server_default="[]", nullable=False),
        sa.Column("manual_reasons", JSONType(), server_default="[]", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("visual_assertion", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("committed_case_id", sa.Integer(), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["functional_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ai_run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["committed_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("id", "project_id", "module_id", "functional_case_id", "ai_run_id", "batch_id", "status", "created_at"):
        op.create_index(f"ix_ui_automation_case_drafts_{column}", "ui_automation_case_drafts", [column])


def downgrade() -> None:
    op.drop_table("ui_automation_case_drafts")
