"""SQLAlchemy 引擎工厂 + DB URL 构造器。

合并自老的 `db_config.py`（build_db_url）+ `engine.py`（get_engine）。
另外原 `db_engine.py`（带 SQLAlchemyHandler 类）整个文件没人用，已删。
"""
from pathlib import Path

from sqlalchemy import create_engine

# object_conf.ini 里 sqlite path 写的是项目根相对路径（"data/db/sqlite.db"）。
# uvicorn / celery / pytest 各自 cwd 可能不一样（特别是从 server/ 起 main.py 时），
# 不锚一下就会去 cwd 下找文件 → "unable to open database file"。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# DB URL 构造器：把 dict 配置 → SQLAlchemy URL 字符串
# 支持 mysql / postgresql / sqlite，外加直接给 url 字段（最高优先级）
# ----------------------------------------------------------------------
def build_db_url(db_conf: dict) -> str:
    db_type = (db_conf.get("type") or "").lower()

    # 1) 直接给完整 URL：最高优先级，省得再拼
    if "url" in db_conf:
        return db_conf["url"]

    # 2) 拆字段 → 拼 URL
    if db_type == "mysql":
        return (
            f"mysql+pymysql://{db_conf['user']}:{db_conf['password']}"
            f"@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}?charset=utf8mb4"
        )

    if db_type == "postgresql":
        return (
            f"postgresql+psycopg2://{db_conf['user']}:{db_conf['password']}"
            f"@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}"
        )

    if db_type == "sqlite":
        raw = db_conf.get("path", "test.db")
        p = Path(raw)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return f"sqlite:///{p}"

    raise ValueError(f"不支持的数据库类型: {db_type}")


# ----------------------------------------------------------------------
# 引擎工厂：按 db_conf 缓存，重复同样的配置只建一次 engine
# ----------------------------------------------------------------------
_engine_cache: dict = {}


def get_engine(db_conf: dict):
    key = str(db_conf)
    if key in _engine_cache:
        return _engine_cache[key]

    url = build_db_url(db_conf)
    engine = create_engine(
        url,
        pool_pre_ping=True,
        # SQLite 需要 check_same_thread=False 给 web/celery 多线程用
        connect_args=(
            {"check_same_thread": False, "timeout": 30}
            if "sqlite" in url
            else {}
        ),
    )
    # ---- SQLite: PRAGMA foreign_keys = ON ----
    if "sqlite" in url:
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: BLE001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()
    _engine_cache[key] = engine
    return engine
