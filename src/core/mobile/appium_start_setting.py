# -*- coding:utf-8 -*-
# Create on
from appium import webdriver
from appium.options.android import UiAutomator2Options
from src.utils.logger import LOGGER, ERROR_LOGGER


class App:
    def __init__(self, start_conf, appium_server_url):
        self._driver = None
        self._config = start_conf
        self._appium_server_url = appium_server_url

    def start(self, restart=False, implicit_wait=5):
        try:
            if self._driver is None or restart:
                options = UiAutomator2Options().load_capabilities(self._config)
                if restart:
                    options.set_capability("dontStopAppOnReset", "true")
                    LOGGER.info("重启应用")
                self._driver = webdriver.Remote(self._appium_server_url, options=options)
                self._driver.implicitly_wait(implicit_wait)
                LOGGER.info(f"启动应用 {self._config} --｜-- {self._driver}")
                return self._driver
            else:
                self._driver.activate_app(self._config['apppackage'])
                LOGGER.info(f"应用已激活 {self._config}")
                return self._driver
        except Exception as e:
            ERROR_LOGGER.error(f"无法启动应用 {self._config}: {e}")
            # 可以添加重试逻辑或其他异常处理


class AppFactory:
    _apps = {}

    @staticmethod
    def create_app(start_conf, appium_server_url):
        """
        创建或获取一个应用实例。
        :param appium_server_url: appium服务地址
        :param start_conf: 应用的名称。
        :return: 应用实例。
        """
        if start_conf['apppackage'] not in AppFactory._apps:
            AppFactory._apps[start_conf['apppackage']] = App(start_conf, appium_server_url)
        return AppFactory._apps[start_conf['apppackage']]

    @staticmethod
    def create_app_with_driver(start_conf, appium_server_url):
        """
        创建应用实例并获取其驱动。
        :param appium_server_url:
        :param start_conf: 启动app配置 {"appPackage": "com.appium.android"}。
        :return: (应用实例, 驱动)
        """
        app_instance = AppFactory.create_app(start_conf, appium_server_url)
        driver = app_instance.start()
        return app_instance, driver
