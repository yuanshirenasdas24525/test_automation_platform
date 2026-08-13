"""project_contexts.content_html：知识库文档的富文本原文

知识库文档与 AI 上下文共用 project_contexts 表（source_type='knowledge'）。
content 存去标签纯文本供检索，content_html 存富文本原文供人阅读/编辑。

Revision ID: knowledge_content_html_001
Revises: device_busy_since_001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "knowledge_content_html_001"
down_revision = "device_busy_since_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_contexts",
        sa.Column("content_html", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_contexts", "content_html")
