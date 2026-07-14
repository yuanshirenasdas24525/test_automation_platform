"""require config_store project_id

Revision ID: cfg_pid_not_null
Revises: rm_global_cfg_001
"""
from __future__ import annotations

from alembic import op


revision = "cfg_pid_not_null"
down_revision = "rm_global_cfg_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("config_store", "project_id", nullable=False)


def downgrade() -> None:
    op.alter_column("config_store", "project_id", nullable=True)
