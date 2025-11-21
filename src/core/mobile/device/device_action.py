# -*- coding:utf-8 -*-
from .input_controller import InputController
from .system_controller import SystemController
from .app_controller import AppController


class DeviceAction:
    """
    Facade 总入口，负责统一调度 input/system/app 控制器。
    """

    def __init__(self, driver):
        self.driver = driver
        self.input = InputController(driver)
        self.system = SystemController(driver)
        self.app = AppController(driver)

    # === 常用方法（外部直接调用） ===
    def take_screenshot(self, name=None):
        return self.system.take_screenshot(name)

    def driver_back(self):
        return self.input.back()

    def tap(self, positions):
        return self.input.tap(positions)

    def swipe(self, *args, **kwargs):
        return self.input.swipe(*args, **kwargs)

    def open_notifications(self):
        return self.system.open_notifications()

    def launch_app(self):
        return self.app.launch_app()

    def close_app(self):
        return self.app.close_app()

    def install_app(self, path):
        return self.app.install_app(path)

    def uninstall_app(self, bundle_id):
        return self.app.uninstall_app(bundle_id)

    # === 兜底：所有未在 facade 中定义的方法，自动从三个控制器中找 ===
    def __getattr__(self, name):
        for c in (self.input, self.system, self.app):
            if hasattr(c, name):
                return getattr(c, name)
        raise AttributeError(f"DeviceAction: 未找到方法 {name}")