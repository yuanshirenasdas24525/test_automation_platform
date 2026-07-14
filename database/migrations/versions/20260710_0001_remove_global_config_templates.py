"""remove global config templates

Revision ID: rm_global_cfg_001
Revises: user_sessions_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "rm_global_cfg_001"
down_revision = "user_sessions_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM config_store WHERE project_id IS NULL")


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO config_store (config_group, config_key, config_value, category, project_id)
            VALUES
                ('host', 'url', 'http://127.0.0.1:5173', 'api', NULL),
                ('browser', 'engine', 'playwright', 'web', NULL)
            """
        )
    )
