# 模块大纲（测试点）持久化与同步 · 设计文档

> 状态：设计评审稿　|　范围：AI 生成用例 → 模块级大纲长期保存、刷新对齐、增量重规划
> 关联代码：`server/api/functional_cases.py`（AI outline/batch）、`frontend/src/pages/FunctionalCasesPage.tsx::AiGenerateDialog`、`frontend/src/components/diff`

---

## 1. 背景与目标

现在的「AI 生成用例」把测试点大纲（`digest` + `points`）临时存在**浏览器 localStorage**（key `ai-generate-draft:v1:{projectId}:{moduleId}:{mode}`），单浏览器、临时、生成完即清。这带来几个问题：

- 大纲**不能长期保存**，换设备/清缓存就没了，也无法团队共享。
- 大纲一旦生成就和用例脱节：用户手动改用例、或「测试记录 AI 全面分析」改了用例后，大纲不会跟着变，逐渐失真。
- 模块新增/变更功能时，只能推翻重来，丢掉已有测试点和覆盖关系。

**目标**：把大纲升级为**模块级、服务端长期保存**的资产，并让它始终能反映真实用例：

1. 大纲按模块持久化（项目层面另做汇总视图）。
2. 「刷新对齐」：手动触发，把大纲和当前用例对齐，用 **diff 预览** 展示变化后再应用。
3. 「AI 重新规划」：支持**增量模式**，在现有大纲基础上只针对需求变更补充测试点。
4. 三个入口共用一套「大纲表 + diff 预览 + 应用」机制。

---

## 2. 概念模型

- **大纲（Outline）**：一个模块一份，包含一段需求摘要 `digest` 和一组**测试点**。
- **测试点（Point）**：一条“应该测什么”，可关联到一条具体用例。
- **覆盖状态**：
  - `covered`：该测试点已关联到一条存在的用例。
  - `gap`：该测试点还没有对应用例（缺口）。
  - `obsolete`：该测试点因需求变更已不再适用（增量重规划时可能产生，保留供人工确认）。
- **来源**：`ai`（AI 规划产出）/ `manual`（对齐时按已有用例补的，或人工新增）。

大纲既是**生成可执行用例的来源**，又是**用例覆盖情况的镜像**——它是计划与现状的结合体。

---

## 3. 数据模型

新增两张表，替代 localStorage 草稿。

### 3.1 `module_outline`（每模块一条）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `module_id` | Integer FK→modules.id, **unique**, index | 一模块一份大纲 |
| `mode` | String(20) | `functional` / `interface` |
| `digest` | Text | AI 产出的需求摘要，供后续分批生成 / 增量规划复用 |
| `model_name` | String(100) nullable | 最近一次生成/规划用的模型 |
| `last_aligned_at` | DateTime nullable | 最近一次“刷新对齐”时间 |
| `created_at` / `updated_at` | DateTime | |

### 3.2 `module_outline_point`（多条，挂大纲）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `outline_id` | Integer FK→module_outline.id, ON DELETE CASCADE, index | |
| `title` | String(200) | 测试点标题 |
| `category` | String(50) nullable | 维度/分类（正常、边界、安全…） |
| `sort_order` | Integer default 0 | |
| `linked_case_id` | Integer FK→test_cases.id, **ON DELETE SET NULL**, nullable, index | 关联到具体用例 |
| `status` | String(20) | `covered` / `gap` / `obsolete` |
| `source` | String(20) | `ai` / `manual` |
| `created_at` / `updated_at` | DateTime | |

> `linked_case_id` 用 `ON DELETE SET NULL`：用例被物理删时，DB 层自动把关联清空；业务层的“对齐”会据此把点标为 `gap`（见 §4.2）。

### 3.3 迁移

新增 Alembic 迁移 `database/migrations/versions/20260702_0001_module_outline.py`：
- `down_revision = "case_repeat_count_001"`（当前 head）。
- `upgrade`：建两张表 + 索引 + 外键；`downgrade`：反向 drop。
- 模型放 `database/models/module_outline.py`，在 `database/models/__init__.py` 导出；JSON 列若用则走 `database.base.JSONType`。

---

## 4. 三个核心流程

三者共用同一套「算 diff（不落库）→ 预览 → 应用（落库）」。diff 的**来源不同**：

| 入口 | diff 比较对象 | 触发 | 是否调 AI |
|---|---|---|---|
| 初次规划 | 无（直接写入） | 首次生成 | 是 |
| 刷新对齐 | 大纲 ↔ 当前用例 | 手动点按钮 | 否（本地对齐，快） |
| AI 增量重规划 | 大纲 ↔ AI 新产出的点 | 手动点按钮 | 是 |

### 4.1 初次规划

复用现有 `POST /api/functional_cases/ai_generate_outline`（输入 module_id、需求文本、mode、coverage、dimensions、模型、图片/文档），拿到 `{digest, points}` 后**写入** `module_outline` + `module_outline_point`（`source=ai`，`status=gap`，暂无关联用例）。随后分批生成用例（现有 `ai_generate_batch`）时，把新用例回写到对应点的 `linked_case_id` 并置 `covered`。

### 4.2 刷新对齐（大纲 ↔ 用例）

**手动**触发，**不调 AI**。后端逻辑：

1. 拉当前模块所有用例（按 mode 过滤 `CASE_TYPE_API` / `CASE_TYPE_FUNCTIONAL`）。
2. 已关联点：
   - 关联用例仍在 → 若用例名变了，产出 `renamed`（黄，旧名→新名）。
   - 关联用例已删（`linked_case_id` 为 NULL 或查不到）→ 产出 `orphaned`，点转 `gap`（红划掉，标“用例已删除”）。
3. 模块里有用例但没被任何点关联（手动新增、或 AI 分析新增）→ 产出 `added`，补一条点（`source=manual`，`covered`，关联该用例，绿 +）。
4. 未变化的点 → 不出现在 diff 里（或标 `unchanged`）。

对齐分两个接口：**算 diff（不落库）** 和 **应用 diff（落库）**，中间给用户 diff 预览确认。

### 4.3 AI 增量重规划（大纲 ↔ AI 新点）

**手动**触发，**调 AI**。用于模块新增/变更功能：

1. 前端小面板：模式（**增量**默认 / 全量重来）+ 只填「本次变更/新增需求」文本。
2. 后端把 **现有 `digest` + 现有测试点（含覆盖状态）+ 变更说明** 一起喂给 AI，指令：“保留仍有效的测试点，只针对本次变更补充新点 / 标记失效点 / 修订措辞”。
3. AI 返回**相对现有大纲的增量**，映射成 diff：
   - 新点 → `added`（绿 +）
   - 变更后不再适用 → `obsolete`（红 −，人工确认是否删）
   - 措辞修订 → `renamed`（黄 ~）
4. 走同一 diff 预览 → 应用；应用后更新 `digest`。

---

## 5. 后端接口设计

沿用 `functional_cases.py` 的 router 前缀（`/api/functional_cases`）。统一响应信封 `{status, data|message}`。

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/module_outline?module_id=` | 读某模块大纲（digest + points + 覆盖统计）。无则返回空。 |
| `POST` | `/module_outline/align_preview` | 入参 `module_id`。**只算** 大纲↔用例 diff，不落库。返回 `changes[]`。 |
| `POST` | `/module_outline/apply` | 入参 `module_id` + 用户确认后的 `changes[]`（或 diff token）。**落库**。 |
| `POST` | `/ai_generate_outline` | **扩展**：新增 `incremental`、`existing_points`、`change_text` 入参；`incremental=true` 时基于现有大纲产出增量（供增量重规划的 diff）。 |

`changes[]` 每项形状（复用给 diff 组件）：

```json
{
  "op": "added | orphaned | renamed | obsolete | unchanged",
  "point_id": 123,            // 已有点则带；added 为 null
  "title": "密码错误登录失败",
  "old_title": "SQL注入登录",  // renamed 时带
  "category": "安全",
  "linked_case_id": 1247,     // 关联/来源用例
  "source": "manual | ai",
  "next_status": "covered | gap | obsolete"
}
```

> 应用接口需幂等、加事务；`align_preview` 与 `apply` 之间用例可能又变了，`apply` 时以最新用例为准做一次校验，避免应用陈旧 diff。

---

## 6. Prompt 改动

`ai_generate_outline` 的模板（`interface_case_outline` / `functional_case_outline`，见 `ai_gateway/prompts/`）新增两个占位符：

- `EXISTING_OUTLINE`：现有测试点清单（标题 + 分类 + 覆盖状态），增量模式下注入。
- `CHANGE_NOTE`：本次变更/新增需求文本。

增量指令要点：保留仍有效的点、只产出增量（新增/失效/修订）、不重复已有点、失效点给出理由。全量模式下这两个占位符留空，行为同现在。

---

## 7. 前端设计

### 7.1 抽屉外壳

把 `AiGenerateDialog` 的**容器从居中弹窗改为右侧抽屉**（内部输入、三阶段、生成、保存逻辑全部复用）：

- 从右侧滑出，仅 `border-left` 分隔，**无遮罩 / 无模糊 / 无阴影**，左侧列表照常可见可交互。
- 顶部可**折叠成窄条**、可关闭。

### 7.2 大纲常态视图

- 工具条：**刷新对齐**（本地对齐 → diff 预览）、**AI 重新规划**（增量面板）、显示上次对齐时间。
- `digest` 折叠展示。
- 测试点列表：顶部统计“覆盖 N / 缺口 M”；`covered` 显示关联用例号（可点进编辑）、`gap` 红色高亮并带「生成用例」按钮；底部「生成全部缺口用例」。

### 7.3 diff 预览

复用 `frontend/src/components/diff`，代码 diff 观感：绿 `+` 新增、红 `−` 划掉（失联/失效）、黄 `~` 改名（旧名划掉）、白色无标记不变。底部「取消 / 应用变更」。

### 7.4 数据来源迁移

大纲数据从 localStorage 改为读 `GET /module_outline`。localStorage 草稿仅保留为“未保存输入的临时缓存”，或直接废弃（`readAiGenerateDraft` / `writeAiGenerateDraft` 相关逻辑清理）。

### 7.5 项目层汇总视图（后续）

项目维度聚合各模块大纲，展示总测试点数、覆盖率、缺口分布，用于整体评审。属第二期，不阻塞本设计。

---

## 8. 分期落地计划

**第一期（后端）**
1. 建 `module_outline` / `module_outline_point` 模型 + Alembic 迁移。
2. `GET /module_outline`、`POST /module_outline/align_preview`、`POST /module_outline/apply`。
3. 初次规划落库：`ai_generate_outline` 成功后写大纲；`ai_generate_batch` 生成用例后回写 `linked_case_id`。

**第二期（前端）**
4. `AiGenerateDialog` 容器改右侧抽屉（无遮罩/模糊/阴影，可折叠）。
5. 大纲常态视图 + 「刷新对齐」→ diff 预览 → 应用。
6. localStorage 草稿迁移/清理。

**第三期（增量重规划 + 汇总）**
7. `ai_generate_outline` 增量模式 + prompt 占位符。
8. 「AI 重新规划」增量面板 → diff。
9. 项目层大纲汇总视图。

---

## 9. 待定 / 风险

- **点↔用例的匹配依据**：优先用 `linked_case_id`；对“模块里有用例但没关联的点”，按用例名做一次模糊匹配再决定“关联已有点”还是“新增点”，需定匹配规则（完全同名 / 相似度阈值）。
- **`obsolete` 点是否自动删**：默认保留供人工确认，不自动删。
- **并发**：`align_preview` 与 `apply` 之间用例可能再变，`apply` 需以最新用例复核。
- **AI 增量幂等**：同一变更多次点“规划变更”应尽量稳定，避免重复补点（靠 `EXISTING_OUTLINE` 去重指令 + 应用时按标题去重）。
- **粒度**：本设计按模块存；项目层仅做只读汇总，不单独存储。
