"""UI 录制数据删除审计。

Revision ID: ui_deletion_audit_004
Revises: ui_execution_context_003
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "ui_deletion_audit_004"
down_revision = "ui_execution_context_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_deletion_audits",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("operator_id", sa.Integer(), nullable=True),
        sa.Column("operator_name", sa.String(length=128), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("object_id", sa.String(length=255), nullable=False),
        sa.Column("object_name", sa.String(length=255), nullable=True),
        sa.Column(
            "cascade_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ui_deletion_audits_project_id", "ui_deletion_audits", ["project_id"])
    op.create_index("ix_ui_deletion_audits_operator_id", "ui_deletion_audits", ["operator_id"])
    op.create_index("ix_ui_deletion_audits_object_type", "ui_deletion_audits", ["object_type"])
    op.create_index("ix_ui_deletion_audits_deleted_at", "ui_deletion_audits", ["deleted_at"])
    op.create_index(
        "ix_ui_deletion_audit_project_time",
        "ui_deletion_audits",
        ["project_id", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ui_deletion_audit_project_time", table_name="ui_deletion_audits")
    op.drop_index("ix_ui_deletion_audits_deleted_at", table_name="ui_deletion_audits")
    op.drop_index("ix_ui_deletion_audits_object_type", table_name="ui_deletion_audits")
    op.drop_index("ix_ui_deletion_audits_operator_id", table_name="ui_deletion_audits")
    op.drop_index("ix_ui_deletion_audits_project_id", table_name="ui_deletion_audits")
    op.drop_table("ui_deletion_audits")
