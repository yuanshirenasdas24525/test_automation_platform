# -*- coding:utf-8 -*-
# Create on
import threading
from src.core.mobile.environment_checker import EnvironmentChecker
from src.core.mobile.appium_start_setting import AppFactory
from src.core.mobile.app_action import AppAction
from src.utils.read_file import read_conf
from src.utils.sql_handler import SQLHandlerFactory
from src.utils.logger import LOGGER, ERROR_LOGGER



class AppInitializer:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AppInitializer, cls).__new__(cls)
                cls._instance.app_managers = {}
                cls._instance.apps = {}
                cls._instance.initialized = set()
        return cls._instance

    def initialize_app(self, index: int):
        with self._lock:
            if index not in self.initialized:
                LOGGER.info(f"开始初始化应用 {index}...")
                all_conf = read_conf()
                config_list = list(all_conf.values())
                if index < len(config_list):
                    config = config_list[index]
                    app_package = config['start_confing']['appPackage']
                    try:
                        LOGGER.info(f"正在初始化应用 {app_package}...")
                        app_manager = AppManager()
                        self.app_managers[index] = app_manager
                        self.apps[index] = app_manager.get_app()
                        self.initialized.add(index)
                        LOGGER.info(f"应用程序 {app_package} 已成功初始化")
                    except Exception as e:
                        ERROR_LOGGER.error(f"设置测试环境失败 ({app_package}): {e}")
                        raise
                else:
                    raise IndexError(f"配置索引 {index} 超出范围，总配置数: {len(config_list)}")
            else:
                LOGGER.info(f"应用 {index} 已经初始化，直接返回")
        return self.apps[index]

    def close_app(self, index):
        with self._lock:
            if index in self.app_managers:
                try:
                    self.app_managers[index].close_all_apps()
                    LOGGER.info(f"应用程序 {index} 已关闭")
                except Exception as e:
                    ERROR_LOGGER.error(f"关闭应用程序 {index} 时出错: {e}")
                del self.app_managers[index]
                del self.apps[index]
                self.initialized.remove(index)


app_initializer = AppInitializer()


def initialize_app(index):
    return app_initializer.initialize_app(index)


def close_app(index):
    app_initializer.close_app(index)


class AppManager:
    def __init__(self):
        self.apps = {}
        self.db_connections = {}
        self.appium_config = read_conf.get_dict("appium_config")
        self.start_conf = read_conf.get_dict("app_start_config")
        self.db_conf = read_conf.get_dict("mysql_db")
        self.identifier = self.start_conf.get("appPackage", "iOS")

    def get_app(self):
        if not EnvironmentChecker(self.appium_config,self.start_conf).check_environment_and_device():
            LOGGER.warning(f"Appium 环境自检异常")
        if self.identifier not in self.apps:
            app_instance, driver = AppFactory.create_app_with_driver(
                self.start_conf, self.appium_config.get("appium_service")
            )
            db_connection = None
            # 如果 db_conf 不是 None 且是字典，则尝试连接数据库
            if self.db_conf and isinstance(self.db_conf, dict):
                if self.identifier not in self.db_connections:
                    self.db_connections[self.identifier] = SQLHandlerFactory.create(self.db_conf)
                db_connection = self.db_connections[self.identifier]
            # 创建 AppAction 实例时传递数据库连接（如果有）
            self.apps[self.identifier] = AppAction(driver, db_connection)
            LOGGER.info(f"创建并初始化了新的AppAction实例: {self.identifier}")
        return self.apps[self.identifier]

    def close_all_apps(self):
        for app in self.apps.values():
            app.close()
        self.apps.clear()
        for conn in self.db_connections.values():
            conn.close()
        self.db_connections.clear()
        LOGGER.info("所有应用和数据库连接已关闭")