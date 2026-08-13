"""合并 UI 录制与知识库富文本迁移分支。

Revision ID: ui_knowledge_merge_001
Revises: ui_recording_baseline_005, knowledge_content_html_001
"""
from __future__ import annotations


revision = "ui_knowledge_merge_001"
down_revision = ("ui_recording_baseline_005", "knowledge_content_html_001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """合并迁移图，不执行额外的数据结构变更。"""


def downgrade() -> None:
    """拆回两个迁移分支，不执行额外的数据结构变更。"""
