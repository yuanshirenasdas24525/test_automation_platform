# 配置中心 → 项目级配置 实施方案

> 将 API / Web / App / AI 配置从全局 `config_store` 迁移为每个项目独立管理，
> 原有全局配置保留为"默认模板"，新增"其他配置"分类。

---

## 一、架构变化

```
当前                                 目标
config_store (全局)               →  config_store (project_id=NULL = 全局模板)
                                        └─ project_id=1 = 测试平台专属
                                        └─ project_id=2 = 电商系统专属

ConfigPage /config                →  全局模板页（给新建项目拷贝用）
                                  →  ProjectManagementPage → "项目配置" Tab
```

---

## 二、数据库层

### 2.1 `config_store` 表

```sql
ALTER TABLE config_store ADD COLUMN project_id INTEGER REFERENCES projects(id);
CREATE INDEX ix_config_store_project_id ON config_store(project_id);

-- 数据迁移：现有全局配置 → "测试平台"项目
UPDATE config_store 
SET project_id = (SELECT id FROM projects WHERE name = '测试平台' LIMIT 1);
```

- `project_id IS NULL` = 全局模板（不作为任何项目的实际配置，仅用于拷贝/回退）
- `project_id IS NOT NULL` = 该项目的专属配置

---

## 三、后端层

### 3.1 模型改动

| 文件 | 改动 |
|------|------|
| `database/models/config_store.py` | 加 `project_id` 列 |
| `database/schemas/config_update_item.py` | 加 `project_id` 可选字段 |

### 3.2 API 端点变化

```
GET  /api/config/all?category=api&project_id=1    ← 加 project_id
     返回：project_id=1 的配置 + NULL 的全局模板（按 group+key 去重）

POST /api/config/save                              ← body 加 project_id
POST /api/config/add                               ← body 加 project_id
DELETE /api/config/delete/{id}                     ← 不变

POST /api/config/copy-from-global                  ← 新端点
     { project_id: 1, categories?: ["api","web"] }

GET  /api/ai-models?project_id=1                   ← 加 project_id
POST /api/ai-models                                ← body 加 project_id
```

### 3.3 ConfigCenter 内存缓存

```python
# 支持 per-project 加载和读取
ConfigCenter.reload(db, project_id=int|None)
ConfigCenter.get(group, key=None, default=None, project_id=None)
  → 项目配置优先，查不到 fallback 全局模板
```

### 3.4 新增文件

| 文件 | 说明 |
|------|------|
| `server/services/config_service.py` | `copy_global_to_project()` 模板导入逻辑 |
| `server/api/config_schemas.py` | 加 `other` category schema |

---

## 四、前端层

### 4.1 路由

- `/config` → 改名为"全局模板"
- `/projects/:id/management` → ProjectManagementPage 加第三个 Tab `项目配置`

### 4.2 新建组件

| 文件 | 说明 |
|------|------|
| `src/pages/config/ProjectConfigTab.tsx` | 项目配置主组件，含 5 个子 Tab |
| `src/pages/config/ProjectConfigSubTab.tsx` | 单个 category 的配置卡片列表 |

### 4.3 改动组件

| 文件 | 改动 |
|------|------|
| `ProjectManagementPage.tsx` | 加 Tab `项目配置`，挂载 ProjectConfigTab |
| `ConfigPage.tsx` | 标题改为"全局模板" |
| `AppLayout.tsx` | 侧边栏 `/config` label 改为"全局模板" |
| `api.ts` | configApi 加 project_id 参数 |
| `domain.ts` | 配置类型加 project_id |

### 4.4 UI 结构预览

```
ProjectManagementPage
├─ Tab: 📥 需求池
├─ Tab: 📊 版本迭代
└─ Tab: ⚙️ 项目配置
    └─ ProjectConfigTab
        ├─ subTab: API   (host / target_db / redis / headers ...)
        ├─ subTab: Web   (browser)
        ├─ subTab: App   (blacklist / session / probe)
        ├─ subTab: AI    (模型 CRUD，复用 AiModelConfigTab 模式)
        └─ subTab: 其他  (自由 key-value)
```

---

## 五、执行顺序

| # | 步骤 | 文件 |
|---|------|------|
| 1 | Alembic 迁移（加列 + 数据迁移） | migration 文件 |
| 2 | ORM 模型 + Schema | `config_store.py`, `config_update_item.py` |
| 3 | ConfigCenter 改造 | `reload_config.py` |
| 4 | 配置 API 改造 | `config.py`, `config_schemas.py` |
| 5 | 配置服务（新建） | `config_service.py` |
| 6 | AI 模型 API 改造 | `ai_models.py`, `ai_model_service.py` |
| 7 | 前端类型 + API | `domain.ts`, `api.ts` |
| 8 | ProjectConfigSubTab（新建） | `ProjectConfigSubTab.tsx` |
| 9 | ProjectConfigTab（新建） | `ProjectConfigTab.tsx` |
| 10 | ProjectManagementPage | +Tab |
| 11 | ConfigPage / AppLayout | 改名为"全局模板" |
| 12 | 验证编译 + 类型检查 | |
