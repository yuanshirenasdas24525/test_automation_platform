# 数据库连接说明

> 平台默认用 SQLite（单文件、零运维、便于本地调试）。生产如果要换 MySQL / PostgreSQL，只改 `config/object_conf.ini` 里的节名，代码无需改。

---

## 1. 当前默认：SQLite 本地文件

### 1.1 文件位置

绝对路径：
```
<项目根>/data/db/sqlite.db
```

对应本机：
```
/Users/Apple/Documents/test_automation_platform/data/db/sqlite.db
```

### 1.2 配置来源

`config/object_conf.ini` 里的 `[sqlite_local]` 节：
```ini
[sqlite_local]
type     = sqlite
database = sqlite.db
path     = /Users/Apple/Documents/test_automation_platform/data/db/sqlite.db
```

启动时 `src/database/db.py` 的 `DB.__init__` 默认就去读这一节；`path` 这个字段就是 SQLAlchemy URL 里的文件路径。

### 1.3 SQLAlchemy 连接串

`src/database/db_config.py` 的 `build_db_url` 把上面的 ini 拼成：
```
sqlite:////Users/Apple/Documents/test_automation_platform/data/db/sqlite.db
```
> 注意：SQLite 的绝对路径在 URL 里是 4 个斜杠 —— `sqlite:///` 是固定前缀，后面再加一个 `/` 才是根路径。

---

## 2. 命令行 / GUI 连接方式

### 2.1 `sqlite3` 命令行（macOS/Linux 自带）

```bash
cd /Users/Apple/Documents/test_automation_platform
sqlite3 data/db/sqlite.db

sqlite> .tables                      # 列所有表
sqlite> .schema test_cases           # 看 test_cases 建表语句
sqlite> .headers on
sqlite> .mode column
sqlite> SELECT id, name, case_type FROM test_cases LIMIT 10;
sqlite> .quit
```

### 2.2 DB Browser for SQLite（免费 GUI，推荐）

下载：<https://sqlitebrowser.org/>

打开后 `File → Open Database → 选 data/db/sqlite.db`。可视化浏览、改数据、出 ER 图。

### 2.3 JetBrains DataGrip / PyCharm 自带 Database 面板

`+ → Data Source → SQLite`：
- File：`<项目根>/data/db/sqlite.db`
- Driver：`Xerial` 或内置 JDBC（首次会提示下载）

### 2.4 VS Code 插件

装 `SQLite Viewer` 或 `SQLite` 插件，`Cmd+Shift+P → SQLite: Open Database` 选文件即可。

---

## 3. Python 程序里连接

### 3.1 直接用项目的 DB 包装（推荐，和平台一致）

```python
from src.database.db import DB
from src.database.models.test_case import TestCase

db = DB()                               # 不传参数就走 sqlite_local
cases = db.session.query(TestCase).limit(5).all()
for c in cases:
    print(c.id, c.name, c.case_type)
db.close()
```

### 3.2 FastAPI 路由里（已有依赖注入）

`src/api/deps.py` 里有 `get_db`，路由直接依赖注入：

```python
from fastapi import Depends
from sqlalchemy.orm import Session
from src.api.deps import get_db

@router.get("/foo")
def handler(db: Session = Depends(get_db)):
    return db.query(...).all()
```

### 3.3 纯 `sqlite3` 快速查（不走 ORM）

```python
import sqlite3
conn = sqlite3.connect("data/db/sqlite.db")
conn.row_factory = sqlite3.Row
for row in conn.execute("SELECT id, name FROM modules WHERE project_id = ?", (50,)):
    print(dict(row))
conn.close()
```

### 3.4 纯 SQLAlchemy（跑一次性脚本）

```python
from sqlalchemy import create_engine, text

eng = create_engine(
    "sqlite:////Users/Apple/Documents/test_automation_platform/data/db/sqlite.db",
    connect_args={"check_same_thread": False},
)
with eng.connect() as conn:
    for row in conn.execute(text("SELECT COUNT(*) FROM test_cases")):
        print(row)
```

---

## 4. 切换到 MySQL / PostgreSQL

### 4.1 MySQL

1. `config/object_conf.ini` 里已经有模板段，直接改：
   ```ini
   [mysql_db]
   type     = mysql
   host     = 10.4.26.13
   port     = 3306
   user     = forex_user
   password = <你的密码>
   database = forex
   ```

2. `src/database/db.py` 里把 `read_conf.get_dict("sqlite_local")` 改成 `read_conf.get_dict("mysql_db")`，或者在 `DB()` 构造时显式传 `DB(db_conf=read_conf.get_dict("mysql_db"))`。

3. 装驱动：
   ```bash
   pip install pymysql cryptography
   ```

4. 先在 MySQL 端把 schema 建起来（用 Alembic）：
   ```bash
   alembic upgrade head
   ```

### 4.2 PostgreSQL

类似上面，但配置段写 `type = postgresql`，驱动装 `psycopg2-binary`。

`db_config.py` 的 `build_db_url` 已经内置 MySQL / PostgreSQL / SQLite 三种拼接。

---

## 5. 迁移 / Schema 变更

### 5.1 首次部署：建空库

```bash
cd <项目根>
alembic upgrade head
```
会自动建出 `test_cases`、`test_steps`、`test_reports`、`test_environments` 等所有表。

### 5.2 从 v1 老库升级到 v2（一次性）

旧库缺 `test_steps` / `test_environments` / `test_variables` / `devices` 四张表，也缺 `test_cases.case_type / priority / env_id / tags / ...` 等新列。访问 `/api/modules/*`、`/api/content/*` 会 500，因为 ORM 读不到这些字段。

修法：跑一次性幂等脚本
```bash
python scripts/migrate_v1_to_v2_sqlite.py            # 默认改 data/db/sqlite.db
# 或指定：
python scripts/migrate_v1_to_v2_sqlite.py /path/to/sqlite.db
```
脚本会：
1. 给 `test_cases` 补 9 个新列（case_type / tags / priority / env_id / pre_hook / post_hook / variables / timeout / retry）
2. 给 `test_step_reports` 补 4 个新列
3. 建 `test_steps / test_environments / test_variables / devices` 四张新表
4. 把 `alembic_version` 写成 `v2_000001`，后续可以继续走 Alembic

脚本是 **幂等** 的 —— 再跑一遍不会报错，已存在的列/表跳过。

### 5.3 日常 schema 改动

改 `src/database/models/*.py`，然后：
```bash
alembic revision --autogenerate -m "add xxx column"
alembic upgrade head
```

---

## 6. 排错 cheatsheet

| 症状 | 原因 | 修法 |
|---|---|---|
| `/api/modules/<id>` 500 报 `no such column: test_cases.case_type` | v1 老库没升 | 跑 `scripts/migrate_v1_to_v2_sqlite.py` |
| `/api/content/<pid>?parent_id=<mid>` 500 | 同上 | 同上 |
| `disk I/O error`（sqlite 从 fuse/虚拟化目录打开） | journal 文件被卡住 | `rm data/db/sqlite.db-journal`（或 `> data/db/sqlite.db-journal` 清零） |
| `database is locked` | 多个进程同时写（celery worker + uvicorn） | 考虑换 MySQL；或单进程起 `--workers 1` |
| 换了 MySQL 后 `ImportError: No module named 'pymysql'` | 没装驱动 | `pip install pymysql cryptography` |
| Alembic `Can't locate revision identified by 'xxx'` | 切换分支后 versions 目录对不上 | `alembic stamp head` 重置，或手动清 `alembic_version` 表 |

---

## 7. 备份 / 恢复

### 备份

```bash
# 热备（即便平台在跑也能拿到一致快照）
sqlite3 data/db/sqlite.db ".backup 'data/db/sqlite.backup.$(date +%Y%m%d).db'"

# 或直接 cp（必须先停服务，否则可能拷到写到一半的状态）
cp data/db/sqlite.db data/db/sqlite.backup.$(date +%Y%m%d).db
```

### 恢复

```bash
# 先停服务
cp data/db/sqlite.backup.YYYYMMDD.db data/db/sqlite.db
# 清可能残留的 journal
rm -f data/db/sqlite.db-journal data/db/sqlite.db-wal data/db/sqlite.db-shm
```

### 导出 SQL

```bash
sqlite3 data/db/sqlite.db .dump > dump.sql
```

---

## 8. 连接串速查表

| 场景 | 连接串 |
|---|---|
| SQLite 本地（默认） | `sqlite:////Users/Apple/Documents/test_automation_platform/data/db/sqlite.db` |
| SQLite 相对路径 | `sqlite:///data/db/sqlite.db` |
| MySQL | `mysql+pymysql://user:pass@host:3306/dbname?charset=utf8mb4` |
| PostgreSQL | `postgresql+psycopg2://user:pass@host:5432/dbname` |

粘到 DataGrip / DBeaver / SQLAlchemy 里都能直接用。
