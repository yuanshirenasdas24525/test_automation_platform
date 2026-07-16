"""
Alembic 运行环境。

设计要点：
1. DB URL 不在 alembic.ini 里硬编码，而是在这里从**环境变量**动态组装
   （与 database/db.py 同源，本地走 .env，容器走 compose env），保证 Alembic
   和业务代码共用同一个数据库。
2. 通过环境变量 ALEMBIC_DB_URL 或 DB_HOST 等切换数据源：
      ALEMBIC_DB_URL=postgresql+psycopg2://user:pwd@host:5432/db alembic upgrade head
      DB_HOST=127.0.0.1 DB_USER=tap DB_PASSWORD=... alembic upgrade head
3. 导入所有 model 让 autogenerate 能扫到（from src.database.models import *）。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------- 把项目根目录塞进 sys.path，保证能 import database.xxx ----------
# 现在布局是 <root>/database/migrations/env.py，所以 parents[2] 就是项目根。
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------- 加载所有 model，供 autogenerate 扫描 ----------
# 这一行极为重要，少了就 autogenerate 不出新表。
from database import models  # noqa: F401
from database.base import Base

# ---------- 组装 DB URL（纯环境变量驱动）----------
def _resolve_db_url() -> str:
    from database.engine import build_db_url

    # 优先级 1：环境变量 ALEMBIC_DB_URL（完整连接串）
    env_url = os.getenv("ALEMBIC_DB_URL")
    if env_url:
        return env_url

    # 优先级 2：DB_HOST 等离散变量（本地 .env / 容器 compose 注入）→ 拼 postgresql URL
    db_host = os.getenv("DB_HOST")
    if db_host:
        return build_db_url({
            "type": "postgresql",
            "host": db_host,
            "port": os.getenv("DB_PORT", "5432"),
            "user": os.getenv("DB_USER", "tap"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", "tap"),
        })

    # 优先级 3（可选）：ALEMBIC_DB_SECTION 指向自备 ini 的某节；默认无此文件。
    section = os.getenv("ALEMBIC_DB_SECTION")
    if section:
        from utils.read_conf import read_conf

        db_conf = read_conf.get_dict(section)
        if db_conf:
            return build_db_url(db_conf)

    raise RuntimeError(
        "无法解析数据库 URL：请设置 ALEMBIC_DB_URL，或 DB_HOST 等环境变量（见 .env）。"
    )


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 把动态解析到的 URL 注入到 config，后续 engine_from_config 会用它
config.set_main_option("sqlalchemy.url", _resolve_db_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # 允许对 Enum / JSON 类型做兼容比较
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连上数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
