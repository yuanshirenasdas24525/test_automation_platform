"""新增 UI 录制控制面、页面快照、元素事实和离线 Mock 数据表。

Revision ID: ui_recording_control_001
Revises: api_case_gen_meta_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "ui_recording_control_001"
down_revision = "api_case_gen_meta_001"
branch_labels = None
depends_on = None


_EMPTY_OBJECT = sa.text("'{}'::jsonb")
_EMPTY_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "ui_recording_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("app_package_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("recorder_agent_id", sa.String(length=128), nullable=True),
        sa.Column("offline_level", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "capture_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column(
            "context_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("paused_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["app_package_id"], ["app_packages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["environment_id"], ["test_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ui_recording_sessions_id", "ui_recording_sessions", ["id"])
    op.create_index("ix_ui_recording_sessions_project_id", "ui_recording_sessions", ["project_id"])
    op.create_index("ix_ui_recording_sessions_platform", "ui_recording_sessions", ["platform"])
    op.create_index("ix_ui_recording_sessions_status", "ui_recording_sessions", ["status"])
    op.create_index("ix_ui_recording_sessions_environment_id", "ui_recording_sessions", ["environment_id"])
    op.create_index("ix_ui_recording_sessions_device_id", "ui_recording_sessions", ["device_id"])
    op.create_index("ix_ui_recording_sessions_app_package_id", "ui_recording_sessions", ["app_package_id"])
    op.create_index("ix_ui_recording_sessions_created_by_id", "ui_recording_sessions", ["created_by_id"])
    op.create_index("ix_ui_recording_sessions_recorder_agent_id", "ui_recording_sessions", ["recorder_agent_id"])
    op.create_index("ix_ui_recording_sessions_created_at", "ui_recording_sessions", ["created_at"])

    op.create_table(
        "ui_page_snapshots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("page_key", sa.String(length=255), nullable=False),
        sa.Column("page_name", sa.String(length=200), nullable=False),
        sa.Column("state_name", sa.String(length=120), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("route", sa.String(length=500), nullable=True),
        sa.Column("app_identifier", sa.String(length=255), nullable=True),
        sa.Column("app_version", sa.String(length=80), nullable=True),
        sa.Column("snapshot_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("screenshot_uri", sa.Text(), nullable=True),
        sa.Column("document_uri", sa.Text(), nullable=True),
        sa.Column("tree_uri", sa.Text(), nullable=True),
        sa.Column("offline_package_uri", sa.Text(), nullable=True),
        sa.Column("is_interactive", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "resource_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column(
            "environment",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column(
            "limitations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_ARRAY,
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["ui_recording_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ui_page_snapshots_session_id", "ui_page_snapshots", ["session_id"])
    op.create_index("ix_ui_page_snapshots_project_id", "ui_page_snapshots", ["project_id"])
    op.create_index("ix_ui_page_snapshots_platform", "ui_page_snapshots", ["platform"])
    op.create_index("ix_ui_page_snapshots_page_key", "ui_page_snapshots", ["page_key"])
    op.create_index("ix_ui_page_snapshots_fingerprint", "ui_page_snapshots", ["fingerprint"])
    op.create_index("ix_ui_page_snapshots_created_at", "ui_page_snapshots", ["created_at"])
    op.create_index(
        "ix_ui_snapshot_project_page",
        "ui_page_snapshots",
        ["project_id", "platform", "page_key"],
    )

    op.create_table(
        "ui_elements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("page_key", sa.String(length=255), nullable=False),
        sa.Column("page_name", sa.String(length=200), nullable=False),
        sa.Column("semantic_name", sa.String(length=200), nullable=False),
        sa.Column("element_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column("first_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("last_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("usage_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["first_snapshot_id"], ["ui_page_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["last_snapshot_id"], ["ui_page_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "platform",
            "page_key",
            "fingerprint",
            name="uq_ui_element_page_fingerprint",
        ),
    )
    op.create_index("ix_ui_elements_id", "ui_elements", ["id"])
    op.create_index("ix_ui_elements_project_id", "ui_elements", ["project_id"])
    op.create_index("ix_ui_elements_platform", "ui_elements", ["platform"])
    op.create_index("ix_ui_elements_page_key", "ui_elements", ["page_key"])
    op.create_index("ix_ui_elements_status", "ui_elements", ["status"])
    op.create_index(
        "ix_ui_element_project_page",
        "ui_elements",
        ["project_id", "platform", "page_key"],
    )

    op.create_table(
        "ui_element_locators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("element_id", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.Column("score", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_unique", sa.Boolean(), nullable=True),
        sa.Column("match_count", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), server_default="recorder", nullable=False),
        sa.Column("last_verified_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["element_id"], ["ui_elements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_verified_snapshot_id"], ["ui_page_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "element_id",
            "strategy",
            "locator",
            name="uq_ui_locator_element_strategy_value",
        ),
    )
    op.create_index("ix_ui_element_locators_id", "ui_element_locators", ["id"])
    op.create_index("ix_ui_element_locators_element_id", "ui_element_locators", ["element_id"])
    op.create_index("ix_ui_element_locators_strategy", "ui_element_locators", ["strategy"])

    op.create_table(
        "ui_recording_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=80), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=20), server_default="info", nullable=False),
        sa.Column("page_key", sa.String(length=255), nullable=True),
        sa.Column("element_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_before_id", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_after_id", sa.BigInteger(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("monotonic_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["element_id"], ["ui_elements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["ui_recording_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "event_key", name="uq_ui_event_session_key"),
        sa.UniqueConstraint("session_id", "sequence_no", name="uq_ui_event_session_sequence"),
    )
    op.create_index("ix_ui_recording_events_session_id", "ui_recording_events", ["session_id"])
    op.create_index("ix_ui_recording_events_event_type", "ui_recording_events", ["event_type"])
    op.create_index("ix_ui_recording_events_source", "ui_recording_events", ["source"])
    op.create_index("ix_ui_recording_events_page_key", "ui_recording_events", ["page_key"])
    op.create_index("ix_ui_recording_events_element_id", "ui_recording_events", ["element_id"])
    op.create_index("ix_ui_recording_events_occurred_at", "ui_recording_events", ["occurred_at"])
    op.create_index(
        "ix_ui_event_session_occurred",
        "ui_recording_events",
        ["session_id", "occurred_at"],
    )

    op.create_table(
        "ui_mock_exchanges",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("exchange_key", sa.String(length=80), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column(
            "request",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column(
            "response",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column(
            "match_rule",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column(
            "timing",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_EMPTY_OBJECT,
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ui_recording_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["ui_page_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "exchange_key", name="uq_ui_mock_session_key"),
    )
    op.create_index("ix_ui_mock_exchanges_session_id", "ui_mock_exchanges", ["session_id"])
    op.create_index("ix_ui_mock_exchanges_snapshot_id", "ui_mock_exchanges", ["snapshot_id"])
    op.create_index(
        "ix_ui_mock_session_request",
        "ui_mock_exchanges",
        ["session_id", "method", "request_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_ui_mock_session_request", table_name="ui_mock_exchanges")
    op.drop_index("ix_ui_mock_exchanges_snapshot_id", table_name="ui_mock_exchanges")
    op.drop_index("ix_ui_mock_exchanges_session_id", table_name="ui_mock_exchanges")
    op.drop_table("ui_mock_exchanges")

    op.drop_index("ix_ui_event_session_occurred", table_name="ui_recording_events")
    op.drop_index("ix_ui_recording_events_occurred_at", table_name="ui_recording_events")
    op.drop_index("ix_ui_recording_events_element_id", table_name="ui_recording_events")
    op.drop_index("ix_ui_recording_events_page_key", table_name="ui_recording_events")
    op.drop_index("ix_ui_recording_events_source", table_name="ui_recording_events")
    op.drop_index("ix_ui_recording_events_event_type", table_name="ui_recording_events")
    op.drop_index("ix_ui_recording_events_session_id", table_name="ui_recording_events")
    op.drop_table("ui_recording_events")

    op.drop_index("ix_ui_element_locators_strategy", table_name="ui_element_locators")
    op.drop_index("ix_ui_element_locators_element_id", table_name="ui_element_locators")
    op.drop_index("ix_ui_element_locators_id", table_name="ui_element_locators")
    op.drop_table("ui_element_locators")

    op.drop_index("ix_ui_element_project_page", table_name="ui_elements")
    op.drop_index("ix_ui_elements_status", table_name="ui_elements")
    op.drop_index("ix_ui_elements_page_key", table_name="ui_elements")
    op.drop_index("ix_ui_elements_platform", table_name="ui_elements")
    op.drop_index("ix_ui_elements_project_id", table_name="ui_elements")
    op.drop_index("ix_ui_elements_id", table_name="ui_elements")
    op.drop_table("ui_elements")

    op.drop_index("ix_ui_snapshot_project_page", table_name="ui_page_snapshots")
    op.drop_index("ix_ui_page_snapshots_created_at", table_name="ui_page_snapshots")
    op.drop_index("ix_ui_page_snapshots_fingerprint", table_name="ui_page_snapshots")
    op.drop_index("ix_ui_page_snapshots_page_key", table_name="ui_page_snapshots")
    op.drop_index("ix_ui_page_snapshots_platform", table_name="ui_page_snapshots")
    op.drop_index("ix_ui_page_snapshots_project_id", table_name="ui_page_snapshots")
    op.drop_index("ix_ui_page_snapshots_session_id", table_name="ui_page_snapshots")
    op.drop_table("ui_page_snapshots")

    op.drop_index("ix_ui_recording_sessions_created_at", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_recorder_agent_id", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_created_by_id", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_app_package_id", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_device_id", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_environment_id", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_status", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_platform", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_project_id", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_id", table_name="ui_recording_sessions")
    op.drop_table("ui_recording_sessions")
