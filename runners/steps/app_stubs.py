"""(DEPRECATED) App step runner 的占位实现 —— 已在 Phase 3 被 `app_actions.py` 替换。

这个模块保留只是为了兼容那些可能还直接 `from src.runners.steps.app_stubs import ...`
的旧代码；所有符号都转发到 `app_actions`。新代码请直接用 `app_actions`。
"""
from __future__ import annotations

from runners.steps.app_actions import build_app_runners  # noqa: F401

__all__ = ["build_app_runners"]
