# src/database/config.py

def build_db_url(db_conf: dict) -> str:
    db_type = db_conf.get("type", "").lower()

    if "url" in db_conf:
        return db_conf["url"]

    if db_type == "mysql":
        return f"mysql+pymysql://{db_conf['user']}:{db_conf['password']}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}?charset=utf8mb4"

    elif db_type == "postgresql":
        return f"postgresql+psycopg2://{db_conf['user']}:{db_conf['password']}@{db_conf['host']}:{db_conf['port']}/{db_conf['database']}"

    elif db_type == "sqlite":
        return f"sqlite:///{db_conf.get('path', 'test.db')}"

    else:
        raise ValueError(f"不支持的数据库类型: {db_type}")