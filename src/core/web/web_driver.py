from playwright.sync_api import sync_playwright
from src.utils.logger import LOGGER
from src.utils.read_file import read_conf

c = read_conf.get_dict("wei_ui_headless")

class UIDriver:
    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance.playwright = None
            cls._instance.browser = None
            cls._instance.context = None
            cls._instance.page = None
        return cls._instance

    def start(self):
        LOGGER.info("启动 Playwright 浏览器")
        self.playwright = sync_playwright().start()
        headless = c.get("ui.headless", False)

        self.browser = self.playwright.chromium.launch(headless=headless)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

        return self.page

    def stop(self):
        LOGGER.info("关闭 Playwright 浏览器")
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()