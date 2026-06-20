"""project.ai_overview + ai_overview_updated_at

给项目加 AI 概览（模块关联图谱）列：AI 读取项目所有模块产出
{summary, modules[], relations[]}，给「按模块生成用例」提供跨模块关联依据，
前端项目页 / AI 弹窗可预览。

Revision ID: proj_ai_overview_001
Revises: fc_edit_sess_001
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "proj_ai_overview_001"
down_revision: Union[str, None] = "fc_edit_sess_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.add_column("projects", sa.Column("ai_overview", json_type, nullable=True))
    op.add_column(
        "projects", sa.Column("ai_overview_updated_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "ai_overview_updated_at")
    op.drop_column("projects", "ai_overview")
