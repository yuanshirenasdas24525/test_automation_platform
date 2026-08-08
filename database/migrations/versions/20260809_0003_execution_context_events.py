"""正式执行上下文事件与制品关联。

Revision ID: ui_execution_context_003
Revises: ui_recording_context_002
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "ui_execution_context_003"
down_revision = "ui_recording_context_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_context_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("context_session_id", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(length=80), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=20), server_default="info", nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("monotonic_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["context_session_id"], ["ui_context_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("context_session_id", "event_key", name="uq_ui_context_event_key"),
        sa.UniqueConstraint("context_session_id", "sequence_no", name="uq_ui_context_event_sequence"),
    )
    op.create_index("ix_ui_context_events_context_session_id", "ui_context_events", ["context_session_id"])
    op.create_index("ix_ui_context_events_event_type", "ui_context_events", ["event_type"])
    op.create_index("ix_ui_context_events_source", "ui_context_events", ["source"])
    op.create_index("ix_ui_context_events_step_id", "ui_context_events", ["step_id"])
    op.create_index("ix_ui_context_event_session_source", "ui_context_events", ["context_session_id", "source"])
    op.add_column("ui_context_artifacts", sa.Column("context_event_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_ui_context_artifact_context_event",
        "ui_context_artifacts",
        "ui_context_events",
        ["context_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ui_context_artifacts_context_event_id",
        "ui_context_artifacts",
        ["context_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ui_context_artifacts_context_event_id", table_name="ui_context_artifacts")
    op.drop_constraint(
        "fk_ui_context_artifact_context_event",
        "ui_context_artifacts",
        type_="foreignkey",
    )
    op.drop_column("ui_context_artifacts", "context_event_id")
    op.drop_index("ix_ui_context_event_session_source", table_name="ui_context_events")
    op.drop_index("ix_ui_context_events_step_id", table_name="ui_context_events")
    op.drop_index("ix_ui_context_events_source", table_name="ui_context_events")
    op.drop_index("ix_ui_context_events_event_type", table_name="ui_context_events")
    op.drop_index("ix_ui_context_events_context_session_id", table_name="ui_context_events")
    op.drop_table("ui_context_events")
