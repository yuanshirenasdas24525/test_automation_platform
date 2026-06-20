"""functional_case_edit_history.session_id

给功能用例编辑历史加 session_id：同一次快速编辑会话的多条改动共享一个 id，
前端按它聚合成「一条编辑记录」。

Revision ID: fc_edit_sess_001
Revises: fc_edit_hist_001
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fc_edit_sess_001"
down_revision: Union[str, None] = "fc_edit_hist_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "functional_case_edit_history",
        sa.Column("session_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_fc_edit_hist_session_id", "functional_case_edit_history", ["session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_fc_edit_hist_session_id", table_name="functional_case_edit_history")
    op.drop_column("functional_case_edit_history", "session_id")
