# PM 重设计 · M3 工作台前端 实施计划

## Context

M1（数据底座，commits `7f8d292..64bb088`）+ M2（API 层，commits `d3b5bd6..a318239`，9 个 task 全部落地）已完成。

M3 目标：**在 M2 API 基础上做完工作台前端**。按角色分 6 个 workspace 页面 + 任务/版本管理页 + 测试报告页"建 Bug"快捷入口；让 dev / test / pm / ui / ops / admin 各看各的视图。

平台无登录态：M3 引入"当前用户选择器"（顶部下拉，写到 `localStorage`），供前端在所有需要 `*_id=me` 的接口处兜出 user_id；后续真正接 auth 时只换数据源不动 UI。

不变量：
- commit 风格沿用 `feat(pm-m3):` 前缀，每 task 单独提交
- 沿用 `frontend/src/lib/api.ts` 命名空间导出（`xxxApi.list/get/create/update/remove`）
- 沿用 `@tanstack/react-query` 数据获取 + `react-hook-form` + `zod` 表单
- 沿用 `shadcn/ui`（Radix）+ Tailwind；不引入新组件库
- 类型集中在 `frontend/src/types/domain.ts`，跟后端 `database/models/*.to_dict()` 对齐
- 信封剥壳由 `request<T>()` 统一处理；业务错误抛 `ApiError`
- 路径别名 `@/* → frontend/src/*`

## 设计决策（M3 锁定）

| # | 主题 | 决策 |
|---|---|---|
| 1 | 当前用户载体 | 顶部"切换用户"下拉，选中的 user_id + role_codes 写 `localStorage('pm.currentUser')`；统一通过 `useCurrentUser()` hook 取 |
| 2 | 多角色用户角色切换 | 当前用户拥有多角色时 → 顶部 `WorkspaceSwitcher`（tabs，沿用 shadcn `Tabs`）；只有一个角色直接进对应工作台 |
| 3 | workspace 路由形态 | `/workspace` 重定向到当前用户首选角色；`/workspace/:role` 渲染对应页面（`role ∈ {dev,test,pm,ui,ops,admin}`） |
| 4 | 工作台 widget 形态 | 卡片栅格（`grid-cols-1 lg:grid-cols-2`），每个 widget = 标题 + 计数 + 列表（最多 5 行 + "查看全部"链接到 `/tasks?...filter`） |
| 5 | TaskList 全量页 | 独立路由 `/tasks`，支持 querystring 过滤（assignee_dev_id / assignee_test_id / type / status / requirement_id），用 react-router `useSearchParams` 双绑表单 |
| 6 | CreateBugModal 触发点 | RunsPage 测试报告详情的 `StepsList` 行内：失败步骤旁加"建 Bug"按钮 → 弹 modal → 提交后 toast 成功 + invalidate `['tasks']` query |
| 7 | "我"语义占位 | 后端 `/api/tasks?assignee_dev_id=me` **不支持** `me` 字面量，前端拼参数时把 `me` 替换成当前 user_id |
| 8 | 一键 Accept / 一键发版 | PM workspace 上的"待验收"卡片 row 内联 `Accept` 按钮 → 调 `POST /api/requirements/:id/accept`；"一键发版"在 VersionBoard 页面（`/versions/:id/board`）顶部，调 `POST /api/version-summaries/:id/regenerate` + 后续 ProjectVersion.status 流转（M3 内只调 regenerate，发版动作 M4 再做） |
| 9 | AppLayout 主导航 | 顶部 NAV 把"工作台"指到 `/workspace` 而非旧 HomePage；保留 HomePage 作为"概览"挂到 `/dashboard`（占位降级，本期不重做内容） |

## 不在 M3 范围

- 真 auth / 登录页
- 站内通知中心（spec §2.1 提到，M4 之后做）
- 一键发版动作（M3 只做"汇总重算"，发版 PUT 流转 M4）
- VersionSummary 报告页（M4）
- 用例库按 `version_id` 回流页（M4）
- 移动端响应式（默认桌面优先）

---

## File Structure

| 路径 | 操作 | 职责 |
|---|---|---|
| `frontend/src/types/domain.ts` | 修改 | 加 `User` / `Role` / `Task` / `TaskCreate` / `TaskUpdate` / `TaskFromTestFailure` / `VersionTestSummary` / `VersionBoard` 类型；扩 `Requirement` 加 `version_id / system_status / business_status / assignee_pm_id / accepted_at`；扩 `Role` codes 常量；导出 `ALL_ROLE_CODES / ALL_TASK_TYPES / ALL_TASK_STATUSES / ALL_BUG_SEVERITIES / ALL_REQUIREMENT_SYSTEM_STATUSES / ALL_REQUIREMENT_BUSINESS_STATUSES` |
| `frontend/src/lib/api.ts` | 修改 | 加 `usersApi / rolesApi / tasksApi / versionSummariesApi`；扩 `requirementsApi`（version_id / system_status / business_status / assignee_pm_id 过滤 + `accept` 端点）；扩 `versionsApi.board` |
| `frontend/src/lib/current-user.ts` | 新建 | `useCurrentUser()` hook + `setCurrentUser()` 写 localStorage + `<CurrentUserProvider>`（Context 让顶部切换器和工作台共享同一份） |
| `frontend/src/components/CurrentUserSwitcher.tsx` | 新建 | 顶部用户下拉 + 角色 tabs；用 `usersApi.list()` 拉所有 active 用户 |
| `frontend/src/components/WorkspaceSwitcher.tsx` | 新建 | 多角色用户的角色切换 tabs（仅多角色时渲染） |
| `frontend/src/components/AppLayout.tsx` | 修改 | NAV "工作台" → `/workspace`；顶栏右侧加 `<CurrentUserSwitcher />` |
| `frontend/src/pages/workspace/WorkspaceRoute.tsx` | 新建 | `/workspace/:role` 路由 wrapper，按 role 渲染对应页 + 处理"用户没选 / 没角色"空态 |
| `frontend/src/pages/workspace/DevWorkspace.tsx` | 新建 | "我在做的" / "我的 bug" / "今日完成" 三 widget |
| `frontend/src/pages/workspace/TestWorkspace.tsx` | 新建 | "待测" / "测试中" / "我创建的 bug" 三 widget |
| `frontend/src/pages/workspace/PmWorkspace.tsx` | 新建 | "需求池" / "待验收"（含一键 Accept） / "本迭代里程碑"（链到 VersionBoard） |
| `frontend/src/pages/workspace/UiWorkspace.tsx` | 新建 | "走查任务" / "设计稿资产" |
| `frontend/src/pages/workspace/OpsWorkspace.tsx` | 新建 | "环境探活"（复用 devicesApi） / "本周发版" / "上线公告" |
| `frontend/src/pages/workspace/AdminWorkspace.tsx` | 新建 | "成员管理"（CRUD users + role 编辑） / "全局看板"占位 / "审计日志"占位 |
| `frontend/src/pages/tasks/TaskListPage.tsx` | 新建 | 独立任务列表页 `/tasks`（querystring 过滤） |
| `frontend/src/pages/tasks/TaskDetailPage.tsx` | 新建 | `/tasks/:id`：详情 + 状态流转 + parent_task / requirement 跳转 |
| `frontend/src/pages/tasks/CreateBugModal.tsx` | 新建 | `<CreateBugModal trigger parent_task related_case_id ... />` 受控弹窗，复用 shadcn Dialog |
| `frontend/src/pages/versions/VersionBoardPage.tsx` | 新建 | `/projects/:pid/versions/:vid/board`：调 `versionsApi.board(vid)`，显示 requirements_by_status 看板 + task_counts_by_type 计数 |
| `frontend/src/pages/RunsPage.tsx` | 修改 | `StepsList` 失败步骤旁加"建 Bug"按钮 → `<CreateBugModal>`；要把 case 关联的 task_id（首选 `report.related_task_id`，没有则 prompt 选择）传过去 |
| `frontend/src/routes.tsx` | 修改 | 加 `/workspace`（重定向）/ `/workspace/:role` / `/tasks` / `/tasks/:id` / `/projects/:pid/versions/:vid/board` 5 条 |
| `frontend/src/pages/HomePage.tsx` | 修改 | 加顶部链接到 `/workspace`（旧首页保留，避免现有书签 404） |

---

## 实施 Task（递增依赖，每 task 一个 commit）

### Task 1 — 类型 + API 扩展

**Files:** modify `frontend/src/types/domain.ts`、`frontend/src/lib/api.ts`

- domain.ts：
  - 加 `User`（id / username / full_name / email / is_active / role_codes / created_at）
  - 加 `Role`（id / code / name / description）
  - 加 `Task`（id / requirement_id / parent_task_id / title / description / type / severity / status / assignee_dev_id / assignee_test_id / created_by_id / related_case_id / metadata / estimated_hours / actual_hours / created_at / closed_at）
  - 加 `TaskCreate / TaskUpdate / TaskFromTestFailurePayload`
  - 加 `VersionTestSummary`（id / version_id / 各计数字段 / first_pass_rate / avg_fix_time_hours / test_coverage / generated_at / payload）
  - 加 `VersionBoard`（version: ProjectVersion / requirements_by_status: Record<string, Requirement[]> / task_counts_by_type: Record<string, ...>）
  - 扩 `Requirement` 加 `version_id` / `system_status` / `business_status` / `assignee_pm_id` / `accepted_at`
  - 扩 `RequirementCreate` / `RequirementUpdate` 加上述字段
  - 导出枚举常量（与后端 `database/models/*.py` 一致）：`ALL_ROLE_CODES = ['admin','dev','test','pm','ui','ops']`、`ALL_TASK_TYPES = ['dev','test','ui_review','bug']`、`ALL_TASK_STATUSES = ['pending','dev_doing','dev_done','test_doing','passed','failed','closed']`、`ALL_BUG_SEVERITIES = ['P0','P1','P2','P3']`、`ALL_REQUIREMENT_SYSTEM_STATUSES = ['approved','developing','testing','ready_to_release']`、`ALL_REQUIREMENT_BUSINESS_STATUSES = ['approved','accepted','released']`
- api.ts：
  - `usersApi`：list（filter is_active / role_code / q） / get / create / update / remove / setRoles(id, role_codes) / removeRole(id, role_code)
  - `rolesApi`：list
  - `tasksApi`：list（filter requirement_id / assignee_dev_id / assignee_test_id / type / status / created_by_id / closed_at_after / parent_task_id） / get / create / update / remove / fromTestFailure(payload)
  - `versionSummariesApi`：get(versionId) / regenerate(versionId)
  - 扩 `requirementsApi`：list 接受 `{ version_id, system_status, business_status, assignee_pm_id }`；加 `accept(id, payload?)`
  - 扩 `versionsApi`：加 `board(versionId)` 调 `/api/project-versions/:vid/board`

**Commit:** `feat(pm-m3): types + api client for users/roles/tasks/version-summaries`

### Task 2 — 当前用户机制 + 顶部切换器

**Files:** create `frontend/src/lib/current-user.ts`、`frontend/src/components/CurrentUserSwitcher.tsx`；modify `frontend/src/components/AppLayout.tsx`

- `current-user.ts`：
  - `CurrentUserContext` + `<CurrentUserProvider>`：从 localStorage 读初值，提供 `{ user, setUser, activeRole, setActiveRole }`
  - `useCurrentUser()` hook
  - `useUserId()` 返回 `user?.id`，给 widget 拼参数用（`me` 替换成 user_id）
- `CurrentUserSwitcher.tsx`：
  - shadcn `DropdownMenu`：拉 `usersApi.list({ is_active: true })`，下拉选用户
  - 选中后写 Provider；如果 `role_codes.length > 1`，下拉里再展示子项 tabs 选当前角色
  - 没选时显示"未登录（选择用户开始）"
- `AppLayout.tsx`：
  - 在顶部 main 区上方加 `<header>` 条，右侧挂 `<CurrentUserSwitcher />`（NAV 内"工作台"链接修改放 Task 9，本 task 暂不动 NAV）
  - 用 `<CurrentUserProvider>` 包住 `<Outlet />`
- `frontend/src/main.tsx` 不动（已有 QueryClientProvider）

**验证:** dev server 起来，顶部下拉能选用户，刷新页面记忆保留，多角色用户能切角色。

**Commit:** `feat(pm-m3): current-user provider + top-bar user switcher`

### Task 3 — Workspace 路由 + WorkspaceSwitcher + Dev/Test 工作台

**Files:** create `frontend/src/components/WorkspaceSwitcher.tsx`、`frontend/src/pages/workspace/WorkspaceRoute.tsx`、`frontend/src/pages/workspace/DevWorkspace.tsx`、`frontend/src/pages/workspace/TestWorkspace.tsx`；modify `frontend/src/routes.tsx`

- `WorkspaceRoute.tsx`：
  - 读 `useParams<{ role: string }>` + `useCurrentUser()`
  - 用户没登录 → 提示"先选用户"
  - 用户角色不含该 role → 403 风格提示 + 链接到首个有权限的 role
  - 否则按 role 分发到对应工作台组件
- `WorkspaceSwitcher.tsx`：用 shadcn `Tabs`，列出当前用户所有角色；点击 `navigate('/workspace/' + role)`
- `DevWorkspace.tsx`：
  - 共用 `<WidgetCard title icon count items renderItem viewAllLink />`（在同文件或 `frontend/src/pages/workspace/_shared.tsx` 抽出来；如抽出加进 file structure）
  - 三个 widget 调 `tasksApi.list()`：
    - "我在做的" → `{ assignee_dev_id: userId, status: 'dev_doing' }`
    - "我的 bug" → `{ assignee_dev_id: userId, type: 'bug' }`（status 多值过滤暂不做：先客户端 filter `status !== 'closed'`）
    - "今日完成" → `{ assignee_dev_id: userId, closed_at_after: <today_iso> }`
  - 每行点击 → `/tasks/:id`
- `TestWorkspace.tsx`：
  - "待测" → `{ assignee_test_id: userId, status: 'dev_done' }`
  - "测试中" → `{ assignee_test_id: userId, status: 'test_doing' }`
  - "我创建的 bug" → `{ created_by_id: userId, type: 'bug' }`
- `routes.tsx`：加
  - `{ path: 'workspace', element: <WorkspaceRedirect /> }`（自动跳到首个角色）
  - `{ path: 'workspace/:role', element: <WorkspaceRoute /> }`

**验证:** 选 alice（dev+test），跳 `/workspace` 自动进 `/workspace/dev`，看到三个 widget；切到 test 角色看到 test widget。

**Commit:** `feat(pm-m3): workspace shell + dev/test workspaces`

### Task 4 — Pm / Ui / Ops / Admin 工作台

**Files:** create `frontend/src/pages/workspace/{PmWorkspace,UiWorkspace,OpsWorkspace,AdminWorkspace}.tsx`

- `PmWorkspace.tsx`：
  - "需求池" → `requirementsApi.list({ assignee_pm_id: userId, business_status: 'approved' })`
  - "待验收" → `requirementsApi.list({ assignee_pm_id: userId, system_status: 'ready_to_release' })`，每行 inline `Accept` 按钮 → `requirementsApi.accept(id, { pm_id: userId })` + `toast.success`
  - "本迭代里程碑" → 让 PM 选一个 project + version（用 shadcn Select），调 `versionsApi.board(vid)`，显示 4 列 system_status 计数 + 链接到 `/projects/:pid/versions/:vid/board`
- `UiWorkspace.tsx`：
  - "走查任务" → `tasksApi.list({ type: 'ui_review', assignee_dev_id: userId })`（注：当前模型 ui_review 也用 assignee_dev_id）
  - "设计稿资产" → 列出当前用户参与的 ProjectVersion 的 `design_doc_items`（先简化：要求选 project + version）
- `OpsWorkspace.tsx`：
  - "环境探活" → 复用 `devicesApi.list()`
  - "本周发版" → `versionsApi.list(projectId)` 然后客户端 filter `status === 'ready_to_release'`（先要求选 project）
  - "上线公告" → 列出 `released` 版本的 `release_notes`
- `AdminWorkspace.tsx`：
  - "成员管理"：表格列出 users + 编辑角色按钮（新建/修改用户用 shadcn Dialog 表单 + react-hook-form + zod）
  - "全局看板" / "审计日志" 占位（"M4 占位"提示卡片）

**验证:** 切到 pm 角色 → "待验收" 列表点 Accept → toast 成功 + 列表自动刷新（react-query invalidate）。

**Commit:** `feat(pm-m3): pm/ui/ops/admin workspaces`

### Task 5 — TaskList + TaskDetail 页

**Files:** create `frontend/src/pages/tasks/TaskListPage.tsx`、`frontend/src/pages/tasks/TaskDetailPage.tsx`；modify `frontend/src/routes.tsx`

- `TaskListPage.tsx`：
  - 顶部过滤器：assignee_dev_id / assignee_test_id（用户下拉）/ type（Select） / status（Select）/ requirement_id（数字输入或下拉）
  - 表格：title / type / status / severity / assignee_dev / assignee_test / created_at / 操作
  - querystring 双绑：`useSearchParams()`，url 直接复制粘贴可用
  - 表格行点击 → `/tasks/:id`
- `TaskDetailPage.tsx`：
  - 头部：title / type / status（可在线流转：下拉切到下一个状态调 `tasksApi.update`）/ severity（bug only）
  - 元信息：requirement / parent_task（链接） / assignee_dev / assignee_test / created_by / created_at / closed_at / estimated_hours / actual_hours
  - 描述：description（markdown 简单渲染或纯文本）
  - 子任务/相关任务列表：`tasksApi.list({ parent_task_id: id })`
  - 关联用例链接（如果 related_case_id）
- `routes.tsx`：加 `/tasks`、`/tasks/:id`

**验证:** 列表 url `?assignee_dev_id=1&type=dev` 正确过滤；点行进详情；改 status → toast + 列表刷新。

**Commit:** `feat(pm-m3): task list + task detail pages`

### Task 6 — VersionBoard 页

**Files:** create `frontend/src/pages/versions/VersionBoardPage.tsx`；modify `frontend/src/routes.tsx`

- 路由 `/projects/:pid/versions/:vid/board`
- 调 `versionsApi.board(vid)` 一次
- 看板：4 列 `requirements_by_status`（approved / developing / testing / ready_to_release），每列 Requirement 卡片（title / 短描述 / Tasks 数量）
- 顶部：version 信息条 + "重算汇总"按钮（调 `versionSummariesApi.regenerate`，toast 成功）
- 右侧：`task_counts_by_type` 小卡（dev / test / ui_review / bug，bug 再列 P0-P3）
- routes.tsx 加路由
- PmWorkspace "本迭代里程碑" widget 链接到这里

**验证:** `/projects/1/versions/1/board` 看到 4 列分桶 + bug 严重度计数。

**Commit:** `feat(pm-m3): version board page`

### Task 7 — CreateBugModal 组件

**Files:** create `frontend/src/pages/tasks/CreateBugModal.tsx`

- 受控 props：`open / onOpenChange / parentTaskId / relatedCaseId? / defaultTitle? / createdById`（默认从 useUserId() 取）
- shadcn Dialog + react-hook-form + zod：
  - title 必填
  - severity Select（P0-P3）必选
  - description textarea
  - metadata 隐藏字段（reproduce_steps / env_snapshot / screenshots，本期先收集 reproduce_steps 文本框）
- 提交调 `tasksApi.fromTestFailure({ parent_task_id, related_case_id, severity, title, description, created_by_id, metadata: { reproduce_steps } })`
- 成功 → toast + invalidateQueries(['tasks']) + onOpenChange(false)
- 失败 → toast.error + 留 modal

**验证:** 单元式手动调用：在 TaskDetailPage 临时挂一个按钮触发它，提交后看 /workspace/test 出现新 bug。

**Commit:** `feat(pm-m3): CreateBugModal component`

### Task 8 — RunsPage 集成"建 Bug"

**Files:** modify `frontend/src/pages/RunsPage.tsx`

- 难点：步骤报告 `TestStepReport` 没有直接的 `task_id` 字段；需要先确认 case→requirement→task 关系
  - 简化做法：modal 打开时让用户选一个 task（拉 `tasksApi.list({ requirement_id })` 让用户挑一个 dev task 当 parent）；后续 M4 再做"自动定位上一次的 dev task"
  - 如果 step 关联的 case 没有任何 dev task → 给提示并禁用按钮
- `StepsList` 行：失败步骤（status === 'failed' || 'broken' || 'error'）右侧加 `Bug` 图标按钮 → 打开 `<CreateBugModal>` 传入 `parentTaskId`（用户在 modal 里选） + `relatedCaseId = step.case_id`（从 detail 拿）
- 当前用户没选 → toast.error 提示

**验证:** 跑一个失败用例 → 进 Run 详情 → 失败步骤旁点 Bug 按钮 → 选 parent task + 填 severity → 提交 → 切到 test 角色工作台看到新 bug。

**Commit:** `feat(pm-m3): create-bug shortcut in run report`

### Task 9 — AppLayout 顶部 NAV + 收尾

**Files:** modify `frontend/src/components/AppLayout.tsx`、`frontend/src/pages/HomePage.tsx`、`frontend/src/routes.tsx`

- AppLayout NAV：把"工作台"项的 `to` 从 `/` 改成 `/workspace`；保留 HomePage 路径改为 `/dashboard`（NAV 不展示，老书签可用）
- HomePage 顶部加一段提示卡片："工作台已迁移到 /workspace"，链接到 `/workspace`
- routes.tsx：原 `index` 元素改成 `<Navigate to="/workspace" replace />`，`/dashboard` 挂 HomePage

**验证:** 进根 url 自动跳 `/workspace`；侧栏"工作台"高亮。

**Commit:** `feat(pm-m3): switch nav default to /workspace`

---

## Critical Files Referenced（不修改、只参考）

| 用途 | 路径 |
|---|---|
| 路由风格 | `frontend/src/routes.tsx` |
| 页面 + react-query 模板 | `frontend/src/pages/RequirementsPage.tsx` / `frontend/src/pages/RunsPage.tsx` |
| `request<T>()` 信封剥壳 | `frontend/src/lib/api.ts:55-100` |
| API 命名空间风格 | `frontend/src/lib/api.ts` 各 `xxxApi = {...}` 块 |
| 类型枚举常量风格 | `frontend/src/types/domain.ts:31-42` `ALL_PROJECT_STACKS` |
| AppLayout NAV 模式 | `frontend/src/components/AppLayout.tsx:21-28` |
| 后端枚举常量来源 | `database/models/role.py` / `task.py` / `requirement.py` |
| 后端 board 接口 | `server/api/project_versions.py:230-290` |
| 后端 from-test-failure | `server/api/tasks.py`（M2 task 5） |

---

## End-to-End 验证（完成 9 task 后）

1. 起后端 + 前端：
   ```bash
   python3 server/main.py &
   cd frontend && npm run dev
   ```
2. 浏览器开 `http://localhost:5173/`：自动跳 `/workspace`，提示"先选用户"
3. 顶部下拉选 `alice`（在 M2 验证里建的，role=dev+test）→ 自动进 `/workspace/dev`，看到 3 个 widget
4. 切角色 tab 到 test → 看到 test 三 widget；"我创建的 bug" 含 M2 验证里建的 P1 bug
5. 进 RunsPage，找一条失败 run → 失败步骤旁点 Bug → 弹窗 → 选 parent task + 填 severity → 提交
6. 回 `/workspace/test` → "我创建的 bug" 列表多一行
7. 切到 pm 用户 → `/workspace/pm` → "待验收"列表点 Accept → toast 成功
8. 进 `/projects/:pid/versions/:vid/board` → 4 列看板 + bug 严重度计数
9. `/tasks?assignee_dev_id=1&type=dev` → 列表过滤工作正常

每步 lint + typecheck 通过：
```bash
cd frontend && npm run typecheck && npm run lint
```

---

## Out of Scope（M3 不做，留给 M4）

- 真 auth / 登录页
- 站内通知中心
- 一键发版 PUT 流程（spec §4.1）
- VersionSummary 报告页（一键导 PDF）
- 用例库 `?version_id=X` 回流页
- 移动端适配

---

## Next Step

approve 后保存到 `docs/superpowers/plans/2026-05-05-pm-redesign-m3-frontend-workspace.md`，按 task-by-task 派发执行（沿用 M1/M2 节奏）。
