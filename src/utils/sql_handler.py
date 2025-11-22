import sqlite3
import pymysql
from typing import Any, List, Optional, Dict, Union
from src.utils.logger import LOGGER, ERROR_LOGGER


class BaseSQLHandler:
    """SQL Handler 抽象基类"""
    def execute_query(self, sql: str, params: Optional[tuple] = None) -> List[tuple]:
        raise NotImplementedError

    def fetchone(self, sql: str) -> Any:
        raise NotImplementedError

    def fetchall(self, sql: str) -> List[Any]:
        raise NotImplementedError

    def close(self):
        raise NotImplementedError


class SQLiteHandler(BaseSQLHandler):
    """SQLite 数据库处理"""
    def __init__(self, db_path: str):
        try:
            LOGGER.info(f"连接 SQLite 数据库: {db_path}")
            self.conn = sqlite3.connect(db_path, timeout=5)
            LOGGER.info("SQLite 连接成功")
        except Exception as e:
            ERROR_LOGGER.error(f"连接 SQLite 数据库失败: {e}")
            raise

    def execute_query(self, sql: str, params: Optional[tuple] = None) -> List[tuple]:
        try:
            cursor = self.conn.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            results = cursor.fetchall()
            LOGGER.debug(f"SQLite 执行 SQL 成功: {sql}, 返回 {len(results)} 条数据")
            return results
        except Exception as e:
            ERROR_LOGGER.error(f"SQLite 执行 SQL 失败: {e} | SQL: {sql}")
            return []

    def fetchone(self, sql: str) -> Any:
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            result = cursor.fetchone()
            LOGGER.debug(f"SQLite fetchone 成功: {sql}, 结果: {result}")
            return result
        except Exception as e:
            ERROR_LOGGER.error(f"SQLite fetchone 失败: {e} | SQL: {sql}")
            return None

    def fetchall(self, sql: str) -> List[Any]:
        return self.execute_query(sql)

    def close(self):
        if self.conn:
            self.conn.close()
            LOGGER.info("SQLite 连接已关闭")


class MySQLHandler(BaseSQLHandler):
    """MySQL 数据库处理"""
    def __init__(self, host: str, port: int, user: str, password: str, db: str):
        try:
            LOGGER.info(f"连接 MySQL 数据库: {host}:{port}/{db}")
            self.conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=db,
                charset="utf8mb4",
                connect_timeout=5
            )
            LOGGER.info("MySQL 连接成功")
        except pymysql.MySQLError as e:
            ERROR_LOGGER.error(f"MySQL 连接失败: {e}")
            raise

    def execute_query(self, sql: str, params: Optional[tuple] = None) -> Union[tuple[tuple[Any, ...], ...], list[Any]]:
        try:
            cursor = self.conn.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            results = cursor.fetchall()
            LOGGER.debug(f"MySQL 执行 SQL 成功: {sql}, 返回 {len(results)} 条数据")
            return results
        except Exception as e:
            ERROR_LOGGER.error(f"MySQL 执行 SQL 失败: {e} | SQL: {sql}")
            return []

    def fetchone(self, sql: str) -> Any:
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            result = cursor.fetchone()
            LOGGER.debug(f"MySQL fetchone 成功: {sql}, 结果: {result}")
            return result
        except Exception as e:
            ERROR_LOGGER.error(f"MySQL fetchone 失败: {e} | SQL: {sql}")
            return None

    def fetchall(self, sql: str) -> List[Any]:
        return self.execute_query(sql)

    def close(self):
        if self.conn:
            self.conn.close()
            LOGGER.info("MySQL 连接已关闭")


class SQLHandlerFactory:
    """工厂类：根据配置创建不同类型的数据库连接"""
    @staticmethod
    def create(db_conf: Dict[str, Any]) -> BaseSQLHandler:
        db_type = db_conf.get("type", "").lower()
        if db_type == "sqlite":
            return SQLiteHandler(db_conf["path"])
        elif db_type == "mysql":
            return MySQLHandler(
                host=db_conf["host"],
                port=int(db_conf["port"]),
                user=db_conf["user"],
                password=db_conf["password"],
                db=db_conf["database"]
            )
        else:
            raise ValueError(f"不支持的数据库类型: {db_type}")

if __name__ == '__main__':
    from src.utils.read_file import read_conf


    # 从配置文件读取数据库信息
    mysql_conf = read_conf.get_dict("db")
    sqlite_conf = read_conf.get_dict("sqlite_local")

    # MySQL 测试
    mysql_db = SQLHandlerFactory.create(mysql_conf)
    print(mysql_db.fetchone("SELECT * FROM t_user_info WHERE id = 1000;"))
    mysql_db.close()

    # # SQLite 测试
    # sqlite_db = SQLHandlerFactory.create(sqlite_conf)
    # print(sqlite_db.fetchall("select * from users;"))
    # sqlite_db.close()