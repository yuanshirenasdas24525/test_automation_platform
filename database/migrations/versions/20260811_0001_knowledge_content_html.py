"""project_contexts.content_html：知识库文档的富文本原文

知识库文档与 AI 上下文共用 project_contexts 表（source_type='knowledge'）。
content 存去标签纯文本供检索，content_html 存富文本原文供人阅读/编辑。

Revision ID: knowledge_content_html_001
Revises: device_busy_since_001
"""
from __future__ import annotations

from alembic import op


revision = "knowledge_content_html_001"
down_revision = "device_busy_since_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 部分历史开发库曾通过手工同步模型提前创建该字段，使用 PostgreSQL
    # 的幂等 DDL，避免迁移版本表落后于实际表结构时启动失败。
    op.execute(
        "ALTER TABLE project_contexts "
        "ADD COLUMN IF NOT EXISTS content_html TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE project_contexts "
        "DROP COLUMN IF EXISTS content_html"
    )
