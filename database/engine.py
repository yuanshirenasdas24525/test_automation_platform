# src/database/engine.py
from sqlalchemy import create_engine
from database.db_config import build_db_url

_engine_cache = {}


def get_engine(db_conf: dict):
    key = str(db_conf)

    if key in _engine_cache:
        return _engine_cache[key]

    url = build_db_url(db_conf)

    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "check_same_thread": False,
            "timeout": 30
        } if "sqlite" in url else {}
    )

    _engine_cache[key] = engine
    return engine