"""ui_page_snapshots 增加 module_id（画面按模块分类）

Revision ID: snapshot_module_id_001
Revises: seed_script_functions_001
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa


revision = "snapshot_module_id_001"
down_revision = "seed_script_functions_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ui_page_snapshots",
        sa.Column("module_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_ui_page_snapshots_module_id", "ui_page_snapshots", ["module_id"]
    )
    op.create_foreign_key(
        "fk_ui_page_snapshots_module_id",
        "ui_page_snapshots",
        "modules",
        ["module_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ui_page_snapshots_module_id", "ui_page_snapshots", type_="foreignkey"
    )
    op.drop_index("ix_ui_page_snapshots_module_id", table_name="ui_page_snapshots")
    op.drop_column("ui_page_snapshots", "module_id")
