"""需求池 / 迭代关联 Phase 2：requirement_assignees + requirement_version_history

- requirement_assignees：需求级 dev/test 多对多分配（一个需求可关多个开发、多个测试）。
- requirement_version_history：需求 ↔ 迭代变更历史（plan/move/remove + 操作人 + 原因）。

system_status 新增枚举值（pm_review / done）属应用层校验，DB 仍是 String(20)，无需 DDL。

Revision ID: pm_000004
Revises: pm_000003
Create Date: 2026-05-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000004"
down_revision: Union[str, None] = "pm_000003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "requirement_assignees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "requirement_id", "user_id", "role",
            name="uq_requirement_assignee_req_user_role",
        ),
    )
    op.create_index(
        "ix_requirement_assignees_requirement_id",
        "requirement_assignees", ["requirement_id"],
    )
    op.create_index(
        "ix_requirement_assignees_user_id",
        "requirement_assignees", ["user_id"],
    )

    op.create_table(
        "requirement_version_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_version_id",
            sa.Integer(),
            sa.ForeignKey("project_versions.id"),
            nullable=True,
        ),
        sa.Column(
            "to_version_id",
            sa.Integer(),
            sa.ForeignKey("project_versions.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "operator_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_requirement_version_history_requirement_id",
        "requirement_version_history", ["requirement_id"],
    )
    op.create_index(
        "ix_requirement_version_history_created_at",
        "requirement_version_history", ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_requirement_version_history_created_at",
        table_name="requirement_version_history",
    )
    op.drop_index(
        "ix_requirement_version_history_requirement_id",
        table_name="requirement_version_history",
    )
    op.drop_table("requirement_version_history")

    op.drop_index(
        "ix_requirement_assignees_user_id",
        table_name="requirement_assignees",
    )
    op.drop_index(
        "ix_requirement_assignees_requirement_id",
        table_name="requirement_assignees",
    )
    op.drop_table("requirement_assignees")
