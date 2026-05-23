# 数据库迁移（Alembic）

本目录使用 [Alembic](https://alembic.sqlalchemy.org/) 管理数据库 schema 迁移。

## 目录结构

```
migrations/
├─ env.py              # Alembic 运行环境（从 object_conf.ini 读 DB 连接）
├─ script.py.mako      # 迁移脚本模板
├─ versions/           # 自动生成的 schema 迁移脚本
├─ data_migrations/    # 业务数据迁移脚本（非 schema，例如"老用例转新 steps"）
└─ README.md           # 本文件
```

## 日常命令

```bash
# 看迁移历史
alembic history

# 看当前数据库处于哪个版本
alembic current

# 生成新迁移（基于 model 变更 autogenerate）
alembic revision --autogenerate -m "add_something"

# 应用所有待执行迁移
alembic upgrade head

# 回滚一个版本
alembic downgrade -1

# 回滚到某个指定版本
alembic downgrade <revision_id>

# 离线模式：只导出 SQL，不执行
alembic upgrade head --sql > upgrade.sql
```

## 切换数据库

默认连 `config/object_conf.ini` 里的 `[postgres_local]`（PostgreSQL 统一标准，SQLite 已废弃）。切换方式：

```bash
# 方式 1：通过 section（推荐）
ALEMBIC_DB_SECTION=postgres_prod alembic upgrade head

# 方式 2：直接给 URL
ALEMBIC_DB_URL='postgresql+psycopg2://user:pwd@host:5432/db' alembic upgrade head
```

## 新增 PostgreSQL 配置

在 `config/object_conf.ini` 加：

```ini
[postgres_local]
type = postgresql
host = localhost
port = 5432
user = postgres
password = postgres
database = test_automation
```

## 迁移流程建议

1. 改 model（`src/database/models/xxx.py`）
2. 本地启动 PostgreSQL（可用 docker）：
   ```bash
   docker run -d --name pg-automation \
     -e POSTGRES_PASSWORD=postgres \
     -e POSTGRES_DB=test_automation \
     -p 5432:5432 postgres:16
   ```
3. 生成迁移：
   ```bash
   ALEMBIC_DB_SECTION=postgres_local \
     alembic revision --autogenerate -m "your_message"
   ```
4. **人工 review** `versions/xxxxxx_your_message.py` —— autogenerate 有时会漏掉或过度生成，特别注意：
   - `JSONB` vs `JSON` 的类型切换
   - `server_default` 的识别（Alembic 经常漏）
   - `ALTER ... NOT NULL` 在有数据的表上需要先 `UPDATE`
   - SQLite 不支持很多 ALTER，需要依赖 `render_as_batch`（env.py 已开启）
5. 应用迁移：
   ```bash
   ALEMBIC_DB_SECTION=postgres_local alembic upgrade head
   ```
6. 如果需要跑**业务数据迁移**（例如老用例转 steps），在 `data_migrations/` 下加独立脚本，手动执行：
   ```bash
   python -m src.database.migrations.data_migrations.v2_cases_to_steps
   ```

## 注意事项

- **不要在 schema 迁移里写业务数据迁移**。schema 用 Alembic；数据用独立脚本。理由：schema 迁移需要可回滚，数据迁移通常不可回滚；混在一起会很难维护。
- **生产环境先备份**：`pg_dump -Fc` 拿 snapshot，再跑 upgrade。
- **SQLite 批处理**：env.py 里已经开启了 `render_as_batch`，但仍要避免过于复杂的 ALTER 组合。推荐本地开发也切 PG，避免 dialect 差异带来的惊喜。
