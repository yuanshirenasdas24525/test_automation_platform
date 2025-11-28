from src.utils.logger import LOGGER

class UIAction:
    def __init__(self, page):
        self.page = page

    def click(self, locator):
        LOGGER.info(f"[UI] 点击元素：{locator}")
        self.page.locator(locator).click()

    def input(self, locator, text):
        LOGGER.info(f"[UI] 输入内容：{text}")
        ele = self.page.locator(locator)
        ele.fill(text)

    def assert_text(self, locator, expected):
        actual = self.page.locator(locator).inner_text()
        if expected not in actual:
            take_screenshot(self.page, "assert_failed.png")
            raise AssertionError(f"断言失败：期望 '{expected}'，实际 '{actual}'")
        LOGGER.info("断言成功")