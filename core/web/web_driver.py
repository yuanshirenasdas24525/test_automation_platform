"""已废弃：旧版 Playwright 单例 UIDriver。

为什么废弃：
  - 单例模式天生不支持"一条 case 一个浏览器上下文"的隔离语义，跑并发用例会互相
    污染 cookies / localStorage。
  - 硬编码 `chromium` / `playwright.sync_api`，无法切到 Selenium。

替代方案：
  - 使用 `src.runners.web.session.WebSession` —— 基于 WebDriverAdapter 抽象，
    Playwright / Selenium 二选一，生命周期绑定到一条 TestCase；
  - step 层直接写 `web_goto / web_click / ...` 即可，不需要自己持有 driver。

保留本文件是为了让历史脚本 `from src.core.web.web_driver import UIDriver` 不立刻
炸掉，仅发出 DeprecationWarning，接口保持不变。预计在 v2.x 彻底移除。
"""
from __future__ import annotations

import warnings

from utils.logger import LOGGER

_DEPRECATION_MSG = (
    "src.core.web.web_driver.UIDriver 已废弃，请切换到 "
    "src.runners.web.session.WebSession（支持 Playwright / Selenium 双引擎）。"
)


class UIDriver:
    """旧版 Playwright 单例 —— 仅为向后兼容保留，新代码禁止使用。"""

    _instance = None

    def __new__(cls):
        warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance.playwright = None
            cls._instance.browser = None
            cls._instance.context = None
            cls._instance.page = None
        return cls._instance

    def start(self):  # pragma: no cover - legacy path
        from playwright.sync_api import sync_playwright
        from utils.read_conf import read_conf

        c = read_conf.get_dict("wei_ui_headless")

        LOGGER.info("[DEPRECATED UIDriver] 启动 Playwright 浏览器")
        self.playwright = sync_playwright().start()
        headless = c.get("ui.headless", False)

        self.browser = self.playwright.chromium.launch(headless=headless)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        return self.page

    def stop(self):  # pragma: no cover - legacy path
        LOGGER.info("[DEPRECATED UIDriver] 关闭 Playwright 浏览器")
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()


__all__ = ["UIDriver"]
