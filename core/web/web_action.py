"""已废弃：旧版 UIAction（直接包 Playwright `page`）。

替代方案：
  - Step 层：用 `web_click / web_input / web_assert_text` 等声明式 step_type；
  - 底层：如果确实要手写代码跑 Web UI，用
    `src.runners.web.adapters.WebDriverAdapter` 的接口（`build_adapter("playwright", ...)`
    或 `build_adapter("selenium", ...)` 得到实例）—— 它屏蔽了 Playwright / Selenium 差异。

保留本文件仅发出 DeprecationWarning，接口与旧版一致，避免老脚本 import 即崩溃。
"""
from __future__ import annotations

import warnings

from utils.logger import LOGGER

_DEPRECATION_MSG = (
    "src.core.web.web_action.UIAction 已废弃，请改用 "
    "src.runners.web.adapters.WebDriverAdapter（通过 WebSession.adapter 获取）。"
)


class UIAction:
    """直接包 Playwright page 的旧接口，仅为历史脚本兼容保留。"""

    def __init__(self, page):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        self.page = page

    def click(self, locator):  # pragma: no cover - legacy path
        LOGGER.info(f"[DEPRECATED UIAction] 点击元素：{locator}")
        self.page.locator(locator).click()

    def input(self, locator, text):  # pragma: no cover - legacy path
        LOGGER.info(f"[DEPRECATED UIAction] 输入内容：{text}")
        self.page.locator(locator).fill(text)

    def assert_text(self, locator, expected):  # pragma: no cover - legacy path
        actual = self.page.locator(locator).inner_text()
        if expected not in actual:
            raise AssertionError(f"断言失败：期望 '{expected}'，实际 '{actual}'")
        LOGGER.info("[DEPRECATED UIAction] 断言成功")


__all__ = ["UIAction"]
