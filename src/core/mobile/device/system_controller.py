# -*- coding:utf-8 -*-
import os
import time


class SystemController:
    """
    控制系统相关操作：截图、通知栏等
    """

    def __init__(self, driver):
        self.driver = driver

    def take_screenshot(self, name=None):
        name = name or f"screenshot_{int(time.time())}.png"
        try:
            path = os.path.join(os.getcwd(), name)
            self.driver.get_screenshot_as_file(path)
            return path
        except:
            return None

    def open_notifications(self):
        try:
            return self.driver.open_notifications()
        except:
            return None