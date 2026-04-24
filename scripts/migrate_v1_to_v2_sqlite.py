"""
把 v1 的 SQLite 库升到 v2（和 Alembic `v2_000001` 等价但独立，适合一次性补丁）。

为什么不直接跑 Alembic？
  - Alembic 需要 alembic_version 表；老库里没有，跑 `upgrade head` 会从 revision=None 开始，
    遇到 batch_alter_table 时如果列已存在就会报错。这里走幂等 SQL 更直观、风险更小。

用法：
    python scripts/migrate_v1_to_v2_sqlite.py [db_path]

不给 db_path 就走 config/object_conf.ini 里 [sqlite_local].path。
执行完会把 alembic_version 写成 'v2_000001'，后续再用 Alembic 从这条记录继续也没问题。

⚠️ 这里的 DDL 必须和 src/database/models/* 的字段一一对应；
   和 Alembic 迁移 `20260419_0001_v2_add_test_steps_and_env.py` 保持完全一致。
   如果 DDL 对不上，ORM join / eager-load 会报 'no such column' 500。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def resolve_db_path(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1]).expanduser().resolve()

    # 尝试读 config/object_conf.ini 的 sqlite_local.path
    import configparser

    ini_path = Path(__file__).resolve().parent.parent / "config" / "object_conf.ini"
    cp = configparser.ConfigParser()
    cp.read(ini_path, encoding="utf-8")
    if cp.has_section("sqlite_local") and cp.has_option("sqlite_local", "path"):
        return Path(cp.get("sqlite_local", "path")).expanduser().resolve()

    # 兜底：项目相对路径
    return (Path(__file__).resolve().parent.parent / "data" / "db" / "sqlite.db").resolve()


def column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def get_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def add_column_if_missing(cur: sqlite3.Cursor, table: str, column: str, ddl: str) -> bool:
    if column_exists(cur, table, column):
        print(f"  = {table}.{column} 已存在")
        return False
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    print(f"  + {table}.{column} 已添加")
    return True


def create_table_if_missing(cur: sqlite3.Cursor, table: str, ddl: str) -> bool:
    if table_exists(cur, table):
        print(f"  = 表 {table} 已存在")
        return False
    cur.execute(ddl)
    print(f"  + 表 {table} 已创建")
    return True


def recreate_table_if_schema_wrong(
    cur: sqlite3.Cursor, table: str, required_columns: set[str], ddl: str
) -> bool:
    """如果表已存在但字段集缺少必需列（= 之前用错误的 DDL 建过），drop 再重建。"""
    if not table_exists(cur, table):
        cur.execute(ddl)
        print(f"  + 表 {table} 已创建")
        return True

    existing = get_columns(cur, table)
    missing = required_columns - existing
    if not missing:
        print(f"  = 表 {table} schema 正确")
        return False

    # 旧 DDL 错了，drop 重建（只对空表这么干；非空时留给人手动处理避免丢数据）
    cnt = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if cnt > 0:
        print(
            f"  ! 表 {table} schema 错误但有 {cnt} 行数据，不敢自动 drop。"
            f"缺字段：{sorted(missing)}；请手动备份后 drop 再重跑。"
        )
        return False

    cur.execute(f"DROP TABLE {table}")
    cur.execute(ddl)
    print(f"  * 表 {table} 重建（schema 错误 → 修正）")
    return True


# ---------------------------------------------------------------------------
# DDL 集合 —— 跟 v2_000001 迁移一一对应，和 src/database/models/* 字段严格对齐
# ---------------------------------------------------------------------------
NEW_TEST_CASE_COLUMNS = [
    ("case_type", "case_type TEXT DEFAULT 'api'"),
    ("tags", "tags TEXT"),  # JSONType 在 SQLite 实际就是 TEXT
    ("priority", "priority INTEGER DEFAULT 2"),
    ("env_id", "env_id INTEGER"),
    ("pre_hook", "pre_hook TEXT"),
    ("post_hook", "post_hook TEXT"),
    ("variables", "variables TEXT"),
    ("timeout", "timeout INTEGER DEFAULT 60"),
    ("retry", "retry INTEGER DEFAULT 0"),
]

NEW_STEP_REPORT_COLUMNS = [
    ("case_execution_id", "case_execution_id INTEGER"),
    ("step_id", "step_id INTEGER"),
    ("step_type", "step_type TEXT"),
    ("attachments", "attachments TEXT"),
]

# -----------------------------------------------------------------------
# test_steps —— 对应 src/database/models/test_step.py
# -----------------------------------------------------------------------
DDL_TEST_STEPS = """
CREATE TABLE test_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    step_order INTEGER NOT NULL DEFAULT 0,
    step_name VARCHAR(255) NOT NULL,
    step_type VARCHAR(50) NOT NULL,
    skip BOOLEAN NOT NULL DEFAULT 0,
    config TEXT NOT NULL,
    extract TEXT,
    assertion TEXT,
    wait_before FLOAT DEFAULT 0,
    timeout INTEGER DEFAULT 30,
    retry INTEGER DEFAULT 0,
    on_failure VARCHAR(20) DEFAULT 'stop',
    FOREIGN KEY (case_id) REFERENCES test_cases(id) ON DELETE CASCADE
)
"""
TEST_STEPS_REQUIRED_COLS = {
    "id", "case_id", "step_order", "step_name", "step_type", "skip",
    "config", "extract", "assertion", "wait_before", "timeout",
    "retry", "on_failure",
}
TEST_STEPS_INDEXES = [
    ("ix_test_steps_case_id", "CREATE INDEX IF NOT EXISTS ix_test_steps_case_id ON test_steps(case_id)"),
    ("ix_test_steps_step_type", "CREATE INDEX IF NOT EXISTS ix_test_steps_step_type ON test_steps(step_type)"),
]

# -----------------------------------------------------------------------
# test_environments —— 对应 src/database/models/test_environment.py
# -----------------------------------------------------------------------
DDL_TEST_ENVIRONMENTS = """
CREATE TABLE test_environments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name VARCHAR(64) NOT NULL,
    category VARCHAR(20),
    description VARCHAR(255),
    host VARCHAR(255),
    device_pool VARCHAR(64),
    browser_config TEXT,
    variables TEXT,
    secrets TEXT,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
)
"""
TEST_ENV_REQUIRED_COLS = {
    "id", "project_id", "name", "category", "description", "host",
    "device_pool", "browser_config", "variables", "secrets",
    "create_time", "update_time",
}

# -----------------------------------------------------------------------
# test_variables —— 对应 src/database/models/test_variable.py
# -----------------------------------------------------------------------
DDL_TEST_VARIABLES = """
CREATE TABLE test_variables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope VARCHAR(20) NOT NULL,
    scope_id INTEGER,
    key VARCHAR(128) NOT NULL,
    value TEXT,
    secret BOOLEAN NOT NULL DEFAULT 0,
    description VARCHAR(255),
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_variable_scope_key UNIQUE (scope, scope_id, key)
)
"""
TEST_VAR_REQUIRED_COLS = {
    "id", "scope", "scope_id", "key", "value", "secret", "description",
    "create_time", "update_time",
}
TEST_VAR_INDEXES = [
    ("ix_test_variables_scope", "CREATE INDEX IF NOT EXISTS ix_test_variables_scope ON test_variables(scope)"),
    ("ix_test_variables_scope_id", "CREATE INDEX IF NOT EXISTS ix_test_variables_scope_id ON test_variables(scope_id)"),
]

# -----------------------------------------------------------------------
# devices —— 对应 src/database/models/device.py
# -----------------------------------------------------------------------
DDL_DEVICES = """
CREATE TABLE devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    udid VARCHAR(128) NOT NULL UNIQUE,
    platform VARCHAR(20) NOT NULL,
    platform_version VARCHAR(32),
    device_name VARCHAR(128),
    brand VARCHAR(64),
    model VARCHAR(128),
    agent_host VARCHAR(128),
    agent_port INTEGER,
    appium_port INTEGER,
    pool VARCHAR(64) DEFAULT 'default',
    status VARCHAR(20) DEFAULT 'offline',
    owner_execution_id INTEGER,
    capabilities TEXT,
    tags TEXT,
    last_heartbeat DATETIME,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
DEVICES_REQUIRED_COLS = {
    "id", "udid", "platform", "platform_version", "device_name", "brand",
    "model", "agent_host", "agent_port", "appium_port", "pool", "status",
    "owner_execution_id", "capabilities", "tags", "last_heartbeat",
    "create_time", "update_time",
}
DEVICES_INDEXES = [
    ("ix_devices_udid", "CREATE INDEX IF NOT EXISTS ix_devices_udid ON devices(udid)"),
    ("ix_devices_pool", "CREATE INDEX IF NOT EXISTS ix_devices_pool ON devices(pool)"),
    ("ix_devices_status", "CREATE INDEX IF NOT EXISTS ix_devices_status ON devices(status)"),
    ("ix_devices_agent_host", "CREATE INDEX IF NOT EXISTS ix_devices_agent_host ON devices(agent_host)"),
    ("ix_devices_owner_execution_id", "CREATE INDEX IF NOT EXISTS ix_devices_owner_execution_id ON devices(owner_execution_id)"),
]


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"[!] 找不到数据库：{db_path}")
        sys.exit(1)

    print(f"[*] 目标数据库：{db_path}")
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()
    changes = 0

    print("\n[1/6] 扩 test_cases：")
    for name, ddl in NEW_TEST_CASE_COLUMNS:
        if add_column_if_missing(cur, "test_cases", name, ddl):
            changes += 1
    # case_type 上补个索引
    cur.execute("CREATE INDEX IF NOT EXISTS ix_test_cases_case_type ON test_cases(case_type)")

    print("\n[2/6] 扩 test_step_reports：")
    for name, ddl in NEW_STEP_REPORT_COLUMNS:
        if add_column_if_missing(cur, "test_step_reports", name, ddl):
            changes += 1
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_step_reports_case_execution_id "
        "ON test_step_reports(case_execution_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_test_step_reports_step_id "
        "ON test_step_reports(step_id)"
    )

    print("\n[3/6] 建/校验 test_environments：")
    if recreate_table_if_schema_wrong(
        cur, "test_environments", TEST_ENV_REQUIRED_COLS, DDL_TEST_ENVIRONMENTS
    ):
        changes += 1

    print("\n[4/6] 建/校验 test_steps：")
    if recreate_table_if_schema_wrong(
        cur, "test_steps", TEST_STEPS_REQUIRED_COLS, DDL_TEST_STEPS
    ):
        changes += 1
    for _, idx_sql in TEST_STEPS_INDEXES:
        cur.execute(idx_sql)

    print("\n[5/6] 建/校验 test_variables + devices：")
    if recreate_table_if_schema_wrong(
        cur, "test_variables", TEST_VAR_REQUIRED_COLS, DDL_TEST_VARIABLES
    ):
        changes += 1
    for _, idx_sql in TEST_VAR_INDEXES:
        cur.execute(idx_sql)

    if recreate_table_if_schema_wrong(
        cur, "devices", DEVICES_REQUIRED_COLS, DDL_DEVICES
    ):
        changes += 1
    for _, idx_sql in DEVICES_INDEXES:
        cur.execute(idx_sql)

    print("\n[6/6] 更新 alembic_version（方便以后接回 Alembic 管理）：")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS alembic_version ("
        "version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    cur.execute("DELETE FROM alembic_version")
    cur.execute("INSERT INTO alembic_version (version_num) VALUES (?)", ("v2_000001",))

    con.commit()
    con.close()

    print(f"\n[✓] 迁移完成 · 改动 {changes} 处")
    print("    现在再访问 /api/modules/* /api/content/* 就不会 500 了。")


if __name__ == "__main__":
    migrate(resolve_db_path(sys.argv))
