# src/database/db.py
from __future__ import annotations

import os

from database.engine import get_engine, build_db_url
from sqlalchemy.orm import sessionmaker
from database.sql_handler import SQLHandler
from utils.read_conf import read_conf


def _resolve_db_conf() -> dict:
    db_url = os.getenv("DB_URL")
    if db_url:
        return {"url": db_url}

    section = os.getenv("DB_SECTION")
    if section:
        return read_conf.get_dict(section)

    db_host = os.getenv("DB_HOST")
    if db_host:
        return {
            "type": "postgresql",
            "host": db_host,
            "port": os.getenv("DB_PORT", "5432"),
            "user": os.getenv("DB_USER", "tap"),
            "password": os.getenv("DB_PASSWORD", "tap_pass"),
            "database": os.getenv("DB_NAME", "tap"),
        }

    return read_conf.get_dict("postgres_local")


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

    def rollback(self):
        self.session.rollback()

    def close(self):
        self.session.close()




