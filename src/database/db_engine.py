# -*- coding:utf-8 -*-
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from typing import Any, List, Optional, Dict, Union
from src.utils.logger import LOGGER


class BaseSQLHandler:
    """SQL Handler 抽象基类"""
    def execute_query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """执行查询并返回字典列表"""
        raise NotImplementedError

    def execute_db(self, sql: str, params: Optional[Dict[str, Any]] = None):
        """执行增删改操作"""
        raise NotImplementedError

    def close(self):
        """关闭连接"""
        raise NotImplementedError


class SQLAlchemyHandler(BaseSQLHandler):
    """
    基于 SQLAlchemy 的通用数据库处理类
    兼容：MySQL, SQLite, PostgreSQL 等
    """
    def __init__(self, db_url: str):
        try:
            LOGGER.info(f"正在通过 SQLAlchemy 初始化数据库连接: {db_url}")
            # pool_pre_ping=True 解决 MySQL 经典的 8 小时断连问题
            self.engine = create_engine(db_url, pool_pre_ping=True, echo=False)
            self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
            LOGGER.info("数据库引擎创建成功")
        except Exception as e:
            LOGGER.error(f"数据库引擎创建失败: {e}")
            raise

    def execute_query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """执行查询，返回结果集字典列表"""
        with self.SessionLocal() as session:
            try:
                # 使用 text() 包装 SQL 以支持 :variable 命名占位符
                result = session.execute(text(sql), params or {})
                # _mapping 将结果行转换为字典，方便 key 访问
                return [dict(row._mapping) for row in result]
            except Exception as e:
                LOGGER.error(f"SQL查询执行失败: {e} | SQL: {sql}")
                return []

    def execute_db(self, sql: str, params: Optional[Dict[str, Any]] = None):
        """执行 Insert/Update/Delete 操作"""
        with self.SessionLocal() as session:
            try:
                session.execute(text(sql), params or {})
                session.commit()
                LOGGER.debug(f"SQL执行成功: {sql}")
            except Exception as e:
                session.rollback()
                LOGGER.error(f"SQL执行事务回滚: {e} | SQL: {sql}")
                raise e

    def close(self):
        """SQLAlchemy 通过连接池管理，通常不需要手动关闭 engine"""
        pass


class SQLHandlerFactory:
    """工厂类：根据配置动态生成处理器"""

    @staticmethod
    def create(db_conf: Dict[str, Any]) -> BaseSQLHandler:
        db_type = db_conf.get("type", "").lower()

        # 1. 如果配置中直接给出了完整的 URL (推荐)
        if "url" in db_conf:
            return SQLAlchemyHandler(db_conf["url"])

        # 2. 如果是旧版拆分配置，自动拼装 URL
        if db_type == "mysql":
            url = f"mysql+pymysql://{db_conf['user']}:{db_conf['password']}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}?charset=utf8mb4"
            return SQLAlchemyHandler(url)
        elif db_type == "sqlite":
            # 兼容绝对路径和相对路径
            path = db_conf.get("path", "test.db")
            url = f"sqlite:///{path}"
            return SQLAlchemyHandler(url)
        else:
            raise ValueError(f"不支持的数据库类型: {db_type}")

# =========================================================
# 使用示例
# =========================================================
