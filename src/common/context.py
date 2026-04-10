# -*- coding:utf-8 -*-
from src.database.db_manager import DBManager
from src.utils.reload_config import config_center
from src.utils.logger import LOGGER

class AppContext:
    """全局上下文指挥中心"""
    def __init__(self):
        # 1. 内部持有数据库处理器 (单例模式)
        try:
            self.db = DBManager().get_db()
            LOGGER.info("AppContext: 数据库处理器初始化成功")
        except Exception as e:
            LOGGER.error(f"AppContext: 数据库初始化失败: {e}")
            raise

        # 2. 内部持有配置中心
        self.config = config_center

        # 3. 默认预热 (加载 API 相关的数据库配置)
        self.warm_up(category="api")

    def warm_up(self, category: str = "api"):
        """
        环境预热/切换：
        当你想从 API 自动化切换到 Web 自动化配置时，只需调用此方法
        """
        LOGGER.info(f"正在切换业务分类至: {category}")
        self.config.reload(db=self.db, category=category)
        LOGGER.info(f"配置重载完成，当前分类: {category}")

# 导出唯一的全局上下文实例
ctx = AppContext()