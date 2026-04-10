from src.utils.logger import LOGGER
from typing import Any, Dict, Union, Optional


class ConfigCenter:
    _instance = None
    _cache: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self._configs = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def reload(self, db, category=None):
        """同步数据库数据到内存缓存"""
        if category:
            # 只查询特定分类的配置
            sql = "SELECT config_group, config_key, config_value FROM config_store WHERE category = :cat"
            params = {"cat": category}
            # 如果是局部刷新，我们可能只想覆盖或仅保留该分类的数据
            # 这里建议先清空旧的分类数据，或者根据需求决定是否保留全量
            LOGGER.info(f"正在精准重载 [{category}] 业务配置...")
        else:
            sql = "SELECT config_group, config_key, config_value FROM config_store"
            params = {}
            self._configs = {}  # 全量重载时才清空所有内容
            LOGGER.info("正在执行全量配置重载...")

        rows = db.execute_query(sql, params)

        # 将查询结果映射到内存字典中
        for row in rows:
            group = row['config_group']
            key = row['config_key']
            val = row['config_value']

            if group not in self._configs:
                self._configs[group] = {}
            self._configs[group][key] = val

    def get(self, group: str, key: Optional[str] = None, default: Any = None) -> Union[Any, Dict[str, Any]]:
        """
        获取配置：
        1. 如果传了 key，返回具体的配置值。
        2. 如果 key 为 None，返回整个 group 的字典内容。
        3. 如果 group 不存在，返回空字典或 default。
        """
        # 获取整个 group 的内容，如果不存在则返回空字典
        group_config = self._configs.get(group, {})

        # 如果没有指定具体的 key，则返回整个组的配置
        if key is None:
            return group_config if group_config else (default if default is not None else {})

        # 返回具体的 key 值，不存在则返回默认值
        return group_config.get(key, default)

config_center = ConfigCenter()

if __name__ == '__main__':

    from src.database.db_engine import SQLHandlerFactory
    from config.settings import ProjectPaths
    sqlite_conf = {
        "type": "sqlite",
        "path": ProjectPaths.SQLITE_DB_PATH
    }
    db_handler = SQLHandlerFactory.create(sqlite_conf)
    # 3. 传入实例进行初始化加载
    config_center.reload(db_handler)
    print(config_center.get("redis"))
