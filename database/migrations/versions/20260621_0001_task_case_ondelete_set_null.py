"""tasks.related_case_id 删除用例时置空

Revision ID: task_case_set_null_001
Revises: proj_ai_overview_001
Create Date: 2026-06-21

"""
from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "task_case_set_null_001"
down_revision: str | None = "proj_ai_overview_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "tasks_related_case_id_fkey",
        "tasks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "tasks_related_case_id_fkey",
        "tasks",
        "test_cases",
        ["related_case_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "tasks_related_case_id_fkey",
        "tasks",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "tasks_related_case_id_fkey",
        "tasks",
        "test_cases",
        ["related_case_id"],
        ["id"],
    )
