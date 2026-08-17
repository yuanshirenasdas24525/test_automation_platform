"""AI 任务公共收尾逻辑的轻量回归测试。"""
from __future__ import annotations

from tasks.ai_tasks import _normalize_prompt_version


def test_prompt_version_is_trimmed_to_database_column_limit():
    assert _normalize_prompt_version("web-ui-v4-progressive-batched") == "web-ui-v4-progressiv"


def test_blank_prompt_version_becomes_none():
    assert _normalize_prompt_version("   ") is None
