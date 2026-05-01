# 项目管理功能需求说明书

> 在项目详情页新增"项目管理"顶层 Tab，提供版本迭代管理、模块管理和文档关联能力。

---

## 一、整体定位

### 1.1 当前架构的问题

目前项目详情页的 Tab 按测试类型划分（功能 / API / Web / Android / iOS），**缺少一个从项目管理维度看项目的入口**。测试人员要管理版本、查看需求、关联文档、记录 SQL 和配置变更时，没有统一的地方。

### 1.2 新增 Tab 位置

```
项目详情页顶部 Tab 栏：
  ┌──────┬─────┬─────┬──────┬──────┬──────┐
  │ 项目管理 │ 功能 │ API │ Web │ Android │ iOS │
  └──────┴─────┴─────┴──────┴──────┴──────┘
```

"项目管理"放在第一位，作为进入项目的默认首页。

### 1.3 导航层级

```
项目列表
  └─ 项目详情
       ├─ 项目管理（默认 Tab）
       │    ├─ 迭代版本列表
       │    ├─ 版本详情（模块关联 + 文档 + 版本信息）
       │    └─ 模块管理（树状结构）
       ├─ 功能（功能用例）
       ├─ API（API 自动化用例）
       ├─ Web（Web 自动化用例）
       ├─ Android（App 自动化用例）
       └─ iOS（App 自动化用例）
```

---

## 二、功能模块总览

| 功能模块 | 说明 |
|---------|------|
| 模块管理 | 创建/修改/删除模块和子模块（独立于迭代版本） |
| 版本迭代 | 按版本组织测试工作，每个版本可关联模块、文档、配置变更等 |
| 版本详情 | 版本内的文档管理（需求文档/设计稿/UI原型图）、版本号、SQL、配置变更 |

---

## 三、模块管理

### 3.1 需求描述

项目下的模块树结构（已有 `modules` 表支持），需要在前端提供可视化的树状管理界面。

### 3.2 功能点

| 功能 | 说明 |
|------|------|
| 模块列表 | 以树状结构展示所有模块（含子模块），支持展开/收起 |
| 创建模块 | 在根层级创建新模块，填写模块名称（≤ 50 字） |
| 创建子模块 | 在已有模块下创建子模块，支持多级嵌套 |
| 修改模块 | 修改模块名称 |
| 删除模块 | 删除模块及其所有子模块和关联用例（级联删除） |
| 拖拽排序 | 同级模块间拖拽调整顺序 |
| 移动模块 | 将模块移动到另一个父模块或根层级 |

### 3.3 前端交互

```
项目管理页
  ├─ 左侧：模块树面板
  │   ├─ 根模块（项目名）
  │   │   ├─ ─ 模块 A
  │   │   ├─ ─ 模块 B
  │   │   │   └─ ─ 子模块 B-1
  │   │   └─ ─ 模块 C
  │   └─ 每个模块的操作：新建子模块 / 重命名 / 删除 / 拖拽
  └─ 右侧：选中模块后的详情 / 版本列表
```

### 3.4 模块管理权限收归

**当前问题**：现有功能 / API / Web / Android / iOS Tab 中，每个页面都自带了模块 CRUD 操作（在 `ProjectDetailPage.tsx` 的模块树中可以直接新建、重命名、删除模块）。这导致模块管理入口分散、权限不统一。

**调整方案**：
- 模块的所有 **CRUD 操作（新建 / 重命名 / 删除 / 移动）** 只保留在"项目管理"Tab 的模块树面板中
- 功能 / API / Web / Android / iOS Tab 的模块树仅保留 **浏览和展开/收起** 功能，移除新增、重命名、删除、移动等操作按钮
- 用户在测试 Tab 中需要操作模块时，引导用户切换到"项目管理"Tab

### 3.5 接口复用

模块 CRUD 接口已存在（`server/api/modules.py`），前端直接复用：
- `POST /api/modules` — 创建
- `PUT /api/modules/{id}` — 重命名
- `DELETE /api/modules/{id}` — 删除
- `PATCH /api/modules/{id}/move` — 移动
- `GET /api/modules` — 列表

---

## 四、版本迭代管理

### 4.1 需求描述

版本迭代是项目管理的核心概念。一个项目有多个迭代版本，每个版本关联一组模块，包含该版本的文档、版本号、SQL、配置变更等信息。

### 4.2 数据结构

核心变化：
- **提测版本号支持多条记录** — 当天提测 xxx，明天提测 yyy，一天也可能多次
- **新增常用命令** — 部署/回滚/数据修复等常用命令
- **SQL 支持文本 + .sql 文件** — 直接在文本区内手写，也支持上传 .sql 文件
- **版本信息 + 注意事项统一为 Markdown 文档（`release_notes`）** — 所有版本相关信息合并在一个 markdown 里，方便通读

```sql
CREATE TABLE project_versions (
    id SERIAL PRIMARY KEY,
    project_id INT NOT NULL REFERENCES projects(id),

    -- 版本标识
    version_name VARCHAR(100) NOT NULL,        -- 如 "v2.3.0"、"Sprint 12"
    display_name VARCHAR(200),                 -- 版本展示名，如 "2026年Q2 春季大促"
    status VARCHAR(20) DEFAULT 'planning',     -- planning / developing / testing / released / archived
    sort_order INT DEFAULT 0,

    -- 提测版本记录（支持多条：今天提测一个，明天提测一个，一天也可能多个）
    -- [{ "version": "feat/v2.3.0-beta.1", "date": "2026-04-28", "notes": "首次提测" },
    --  { "version": "feat/v2.3.0-hotfix", "date": "2026-04-28", "notes": "修复闪退" }]
    frontend_versions JSONB DEFAULT '[]',      -- 前端提测版本号列表
    backend_versions JSONB DEFAULT '[]',       -- 后端服务版本号列表

    -- 统一的 Markdown 文档（包含：版本号说明、SQL 变更、配置变更、常用命令、注意事项）
    -- 所有版本相关信息合并在这里，通读这个文件就能了解版本全貌
    release_notes TEXT DEFAULT '',

    -- 文档链接（URL 或文件路径）
    test_plan_url TEXT,                        -- 测试计划链接
    requirement_doc_url TEXT,                  -- 需求文档链接
    design_doc_url TEXT,                       -- 设计稿链接
    ui_prototype_url TEXT,                     -- UI 原型图链接

    -- 时间
    planned_start_at TIMESTAMP,
    planned_end_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 版本关联的模块（多对多）
CREATE TABLE project_version_modules (
    id SERIAL PRIMARY KEY,
    version_id INT NOT NULL REFERENCES project_versions(id),
    module_id INT NOT NULL REFERENCES modules(id)
);
```

#### `release_notes` Markdown 示例

```markdown
# v2.3.0 版本说明

## 提测版本记录
- 前端: feat/v2.3.0-beta.1（2026-04-28，首次提测）
- 前端: feat/v2.3.0-hotfix（2026-04-28，修复闪退）
- 后端: v2.3.0-rc1（2026-04-28）

## SQL 变更
\`\`\`sql
ALTER TABLE orders ADD COLUMN promo_id INT;
CREATE TABLE order_refunds (...);
\`\`\`

## 配置变更
- feature_flag.new_checkout=true
- payment.timeout=30s
- order.max_retry_count=3

## 常用命令
\`\`\`bash
# 数据库迁移
python manage.py migrate

# 部署
kubectl apply -f deploy/v2.3.0/

# 回滚
kubectl rollout undo deployment/api

# 数据修复
python scripts/fix_order_status.py --date 2026-04-28
\`\`\`

## 注意事项与测试要点
1. 本次涉及支付模块重构，需要重点回归支付流程
2. 数据库迁移脚本不可逆，执行前请备份
3. 新老 API 兼容期 48 小时，之后老接口下线
4. 需要验证 Android / iOS 两个端的行为一致
```

### 4.3 功能点

#### 4.3.1 版本迭代列表

| 功能 | 说明 |
|------|------|
| 版本列表 | 按时间倒序展示所有版本迭代，卡片或列表形式 |
| 版本状态 | 每个版本显示状态标签（规划中 / 开发中 / 测试中 / 已发布 / 已归档） |
| 版本标识 | 显示版本名 + 展示名，如 "v2.3.0 — 2026年Q2 春季大促" |
| 快速概览 | 每个版本卡片显示关联模块数、文档数等统计 |

#### 4.3.2 创建/编辑版本

| 功能 | 说明 |
|------|------|
| 版本名 | 必填，如 "v2.3.0" |
| 展示名 | 可选，中文描述 |
| 关联模块 | 从模块树中选择该版本涉及的模块（多选，树形选择器） |
| 状态 | planning / developing / testing / released / archived |
| 时间范围 | 计划开始日期 / 计划结束日期 |
| 排序 | 拖拽或手动排序版本迭代 |

#### 4.3.3 版本详情

点击某个版本后进入详情页，展示该版本的所有关联信息：

##### 文档管理区域

| 字段 | 类型 | 说明 |
|------|------|------|
| 测试计划 | 链接 + 文本粘贴 + 文件上传 | 测试计划的 URL 或上传文档 |
| 需求文档 | 链接 + 文本粘贴 + 文件上传 + **AI 分析** | PRD / 需求规格的链接或附件，支持 AI 一键分析 |
| 设计稿 | 链接 + 文本粘贴 + 文件上传 | 设计稿 Figma 链接或上传图片 |
| UI 原型图 | 链接 + 文本粘贴 + 文件上传 | 原型图链接或上传图片 |

对于每个文档类型，支持三种输入方式：
- **链接**：粘贴 URL（如 Figma 链接、Confluence 链接）
- **文本粘贴**：直接粘贴文档内容（如需求描述文本）
- **文件上传**：上传文件（支持 PDF / 图片 / DOCX / MD）

**需求文档特有 — AI 分析按钮**：
在"需求文档"字段旁增加 **"AI 分析"** 按钮，取代原来在独立页面的 AI 需求分析入口。工作流程：

```
版本详情 → 需求文档区域
  ├─ 上传/粘贴需求文档内容
  ├─ 选中一条或多条需求文档记录
  ├─ 点击 "AI 分析" 按钮
  ├─ AI 异步分析（复用现有 ai_tasks 链路）
  └─ 分析完成后：
       ├─ 生成结构化需求条目列表
       ├─ 每条可点击展开查看详细分析内容（含 description, acceptance_criteria, priority, tags）
       ├─ 关联的交叉模块分析说明
       └─ 用户确认后一键导入到"需求管理"模块
```

分析结果以 **可展开的列表**展示：

```
┌──────────────────────────────────────────────────────┐
│ ✅ AI 分析完成 — 生成 8 条需求                       │
│                                                      │
│ ▶ 用户登录-邮箱登录          P0  ⚡ 与已有登录模块重叠 │
│   ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐   │
│   │ 描述：用户输入已注册的邮箱和密码进行登录         │   │
│   │ 验收标准:                                       │   │
│   │  • 正确邮箱+密码 → 登录成功跳转首页              │   │
│   │  • 未注册邮箱 → 提示"账号不存在"                 │   │
│   │  • 密码错误 → 提示"密码错误"                     │   │
│   └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘   │
│                                                      │
│ ▶ 用户登录-手机号登录          P1                    │
│ ▶ 登录失败锁定策略            P0                     │
│ ...                                                  │
│                                                      │
│ [一键导入到需求管理]                                  │
└──────────────────────────────────────────────────────┘
```

##### 提测版本记录

支持多条提测版本记录，每条包含：

| 字段 | 说明 |
|------|------|
| 版本号 | 如 "feat/v2.3.0-beta.1"、"feat/v2.3.0-hotfix" |
| 提测日期 | 如 "2026-04-28" |
| 备注 | 如 "首次提测"、"修复闪退" |

前端和后端分开管理，均可添加多条记录。一天可提测多个版本，第二天可继续追加。

##### 统一版本说明（Markdown 编辑器）

**不再分散为多个字段，所有版本信息合并为一个 Markdown 文档（`release_notes`）**。使用 Markdown 编辑器，版本管理员在编辑器内自由编写以下内容：

```markdown
# v2.3.0 版本说明

## 提测版本记录
- 前端: xxxx (2026-04-28)
- 后端: xxxx (2026-04-28)

## SQL 变更
（支持手写 SQL 文本，也可以引用上传的 .sql 文件）

## 配置变更

## 常用命令
（部署 / 回滚 / 数据修复等命令）

## 注意事项与测试要点
（该版本的风险点、测试重点、特殊说明）
```

每个版本详情页配一个全屏 Markdown 编辑器，保持通读性。SQL 语句支持直接在编辑器内手写，也支持上传 `.sql` 文件。

##### 关联模块

版本关联的模块列表，可从模块树中多选。用于快速了解该版本影响的范围。

### 4.4 数据库模型设计

除了上述 SQL 定义外，补充一下关系：

```
Project (projects)
  ├── Module (modules) — 模块树
  ├── ProjectVersion (project_versions) — 版本迭代
  │     └── ProjectVersionModule (project_version_modules) — 版本 ⇄ 模块关联
  ├── Requirement (requirements) — 需求条目
  ├── ProjectContext (project_contexts) — 项目上下文
  └── TestPlan (test_plans) — 测试计划
```

---

## 五、前端页面结构

### 5.1 项目管理首页

```
ProjectDetailPage 新增 stack="management" Tab
  └─ ProjectManagementPage 组件
       ├─ 左侧面板：模块树
       │   ├─ 项目名（根节点，不可操作）
       │   ├─ 模块 A [➕新建子模块] [✏️重命名] [🗑删除]
       │   │   └─ 子模块 A-1 [➕新建子模块] [✏️重命名] [🗑删除]
       │   └─ 模块 B [...]
       └─ 右侧主区域
            ├─ 顶部：版本操作栏
            │   ├─ "新建迭代版本" 按钮
            │   └─ 筛选/搜索
            └─ 版本迭代列表
                 ├─ 版本卡片 v2.3.0 [测试中] [2026-04-01 ~ 2026-04-30]
                 │   ├─ 关联模块：订单管理、支付、售后
                 │   ├─ 文档数：3
                 │   └─ [查看详情] [编辑] [删除]
                 └─ 版本卡片 v2.2.0 [已发布] [...]
```

### 5.2 版本详情页

```
点击版本卡片 → 进入版本详情页
  └─ ProjectVersionDetail 组件
       ├─ 版本标题区域
       │   ├─ 版本名 + 展示名
       │   ├─ 状态标签（可切换）
       │   ├─ 编辑基本信息按钮（版本名、展示名、时间）
       │   └─ 返回按钮
       │
       ├─ 左侧面板（结构化信息）
       │   ├─ 文档管理
       │   │   ├─ 测试计划 [链接/文本/文件]
       │   │   ├─ 需求文档 [链接/文本/文件]
       │   │   ├─ 设计稿 [链接/文本/文件]
       │   │   └─ UI 原型图 [链接/文本/文件]
       │   ├─ 提测版本记录
       │   │   ├─ 前端：[+] 添加，列表展示
       │   │   └─ 后端：[+] 添加，列表展示
       │   └─ 关联模块
       │       └─ 已选模块列表，可编辑
       │
       └─ 右侧/主区域：统一版本说明（Markdown 编辑器）
            └─ 全屏 Markdown 编辑器，包含：
                ├─ 提测版本记录
                ├─ SQL 变更（支持上传 .sql 文件）
                ├─ 配置变更
                ├─ 常用命令
                └─ 注意事项与测试要点
```

---

## 六、后端 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/{id}/versions` | 创建版本迭代 |
| GET | `/api/projects/{id}/versions` | 列出项目下的所有版本 |
| GET | `/api/projects/{id}/versions/{vid}` | 获取版本详情 |
| PUT | `/api/projects/{id}/versions/{vid}` | 更新版本信息 |
| DELETE | `/api/projects/{id}/versions/{vid}` | 删除版本 |
| PUT | `/api/projects/{id}/versions/{vid}/modules` | 更新版本关联的模块列表 |
| GET | `/api/projects/{id}/versions/{vid}/modules` | 获取版本关联的模块列表 |

### 请求/响应示例

**创建版本：**

```json
POST /api/projects/1/versions
{
  "version_name": "v2.3.0",
  "display_name": "2026年Q2 春季大促",
  "status": "planning",
  "module_ids": [1, 3, 5],
  "frontend_versions": [
    {"version": "feat/v2.3.0-beta.1", "date": "2026-04-28", "notes": "首次提测"}
  ],
  "backend_versions": [
    {"version": "v2.3.0-rc1", "date": "2026-04-28", "notes": ""}
  ],
  "release_notes": "# v2.3.0 版本说明\n\n## 提测版本记录\n- 前端: feat/v2.3.0-beta.1（2026-04-28）\n\n## SQL 变更\n\n## 配置变更\n\n## 常用命令\n\n## 注意事项与测试要点\n",
  "test_plan_url": "",
  "requirement_doc_url": "https://confluence.example.com/prd-v230",
  "design_doc_url": "https://figma.com/file/xxx",
  "ui_prototype_url": "",
  "planned_start_at": "2026-04-01",
  "planned_end_at": "2026-04-30"
}
```

**响应：**

```json
{
  "status": "success",
  "data": {
    "id": 1,
    "version_name": "v2.3.0",
    "status": "planning",
    "associated_modules": [{"id": 1, "name": "订单管理"}, ...],
    "created_at": "2026-04-30T10:00:00"
  }
}
```

---

## 七、前端技术方案

### 7.1 新增文件

| 文件 | 职责 |
|------|------|
| `frontend/src/pages/ProjectManagementPage.tsx` | 项目管理主页面（左侧模块树 + 右侧版本列表） |
| `frontend/src/pages/ProjectVersionDetailPage.tsx` | 版本详情页 |
| `frontend/src/lib/api.ts` 中新增 `versionsApi` | 版本 CRUD API 封装 |
| `frontend/src/types/domain.ts` 新增 `ProjectVersion` 类型 | TypeScript 类型定义 |

### 7.2 路由修改

`frontend/src/routes.tsx` 中新增：

```tsx
{
  path: "projects/:id/management",
  element: <ProjectManagementPage />,
},
{
  path: "projects/:id/versions/:vid",
  element: <ProjectVersionDetailPage />,
}
```

### 7.3 ProjectDetailPage Tab 修改

在 `ProjectDetailPage.tsx` 的 `StackTabs` 组件中新增：

```tsx
const ALL_STACKS = [
  { value: "management", label: "项目管理", icon: FolderKanban },
  { value: "functional", label: "功能", icon: ClipboardList },
  { value: "api", label: "API", icon: Api },
  { value: "web", label: "Web", icon: Globe },
  { value: "android", label: "Android", icon: Smartphone },
  { value: "ios", label: "iOS", icon: Apple },
];
```

当 `stack === "management"` 时，渲染 `ProjectManagementPage` 组件。

---

## 八、阶段划分

### Phase 1（核心功能）

| 功能 | 工作量估计 |
|------|-----------|
| 后端：ProjectVersion 模型 + 迁移 | 小 |
| 后端：版本 CRUD API | 中 |
| 前端：模块树面板（复用现有接口） | 小 |
| 前端：版本迭代列表 | 中 |
| 前端：版本详情页（基础字段） | 中 |
| 路由集成到 ProjectDetailPage | 小 |

### Phase 2（增强功能）

| 功能 | 说明 |
|------|------|
| 文件上传 | 文档附件上传功能（需要 MinIO 或本地存储） |
| 版本对比 | 两个版本间的差异对比 |
| 版本报告 | 自动生成版本测试报告概要 |
| 模块版本历史 | 查看某个模块在不同版本中的变更轨迹 |

---

## 九、验收标准

- [ ] 项目详情页 Tab 栏新增"项目管理"，且为默认 Tab
- [ ] 左侧模块树展示正确，支持展开/收起
- [ ] 仅在"项目管理"Tab 可创建/重命名/删除/移动模块和子模块
- [ ] 功能 / API / Web / Android / iOS Tab 的模块树仅保留浏览功能，移除 CRUD 操作按钮
- [ ] 可创建版本迭代，填写版本名、展示名、状态、时间范围
- [ ] 创建版本时可关联模块（从模块树中选择）
- [ ] 版本详情页展示文档管理区：测试计划、需求文档、设计稿、UI原型图
- [ ] 版本详情页支持添加多条前端/后端提测版本记录（含日期和备注）
- [ ] 版本详情页提供统一的 Markdown 编辑器用于 `release_notes`
- [ ] Markdown 编辑器中可写入：提测版本记录、SQL 变更、配置变更、常用命令、注意事项
- [ ] SQL 支持直接在编辑器内手写，也支持上传 .sql 文件
- [ ] 支持编辑和删除版本迭代
- [ ] 版本列表按时间倒序排列
