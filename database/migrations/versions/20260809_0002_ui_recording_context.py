"""补齐 UI 动作、元素证据和统一技术上下文模型。

Revision ID: ui_recording_context_002
Revises: ui_recording_control_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "ui_recording_context_002"
down_revision = "ui_recording_control_001"
branch_labels = None
depends_on = None


_EMPTY_OBJECT = sa.text("'{}'::jsonb")
_EMPTY_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "ui_element_occurrences",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("bounds", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("locators", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_ARRAY, nullable=False),
        sa.Column("element_screenshot_uri", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["element_id"], ["ui_elements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["ui_recording_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ui_page_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("element_id", "snapshot_id", name="uq_ui_occurrence_element_snapshot"),
    )
    op.create_index("ix_ui_element_occurrences_session_id", "ui_element_occurrences", ["session_id"])
    op.create_index("ix_ui_element_occurrences_snapshot_id", "ui_element_occurrences", ["snapshot_id"])
    op.create_index("ix_ui_element_occurrences_element_id", "ui_element_occurrences", ["element_id"])
    op.create_index("ix_ui_occurrence_snapshot_element", "ui_element_occurrences", ["snapshot_id", "element_id"])

    op.create_table(
        "ui_recorded_actions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("source_event_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="captured", nullable=False),
        sa.Column("target_element_id", sa.Integer(), nullable=True),
        sa.Column("page_before_key", sa.String(length=255), nullable=True),
        sa.Column("page_after_key", sa.String(length=255), nullable=True),
        sa.Column("snapshot_before_id", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_after_id", sa.BigInteger(), nullable=True),
        sa.Column("screenshot_before_uri", sa.Text(), nullable=True),
        sa.Column("screenshot_after_uri", sa.Text(), nullable=True),
        sa.Column("element_screenshot_uri", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("context_event_from_seq", sa.BigInteger(), nullable=False),
        sa.Column("context_event_to_seq", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ui_recording_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_event_id"], ["ui_recording_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_element_id"], ["ui_elements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_before_id"], ["ui_page_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_after_id"], ["ui_page_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "source_event_id", name="uq_ui_action_source_event"),
    )
    op.create_index("ix_ui_recorded_actions_session_id", "ui_recorded_actions", ["session_id"])
    op.create_index("ix_ui_recorded_actions_source_event_id", "ui_recorded_actions", ["source_event_id"])
    op.create_index("ix_ui_recorded_actions_action_type", "ui_recorded_actions", ["action_type"])
    op.create_index("ix_ui_recorded_actions_target_element_id", "ui_recorded_actions", ["target_element_id"])
    op.create_index("ix_ui_action_session_order", "ui_recorded_actions", ["session_id", "sequence_no"])

    op.create_table(
        "ui_page_transitions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("source_event_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("source_page_key", sa.String(length=255), nullable=True),
        sa.Column("target_page_key", sa.String(length=255), nullable=False),
        sa.Column("action_id", sa.BigInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["ui_recorded_actions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["ui_recording_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_event_id"], ["ui_recording_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "source_event_id", name="uq_ui_transition_source_event"),
    )
    for column in ("project_id", "session_id", "platform", "source_page_key", "target_page_key"):
        op.create_index(f"ix_ui_page_transitions_{column}", "ui_page_transitions", [column])

    op.create_table(
        "ui_context_sessions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("recording_session_id", sa.Integer(), nullable=True),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_ARRAY, nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recording_session_id"], ["ui_recording_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["test_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recording_session_id"),
    )
    for column in ("project_id", "recording_session_id", "report_id", "kind", "platform"):
        op.create_index(f"ix_ui_context_sessions_{column}", "ui_context_sessions", [column])

    op.create_table(
        "ui_context_artifacts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("context_session_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=True),
        sa.Column("artifact_type", sa.String(length=40), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["context_session_id"], ["ui_context_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["ui_recording_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ui_context_artifacts_context_session_id", "ui_context_artifacts", ["context_session_id"])
    op.create_index("ix_ui_context_artifacts_event_id", "ui_context_artifacts", ["event_id"])
    op.create_index("ix_ui_context_artifacts_artifact_type", "ui_context_artifacts", ["artifact_type"])
    op.create_index("ix_ui_context_artifact_session_type", "ui_context_artifacts", ["context_session_id", "artifact_type"])

    op.create_table(
        "ui_step_context_links",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("context_session_id", sa.BigInteger(), nullable=False),
        sa.Column("recorded_action_id", sa.BigInteger(), nullable=True),
        sa.Column("test_step_report_id", sa.Integer(), nullable=True),
        sa.Column("event_from_seq", sa.BigInteger(), nullable=True),
        sa.Column("event_to_seq", sa.BigInteger(), nullable=True),
        sa.Column("screenshot_before_id", sa.BigInteger(), nullable=True),
        sa.Column("screenshot_after_id", sa.BigInteger(), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default=_EMPTY_OBJECT, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["context_session_id"], ["ui_context_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_action_id"], ["ui_recorded_actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_step_report_id"], ["test_step_reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["screenshot_before_id"], ["ui_context_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["screenshot_after_id"], ["ui_context_artifacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "context_session_id", "recorded_action_id", "test_step_report_id",
            name="uq_ui_step_context_target",
        ),
    )
    for column in ("context_session_id", "recorded_action_id", "test_step_report_id"):
        op.create_index(f"ix_ui_step_context_links_{column}", "ui_step_context_links", [column])

    op.add_column("test_reports", sa.Column("context_session_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_test_reports_context_session",
        "test_reports", "ui_context_sessions", ["context_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_test_reports_context_session_id", "test_reports", ["context_session_id"])
    op.add_column("test_step_reports", sa.Column("context_session_id", sa.BigInteger(), nullable=True))
    op.add_column("test_step_reports", sa.Column("context_event_from_seq", sa.BigInteger(), nullable=True))
    op.add_column("test_step_reports", sa.Column("context_event_to_seq", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_test_step_reports_context_session",
        "test_step_reports", "ui_context_sessions", ["context_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_test_step_reports_context_session_id", "test_step_reports", ["context_session_id"])


def downgrade() -> None:
    op.drop_index("ix_test_step_reports_context_session_id", table_name="test_step_reports")
    op.drop_constraint("fk_test_step_reports_context_session", "test_step_reports", type_="foreignkey")
    op.drop_column("test_step_reports", "context_event_to_seq")
    op.drop_column("test_step_reports", "context_event_from_seq")
    op.drop_column("test_step_reports", "context_session_id")
    op.drop_index("ix_test_reports_context_session_id", table_name="test_reports")
    op.drop_constraint("fk_test_reports_context_session", "test_reports", type_="foreignkey")
    op.drop_column("test_reports", "context_session_id")

    for column in ("test_step_report_id", "recorded_action_id", "context_session_id"):
        op.drop_index(f"ix_ui_step_context_links_{column}", table_name="ui_step_context_links")
    op.drop_table("ui_step_context_links")
    op.drop_index("ix_ui_context_artifact_session_type", table_name="ui_context_artifacts")
    op.drop_index("ix_ui_context_artifacts_artifact_type", table_name="ui_context_artifacts")
    op.drop_index("ix_ui_context_artifacts_event_id", table_name="ui_context_artifacts")
    op.drop_index("ix_ui_context_artifacts_context_session_id", table_name="ui_context_artifacts")
    op.drop_table("ui_context_artifacts")
    for column in ("platform", "kind", "report_id", "recording_session_id", "project_id"):
        op.drop_index(f"ix_ui_context_sessions_{column}", table_name="ui_context_sessions")
    op.drop_table("ui_context_sessions")
    for column in ("target_page_key", "source_page_key", "platform", "session_id", "project_id"):
        op.drop_index(f"ix_ui_page_transitions_{column}", table_name="ui_page_transitions")
    op.drop_table("ui_page_transitions")
    op.drop_index("ix_ui_action_session_order", table_name="ui_recorded_actions")
    op.drop_index("ix_ui_recorded_actions_target_element_id", table_name="ui_recorded_actions")
    op.drop_index("ix_ui_recorded_actions_action_type", table_name="ui_recorded_actions")
    op.drop_index("ix_ui_recorded_actions_source_event_id", table_name="ui_recorded_actions")
    op.drop_index("ix_ui_recorded_actions_session_id", table_name="ui_recorded_actions")
    op.drop_table("ui_recorded_actions")
    op.drop_index("ix_ui_occurrence_snapshot_element", table_name="ui_element_occurrences")
    op.drop_index("ix_ui_element_occurrences_element_id", table_name="ui_element_occurrences")
    op.drop_index("ix_ui_element_occurrences_snapshot_id", table_name="ui_element_occurrences")
    op.drop_index("ix_ui_element_occurrences_session_id", table_name="ui_element_occurrences")
    op.drop_table("ui_element_occurrences")
