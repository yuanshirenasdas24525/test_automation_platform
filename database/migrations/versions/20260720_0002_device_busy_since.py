"""devices.busy_since：设备租约起点（超时由 probe_devices 强制释放）

Revision ID: device_busy_since_001
Revises: api_keys_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "device_busy_since_001"
down_revision = "api_keys_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("busy_since", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "busy_since")
