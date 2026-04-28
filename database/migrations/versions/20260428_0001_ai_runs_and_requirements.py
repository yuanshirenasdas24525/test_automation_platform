"""ai_runs + requirements 两张新表

  1. ai_runs：所有 AI 调用的元信息（feature/status/payload/token/cost）
  2. requirements：项目下的需求点（AI 解析 PRD 产出，也可手工建）

兼容性：纯新增表，不改老表，零数据迁移。

Revision ID: ai_phase_000001
Revises: func_case_000001
Create Date: 2026-04-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ai_phase_000001"
down_revision: Union[str, None] = "func_case_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) ai_runs ----------------------------------------------------------
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("feature", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
        sa.Column("operator", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ai_runs_feature", "ai_runs", ["feature"])
    op.create_index("ix_ai_runs_status", "ai_runs", ["status"])
    op.create_index("ix_ai_runs_project_id", "ai_runs", ["project_id"])
    op.create_index("ix_ai_runs_celery_task_id", "ai_runs", ["celery_task_id"])
    op.create_index("ix_ai_runs_prompt_hash", "ai_runs", ["prompt_hash"])
    op.create_index("ix_ai_runs_created_at", "ai_runs", ["created_at"])

    # 2) requirements -----------------------------------------------------
    op.create_table(
        "requirements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="2"),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("depends_on", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
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
    op.create_index("ix_requirements_project_id", "requirements", ["project_id"])
    op.create_index("ix_requirements_status", "requirements", ["status"])
    op.create_index("ix_requirements_ai_run_id", "requirements", ["ai_run_id"])


def downgrade() -> None:
    op.drop_index("ix_requirements_ai_run_id", table_name="requirements")
    op.drop_index("ix_requirements_status", table_name="requirements")
    op.drop_index("ix_requirements_project_id", table_name="requirements")
    op.drop_table("requirements")

    op.drop_index("ix_ai_runs_created_at", table_name="ai_runs")
    op.drop_index("ix_ai_runs_prompt_hash", table_name="ai_runs")
    op.drop_index("ix_ai_runs_celery_task_id", table_name="ai_runs")
    op.drop_index("ix_ai_runs_project_id", table_name="ai_runs")
    op.drop_index("ix_ai_runs_status", table_name="ai_runs")
    op.drop_index("ix_ai_runs_feature", table_name="ai_runs")
    op.drop_table("ai_runs")
