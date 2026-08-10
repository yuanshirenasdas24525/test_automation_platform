"""UI 录制主基线与补充会话。

Revision ID: ui_recording_baseline_005
Revises: ui_deletion_audit_004
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ui_recording_baseline_005"
down_revision = "ui_deletion_audit_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ui_recording_sessions",
        sa.Column("recording_role", sa.String(length=20), server_default="history", nullable=False),
    )
    op.add_column(
        "ui_recording_sessions",
        sa.Column("baseline_session_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ui_recording_sessions",
        sa.Column("baseline_included", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "ui_recording_sessions",
        sa.Column("baseline_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "ui_recording_sessions",
        sa.Column("merged_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ui_recording_baseline_session",
        "ui_recording_sessions",
        "ui_recording_sessions",
        ["baseline_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ui_recording_sessions_recording_role",
        "ui_recording_sessions",
        ["recording_role"],
    )
    op.create_index(
        "ix_ui_recording_sessions_baseline_session_id",
        "ui_recording_sessions",
        ["baseline_session_id"],
    )

    # 历史数据按“页面最多、Mock 最多、时间最新”选出每个平台的主录制。
    op.execute(
        """
        WITH metrics AS (
            SELECT
                s.id,
                s.project_id,
                s.platform,
                s.created_at,
                COUNT(DISTINCT p.id) AS page_count,
                COUNT(DISTINCT m.id) AS mock_count
            FROM ui_recording_sessions AS s
            LEFT JOIN ui_page_snapshots AS p ON p.session_id = s.id
            LEFT JOIN ui_mock_exchanges AS m ON m.session_id = s.id
            WHERE s.status = 'completed'
            GROUP BY s.id, s.project_id, s.platform, s.created_at
        ),
        ranked AS (
            SELECT
                id,
                FIRST_VALUE(id) OVER (
                    PARTITION BY project_id, platform
                    ORDER BY page_count DESC, mock_count DESC, created_at DESC, id DESC
                ) AS primary_id
            FROM metrics
        )
        UPDATE ui_recording_sessions AS s
        SET
            recording_role = CASE WHEN s.id = r.primary_id THEN 'primary' ELSE 'supplement' END,
            baseline_session_id = CASE WHEN s.id = r.primary_id THEN NULL ELSE r.primary_id END,
            baseline_included = CASE WHEN s.id = r.primary_id THEN TRUE ELSE FALSE END,
            merged_at = NULL
        FROM ranked AS r
        WHERE s.id = r.id
        """
    )
    op.execute(
        """
        UPDATE ui_recording_sessions AS primary_session
        SET baseline_version = 1
        WHERE primary_session.recording_role = 'primary'
        """
    )
    op.create_index(
        "uq_ui_recording_primary_project_platform",
        "ui_recording_sessions",
        ["project_id", "platform"],
        unique=True,
        postgresql_where=sa.text("recording_role = 'primary'"),
    )


def downgrade() -> None:
    op.drop_index("uq_ui_recording_primary_project_platform", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_baseline_session_id", table_name="ui_recording_sessions")
    op.drop_index("ix_ui_recording_sessions_recording_role", table_name="ui_recording_sessions")
    op.drop_constraint(
        "fk_ui_recording_baseline_session",
        "ui_recording_sessions",
        type_="foreignkey",
    )
    op.drop_column("ui_recording_sessions", "merged_at")
    op.drop_column("ui_recording_sessions", "baseline_version")
    op.drop_column("ui_recording_sessions", "baseline_included")
    op.drop_column("ui_recording_sessions", "baseline_session_id")
    op.drop_column("ui_recording_sessions", "recording_role")
