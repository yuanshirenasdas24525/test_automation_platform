"""project_contexts + requirement_analyses 两张新表

  1. project_contexts：项目上下文记忆层，存储从文档提取的各类内容片段
     （业务规则、数据模型、API 契约、术语定义等）
  2. requirement_analyses：AI 需求分析记录，追踪每次分析的文档、匹配的上下文、
     产出的需求和上下文条目

兼容性：纯新增表，不改老表，零数据迁移。

Revision ID: ai_phase_000003
Revises: ai_phase_000002
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ai_phase_000003"
down_revision: Union[str, None] = "ai_phase_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) project_contexts ----------------------------------------------------
    op.create_table(
        "project_contexts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "module_id",
            sa.Integer(),
            sa.ForeignKey("modules.id"),
            nullable=True,
        ),
        sa.Column(
            "source_type",
            sa.String(length=30),
            nullable=False,
            server_default="document",
        ),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_version", sa.Integer(), server_default="1"),
        sa.Column("context_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),  # pgvector 不可用时用 JSON 文本
        sa.Column("importance", sa.Integer(), server_default="3"),
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
    op.create_index("ix_pc_project_id", "project_contexts", ["project_id"])
    op.create_index("ix_pc_module_id", "project_contexts", ["module_id"])
    op.create_index("ix_pc_context_type", "project_contexts", ["context_type"])

    # 2) requirement_analyses --------------------------------------------------
    op.create_table(
        "requirement_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "ai_run_id",
            sa.Integer(),
            sa.ForeignKey("ai_runs.id"),
            nullable=True,
        ),
        sa.Column("document_name", sa.String(length=255), nullable=True),
        sa.Column("document_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "source_type",
            sa.String(length=20),
            nullable=False,
            server_default="text",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "analysis_mode",
            sa.String(length=20),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("analysis_result", sa.JSON(), nullable=True),
        sa.Column("new_requirement_ids", sa.JSON(), nullable=True),
        sa.Column("new_context_ids", sa.JSON(), nullable=True),
        sa.Column("matched_context_ids", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ra_project_id", "requirement_analyses", ["project_id"])
    op.create_index("ix_ra_ai_run_id", "requirement_analyses", ["ai_run_id"])
    op.create_index("ix_ra_status", "requirement_analyses", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ra_status", table_name="requirement_analyses")
    op.drop_index("ix_ra_ai_run_id", table_name="requirement_analyses")
    op.drop_index("ix_ra_project_id", table_name="requirement_analyses")
    op.drop_table("requirement_analyses")

    op.drop_index("ix_pc_context_type", table_name="project_contexts")
    op.drop_index("ix_pc_module_id", table_name="project_contexts")
    op.drop_index("ix_pc_project_id", table_name="project_contexts")
    op.drop_table("project_contexts")
