"""functional_case_edit_history

功能用例编辑历史表：记录 functional 用例的新建 / 修改 / 删除。

Revision ID: fc_edit_hist_001
Revises: c8753707405a
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fc_edit_hist_001"
down_revision: Union[str, None] = "c8753707405a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "functional_case_edit_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("module_id", sa.Integer(), nullable=True),
        sa.Column("case_name", sa.String(length=255), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_fc_edit_hist_case_id", "functional_case_edit_history", ["case_id"]
    )
    op.create_index(
        "ix_fc_edit_hist_module_id", "functional_case_edit_history", ["module_id"]
    )
    op.create_index(
        "ix_fc_edit_hist_created_at", "functional_case_edit_history", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_fc_edit_hist_created_at", table_name="functional_case_edit_history")
    op.drop_index("ix_fc_edit_hist_module_id", table_name="functional_case_edit_history")
    op.drop_index("ix_fc_edit_hist_case_id", table_name="functional_case_edit_history")
    op.drop_table("functional_case_edit_history")
