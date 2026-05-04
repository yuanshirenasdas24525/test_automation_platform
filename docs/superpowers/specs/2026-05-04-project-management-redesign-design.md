# 项目管理系统重设计 · Design Spec

- **日期**：2026-05-04
- **状态**：Approved（经 6 轮 brainstorming 与计划审批）
- **范围**：测试自动化平台的"项目管理"层 —— 把现有 Project / ProjectVersion / Module / Requirement / TestCase 五张主表，扩展为完整的 SDLC（迭代 → 需求拆分 → 分配 → 开发 → 测试 → bug → 报告 → 资产沉淀）支撑系统
- **关联仓库**：`/Users/Apple/Documents/test_automation_platform`
- **后续**：approve 后由 `superpowers:writing-plans` skill 拆分 task-by-task 实施计划

---

## 1. Context

### 1.1 现状

平台目前的"项目管理"层只覆盖以下实体：

| 实体 | 表 | 现有职责 |
|---|---|---|
| 项目 | `projects` | `enabled_stacks` JSONB 数组 |
| 模块 | `modules` | 项目内树形结构，无 stack 列（说明天然就是统一模块树） |
| 项目版本 | `project_versions` | `display_name / status (planning/developing/testing/released/archived) / frontend_versions / backend_versions / release_notes / docs` + m2m `project_version_modules` |
| 需求 | `requirements` | `title / description / acceptance_criteria / priority / status (draft/approved/archived) / source (manual/ai_generated)` |
| 用例 | `test_cases` | 多 case_type（api / web / android / ios / mixed / functional），统一走 v2 pytest 入口 |

### 1.2 缺失的承载

完整 SDLC 工作流需要的实体目前**没有**：

- ❌ 没有 **Task** 表 → 一个 Requirement 没法并行分给前端/后端/UI/测试
- ❌ 没有 **Bug** 实体 → 测试发现问题没法系统跟踪、统计一次过率
- ❌ 没有 **User / Role** 模型 → Requirement.assignee 不存在，"开发只看自己的活"无法过滤
- ❌ 没有**状态级联** → Task / Req / Version 状态各自孤立，"版本完成度"无法自动算
- ❌ 没有按角色的**工作台** → 管理 / 开发 / 测试 / 产品 / UI / 运维 没有专属入口
- ❌ 没有**版本测试汇总** → 一次过率 / Bug 等级分布 / 修复时长 等指标无落点
- ❌ **资产沉淀机制松散** → 用例 / 文档 / Bug 没按版本归档

### 1.3 目标

**最小动刀**复用既有架构（FastAPI + SQLAlchemy 2.0 + Celery + React/Vite/shadcn），把上述能力补齐，不重写已工作的部分。

---

## 2. 设计决策（含备选方案与选择理由）

### 决策 1 · 模块结构：统一模块树 + stack 视图过滤

**选项**：
- A · 统一模块树（跨 stack 共享）
- B · 按 stack 拆树（web / api / android / ios / functional 各一棵）
- **C · 统一树 + stack 视图过滤** ✅

**选择 C 的理由**：
- 业务上"订单模块"是一个概念，B 把它拆 5 棵树违直觉
- 数据层 0 改动（`modules` 表本来就没 stack 列），前端只需按 `case_type` 过滤即可
- 跨栈聚合需求覆盖度天然支持

**实现路径**：前端在 stack Tab 下做 `case_type` 筛选，空模块自动隐藏。后端不改。

---

### 决策 2 · 工作流颗粒度：三层 Version → Requirement → Task

**选项**：
- A · 两层（Requirement 直接是工单，加 status / assignee 字段即可）
- **B · 三层（Version → Requirement → Task）** ✅
- C · 四层 Jira 风格（Version → Requirement → Story → Task）

**选择 B 的理由**：
- 工作流里"开发完成 → 流转测试"暗示开发任务和测试任务是两件事，A 用一个 status 字段表达不了"前端做完后端没做完"的中间态
- C 多出来的 Story 层在 10 人以下团队基本是装饰

**实现路径**：新建 `tasks` 表，一个 Requirement 拆 N 个 Task（dev / test / ui_review / bug）。

---

### 决策 3 · 状态机：Task 线性流 + Bug 解耦

**选项**：
- A · 简洁线性（5 状态，失败回退）
- **B · Bug 解耦（Task 线性 + bug 是独立 type 的 Task）** ✅
- C · Kanban 列（无 status 字段）

**选择 B 的理由**：
- 测试报告需要"一次过率 / bug 数 / 平均修复时间" → bug 必须是一等公民
- A 把失败状态当成状态回退会丢失这些指标
- C 在中文团队拖拽体验差，移动端尤其难用

**Task 状态枚举**：
```
pending → dev_doing → dev_done → test_doing → passed → closed
                                              ↓ failed
                                          （新建 type=bug 的 Task）
                                              ↓
                                          dev_doing → dev_done → ... 重测
```

---

### 决策 4 · Bug 创建路径

不为 Bug 单建表，**沿用 `tasks.type='bug'`**。但 UI 上需要 3 个入口：

1. **测试报告失败用例旁的"建 Bug"按钮**（最高频）
   - 自动带：`parent_task_id`、`related_case_id`、`assignee_dev_id`（默认原开发）、环境快照（metadata）
2. **看板/Task 列表的 type 过滤 Tab**
3. **Requirement 详情页的"关联 Bug"区**

后端提供快捷端点 `POST /api/tasks/from-test-failure`，自动填上述字段。

---

### 决策 5 · 人员模型：user × role m2m

**选项**：
- A · 单角色（`users.role` 单字符串）
- **B · 多角色（`users` × `roles` × `user_roles`）** ✅
- C · 项目级角色（`project_members` 表）

**选择 B 的理由**：
- 6 个固定角色（admin / dev / test / pm / ui / ops）→ 一张 roles 表正合适
- 一人多角色是真实需求（PM 兼测试组长）
- C 在 <5 项目场景无价值；B → C 的升级成本低（加张表即可），先 B

---

### 决策 6 · 状态级联：自动算 + 人工 Gate

**选项**：
- A · 完全独立（PM 手动维护）
- B · 完全自动（聚合驱动）
- **C · 自动算系统状态 + 业务状态留 PM 验收** ✅

**选择 C 的理由**：
- "开发完成 → 流转测试"链是机械的，自动算最合适
- 但"项目信息对齐"暗示 PM 需要验收节点
- 双 status 字段：`system_status`（自动）+ `business_status`（PM 维护）

**级联规则**：见 §4.1。

---

### 决策 7 · 工作台：每角色一个独立页面

**选项**：
- A · 单页面 + 角色筛选（堆 widget）
- **B · 每角色一个工作台页（A2）** ✅
- C · 自由 widget 拖拽（Notion 式）

**选择 B 的理由**：
- 6 角色场景下 A 的信息密度爆炸
- C 对小团队是过度工程，没人会去自定义
- B 的实现量可控（6 页面 × ~200 行 ≈ 1200 行 TSX）

**路由**：`/workspace/{dev|test|pm|ui|ops|admin}`，多角色用户顶部切换器。

---

### 决策 8 · 资产沉淀：用例 + Bug + 版本三位一体

具体落地：

| 资产 | 沉淀方式 |
|---|---|
| 用例库 | `test_cases` 加 `version_id` 标签，本迭代新增/修改自动打标，下次回归筛选 |
| Bug 库 | `tasks` where `type=bug`，按 module 聚合统计"哪个模块最容易出 bug"反哺用例 |
| 版本归档 | `version_test_summaries` 表持久化指标 + `release_notes` + ProjectVersion.docs 三件套挂版本上 |
| 文档版本绑定 | 沿用 `ProjectVersion.docs`，每个版本的接口文档/设计稿/变更说明都挂版本 |

**项目信息对齐**（让所有角色看到同一份当前状态）：
- 版本看板（`/projects/:id/versions/:vid/board`）跨角色共享
- 站内通知中心（Task 状态流转、Bug 创建、版本发布触发）
- 版本简报自动生成（迭代会前一天定时跑，输出 Markdown）

---

## 3. 数据模型

### 3.1 新建表

#### `tasks`
```sql
CREATE TABLE tasks (
  id              SERIAL PRIMARY KEY,
  requirement_id  INT REFERENCES requirements(id) ON DELETE CASCADE,
  parent_task_id  INT REFERENCES tasks(id),  -- bug 指向原 task
  title           VARCHAR(255) NOT NULL,
  description     TEXT,
  type            VARCHAR(20) NOT NULL,  -- dev | test | ui_review | bug
  severity        VARCHAR(4),            -- P0 | P1 | P2 | P3 (仅 bug)
  status          VARCHAR(20) NOT NULL,  -- pending|dev_doing|dev_done|test_doing|passed|failed|closed
  assignee_dev_id   INT REFERENCES users(id),
  assignee_test_id  INT REFERENCES users(id),
  related_case_id   INT REFERENCES test_cases(id),
  created_by_id     INT REFERENCES users(id),
  metadata          JSONB,
  estimated_hours   NUMERIC(5,2),
  actual_hours      NUMERIC(5,2),
  created_at, updated_at, closed_at TIMESTAMP
);
CREATE INDEX idx_tasks_requirement ON tasks(requirement_id);
CREATE INDEX idx_tasks_assignee_dev ON tasks(assignee_dev_id) WHERE assignee_dev_id IS NOT NULL;
CREATE INDEX idx_tasks_assignee_test ON tasks(assignee_test_id) WHERE assignee_test_id IS NOT NULL;
CREATE INDEX idx_tasks_type_status ON tasks(type, status);
CREATE INDEX idx_tasks_parent ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;
```

#### `roles` + `user_roles`
```sql
CREATE TABLE roles (
  id SERIAL PRIMARY KEY,
  code VARCHAR(20) UNIQUE NOT NULL,  -- admin | dev | test | pm | ui | ops
  name VARCHAR(50),
  description TEXT
);
CREATE TABLE user_roles (
  user_id INT REFERENCES users(id) ON DELETE CASCADE,
  role_id INT REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, role_id)
);
-- seed: 6 个 role 在 migration 里 INSERT
```

#### `version_test_summaries`
```sql
CREATE TABLE version_test_summaries (
  id SERIAL PRIMARY KEY,
  version_id INT REFERENCES project_versions(id) UNIQUE,
  total_requirements INT,
  total_tasks INT,
  total_test_cases INT,
  passed INT, failed INT, blocked INT,
  total_bugs INT,
  p0_bugs INT, p1_bugs INT, p2_bugs INT, p3_bugs INT,
  first_pass_rate     NUMERIC(5,4),  -- 1 - bug_count / dev_task_count
  avg_fix_time_hours  NUMERIC(8,2),
  test_coverage       NUMERIC(5,4),  -- 关联了用例的需求数 / 全部需求
  generated_at TIMESTAMP,
  payload JSONB                       -- 完整快照，便于发布后回溯
);
```

### 3.2 修改表

#### `requirements`
```sql
ALTER TABLE requirements
  ADD COLUMN version_id        INT REFERENCES project_versions(id),
  ADD COLUMN system_status     VARCHAR(20),  -- approved|developing|testing|ready_to_release（自动算）
  ADD COLUMN business_status   VARCHAR(20),  -- approved|accepted|released（PM 维护）
  ADD COLUMN assignee_pm_id    INT REFERENCES users(id),
  ADD COLUMN accepted_at       TIMESTAMP;
CREATE INDEX idx_requirements_version ON requirements(version_id);
CREATE INDEX idx_requirements_pm ON requirements(assignee_pm_id) WHERE assignee_pm_id IS NOT NULL;
```

#### `project_versions`
- 状态枚举补 `ready_to_release`

#### `test_cases`
- 加 `version_id` 标签字段（用于资产沉淀的版本回流）

### 3.3 待审视

仓库已有 `system_user` 概念，需在实施前确认：
- 是否已存在 users 表？字段如何？
- 如已存在但字段不完整，本次扩展而非新建

---

## 4. 关键流程

### 4.1 状态级联（自动算 system_status）

任意 Task.status 变化触发 `task_service.recompute_requirement_status(req_id)`：

```
该 Req 下所有 Task：
  全部 closed/passed                   → req.system_status = 'ready_to_release'
  有 Task 处于 test_doing 或 dev_done  → req.system_status = 'testing'
  有 Task 处于 dev_doing               → req.system_status = 'developing'
  全部 pending                         → req.system_status = 'approved'
```

`req.system_status = 'ready_to_release'` 时：
- 站内通知 push 给 `assignee_pm_id`
- PM 在 `/workspace/pm` 的"待验收"列表看到
- PM 一键 Accept → `business_status = 'accepted'`，`accepted_at = now()`

Version 发布（PM 在 /workspace/pm 操作）：
- 把所有 `business_status = 'accepted'` 的 Requirement 标 `released`
- 触发 `version_summary_service.generate(version_id)` → 写入 `version_test_summaries`
- `ProjectVersion.status = 'released'`

### 4.2 Bug 创建快捷路径

```
POST /api/tasks/from-test-failure
{
  parent_task_id, related_case_id,
  severity, title, description,
  metadata: { reproduce_steps, env_snapshot, screenshots }
}
```

后端自动逻辑：
- `assignee_dev_id ← parent_task.assignee_dev_id`
- `type = 'bug'`, `status = 'dev_doing'`（直接给开发，跳过 pending）
- `parent_task.status` 不变（保留历史："测过了，结果不行"）
- 触发站内通知 push 给 dev

### 4.3 工作台数据来源（A2 方案）

每个工作台 = 几个 widget × 现有/新 API 调用：

```
/workspace/dev
  - "我在做的"      GET /api/tasks?assignee_dev_id=me&status=dev_doing
  - "我的 bug"      GET /api/tasks?assignee_dev_id=me&type=bug&status!=closed
  - "今日完成"      GET /api/tasks?assignee_dev_id=me&closed_at>=today
  - 顶部 dev / bug 视图切换

/workspace/test
  - "待测"          GET /api/tasks?assignee_test_id=me&status=dev_done
  - "测试中"        GET /api/tasks?assignee_test_id=me&status=test_doing
  - "我创建的 bug"  GET /api/tasks?type=bug&created_by_id=me
  - 失败用例旁的"建 Bug"快捷入口（绑定 TestReport 详情页）

/workspace/pm
  - "需求池"        GET /api/requirements?assignee_pm_id=me&business_status=approved
  - "待验收"        GET /api/requirements?assignee_pm_id=me&system_status=ready_to_release
  - "本迭代里程碑"  GET /api/project-versions/:id/board
  - 一键 Accept / 一键发版按钮

/workspace/ui
  - "走查任务"      GET /api/tasks?type=ui_review&assignee_dev_id=me
  - "设计稿资产"    ProjectVersion.docs filter type='design'

/workspace/ops
  - "环境探活"      现有 /api/devices
  - "本周发版"      GET /api/project-versions?status=ready_to_release
  - "上线公告"      发版后从 release_notes 拉

/workspace/admin
  - "成员管理"      GET /api/users + user_roles 编辑
  - "全局看板"      跨项目状态汇总
  - "审计日志"      v2，本期占位
```

---

## 5. 文件清单

### 5.1 后端新建

- `database/models/role.py`
- `database/models/user.py`（先确认是否已存在）
- `database/models/task.py`
- `database/models/version_test_summary.py`
- `database/migrations/versions/2026xxxx_pm_redesign.py`（alembic）
- `server/api/users.py`、`server/api/roles.py`、`server/api/tasks.py`、`server/api/version_summaries.py`
- `server/services/task_service.py`、`server/services/version_summary_service.py`

### 5.2 后端修改

- `database/models/requirement.py`、`database/models/project_version.py`、`database/models/test_case.py`
- `server/api/requirements.py`（加 version 关联 + accept 端点）
- `server/api/__init__.py`、`server/main.py`（注册新 router）

### 5.3 前端新建

- `pages/workspace/{Dev,Test,Pm,Ui,Ops,Admin}Workspace.tsx`、`pages/workspace/WorkspaceSwitcher.tsx`
- `pages/tasks/{TaskList,TaskDetail,CreateBugModal}.tsx`
- `pages/versions/{VersionBoard,VersionSummary}.tsx`
- `types/domain.ts`（加 Task / Bug / Role / User / VersionSummary 类型）
- `lib/api.ts` 或 `api/{tasks,users,roles}.ts`（按现有惯例走）

---

## 6. 实施 Milestone（增量上线）

**M1 · 数据底座**（不影响 UI / 现有用例）
- alembic：新建 4 表 + requirements 5 字段 + project_version 状态枚举扩展 + test_cases 加 version_id
- ORM 模型 + Pydantic schema
- seed：6 个 role
- 可选：把现有 Requirement 拆出默认 dev Task

**M2 · API 层**
- /api/users + /api/roles + /api/tasks + /api/version-summaries
- requirements API 加 version 关联 + accept 端点
- task_service（状态流转 + 自动重算 system_status）
- version_summary_service（聚合 SQL）
- /api/tasks/from-test-failure 快捷端点

**M3 · 工作台前端**
- 6 个 workspace 页面 + 顶部角色切换
- TaskList / TaskDetail / VersionBoard / CreateBugModal
- 测试报告"建 Bug"按钮接入

**M4 · 资产沉淀 + 报告**
- 版本测试报告页（VersionSummary）+ 一键生成 PDF
- 用例库回流：TestCase 按 version_id 筛选
- 版本归档页：summary + release_notes + docs 三件套

---

## 7. Out of Scope（本次不做）

- 审计日志 audit_log（v2）
- 跨项目权限（项目级 role / `project_members` 表，<5 项目无价值）
- IM / 邮件通知接入（先只做站内通知）
- 用户自定义 widget dashboard（A3 方案）
- bug 单独建表（沿用 `tasks.type='bug'`）
- 工时统计 / 燃尽图（M4 之后单独 milestone）
- AI 推荐验收 / 自动出报告草稿（已有 `ai_gateway`，后续接）

---

## 8. 验证（按 milestone）

**M1**
- `alembic upgrade head` 成功 + `alembic downgrade -1` 能回退
- `python -c "from database.models import Task, Role, UserRole, VersionTestSummary; print('ok')"`
- v2 入口跑现有用例确认表结构改动不影响执行

**M2**
- 完整跑通一个 Task 生命周期：`POST /api/tasks` → 状态流转 dev_doing → dev_done → test_doing → passed
- 改最后一个 Task 为 passed → `requirements/:id` 应看到 `system_status='ready_to_release'`
- `POST /api/tasks/from-test-failure` → bug 自动带 parent / dev assignee
- `POST /api/requirements/:id/accept` → `business_status='accepted'`

**M3**
- 6 个 `/workspace/*` 页面数据展示正确
- 多角色用户顶部切换流畅
- 测试报告页"建 Bug"按钮 → 弹窗 → 提交 → 在 /workspace/test 看到新 bug

**M4**
- 一键生成版本测试报告 → version_test_summaries 落库 + 页面渲染
- 用例库 `?version_id=X` 看本版本回流的用例
- 发布版本后 release_notes / docs / summary 三件套挂在版本归档页

---

## 9. Critical Files Referenced

| 用途 | 路径 |
|---|---|
| 现有 requirement 模型 | `database/models/requirement.py` |
| 现有 project_version 模型 | `database/models/project_version.py` |
| 现有 module 模型（确认无 stack 列） | `database/models/module.py` |
| 现有 project 模型 | `database/models/project.py` |
| 路由注册中心 | `server/api/__init__.py`、`server/main.py` |
| DB session 依赖 | `server/api/deps.py` |
| 测试报告聚合（可借鉴写法） | `database/data_sync.py` |
| 已有项目管理草稿 | `docs/project_management_requirements.md` |
| 已有迁移目录 | `database/migrations/versions/` |
| 前端类型集中 | `frontend/src/types/domain.ts` |
| 前端 API client | `frontend/src/lib/api.ts` |

---

## 10. Open Questions / 实施前需确认

1. **users 表现状**：仓库已有 `system_user`，需打开看：是否已是 users 表？字段是否完整？决定 M1 是新建还是扩展。
2. **现有 requirements 数据**：是否需要做数据迁移，把已有 Requirement 拆默认 dev Task？还是允许 Task 表先空着、新建需求再走 Task 流程？
3. **测试用例与 Task 的关联粒度**：`tasks.related_case_id` 是单值，但一个 bug 可能由多个用例同时暴露 —— 是否要改为 m2m？（短期单值够用，按需升级）
4. **multi-stack mixed 用例的 module 归属**：一个 mixed case 可能跨多个模块，目前 `test_cases.module_id` 是单值 —— 与"决策 1（统一模块树 + 视图过滤）"是否完全兼容？
