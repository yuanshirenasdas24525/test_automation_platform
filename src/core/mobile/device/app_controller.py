# -*- coding:utf-8 -*-
from src.utils.logger import LOGGER, ERROR_LOGGER

class AppController:
    def __init__(self, driver):
        self.driver = driver

    def install_app(self, path):
        try:
            self.driver.install_app(path)
            LOGGER.info(f"应用已安装: {path}")
        except:
            pass

    def uninstall_app(self, app_id):
        try:
            self.driver.remove_app(app_id)
            LOGGER.info(f"应用已卸载: {app_id}")
        except:
            pass

    def launch_app(self):
        try:
            self.driver.launch_app()
            LOGGER.info("应用已启动")
        except:
            pass

    def close_app(self):
        try:
            self.driver.close_app()
            LOGGER.info("应用已关闭")
        except:
            pass

    def is_app_installed(self, app_id):
        try:
            return self.driver.is_app_installed(app_id)
        except:
            return False

    def reset_app(self):
        try:
            self.driver.reset()
            LOGGER.info("应用已重置")
        except:
            pass