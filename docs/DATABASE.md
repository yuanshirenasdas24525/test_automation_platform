# 数据库连接说明

> 平台统一使用 **PostgreSQL**。Docker Compose 默认启动 postgres:15 容器，配置在 `docker-compose.yaml` 中。
> SQLite 已废弃，不再维护兼容路径。

---

## 1. 当前标准：PostgreSQL

### 1.1 Docker 环境

`docker-compose.yaml` 中已配置：

```yaml
environment:
  DB_HOST: postgres
  DB_PORT: "5432"
  DB_USER: tap
  DB_PASSWORD: tap_pass
  DB_NAME: tap
```

### 1.2 本地直连

`config/object_conf.ini` 里的 `[postgres_local]` 节：

```ini
[postgres_local]
type     = postgresql
host     = 127.0.0.1
port     = 5432
user     = tap
password = tap_pass
database = tap
```

### 1.3 SQLAlchemy 连接串

```
postgresql+psycopg2://tap:tap_pass@127.0.0.1:5432/tap
```

---

## 2. 命令行 / GUI 连接方式

### 2.1 `psql` 命令行

```bash
docker exec -it tap_postgres psql -U tap
# 或本地
psql -h 127.0.0.1 -U tap -d tap
```

### 2.2 JetBrains DataGrip / PyCharm 自带 Database 面板

`+ → Data Source → PostgreSQL`：
- Host: `127.0.0.1`
- Port: `5432`
- User: `tap`
- Password: `tap_pass`
- Database: `tap`

---

## 3. Python 程序里连接

### 3.1 直接用项目的 DB 包装（推荐）

```python
from database.db import DB

db = DB()                               # 不传参数走 postgres_local 或环境变量
db.session.execute(...)
db.close()
```

### 3.2 FastAPI 路由里（已有依赖注入）

```python
from server.api.deps import DBDep

@router.get("/foo")
def handler(db: DBDep):
    return ...
```

### 3.3 纯 SQLAlchemy（跑一次性脚本）

```python
from sqlalchemy import create_engine, text

eng = create_engine("postgresql+psycopg2://tap:tap_pass@127.0.0.1:5432/tap")
with eng.connect() as conn:
    for row in conn.execute(text("SELECT COUNT(*) FROM test_cases")):
        print(row)
```

---

## 4. 迁移 / Schema 变更

### 4.1 首次部署

```bash
alembic upgrade head
```

### 4.2 日常 schema 改动

```bash
# 1. 改 database/models/*.py
# 2. 生成迁移
alembic revision --autogenerate -m "add xxx column"
# 3. review 迁移文件（autogenerate 经常漏 server_default / index）
# 4. 应用
alembic upgrade head
```

---

## 5. 备份 / 恢复（PostgreSQL）

```bash
# 备份
pg_dump -U tap -h 127.0.0.1 -Fc tap > backup_$(date +%Y%m%d).dump

# 恢复
pg_restore -U tap -h 127.0.0.1 -d tap backup_YYYYMMDD.dump
```

---

## 6. 连接串速查表

| 场景 | 连接串 |
|---|---|
| PostgreSQL 本地 | `postgresql+psycopg2://tap:tap_pass@127.0.0.1:5432/tap` |
| PostgreSQL Docker | `postgresql+psycopg2://tap:tap_pass@postgres:5432/tap` |
| ~~SQLite（已废弃）~~ | ~~`sqlite:///data/db/sqlite.db`~~ |

---

## 7. 从 SQLite 迁移到 PostgreSQL

如需把旧 SQLite 数据迁到 PG，可使用 `pgloader` 或导出 CSV 后导入：

```bash
# 1. 导出 SQLite 为 SQL
sqlite3 data/db/sqlite.db .dump > dump.sql

# 2. 手动改写 SQL（去掉 SQLite 特有语法）
# 3. 导入 PG
psql -U tap -d tap < dump.sql
```
