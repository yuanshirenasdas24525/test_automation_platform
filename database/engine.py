"""SQLAlchemy 引擎工厂 + PostgreSQL DB URL 构造器。"""

from sqlalchemy import create_engine

# ----------------------------------------------------------------------
# DB URL 构造器：平台元数据库只支持 PostgreSQL
# ----------------------------------------------------------------------
def build_db_url(db_conf: dict) -> str:
    db_type = (db_conf.get("type") or "").lower()

    if "url" in db_conf:
        url = str(db_conf["url"])
        if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
            raise ValueError("平台元数据库只支持 PostgreSQL URL")
        return url

    if db_type == "postgresql":
        return (
            f"postgresql+psycopg2://{db_conf['user']}:{db_conf['password']}"
            f"@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}"
        )

    raise ValueError(f"平台元数据库只支持 PostgreSQL，不支持: {db_type or '<empty>'}")


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
    )
    _engine_cache[key] = engine
    return engine
