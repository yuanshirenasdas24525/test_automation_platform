# PM 重设计 · M4 资产沉淀 + 报告 设计

**日期：** 2026-05-07
**前置：** M1（数据底座）/ M2（API 层）/ M3（工作台前端）已完成，commits `7f8d292..9604627`
**总 spec：** [`2026-05-04-project-management-redesign-design.md`](./2026-05-04-project-management-redesign-design.md) §6 M4 / §8 M4 验证

---

## 1. 目标

把 M3 单一 board 路由扩展成"版本视图中心"，承接发版前后两类场景：

- **发版前**：跑完一轮测试 → 一键生成版本测试报告（HTML 页 + PDF 导出），交付给 PM/上级
- **发版后**：summary + release_notes + docs 三件套挂在版本归档页，未来回溯有完整快照

同时把"用例库回流"（`TestCase.version_id` 字段在 M1 已加但前端没消费）做出来：本版本绑定的用例汇成一页，PM 能快速看到这次迭代到底覆盖了哪些自动化用例。

## 2. 关键决策

| # | 主题 | 决策 |
|---|---|---|
| 1 | PDF 生成方式 | 浏览器原生打印（`window.print()` + `@media print` CSS）。零依赖；HTML/CSS 直接控样式；后续要做服务端 PDF 也可复用同份样式 |
| 2 | 报告页位置 | `/projects/:pid/versions/:vid/board?tab=report` —— 不开新路由，VersionBoardPage 升级成 4-tab shell |
| 3 | 用例回流页位置 | 同上 `?tab=cases`，跟 board / report / archive 共享 version header |
| 4 | 归档页位置 | 同上 `?tab=archive`，**仅 `version.status ∈ {released, archived}` 时 tab 显示** |
| 5 | 报告内容范围 | 指标卡（4 列）+ bug 明细表 + 失败用例表 + 需求覆盖表。不做 timeline / 燃尽图（spec §7 已划 OOS） |
| 6 | 后端按版本列用例 | 新增 `GET /api/project-versions/:vid/cases`（与 board 同 namespace，参数最少）；不复用 functional_cases 那套（它只管 functional） |
| 7 | tasks 批量按 ID 拉取 | 扩 `tasksApi.list` 加 `?ids=1,2,3`，避免 N+1（report tab 拿 `summary.payload.bug_ids` 一次拉回） |
| 8 | release_notes 渲染 | 沿用 edit 态的 `{sql, config, commands, notes}` 4 段 JSON 结构；4 段 `<pre>` 渲染，不引 markdown 库 |
| 9 | PmWorkspace 链接 | "本迭代里程碑" widget 旁加"📊 报告"小链接 → `?tab=report`；不改原 board 链接 |
| 10 | 打印实现 | `_print.css` + DOM `data-attr` 钩子；不改 AppLayout 行为，只加一个 `data-app-header` 标记 |

## 3. 不在 M4 范围

- 工时统计 / 燃尽图 / timeline（spec §7 OOS）
- AI 生成报告草稿（spec §7 OOS）
- 服务端 PDF 渲染（weasyprint / reportlab）
- 跨项目版本对比
- bug 自动归档
- 移动端响应式（M4 仍桌面优先）

---

## 4. 架构

### 4.1 路由形态

```
/projects/:pid/versions/:vid/board                ← M3 现有路由
                              ?tab=board          ← 默认（M3 行为不变）
                              ?tab=report         ← M4 新
                              ?tab=cases          ← M4 新
                              ?tab=archive        ← M4 新（仅已发版状态显示）
```

Tab 状态通过 `useSearchParams('tab')` 双绑：URL 即真理，刷新/分享 URL 都带 tab 参数。

### 4.2 数据流

| Tab | 调用 | 数据来源 |
|---|---|---|
| board | `versionsApi.board(vid)` | M3 已有 `/api/project-versions/:vid/board` |
| report | `versionSummariesApi.get(vid)` + `versionsApi.board(vid)` + `tasksApi.list({ids: payload.bug_ids})` | summary 拿指标，board 拿需求列表，tasks 拿 bug 明细 |
| cases | `versionsApi.listCases(vid, {filter})` | M4 新 `/api/project-versions/:vid/cases` |
| archive | `versionsApi.get(pid, vid)` + `versionSummariesApi.get(vid)` | 复用现有 + summary |

### 4.3 文件结构

```
frontend/src/pages/versions/
├── VersionBoardPage.tsx           ← 改：变成 4-tab shell + version header
├── tabs/
│   ├── BoardTab.tsx               ← 新：原 VersionBoardPage 主体迁过来
│   ├── ReportTab.tsx              ← 新
│   ├── CasesTab.tsx               ← 新
│   └── ArchiveTab.tsx             ← 新
└── _print.css                     ← 新：@media print 隐藏 chrome

server/api/
├── project_versions.py            ← 改：加 list_version_cases 路由
└── tasks.py                       ← 改：list 路由加 ids query 参数

frontend/src/lib/api.ts            ← 改：versionsApi.listCases + tasksApi.list ids 入参
frontend/src/types/domain.ts       ← 改：加 VersionCase 类型；TaskListParams 加 ids
frontend/src/components/AppLayout.tsx  ← 改：顶部 header 加 data-app-header
frontend/src/pages/workspace/PmWorkspace.tsx  ← 改：milestone widget 加"报告"链接
```

---

## 5. 后端设计

### 5.1 新增：`GET /api/project-versions/:vid/cases`

**入参：**
- 路径：`vid: int`
- query: `module_id?: int`、`case_type?: str`、`status?: str`（多值逗号分隔，如 `failed,broken`）

**返回：**
```json
{
  "status": "success",
  "data": {
    "items": [
      {
        "id": 12,
        "name": "登录-账号密码-成功",
        "case_type": "web",
        "module_id": 7,
        "module_name": "用户中心 / 登录",
        "sort_order": 0,
        "latest_run": {
          "status": "passed",
          "report_id": 89,
          "executed_at": "2026-05-07T12:34:56"
        }
      }
    ],
    "total": 1
  }
}
```

**实现要点：**
- 主 query：`TestCase.version_id == vid`，可选叠加 `module_id` / `case_type`
- join `Module` 拿 `module_name`
- 最近一次执行：参考 `functional_cases.py` 里 `_latest_runs_map` 的写法（按 case_id group + max(report_id) → join TestStepReport 拿 status/executed_at）；该 helper 是私有，本路由复制一份成同文件的私有 helper，**不限 case_type**（api/web/app 都要）
- `status` 过滤在 Python 端做（latest_run 是聚合，不能直接 SQL where）
- 返回时 `total` = 过滤后总数（与 functional_cases 行为一致）

### 5.2 扩展：`GET /api/tasks` 加 `ids` query

```python
@router.get("")
def list_tasks(
    db: DBDep,
    # ... 已有参数
    ids: Optional[str] = Query(None, description="按主键过滤，多值逗号分隔"),
):
    q = db.session.query(Task)
    # ... 已有过滤
    if ids:
        try:
            id_list = [int(s.strip()) for s in ids.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(400, "ids 必须是整数列表")
        if id_list:
            q = q.filter(Task.id.in_(id_list))
    # ...
```

`ids` 与其它过滤可叠加，按 AND 语义（一般 ReportTab 不会同时传，但保持灵活性）。

### 5.3 不动的端点

`GET /api/version-summaries/:vid` / `POST /:vid/regenerate` / `GET /api/project-versions/:vid/board` 已在 M2/M3 完成，直接复用。

---

## 6. 前端设计

### 6.1 VersionBoardPage（升级成 tab shell）

```tsx
export function VersionBoardPage() {
  const { id: pid, vid } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "board";

  const versionQuery = useQuery({ ... });   // 拿 version 拿 status
  const projectQuery = useQuery({ ... });   // 拿 projectName，给 PDF 文件名用

  const showArchive = ["released", "archived"].includes(versionQuery.data?.status);

  return (
    <div>
      {/* version header（4 tab 共用） */}
      <VersionHeader version={versionQuery.data} project={projectQuery.data} />

      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v })}>
        <TabsList data-version-tabs-header>
          <TabsTrigger value="board">看板</TabsTrigger>
          <TabsTrigger value="report">测试报告</TabsTrigger>
          <TabsTrigger value="cases">用例库</TabsTrigger>
          {showArchive && <TabsTrigger value="archive">归档</TabsTrigger>}
        </TabsList>

        <TabsContent value="board" data-version-tab-content><BoardTab vid={vid} /></TabsContent>
        <TabsContent value="report" data-version-tab-content><ReportTab vid={vid} project={...} version={...} /></TabsContent>
        <TabsContent value="cases" data-version-tab-content><CasesTab vid={vid} /></TabsContent>
        {showArchive && <TabsContent value="archive" data-version-tab-content><ArchiveTab vid={vid} /></TabsContent>}
      </Tabs>
    </div>
  );
}
```

### 6.2 BoardTab

把 M3 现有 `VersionBoardPage` 主体（4 列需求看板 + bug 严重度计数 + 重算汇总按钮）原样迁进来，**不改逻辑**。这一步是纯重构。

### 6.3 ReportTab

垂直堆叠 4 个 Card：

**Card 1 — 指标卡片栅格（4 列）：**
- 计数：`total_requirements / total_tasks / total_test_cases`
- 用例执行：`passed / failed / blocked`
- bug：`total_bugs / p0_bugs / p1_bugs / p2_bugs / p3_bugs`
- 计算指标：`first_pass_rate (%) / test_coverage (%) / avg_fix_time_hours (h)`

**Card 2 — bug 明细表：**
- 数据：`tasksApi.list({ ids: summary.payload.bug_ids })`，按 severity asc + created_at desc 排序
- 列：title · severity · assignee_dev_id（解析成 username） · created_at · closed_at · 修复时长（`closed_at - created_at` 显示成 `Xh Ym`，未关闭显示 `—`）
- 行可点击 → `/tasks/:id`

**Card 3 — 失败用例表：**
- 数据：`versionsApi.listCases(vid, { status: 'failed,broken,error' })`
- 列：name · case_type · latest_run.status badge · latest_run.executed_at · 报告链接（→ `/runs/:report_id`）

**Card 4 — 需求覆盖表：**
- 数据：`versionsApi.board(vid).requirements_by_status` 平铺
- 列：title · system_status · business_status · Tasks 数（按 `requirement_id` group `summary.payload.task_ids` 对应 task → req 关系即可；后端 board 接口已返回 task_counts_by_type 但只到 type 维度，不细分到 req，前端可以用 `tasksApi.list({ requirement_id })` 各 req 拉一次 —— 但本表不超过几十行，N+1 可接受；或在 ReportTab 里改成一次 `tasksApi.list({ ids: payload.task_ids })` 然后按 requirement_id group）
- "关联用例数"列 M4 不做（payload 没按 req 切分用例的数据；M5 加）

**顶部按钮组（带 `data-print-hide` 钩子）：**
- "重算汇总" → `versionSummariesApi.regenerate(vid)` + invalidate
- "导出 PDF" → `handleExportPdf()`：临时改 `document.title` 后 `window.print()`

```typescript
function handleExportPdf() {
  const oldTitle = document.title;
  document.title = `${projectName}_${versionName}_报告`;
  window.print();
  setTimeout(() => { document.title = oldTitle; }, 0);
}
```

### 6.4 CasesTab

扁平表 + 3 个过滤器（顶部 toolbar）：

**过滤器：**
- module（Select，选项来自 `modulesApi.listForPicker(projectId)`，默认"全部"用 `__any__` 哨兵）
- case_type（Select，选项 `ALL_CASE_TYPES`）
- status（Select，多值；options：passed / failed / broken / error / skipped / blocked / pending）

**列：** name · module_name · case_type · sort_order · latest_run.status badge · 操作链 → `/runs?case_id=:id`

数据：`versionsApi.listCases(vid, {module_id, case_type, status})`，参数变化时重新拉。

### 6.5 ArchiveTab

仅在 `version.status ∈ {released, archived}` 时挂在 tab header（shell 已门控；这里假设能进来）。

**3 块卡片，垂直堆叠：**

**Card 1 — Summary 摘要：**
- 复用 ReportTab 第 1 块的精简版（只显示 4 个核心数：通过率、bug 总数、覆盖率、修复时长）
- 提示文案 `汇总于 <summary.generated_at>`（不是 version 的归档时间，用 summary 的 generated_at 字段）

**Card 2 — Release Notes（4 段）：**
- 解析 `version.release_notes` JSON：`{sql, config, commands, notes}`
- 4 个子标题 + 4 个 `<pre>` 块，空段显示 `（无）`

**Card 3 — Docs 链接列表：**
- 4 类：`test_plan_items / requirement_doc_items / design_doc_items / ui_prototype_items`
- 每类一组超链接：`<a href={item.url} target="_blank">{item.name}</a>`，列出 `type` 标签
- 任一类为空时整组隐藏

### 6.6 PmWorkspace 链接调整

`pages/workspace/PmWorkspace.tsx` 中 "本迭代里程碑" widget：
- 原 `viewAllHref={\`/projects/${pid}/versions/${vid}/board\`}` 不变（默认进 board tab）
- widget 卡片底部 footer 区域加一行小字链接："📊 测试报告"，链接到 `?tab=report`

---

## 7. 类型扩展

`frontend/src/types/domain.ts`：

```typescript
// 新增
export type VersionCase = {
  id: number;
  name: string;
  case_type: CaseType;
  module_id: number;
  module_name: string;
  sort_order: number;
  latest_run: {
    status: string;       // passed / failed / broken / error / skipped / blocked
    report_id: number;
    executed_at: string;
  } | null;
};

// 已有 TaskListParams 加：
ids?: number[];
```

`frontend/src/lib/api.ts`：

```typescript
// versionsApi 加：
listCases(
  vid: number,
  params?: { module_id?: number; case_type?: CaseType; status?: string },
): Promise<{ items: VersionCase[]; total: number }>

// tasksApi.list 实现里：
if (params.ids?.length) qs.set("ids", params.ids.join(","));
```

---

## 8. 打印样式

新建 `frontend/src/pages/versions/_print.css`，由 `VersionBoardPage` 顶层 `import "./\_print.css"` 引入：

```css
@media print {
  html, body { background: white !important; }

  /* 隐藏 chrome */
  aside, header[data-app-header] { display: none !important; }
  main { overflow: visible !important; }
  [data-version-tabs-header],
  [data-print-hide] { display: none !important; }

  /* tab content 撑满 */
  [data-version-tab-content] {
    height: auto !important;
    overflow: visible !important;
    padding: 0 !important;
  }

  /* 表格不切坏 */
  table, .card { page-break-inside: avoid; break-inside: avoid; }

  /* 链接显示 URL */
  a[href]::after {
    content: " (" attr(href) ")";
    font-size: 0.85em;
    color: #555;
  }

  /* 去阴影 */
  .shadow, .shadow-sm, .shadow-md, .shadow-lg { box-shadow: none !important; }
}
```

**配套 DOM 标记：**

| 组件 | data-attr |
|---|---|
| `AppLayout` 顶部 header | `data-app-header` |
| `VersionBoardPage` Tabs `<TabsList>` | `data-version-tabs-header` |
| `VersionBoardPage` 每个 `<TabsContent>` | `data-version-tab-content` |
| ReportTab 顶部按钮组 div | `data-print-hide` |

---

## 9. 验收清单

### 后端验证

```bash
# 1. /api/project-versions/:vid/cases
curl 'http://127.0.0.1:54351/api/project-versions/1/cases' \
  | jq '.data.items[0]'
# 期望：含 latest_run 字段（首次跑完用例后）

curl 'http://127.0.0.1:54351/api/project-versions/1/cases?case_type=api&status=failed,broken'
# 期望：仅 api 类型 + 最近一次失败的用例

# 2. /api/tasks?ids=1,2,3
curl 'http://127.0.0.1:54351/api/tasks?ids=1,2,3' | jq 'length'
# 期望：≤ 3
```

### 前端验证

1. **4-tab shell**：打开 `/projects/1/versions/1/board` → 默认 board tab；切到其它 tab，URL 同步；刷新保持
2. **archive 门控**：把 version.status 改成 `released` → archive tab 出现；改回 `developing` → 消失
3. **ReportTab 数据**：4 张 Card 数据正确（指标卡、bug 表、失败用例表、需求覆盖表）；点"重算汇总" → 数据刷新；bug 表"修复时长"列：closed_at 非空显示 `Xh Ym`，未关闭显示 `—`
4. **CasesTab 过滤**：单选 module / case_type 联动；status 多选时返回正确子集；点行跳 `/runs?case_id=X`
5. **ArchiveTab**：4 段 release_notes 正确渲染；4 类 docs 链接显示；空类隐藏
6. **PDF 导出**：report tab 点"导出 PDF" → 浏览器打印对话框；预览：NAV 侧栏 / tab 头 / 按钮组都不可见，只剩报告内容；文件名默认 `<projectName>_<versionName>_报告.pdf`；表格不被切坏；链接尾部带 URL 注释
7. **PmWorkspace 链接**：milestone widget 多一个"📊 测试报告"小链接，点击进 `?tab=report`

### 联动验证

- board tab 的 `requirements_by_status` 总数 = report tab 需求覆盖表行数
- summary `bug_ids.length` = report tab bug 表行数
- 跨 tab 切换不丢数据（react-query 缓存命中）

每步通过：

```bash
cd frontend && npm run typecheck && npm run lint
```

---

## 10. 实施 Task 拆分

每 task 一个 commit，沿用 `feat(pm-m4):` 前缀，递增依赖：

| # | Task | 文件 |
|---|---|---|
| 1 | 后端 `/api/project-versions/:vid/cases` | `server/api/project_versions.py` |
| 2 | 后端 `/api/tasks?ids=...` 扩展 | `server/api/tasks.py` |
| 3 | 前端类型 + api client 扩展 | `domain.ts`、`api.ts` |
| 4 | VersionBoardPage 拆出 BoardTab + 4-tab shell（纯重构） | `VersionBoardPage.tsx`、`tabs/BoardTab.tsx` |
| 5 | ReportTab + 顶部"重算/导出"按钮（不含打印 CSS） | `tabs/ReportTab.tsx` |
| 6 | CasesTab + 过滤器 | `tabs/CasesTab.tsx` |
| 7 | ArchiveTab + status 显示门控 | `tabs/ArchiveTab.tsx` |
| 8 | _print.css + DOM `data-attr` + AppLayout 标记 + PmWorkspace 链接 | `_print.css`、`AppLayout.tsx`、`PmWorkspace.tsx` |

实施计划由 writing-plans 接管，按 task-by-task 派发。

---

## 11. Out of Scope（M4 不做，延后）

- 工时统计 / 燃尽图（spec §7）
- AI 自动生成报告草稿（已有 ai_gateway，可后续接）
- 服务端 PDF 渲染（weasyprint/reportlab，需要时再加）
- 跨项目版本对比页
- bug 自动归档
- 用例执行历史 timeline
- 移动端响应式
