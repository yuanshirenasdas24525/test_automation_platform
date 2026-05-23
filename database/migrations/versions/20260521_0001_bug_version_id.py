"""Bug 关联版本迭代：tasks 表新增 version_id 列 + 索引 + 外键

Revision ID: bug_version_id_001
Revises: ai_studio_m1_001
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bug_version_id_001"
down_revision: Union[str, None] = "ai_studio_m1_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("version_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_tasks_version_id", "tasks", ["version_id"])
    op.create_foreign_key(
        "fk_tasks_version_id_project_versions",
        "tasks",
        "project_versions",
        ["version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_tasks_version_id_project_versions", "tasks", type_="foreignkey")
    op.drop_index("ix_tasks_version_id", table_name="tasks")
    op.drop_column("tasks", "version_id")
