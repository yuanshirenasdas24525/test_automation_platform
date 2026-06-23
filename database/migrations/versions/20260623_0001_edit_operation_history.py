"""新增通用可回滚编辑操作记录。

Revision ID: edit_operation_history_001
Revises: api_case_edit_history_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from database.base import JSONType


revision = "edit_operation_history_001"
down_revision = "api_case_edit_history_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edit_operation_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(length=512), nullable=True),
        sa.Column("rollback_status", sa.String(length=20), server_default="none", nullable=False),
        sa.Column("rollback_batch_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edit_operation_batches_id", "edit_operation_batches", ["id"])
    op.create_index("ix_edit_operation_batches_entity_type", "edit_operation_batches", ["entity_type"])
    op.create_index("ix_edit_operation_batches_action", "edit_operation_batches", ["action"])
    op.create_index("ix_edit_operation_batches_operator_id", "edit_operation_batches", ["operator_id"])
    op.create_index("ix_edit_operation_batches_rollback_batch_id", "edit_operation_batches", ["rollback_batch_id"])
    op.create_index("ix_edit_operation_batches_created_at", "edit_operation_batches", ["created_at"])

    op.create_table(
        "edit_operation_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("entity_label", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("before_snapshot", JSONType, nullable=True),
        sa.Column("after_snapshot", JSONType, nullable=True),
        sa.Column("field_changes", JSONType, nullable=True),
        sa.Column("rollback_status", sa.String(length=20), server_default="none", nullable=False),
        sa.Column("rollback_event_id", sa.Integer(), nullable=True),
        sa.Column("rollback_available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("snapshot_expires_at", sa.DateTime(), nullable=True),
        sa.Column("snapshot_purged_at", sa.DateTime(), nullable=True),
        sa.Column("purge_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["edit_operation_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_edit_operation_events_id", "edit_operation_events", ["id"])
    op.create_index("ix_edit_operation_events_batch_id", "edit_operation_events", ["batch_id"])
    op.create_index("ix_edit_operation_events_entity_type", "edit_operation_events", ["entity_type"])
    op.create_index("ix_edit_operation_events_entity_id", "edit_operation_events", ["entity_id"])
    op.create_index("ix_edit_operation_events_action", "edit_operation_events", ["action"])
    op.create_index("ix_edit_operation_events_rollback_event_id", "edit_operation_events", ["rollback_event_id"])
    op.create_index("ix_edit_operation_events_snapshot_expires_at", "edit_operation_events", ["snapshot_expires_at"])
    op.create_index("ix_edit_operation_events_created_at", "edit_operation_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("edit_operation_events")
    op.drop_table("edit_operation_batches")
