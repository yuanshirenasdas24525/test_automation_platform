# src/database/db.py
from __future__ import annotations

import os

from database.engine import get_engine, build_db_url
from sqlalchemy.orm import sessionmaker
from database.sql_handler import SQLHandler
from utils.read_conf import read_conf


def _resolve_db_conf() -> dict:
    """解析数据库连接配置。

    纯环境变量驱动（本地开发通过 .env，容器通过 compose env 注入）：
      优先级 1：DB_URL（完整连接串）
      优先级 2：DB_SECTION（可选，从自备 ini 的某节读；默认无此文件）
      优先级 3：DB_HOST 等离散变量拼 postgresql URL
    三者都没有 → 直接报错（fail-closed），不再隐式回落到已删除的 object_conf.ini。
    """
    db_url = os.getenv("DB_URL")
    if db_url:
        return {"url": db_url}

    section = os.getenv("DB_SECTION")
    if section:
        conf = read_conf.get_dict(section)
        if conf:
            return conf
        raise RuntimeError(f"DB_SECTION={section} 未找到对应配置（配置文件不存在或该节为空）")

    db_host = os.getenv("DB_HOST")
    if db_host:
        return {
            "type": "postgresql",
            "host": db_host,
            "port": os.getenv("DB_PORT", "5432"),
            "user": os.getenv("DB_USER", "tap"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "tap"),
        }

    raise RuntimeError(
        "未配置数据库连接：请在 .env / 环境变量中设置 DB_HOST"
        "（及 DB_USER / DB_PASSWORD / DB_NAME），或直接设置 DB_URL。"
    )


class DB:

    def __init__(self, db_conf: dict = None):

        if db_conf is None:
            db_conf = _resolve_db_conf()
        engine = get_engine(db_conf)

        SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine
        )

        self.session = SessionLocal()
        self.sql = SQLHandler(self.session)

    def commit(self):
        self.session.commit()

    def fetchone(self, sql: str, params: dict | None = None):
        return self.sql.fetchone(sql, params)

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.session.close()



