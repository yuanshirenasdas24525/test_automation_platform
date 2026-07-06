"""script_store

页面可编辑脚本库：支持全局脚本和项目脚本。

Revision ID: script_store_001
Revises: edit_operation_history_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "script_store_001"
down_revision = "edit_operation_history_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script_store",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_script_store_id", "script_store", ["id"])
    op.create_index("ix_script_store_name", "script_store", ["name"])
    op.create_index("ix_script_store_kind", "script_store", ["kind"])
    op.create_index("ix_script_store_enabled", "script_store", ["enabled"])
    op.create_index("ix_script_store_project_id", "script_store", ["project_id"])
    op.create_index("ix_script_store_created_at", "script_store", ["created_at"])
    op.create_index(
        "ux_script_store_project_kind_name",
        "script_store",
        ["project_id", "kind", "name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_script_store_project_kind_name", table_name="script_store")
    op.drop_index("ix_script_store_created_at", table_name="script_store")
    op.drop_index("ix_script_store_project_id", table_name="script_store")
    op.drop_index("ix_script_store_enabled", table_name="script_store")
    op.drop_index("ix_script_store_kind", table_name="script_store")
    op.drop_index("ix_script_store_name", table_name="script_store")
    op.drop_index("ix_script_store_id", table_name="script_store")
    op.drop_table("script_store")
