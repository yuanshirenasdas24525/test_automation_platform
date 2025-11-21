# -*- coding:utf-8 -*-
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from tenacity import retry, stop_after_attempt, wait_fixed
from src.utils.allure_utils import add_allure_attachment
from src.utils.logger import LOGGER, ERROR_LOGGER


class _AppiumBy:
    LOCATORS = {
        "id": AppiumBy.ID,
        "xpath": AppiumBy.XPATH,
        "image": AppiumBy.IMAGE,
        "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
        "android_uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
        "android_viewtag": AppiumBy.ANDROID_VIEWTAG,
        "android_data_matcher": AppiumBy.ANDROID_DATA_MATCHER,
        "android_view_matcher": AppiumBy.ANDROID_VIEW_MATCHER,
        "ios_predicate": AppiumBy.IOS_PREDICATE,
        "ios_class_chain": AppiumBy.IOS_CLASS_CHAIN,
        "class_name": AppiumBy.CLASS_NAME,
        "link_text": AppiumBy.LINK_TEXT,
        "css_selector": AppiumBy.CSS_SELECTOR,
        "name": AppiumBy.NAME,
    }

    @classmethod
    def by(cls, by, value):
        by = by.lower()
        if by not in cls.LOCATORS:
            raise ValueError(f"无效的定位策略 '{by}'")
        return cls.LOCATORS[by], value


class Finder:
    def __init__(self, driver, blacklist=None, whitelist=None, device_action=None):
        self.driver = driver
        self.blacklist = blacklist or []
        self.whitelist = whitelist or []
        self.device_action = device_action
        self._error_count = 0
        self._error_max = 10

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    def find(self, by, value, timeout=5):
        try:
            el = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located(_AppiumBy.by(by, value))
            )
            self._error_count = 0
            return el
        except Exception as e:
            try:
                self.handle_blacklist()
            except:
                pass

            if (by, value) in self.whitelist:
                return "whitelist"

            self._error_count += 1
            ERROR_LOGGER.error(f"定位失败: {by}_{value}")

            if self._error_count % 3 == 0 and self.device_action:
                self.device_action.take_screenshot("find_error.png")
                add_allure_attachment("定位失败", str(e))

            if self._error_count >= self._error_max:
                raise Exception("达到最大定位失败次数") from e

            raise

    def swipe_find(self, step, attempts=3):
        size = self.driver.get_window_size()
        mode = step.get("sliding_location", "vertical").split("_")[0]

        by = step["by"]
        value = step["finder"]

        for _ in range(attempts):
            try:
                return WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located(_AppiumBy.by(by, value))
                )
            except:
                if mode == "vertical":
                    self.driver.swipe(size["width"]//2, int(size["height"]*0.8),
                                      size["width"]//2, int(size["height"]*0.2), 500)
                else:
                    self.driver.swipe(int(size["width"]*0.8), size["height"]//2,
                                      int(size["width"]*0.2), size["height"]//2, 500)

        raise Exception("滑动查找失败")

    def handle_blacklist(self):
        size = self.driver.get_window_size()
        for _ in range(5):
            clicked = False
            for by, value in self.blacklist:
                try:
                    els = self.driver.find_elements(*_AppiumBy.by(by, value))
                    if els:
                        LOGGER.info(f"处理黑名单: {value}")
                        try:
                            els[0].click()
                        except:
                            self.driver.tap([
                                (int(size["width"] * 0.9), int(size["height"] * 0.2))
                            ])
                        clicked = True
                except:
                    pass
            if not clicked:
                return

        self.device_action.take_screenshot("blacklist_fail.png")
        add_allure_attachment("黑名单处理失败", "")
        raise Exception("黑名单处理达到最大次数")