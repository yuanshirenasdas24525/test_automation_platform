"""版本迭代文档字段升级为 JSONB 数组

  新增 4 个 JSONB 列替代旧的 TEXT 列：
    test_plan_items       → JSONB, 替代 test_plan_url
    requirement_doc_items → JSONB, 替代 requirement_doc_url
    design_doc_items      → JSONB, 替代 design_doc_url
    ui_prototype_items    → JSONB, 替代 ui_prototype_url

  旧列保留（向后兼容），后续版本可删除。

Revision ID: pm_000002
Revises: pm_000001
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000002"
down_revision: Union[str, None] = "pm_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in (
        "test_plan_items", "requirement_doc_items",
        "design_doc_items", "ui_prototype_items",
    ):
        op.add_column(
            "project_versions",
            sa.Column(col, sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    for col in (
        "test_plan_items", "requirement_doc_items",
        "design_doc_items", "ui_prototype_items",
    ):
        op.drop_column("project_versions", col)
