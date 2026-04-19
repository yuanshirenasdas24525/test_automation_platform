# 数据库升级操作指南（v1 → v2）

本指南面向**运维 / 项目负责人**，讲清楚从 v1（只支持 API 自动化）升级到 v2（统一支持 API / App / Web）时，数据库层要做哪些事、顺序是什么、出问题如何回滚。

> **开始前先读完整份文档。** v2 升级涉及 schema 变更 + 数据重排，按顺序来最稳。

---

## 一、总体流程

```
┌────────────┐   ①备份    ┌────────────┐   ②schema  ┌────────────┐   ③数据  ┌────────────┐
│  当前 v1   │──────────▶ │  备份快照  │──────────▶ │  v2 空壳   │────────▶│  v2 已迁移 │
└────────────┘            └────────────┘  alembic    └────────────┘  脚本    └────────────┘
```

三步：

1. **备份** —— 拿到一个可回滚的 snapshot
2. **schema 迁移** —— `alembic upgrade head`，新增表和列
3. **数据迁移** —— `v2_cases_to_steps.py`，把老 API 用例拆成 step

---

## 二、前置检查

升级前先把下面这些确认一遍，能省掉一半事故。

```bash
# 1) 看看目前是什么数据库
alembic current                    # 没有输出 = 库里没有 alembic 版本表，属于首次接入
grep -n '\[sqlite\|postgres\|mysql' config/object_conf.ini

# 2) 看看有多少老 API 用例会被迁
# （Python 交互式看一下就行，下面是 SQL 版本）
sqlite3 data/app.db "SELECT COUNT(*) FROM test_cases WHERE method IS NOT NULL;"
# 或
psql -c "SELECT COUNT(*) FROM test_cases WHERE method IS NOT NULL;"

# 3) 看看模型文件是不是最新的（要有新模型才能 alembic 识别）
ls src/database/models/ | grep -E 'test_step|test_environment|device|test_variable'
# 应该能看到 test_step.py / test_environment.py / device.py / test_variable.py
```

如果这一步就出异常（例如库里已经有 v2 的表、或 `alembic current` 显示的版本号不在 `versions/` 里），**先停下来排查**，不要硬跑。

---

## 三、第一步：备份

这一步**不能省**。v2 升级虽然尽量做到可回滚，但数据迁移脚本是不可逆的（下面第五节会讲回滚）。

### PostgreSQL（推荐）

```bash
pg_dump -Fc -h <host> -U <user> -d test_automation \
    -f backup_v1_$(date +%Y%m%d_%H%M).dump
```

`-Fc` 是自定义格式，后续用 `pg_restore` 恢复。

### MySQL

```bash
mysqldump -h <host> -u <user> -p --single-transaction \
    --routines --triggers test_automation \
    > backup_v1_$(date +%Y%m%d_%H%M).sql
```

### SQLite

```bash
cp data/app.db data/backup_v1_$(date +%Y%m%d_%H%M).db
```

> 验证备份可用：用备份文件另建一个临时库，能正常启动就算 OK。别等真出问题才发现快照其实是坏的。

---

## 四、第二步：schema 迁移

### 4.1 安装依赖

```bash
pip install -r requirements.txt
# 确认 alembic 装上了
alembic --version   # 应当 ≥ 1.13
```

### 4.2 选择目标数据库

默认跑在 `config/object_conf.ini` 的 `[sqlite_local]`。要切 PG / MySQL：

```bash
# 方式 A（推荐）：用 section 名
export ALEMBIC_DB_SECTION=postgres_local

# 方式 B：直接塞 URL
export ALEMBIC_DB_URL='postgresql+psycopg2://user:pwd@host:5432/test_automation'
```

### 4.3 首次接入 alembic（老库从未跑过 alembic）

老库里没有 `alembic_version` 表，需要先"打标"，让 alembic 认为当前库已经是 v1 基线：

```bash
# ⚠️ 不要直接 upgrade head。先看一下
alembic history

# 标记：我们的 v2_000001 里的 create_table 是从零起的，所以老库不能 stamp head
# 而是要先真跑一次 upgrade，让新表和新列都建好
alembic upgrade head
```

> 本仓库的 v2 首个迁移 `20260419_0001_v2_000001` 同时做了 "建新表" + "改 test_cases" 两件事，对已有库跑没问题（`add_column` / `create_table` 都是增量）。

### 4.4 执行迁移

```bash
alembic upgrade head
```

跑完后验证：

```bash
alembic current
# 应输出：v2_000001 (head)
```

数据库里检查新表：

```sql
-- PostgreSQL
\dt test_*
-- 应该能看到：test_cases / test_steps / test_environments / test_variables /
--              test_reports / test_step_reports

-- 看新列
\d test_cases
-- 应该有：case_type / tags / env_id / pre_hook / post_hook /
--         variables / timeout / retry / priority
--         （method / data_type / assertion 应该都变成了 nullable）
```

### 4.5 离线模式（生产变更单需要 SQL 才能执行）

```bash
alembic upgrade head --sql > upgrade_v2.sql
# 把 upgrade_v2.sql 交给 DBA 审批 / 在变更窗口执行
```

---

## 五、第三步：数据迁移（老用例拆 step）

schema 建好了，老用例还躺在 `test_cases.method/path/...` 里，需要搬到 `test_steps`。

### 5.1 先跑 dry-run 看报告

```bash
python -m src.database.migrations.data_migrations.v2_cases_to_steps
```

输出类似：

```
INFO v2_migration: 🧪 dry-run：会迁移 127 条，失败 2 条（未写库，加 --commit 才真的写）
WARN v2_migration: ----- 失败列表 -----
WARN v2_migration:   case#42: ...
WARN v2_migration:   case#91: ...
```

**失败列表要逐条查清楚**。常见原因：

- `extract_data` / `assertion` JSON 格式损坏（老数据里偶尔会有单引号）——脚本已兜底 `ast.literal_eval`，剩下的就是真脏数据，手动修。
- `method` 字段里混了奇怪值（例如 `get ` 带空格）——脚本会原样转大写，大多数 Runner 能接受。

### 5.2 灰度执行

大库（≥10k 条）建议分批：

```bash
# 先跑 100 条看看
python -m src.database.migrations.data_migrations.v2_cases_to_steps --commit --limit 100

# 看一下生成的 step 是否正确
psql -c "SELECT case_id, step_type, config->'method', config->'path'
         FROM test_steps ORDER BY id DESC LIMIT 20;"

# 没问题再全量
python -m src.database.migrations.data_migrations.v2_cases_to_steps --commit
```

### 5.3 按项目分批

多项目共库时，一个项目一个项目上：

```bash
python -m src.database.migrations.data_migrations.v2_cases_to_steps \
    --commit --project-id 3
```

### 5.4 脚本特性速览

| 特性 | 说明 |
|------|------|
| **幂等** | 已经有 step 的 case 会跳过，脚本跑多少次都安全 |
| **不删老字段** | `method/path/...` 保留，以防紧急回滚 |
| **默认 dry-run** | 不加 `--commit` 只打印，不写库 |
| **可指定 DB** | `--db-section postgres_local` 切 section |

---

## 六、回滚

### 6.1 只想撤 schema（还没跑数据迁移）

```bash
alembic downgrade -1
```

⚠️ 注意：downgrade 会把 `test_cases.method/data_type/assertion` 恢复成 `NOT NULL`，如果在这期间有新写入的 v2 用例没有填这些字段，downgrade 会失败。先把它们清掉或补上默认值：

```sql
-- 紧急处理：给新写入的空字段填默认值
UPDATE test_cases SET method = 'GET' WHERE method IS NULL;
UPDATE test_cases SET data_type = 'json' WHERE data_type IS NULL;
UPDATE test_cases SET assertion = '{}' WHERE assertion IS NULL;
```

然后再 `alembic downgrade -1`。

### 6.2 已经跑了数据迁移，想完整回到 v1

**数据迁移本身不可逆**（step 是新增的，老字段还在，不存在"丢数据"，但 schema 回退需要先清 step）：

```bash
# 1) 清掉 v2 产出的 step
psql -c "DELETE FROM test_steps;"
psql -c "DELETE FROM test_step_reports;"  # 只有 v2 之后产生的报告才需要清

# 2) 回退 schema
alembic downgrade base

# 3) 如果还不放心，用备份全量恢复
#    pg_restore -d test_automation backup_v1_xxx.dump
```

### 6.3 最坏情况：从备份全量恢复

```bash
# PostgreSQL
dropdb test_automation
createdb test_automation
pg_restore -d test_automation backup_v1_YYYYMMDD_HHMM.dump

# MySQL
mysql -u root -p test_automation < backup_v1_YYYYMMDD_HHMM.sql

# SQLite
cp data/backup_v1_YYYYMMDD_HHMM.db data/app.db
```

---

## 七、常见问题 FAQ

### Q1：可以跳过 dry-run 直接 `--commit` 吗？
不建议。dry-run 能提前暴露脏数据，多花 1 分钟省 1 小时。

### Q2：执行 `alembic upgrade` 时报 "table test_cases already exists"？
说明你的库已经在之前某次尝试里建过部分表了。检查 `alembic_version` 表：

```sql
SELECT version_num FROM alembic_version;
```

- 如果里面已经有 `v2_000001`，说明上次其实跑完了，不需要再跑。
- 如果是空的或别的版本号，先根据实际情况 `alembic stamp <正确版本>`，再继续。

### Q3：老用例里 `assertion` 是纯文本（不是 JSON）怎么办？
脚本会生成一条 `{"type":"raw", "target":"", "expected":<原文>}` 的断言，Runner 侧需要兼容 `type=raw` 的老式断言（暂走老 `response_match.py` 逻辑）。彻底清理在 v2.1 再做。

### Q4：开发机是 SQLite，生产是 PostgreSQL，迁移脚本能共用吗？
能。`_json_type()` 在 SQLite 下是 `JSON`、PG 下是 `JSONB`。所有 `batch_alter_table` 在 SQLite 会走"新建-复制-重命名"策略。只有一个差异：`test_cases.env_id → test_environments.id` 外键**只在 PG 下建**，SQLite 跳过。这是因为 SQLite 的 batch 模式对增加 FK 支持不好。业务层面不受影响。

### Q5：v2 稳定了，什么时候可以删掉老字段（method/path/...）？
计划 v2 稳定运行 **3 个月** 后，发一个 v3 schema 迁移，`drop_column` 老字段。届时会单独出一份指南。

### Q6：并发问题 —— 迁移时 Web 服务还开着可以吗？
不建议。数据迁移本身只加 step、不改老字段，理论上并发读写不会打架，但 schema 迁移的 `ALTER TABLE` 在 PG 下会拿 AccessExclusive 锁。**建议选在业务低峰、平台停服的时间窗执行**，整个窗口大约 5~15 分钟（取决于数据量）。

---

## 八、升级检查清单

跑完所有步骤后，对照这个清单核验：

- [ ] `alembic current` 显示 `v2_000001 (head)`
- [ ] 新表都建起来了：`test_steps` / `test_environments` / `test_variables` / `devices`
- [ ] `test_cases` 里多了 9 个新列，老字段变为 nullable
- [ ] `SELECT COUNT(*) FROM test_steps` 接近于 `SELECT COUNT(*) FROM test_cases WHERE method IS NOT NULL`
- [ ] 随机挑 5 条老 case，检查它对应的 step 里 `config->>'method'`、`config->>'path'` 与老字段一致
- [ ] 平台跑一条老 API 用例，能正常出结果（验证 Runner 适配）
- [ ] 备份文件还在，且经过 `pg_restore --list` / `md5sum` 抽查

---

## 九、相关文档

- [迁移目录 README](../src/database/migrations/README.md) —— 日常 alembic 命令
- [架构重构方案](./架构重构方案.md) —— 为什么这么改、后续演进路线
- 脚本源码：`src/database/migrations/data_migrations/v2_cases_to_steps.py`
