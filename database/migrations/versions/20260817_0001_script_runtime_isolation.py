"""script runtime isolation

Revision ID: script_runtime_isolation_001
Revises: ui_auto_case_draft_001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "script_runtime_isolation_001"
down_revision = "ui_auto_case_draft_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "script_store",
        sa.Column(
            "requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("script_store", "requirements")
