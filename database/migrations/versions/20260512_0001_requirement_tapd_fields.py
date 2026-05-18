"""requirement TAPD 字段：父子需求 / 关联模块 / 预计起止时间

- requirements 表新增列：
    parent_requirement_id  自引用 FK，ON DELETE CASCADE（删父连子一起删）
    module_id              FK modules.id，ON DELETE SET NULL（模块删了需求保留）
    planned_start_at       预计开始
    planned_end_at         预计完成
- 索引：parent_requirement_id、module_id、(project_id, planned_end_at)

requirement_assignees.role 在应用层扩到 dev/test/pm/ui，无 DB CHECK，
故本迁移不涉及该表 DDL。

Revision ID: pm_000006
Revises: pm_000005
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "pm_000006"
down_revision: Union[str, None] = "pm_000005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def _index_exists(index_name: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    try:
        indexes = insp.get_indexes("requirements")
    except Exception:
        return False
    return any(i["name"] == index_name for i in indexes)


def upgrade() -> None:
    if not _column_exists("requirements", "parent_requirement_id"):
        op.add_column(
            "requirements",
            sa.Column(
                "parent_requirement_id",
                sa.Integer(),
                sa.ForeignKey("requirements.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
    if not _column_exists("requirements", "module_id"):
        op.add_column(
            "requirements",
            sa.Column(
                "module_id",
                sa.Integer(),
                sa.ForeignKey("modules.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if not _column_exists("requirements", "planned_start_at"):
        op.add_column(
            "requirements",
            sa.Column("planned_start_at", sa.DateTime(), nullable=True),
        )
    if not _column_exists("requirements", "planned_end_at"):
        op.add_column(
            "requirements",
            sa.Column("planned_end_at", sa.DateTime(), nullable=True),
        )

    if not _index_exists("ix_requirements_parent_requirement_id"):
        op.create_index(
            "ix_requirements_parent_requirement_id",
            "requirements",
            ["parent_requirement_id"],
        )
    if not _index_exists("ix_requirements_module_id"):
        op.create_index(
            "ix_requirements_module_id",
            "requirements",
            ["module_id"],
        )
    if not _index_exists("ix_requirements_project_planned_end"):
        op.create_index(
            "ix_requirements_project_planned_end",
            "requirements",
            ["project_id", "planned_end_at"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_requirements_project_planned_end", table_name="requirements"
    )
    op.drop_index("ix_requirements_module_id", table_name="requirements")
    op.drop_index(
        "ix_requirements_parent_requirement_id", table_name="requirements"
    )
    with op.batch_alter_table("requirements") as batch:
        batch.drop_column("planned_end_at")
        batch.drop_column("planned_start_at")
        batch.drop_column("module_id")
        batch.drop_column("parent_requirement_id")
