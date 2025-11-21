# -*- coding:utf-8 -*-
class AppController:
    """
    控制 App 相关操作：启动、关闭、安装卸载等
    """

    def __init__(self, driver):
        self.driver = driver

    def launch_app(self):
        try:
            return self.driver.launch_app()
        except:
            return None

    def close_app(self):
        try:
            return self.driver.close_app()
        except:
            return None

    def install_app(self, path):
        try:
            return self.driver.install_app(path)
        except:
            return None

    def uninstall_app(self, bundle_id):
        try:
            return self.driver.remove_app(bundle_id)
        except:
            return None