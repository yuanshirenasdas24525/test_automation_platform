"""project_versions + project_version_modules 两张新表

  1. project_versions：版本迭代管理
  2. project_version_modules：版本 ⇄ 模块多对多关联

Revision ID: pm_000001
Revises: ai_phase_000003
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000001"
down_revision: Union[str, None] = "ai_phase_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("version_name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="planning",
        ),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("frontend_versions", sa.JSON(), nullable=True),
        sa.Column("backend_versions", sa.JSON(), nullable=True),
        sa.Column("release_notes", sa.Text(), nullable=True),
        sa.Column("test_plan_url", sa.Text(), nullable=True),
        sa.Column("requirement_doc_url", sa.Text(), nullable=True),
        sa.Column("design_doc_url", sa.Text(), nullable=True),
        sa.Column("ui_prototype_url", sa.Text(), nullable=True),
        sa.Column("planned_start_at", sa.DateTime(), nullable=True),
        sa.Column("planned_end_at", sa.DateTime(), nullable=True),
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
    op.create_index("ix_pv_project_id", "project_versions", ["project_id"])
    op.create_index("ix_pv_status", "project_versions", ["status"])

    # 版本 ⇄ 模块关联
    op.create_table(
        "project_version_modules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "version_id",
            sa.Integer(),
            sa.ForeignKey("project_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "module_id",
            sa.Integer(),
            sa.ForeignKey("modules.id"),
            nullable=False,
        ),
    )
    op.create_index("ix_pvm_version_id", "project_version_modules", ["version_id"])
    op.create_index("ix_pvm_module_id", "project_version_modules", ["module_id"])


def downgrade() -> None:
    op.drop_index("ix_pvm_module_id", table_name="project_version_modules")
    op.drop_index("ix_pvm_version_id", table_name="project_version_modules")
    op.drop_table("project_version_modules")

    op.drop_index("ix_pv_status", table_name="project_versions")
    op.drop_index("ix_pv_project_id", table_name="project_versions")
    op.drop_table("project_versions")
