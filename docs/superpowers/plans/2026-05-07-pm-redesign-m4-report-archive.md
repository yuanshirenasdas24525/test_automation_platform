# PM Redesign · M4 Report + Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 M3 的 VersionBoardPage 升级成 4-tab shell（board / report / cases / archive），交付版本测试报告页（带浏览器原生打印导出 PDF）+ 用例库回流页 + 版本归档页。

**Architecture:** 复用 M2 已有的 `version_test_summaries` 表 + `versionSummariesApi`；新增 `GET /api/project-versions/:vid/cases` 端点 + `tasksApi.list({ids})` 扩展。前端 `VersionBoardPage` 拆出 `BoardTab` 后用 shadcn Tabs 切换 4 个子组件；用 `@media print` + `data-attr` 钩子让浏览器打印只渲染当前 tab 内容。

**Tech Stack:** FastAPI / SQLAlchemy 2.0 / Pydantic v2（后端） · React 19 + Vite + TypeScript strict + Tailwind + shadcn/ui + react-router-dom + @tanstack/react-query + sonner（前端）

**Spec:** [`docs/superpowers/specs/2026-05-07-pm-redesign-m4-report-archive-design.md`](../specs/2026-05-07-pm-redesign-m4-report-archive-design.md)

**Commit prefix:** `feat(pm-m4):` / `docs(pm-m4):`

**Backend smoke test invariant:** 本仓库**没有传统单测**（CLAUDE.md 已说明）。后端验证靠 `python -m compileall server/` + `curl` 实跑端点；前端验证靠 `npm run typecheck && npm run lint` + 手动 UI smoke。

---

## File Structure

| 路径 | 操作 | 职责 |
|---|---|---|
| `server/api/project_versions.py` | 修改 | 加 `list_version_cases` 路由（GET `/api/project-versions/:vid/cases`）+ 一个私有 `_latest_step_run_map` helper（参考 functional_cases 同名 helper，但 query TestStepReport 而不是 FunctionalCaseRun） |
| `server/api/tasks.py:153-204` | 修改 | `list_tasks` 加 `ids: Optional[str] = Query(None)` 参数，逗号分隔，`Task.id.in_(...)` 过滤 |
| `frontend/src/types/domain.ts` | 修改 | 新增 `VersionCase`；`TaskListFilters` 加 `ids?: number[]`；窄化 `VersionTestSummary.payload` 类型；新增 `VersionReleaseNotes` JSON shape |
| `frontend/src/lib/api.ts` | 修改 | `versionsApi.listCases(vid, params?)` 新方法；`tasksApi.list` 入参支持 `ids` |
| `frontend/src/pages/versions/VersionBoardPage.tsx` | 修改 | 变成 4-tab shell：版本头 + Tabs；body 抽到 `tabs/BoardTab.tsx` |
| `frontend/src/pages/versions/tabs/BoardTab.tsx` | 新建 | M3 原 VersionBoardPage 主体（4 列需求看板 + 任务计数侧栏） |
| `frontend/src/pages/versions/tabs/ReportTab.tsx` | 新建 | 指标卡 + bug 表 + 失败用例表 + 需求覆盖表 + 顶部"重算/导出 PDF"按钮 |
| `frontend/src/pages/versions/tabs/CasesTab.tsx` | 新建 | 扁平用例表 + module / case_type / status 三个过滤器 |
| `frontend/src/pages/versions/tabs/ArchiveTab.tsx` | 新建 | summary 摘要 + release_notes 4 段 + 4 类 docs 链接 |
| `frontend/src/pages/versions/_print.css` | 新建 | `@media print` 样式：隐藏 NAV 侧栏 / tab header / 按钮组 |
| `frontend/src/components/AppLayout.tsx` | 修改 | 顶部 `<header>` 加 `data-app-header` 属性 |
| `frontend/src/pages/workspace/PmWorkspace.tsx` | 修改 | "本迭代里程碑" widget 加一个"📊 测试报告"小链接 → `?tab=report` |

---

## Task 1: 后端 `GET /api/project-versions/:vid/cases`

**Files:**
- Modify: `server/api/project_versions.py`

- [ ] **Step 1: 添加 `_latest_step_run_map` 私有 helper**

打开 `server/api/project_versions.py`，在文件末尾的 `_parse_dt` 函数之前（即 `# ---------------------------------------------------------------------------\n# 工具` 注释块上方）插入以下代码块：

```python
# ---------------------------------------------------------------------------
# 按版本列用例（M4）
# ---------------------------------------------------------------------------
def _latest_step_run_map(db, case_ids: list[int]) -> dict[int, dict]:
    """一次拿一组自动化用例的"最近一次执行结果"。

    实现：先 GROUP BY case_id 取 max(report_id)，再 join TestStepReport
    取该 report 下该 case 的代表性 status —— 任一 step 是 failed/error/broken
    则该 case 算 failed；否则按现有 step 的 status 取 max（passed > skipped）。
    简化：直接取该 (case_id, report_id) 下所有 step status 的"最差"那个。
    """
    if not case_ids:
        return {}
    from sqlalchemy import func as sa_func
    from database.models import TestStepReport

    latest_sq = (
        db.session.query(
            TestStepReport.case_id.label("cid"),
            sa_func.max(TestStepReport.report_id).label("rid"),
        )
        .filter(TestStepReport.case_id.in_(case_ids))
        .group_by(TestStepReport.case_id)
        .subquery()
    )
    rows = (
        db.session.query(TestStepReport)
        .join(
            latest_sq,
            (TestStepReport.case_id == latest_sq.c.cid)
            & (TestStepReport.report_id == latest_sq.c.rid),
        )
        .all()
    )

    # 把每个 case 在该 report 下的若干 step status 折成单个 status
    by_case: dict[int, list[TestStepReport]] = {}
    for r in rows:
        by_case.setdefault(r.case_id, []).append(r)

    def _aggregate(steps: list) -> str:
        statuses = [s.status or "" for s in steps]
        # 优先级：error > failed > broken > skipped > passed
        for bad in ("error", "failed", "broken"):
            if bad in statuses:
                return bad
        if "skipped" in statuses and not any(s == "passed" for s in statuses):
            return "skipped"
        if "passed" in statuses:
            return "passed"
        return statuses[0] if statuses else "pending"

    out: dict[int, dict] = {}
    for cid, steps in by_case.items():
        # report_id / 最近时间从任一 step 取（同一 report 的 step 时间相近）
        first = steps[0]
        out[cid] = {
            "status": _aggregate(steps),
            "report_id": first.report_id,
            "executed_at": first.create_time.isoformat() if first.create_time else None,
        }
    return out
```

- [ ] **Step 2: 添加 `list_version_cases` 路由**

紧接着 `_latest_step_run_map` 之后添加路由：

```python
@router.get("/project-versions/{version_id}/cases")
def list_version_cases(
    version_id: int,
    db: DBDep,
    module_id: Optional[int] = Query(None),
    case_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="多值逗号分隔；可包含 'pending' 表示从未执行"),
):
    """列本版本绑定的所有自动化用例（含 functional），跨模块、扁平。"""
    from database.models import TestCase

    v = db.session.query(ProjectVersion).filter(ProjectVersion.id == version_id).first()
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")

    q = db.session.query(TestCase).filter(TestCase.version_id == version_id)
    if module_id is not None:
        q = q.filter(TestCase.module_id == module_id)
    if case_type:
        q = q.filter(TestCase.case_type == case_type)

    cases = q.order_by(TestCase.sort_order.asc(), TestCase.id.asc()).all()
    if not cases:
        return {"status": "success", "data": {"items": [], "total": 0}}

    # 拿模块名（避免 N+1）
    mod_ids = list({c.module_id for c in cases if c.module_id is not None})
    mod_name_map = {
        m.id: m.name for m in db.session.query(Module).filter(Module.id.in_(mod_ids)).all()
    }

    latest_map = _latest_step_run_map(db, [c.id for c in cases])

    wanted: Optional[set[str]] = None
    if status:
        wanted = {s.strip().lower() for s in status.split(",") if s.strip()}

    items: list[dict] = []
    for c in cases:
        latest = latest_map.get(c.id)
        current_status = (latest or {}).get("status") or "pending"
        if wanted is not None and current_status not in wanted:
            continue
        items.append({
            "id": c.id,
            "name": c.name,
            "case_type": c.case_type,
            "module_id": c.module_id,
            "module_name": mod_name_map.get(c.module_id, ""),
            "sort_order": c.sort_order,
            "latest_run": latest,  # None or {status, report_id, executed_at}
        })

    return {"status": "success", "data": {"items": items, "total": len(items)}}
```

- [ ] **Step 3: 编译检查**

```bash
cd /Users/Apple/Documents/test_automation_platform && python -m compileall server/api/project_versions.py
```
Expected: `Compiling 'server/api/project_versions.py'...` 无 SyntaxError。

- [ ] **Step 4: 启动后端冒烟（手动）**

```bash
cd /Users/Apple/Documents/test_automation_platform && python server/main.py &
sleep 3
curl -s 'http://127.0.0.1:54351/api/project-versions/1/cases' | python -m json.tool | head -30
kill %1
```
Expected: `{"status": "success", "data": {"items": [...], "total": N}}`，每个 item 含 `id / name / case_type / module_id / module_name / sort_order / latest_run`。`latest_run` 为 `null` 或 `{status, report_id, executed_at}`。如果 version_id=1 没用例，items 为 `[]`、total 为 0。

- [ ] **Step 5: 过滤参数冒烟**

```bash
curl -s 'http://127.0.0.1:54351/api/project-versions/1/cases?case_type=api&status=failed,broken' | python -m json.tool | head -10
```
Expected: 仅返回 case_type=api 且 latest_run.status ∈ {failed, broken} 的 items。

- [ ] **Step 6: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add server/api/project_versions.py
git commit -m "$(cat <<'EOF'
feat(pm-m4): /api/project-versions/:vid/cases endpoint

按版本列出绑定的自动化用例，附带每条用例最近一次执行的聚合状态
（report_id 取 max，step status 取最差那个）。支持 module_id / case_type
/ status 过滤。给 M4 CasesTab 用例库回流页用。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 后端 `GET /api/tasks?ids=...` 扩展

**Files:**
- Modify: `server/api/tasks.py:153-204`

- [ ] **Step 1: 给 `list_tasks` 加 `ids` 参数**

打开 `server/api/tasks.py`，找到 `def list_tasks(...)`（第 154 行附近）。在 `parent_task_id: Optional[int] = Query(None),` 那行**下方**插入：

```python
    ids: Optional[str] = Query(None, description="按主键过滤，多值逗号分隔；与其它过滤 AND"),
```

然后在函数体内（在 `if parent_task_id is not None:` 之后、`rows = query.order_by(...)` 之前）插入：

```python
    if ids:
        try:
            id_list = [int(s.strip()) for s in ids.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="ids 必须是整数列表")
        if id_list:
            query = query.filter(Task.id.in_(id_list))
```

- [ ] **Step 2: 编译检查**

```bash
cd /Users/Apple/Documents/test_automation_platform && python -m compileall server/api/tasks.py
```
Expected: 无 SyntaxError。

- [ ] **Step 3: 冒烟**

```bash
cd /Users/Apple/Documents/test_automation_platform && python server/main.py &
sleep 3
curl -s 'http://127.0.0.1:54351/api/tasks?ids=1,2,3' | python -m json.tool | head -20
kill %1
```
Expected: data 是 array，长度 ≤ 3，每个元素 `id ∈ {1,2,3}`（如果这些 task 存在）。

- [ ] **Step 4: 边界冒烟**

```bash
cd /Users/Apple/Documents/test_automation_platform && python server/main.py &
sleep 3
# 非法 ids：返回 400
curl -s -o /dev/null -w "%{http_code}\n" 'http://127.0.0.1:54351/api/tasks?ids=abc'
# 空 ids：等同没传，全量
curl -s 'http://127.0.0.1:54351/api/tasks?ids=' | python -c "import json,sys; print(len(json.load(sys.stdin).get('data') or []))"
kill %1
```
Expected: 400；非空数字。

- [ ] **Step 5: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add server/api/tasks.py
git commit -m "$(cat <<'EOF'
feat(pm-m4): tasks list endpoint accepts ?ids=1,2,3

ReportTab 拿到 summary.payload.bug_ids 后一次性拉回所有 bug task，
避免 N+1。与现有过滤参数 AND 叠加。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 前端类型 + api client 扩展

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 加 `VersionCase` + `VersionReleaseNotes` 类型**

打开 `frontend/src/types/domain.ts`，在 `VersionTestSummary` interface（第 678 行附近）**之后**追加：

```typescript
/** GET /api/project-versions/{vid}/cases 返回的 item。 */
export interface VersionCase {
  id: number;
  name: string;
  case_type: CaseType;
  module_id: number;
  module_name: string;
  sort_order: number;
  latest_run: {
    status: string; // passed | failed | broken | error | skipped | pending
    report_id: number;
    executed_at: string | null;
  } | null;
}

/** ProjectVersion.release_notes 是 JSON 字符串，结构如下；ArchiveTab 解析用。
 *  解析失败时降级把整段当 notes 显示。 */
export interface VersionReleaseNotes {
  sql: string;
  config: string;
  commands: string;
  notes: string;
}
```

- [ ] **Step 2: 窄化 `VersionTestSummary.payload`**

仍在 `domain.ts`，把 `VersionTestSummary` 的 `payload: Record<string, unknown>;` 改成：

```typescript
  payload: {
    requirement_ids?: number[];
    task_ids?: number[];
    bug_ids?: number[];
    test_case_ids?: number[];
  };
```

- [ ] **Step 3: 给 `TaskListFilters` 加 `ids`**

仍在 `domain.ts`，找到 `interface TaskListFilters`（第 655 行附近），在 `parent_task_id?: number;` 行下方加：

```typescript
  ids?: number[];
```

- [ ] **Step 4: import VersionCase 进 api.ts**

打开 `frontend/src/lib/api.ts`，找到顶部 `import type { ... } from "@/types/domain"` 块，在其中**按字母序**插入：

```typescript
  VersionCase,
```

（紧挨在 `VersionBoard` 之后即可）。

- [ ] **Step 5: 在 `versionsApi` 加 `listCases`**

仍在 `api.ts`，找到 `versionsApi.board(...)` 方法（第 966 行附近），在它**之后**、闭合 `}` 之前添加：

```typescript
  /** 按版本列绑定的自动化用例 + 每条最近一次执行状态。M4 CasesTab 用。 */
  listCases(
    versionId: number,
    params?: { module_id?: number; case_type?: CaseType; status?: string },
  ) {
    const qs = new URLSearchParams();
    if (params?.module_id !== undefined) qs.set("module_id", String(params.module_id));
    if (params?.case_type) qs.set("case_type", params.case_type);
    if (params?.status) qs.set("status", params.status);
    const search = qs.toString();
    return request<{ items: VersionCase[]; total: number }>(
      `/api/project-versions/${versionId}/cases${search ? `?${search}` : ""}`,
    );
  },
```

- [ ] **Step 6: 在 `tasksApi.list` 处理 `ids`**

仍在 `api.ts`，找到 `tasksApi.list(...)` 方法（第 1024 行附近）。在 `if (filters.parent_task_id !== undefined) ... ;` 行下方添加：

```typescript
    if (filters.ids?.length) qs.set("ids", filters.ids.join(","));
```

- [ ] **Step 7: 类型 + lint 检查**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run typecheck && npm run lint
```
Expected: 两个命令都返回 0；没有红色错误。

- [ ] **Step 8: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add frontend/src/types/domain.ts frontend/src/lib/api.ts
git commit -m "$(cat <<'EOF'
feat(pm-m4): types + api client for version cases / tasks ids filter

新增 VersionCase / VersionReleaseNotes 类型；窄化 VersionTestSummary.payload；
TaskListFilters.ids 支持。versionsApi.listCases、tasksApi.list({ids}) 接入。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: VersionBoardPage 拆出 BoardTab + 4-tab shell

**Files:**
- Create: `frontend/src/pages/versions/tabs/BoardTab.tsx`
- Modify: `frontend/src/pages/versions/VersionBoardPage.tsx`

> 这一步**只做重构**：把现有 board 主体迁到 BoardTab，外层 VersionBoardPage 改成 Tabs 容器。Report/Cases/Archive tab 内容下一 task 起逐步填。Tabs 的"加载中"、"未实现"占位先顶住。

- [ ] **Step 1: 新建 BoardTab，迁移现有 board 主体**

新建 `frontend/src/pages/versions/tabs/BoardTab.tsx`（绝对路径 `/Users/Apple/Documents/test_automation_platform/frontend/src/pages/versions/tabs/BoardTab.tsx`），内容：

```tsx
/**
 * 看板 tab：4 列 system_status 需求分桶 + 任务计数侧栏。
 * 由 VersionBoardPage 在 ?tab=board（默认）时渲染。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { versionSummariesApi, versionsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  ALL_BUG_SEVERITIES,
  ALL_REQUIREMENT_SYSTEM_STATUSES,
  ALL_TASK_TYPES,
} from "@/types/domain";
import type {
  Requirement,
  RequirementSystemStatus,
  TaskType,
  VersionTaskBucket,
} from "@/types/domain";

const STATUS_LABELS: Record<RequirementSystemStatus, string> = {
  approved: "已立项",
  developing: "开发中",
  testing: "测试中",
  ready_to_release: "待发版",
};

const STATUS_TONES: Record<RequirementSystemStatus, string> = {
  approved: "border-blue-200 bg-blue-50/40",
  developing: "border-amber-200 bg-amber-50/40",
  testing: "border-violet-200 bg-violet-50/40",
  ready_to_release: "border-emerald-200 bg-emerald-50/40",
};

const TASK_TYPE_LABELS: Record<TaskType, string> = {
  dev: "开发任务",
  test: "测试任务",
  ui_review: "走查任务",
  bug: "Bug",
};

export function BoardTab({ projectId, versionId }: { projectId: number; versionId: number }) {
  const queryClient = useQueryClient();

  const boardQuery = useQuery({
    queryKey: ["version-board", versionId],
    queryFn: () => versionsApi.board(versionId),
    enabled: !Number.isNaN(versionId),
  });

  const regenerateMutation = useMutation({
    mutationFn: () => versionSummariesApi.regenerate(versionId),
    onSuccess: () => {
      toast.success("已重算汇总");
      queryClient.invalidateQueries({ queryKey: ["version-board", versionId] });
      queryClient.invalidateQueries({ queryKey: ["version-summary", versionId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  if (boardQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (boardQuery.isError || !boardQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载失败：
        {(boardQuery.error as Error | undefined)?.message ?? "version 不存在"}
      </div>
    );
  }

  const board = boardQuery.data;

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-end" data-print-hide>
        <Button
          size="sm"
          variant="outline"
          disabled={regenerateMutation.isPending}
          onClick={() => regenerateMutation.mutate()}
        >
          <RefreshCw
            className={cn(
              "mr-1 h-3.5 w-3.5",
              regenerateMutation.isPending && "animate-spin",
            )}
          />
          重算汇总
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {ALL_REQUIREMENT_SYSTEM_STATUSES.map((status) => {
            const items = board.requirements_by_status[status] ?? [];
            return (
              <RequirementColumn
                key={status}
                status={status}
                items={items}
                projectId={projectId}
              />
            );
          })}
        </div>

        <div className="space-y-3">
          {ALL_TASK_TYPES.map((type) => {
            const bucket = board.task_counts_by_type[type] ?? {
              total: 0,
              by_status: {},
            } as VersionTaskBucket;
            return <TaskTypeCard key={type} type={type} bucket={bucket} />;
          })}
          {board.requirements_by_status.unassigned &&
          board.requirements_by_status.unassigned.length > 0 ? (
            <Card className="border-dashed">
              <CardContent className="p-3 text-xs text-muted-foreground">
                还有 {board.requirements_by_status.unassigned.length} 条需求没设
                system_status，已挂在 unassigned 桶里。
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function RequirementColumn({
  status,
  items,
  projectId,
}: {
  status: RequirementSystemStatus;
  items: Requirement[];
  projectId: number;
}) {
  return (
    <div
      className={cn(
        "flex min-h-[140px] flex-col rounded-md border p-3",
        STATUS_TONES[status],
      )}
    >
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-semibold">{STATUS_LABELS[status]}</span>
        <span className="rounded bg-background/70 px-1.5 py-0.5 text-xs text-muted-foreground">
          {items.length}
        </span>
      </div>
      <div className="space-y-2">
        {items.length === 0 ? (
          <div className="text-xs text-muted-foreground">空</div>
        ) : (
          items.map((req) => <RequirementCard key={req.id} req={req} projectId={projectId} />)
        )}
      </div>
    </div>
  );
}

function RequirementCard({
  req,
  projectId,
}: {
  req: Requirement;
  projectId: number;
}) {
  return (
    <a
      href={`/projects/${projectId}/requirements`}
      className="block rounded border bg-background p-2 text-xs shadow-sm hover:border-primary/40"
    >
      <div className="line-clamp-2 font-medium">{req.title}</div>
      {req.description ? (
        <div className="mt-1 line-clamp-2 text-muted-foreground">
          {req.description}
        </div>
      ) : null}
      {req.business_status ? (
        <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          biz: {req.business_status}
        </div>
      ) : null}
    </a>
  );
}

function TaskTypeCard({
  type,
  bucket,
}: {
  type: TaskType;
  bucket: VersionTaskBucket;
}) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="flex items-center justify-between text-sm">
          <span className="font-semibold">{TASK_TYPE_LABELS[type]}</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            {bucket.total}
          </span>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1 text-[11px]">
          {Object.entries(bucket.by_status).map(([status, count]) => (
            <div key={status} className="flex items-center justify-between">
              <span className="text-muted-foreground">{status}</span>
              <span>{count}</span>
            </div>
          ))}
        </div>
        {type === "bug" && bucket.by_severity ? (
          <div className="mt-2 border-t pt-2 text-[11px]">
            <div className="mb-1 text-muted-foreground">严重度</div>
            <div className="grid grid-cols-4 gap-1 text-center">
              {ALL_BUG_SEVERITIES.map((sev) => (
                <div
                  key={sev}
                  className="rounded bg-muted px-1 py-0.5"
                  title={`${sev}: ${bucket.by_severity?.[sev] ?? 0}`}
                >
                  <div className="text-[10px] text-muted-foreground">{sev}</div>
                  <div className="font-semibold">
                    {bucket.by_severity?.[sev] ?? 0}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: 把 VersionBoardPage 改成 4-tab shell**

完整覆盖 `frontend/src/pages/versions/VersionBoardPage.tsx`：

```tsx
/**
 * /projects/:pid/versions/:vid/board —— 版本视图中心（4 tab）。
 *
 * Tabs：
 *   - board    （默认） 4 列需求看板 + 任务计数
 *   - report   测试报告 + 一键导出 PDF
 *   - cases    本版本绑定用例的扁平列表（用例库回流）
 *   - archive  归档页（仅 status ∈ {released, archived} 时显示）
 *
 * Tab 状态用 useSearchParams('tab')；URL 即真理。
 */
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { projectsApi, versionsApi } from "@/lib/api";

import { ArchiveTab } from "./tabs/ArchiveTab";
import { BoardTab } from "./tabs/BoardTab";
import { CasesTab } from "./tabs/CasesTab";
import { ReportTab } from "./tabs/ReportTab";

import "./_print.css";

const ARCHIVED_STATUSES = new Set(["released", "archived"]);

export function VersionBoardPage() {
  const params = useParams<{ id: string; vid: string }>();
  const projectId = Number(params.id);
  const versionId = Number(params.vid);
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") || "board";

  const versionQuery = useQuery({
    queryKey: ["version", projectId, versionId],
    queryFn: () => versionsApi.get(projectId, versionId),
    enabled: !Number.isNaN(versionId) && !Number.isNaN(projectId),
  });

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: !Number.isNaN(projectId),
  });

  if (Number.isNaN(versionId) || Number.isNaN(projectId)) {
    return <div className="p-6 text-sm text-destructive">非法 url 参数。</div>;
  }
  if (versionQuery.isLoading || projectQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (versionQuery.isError || !versionQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载失败：
        {(versionQuery.error as Error | undefined)?.message ?? "版本不存在"}
      </div>
    );
  }

  const version = versionQuery.data;
  const project = projectQuery.data;
  const showArchive = ARCHIVED_STATUSES.has(version.status);

  const handleTabChange = (next: string) => {
    const nextSearch = new URLSearchParams(search);
    nextSearch.set("tab", next);
    setSearch(nextSearch, { replace: true });
  };

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between gap-2" data-print-hide>
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
          <ArrowLeft className="mr-1 h-3.5 w-3.5" /> 返回
        </Button>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 p-4 text-sm">
          <div>
            <div className="text-xs text-muted-foreground">项目</div>
            <div className="font-semibold">{project?.name ?? `#${projectId}`}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">版本</div>
            <div className="font-semibold">
              {version.display_name || version.version_name}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">状态</div>
            <div className="font-medium">{version.status}</div>
          </div>
          {version.planned_start_at ? (
            <div>
              <div className="text-xs text-muted-foreground">计划开始</div>
              <div>{formatDate(version.planned_start_at)}</div>
            </div>
          ) : null}
          {version.planned_end_at ? (
            <div>
              <div className="text-xs text-muted-foreground">计划结束</div>
              <div>{formatDate(version.planned_end_at)}</div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={handleTabChange}>
        <TabsList data-version-tabs-header>
          <TabsTrigger value="board">看板</TabsTrigger>
          <TabsTrigger value="report">测试报告</TabsTrigger>
          <TabsTrigger value="cases">用例库</TabsTrigger>
          {showArchive ? <TabsTrigger value="archive">归档</TabsTrigger> : null}
        </TabsList>

        <TabsContent value="board" data-version-tab-content>
          <BoardTab projectId={projectId} versionId={versionId} />
        </TabsContent>
        <TabsContent value="report" data-version-tab-content>
          <ReportTab
            projectId={projectId}
            versionId={versionId}
            projectName={project?.name ?? `项目${projectId}`}
            versionName={version.display_name || version.version_name}
          />
        </TabsContent>
        <TabsContent value="cases" data-version-tab-content>
          <CasesTab projectId={projectId} versionId={versionId} />
        </TabsContent>
        {showArchive ? (
          <TabsContent value="archive" data-version-tab-content>
            <ArchiveTab projectId={projectId} versionId={versionId} />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  );
}

function formatDate(s: string | null | undefined) {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleDateString();
  } catch {
    return s;
  }
}
```

- [ ] **Step 3: 创建占位 ReportTab / CasesTab / ArchiveTab + _print.css**

VersionBoardPage 引用了 4 个 tab 子组件 + `_print.css` —— 这些后面 task 才填实，但 typecheck 现在必须通过。先建占位。

新建 `frontend/src/pages/versions/_print.css`（暂时空内容即可）：
```css
/* M4 print styles. Filled in Task 8. */
```

新建 `frontend/src/pages/versions/tabs/ReportTab.tsx`：
```tsx
export function ReportTab(_props: {
  projectId: number;
  versionId: number;
  projectName: string;
  versionName: string;
}) {
  return (
    <div className="p-6 text-sm text-muted-foreground">
      测试报告 tab（M4 task 5 实施）。
    </div>
  );
}
```

新建 `frontend/src/pages/versions/tabs/CasesTab.tsx`：
```tsx
export function CasesTab(_props: { projectId: number; versionId: number }) {
  return (
    <div className="p-6 text-sm text-muted-foreground">
      用例库 tab（M4 task 6 实施）。
    </div>
  );
}
```

新建 `frontend/src/pages/versions/tabs/ArchiveTab.tsx`：
```tsx
export function ArchiveTab(_props: { projectId: number; versionId: number }) {
  return (
    <div className="p-6 text-sm text-muted-foreground">
      归档 tab（M4 task 7 实施）。
    </div>
  );
}
```

- [ ] **Step 4: typecheck + lint**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run typecheck && npm run lint
```
Expected: 全绿。

- [ ] **Step 5: 手动 UI 冒烟**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run dev &
sleep 3
```
浏览器打开 `http://localhost:5173/projects/1/versions/1/board`：
- 看到 4 个 tab（archive 仅当 version.status ∈ {released,archived} 时出现）
- 默认进 `board` tab，渲染 4 列需求看板 + 任务计数侧栏（与 M3 行为一致）
- 切到 report / cases tab → URL 变成 `?tab=report`/`?tab=cases`，内容是占位 placeholder
- 刷新页面 → tab 选择保持

完事 `kill %1`。

- [ ] **Step 6: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add frontend/src/pages/versions/VersionBoardPage.tsx \
        frontend/src/pages/versions/tabs/BoardTab.tsx \
        frontend/src/pages/versions/tabs/ReportTab.tsx \
        frontend/src/pages/versions/tabs/CasesTab.tsx \
        frontend/src/pages/versions/tabs/ArchiveTab.tsx \
        frontend/src/pages/versions/_print.css
git commit -m "$(cat <<'EOF'
feat(pm-m4): VersionBoardPage 4-tab shell + BoardTab refactor

把 M3 现有 board 主体迁到 BoardTab；外层用 shadcn Tabs 切 board / report
/ cases / archive 4 个 tab。tab 状态走 ?tab=xxx querystring。
report/cases/archive 先放占位，后续 task 逐个填实。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: ReportTab（指标卡 + bug 表 + 失败用例表 + 需求覆盖表）

**Files:**
- Modify: `frontend/src/pages/versions/tabs/ReportTab.tsx`

- [ ] **Step 1: 完整覆盖 ReportTab**

把 task 4 创建的占位 `ReportTab.tsx` 完整替换为：

```tsx
/**
 * 测试报告 tab：4 张 Card 垂直堆叠 + 顶部"重算汇总 / 导出 PDF"按钮。
 *
 * 数据来源：
 *  - version-summary    指标 + payload（含 bug_ids）
 *  - version-board      requirements_by_status 平铺成需求覆盖表
 *  - tasks?ids=bug_ids  bug 明细（一次拉回，避免 N+1）
 *  - version cases      失败用例表（status=failed,broken,error）
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { tasksApi, versionSummariesApi, versionsApi } from "@/lib/api";
import type { Requirement, Task, VersionCase } from "@/types/domain";

export function ReportTab({
  projectId,
  versionId,
  projectName,
  versionName,
}: {
  projectId: number;
  versionId: number;
  projectName: string;
  versionName: string;
}) {
  void projectId;
  const queryClient = useQueryClient();

  const summaryQuery = useQuery({
    queryKey: ["version-summary", versionId],
    queryFn: () => versionSummariesApi.get(versionId),
  });
  const boardQuery = useQuery({
    queryKey: ["version-board", versionId],
    queryFn: () => versionsApi.board(versionId),
  });
  const failedCasesQuery = useQuery({
    queryKey: ["version-cases", versionId, "failed"],
    queryFn: () =>
      versionsApi.listCases(versionId, { status: "failed,broken,error" }),
  });

  const bugIds = summaryQuery.data?.payload.bug_ids ?? [];
  const bugsQuery = useQuery({
    queryKey: ["tasks", "by-ids", bugIds.join(",")],
    queryFn: () => tasksApi.list({ ids: bugIds }),
    enabled: bugIds.length > 0,
  });

  const regenerateMutation = useMutation({
    mutationFn: () => versionSummariesApi.regenerate(versionId),
    onSuccess: () => {
      toast.success("已重算汇总");
      queryClient.invalidateQueries({ queryKey: ["version-summary", versionId] });
      queryClient.invalidateQueries({ queryKey: ["version-board", versionId] });
      queryClient.invalidateQueries({ queryKey: ["tasks", "by-ids"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  function handleExportPdf() {
    const oldTitle = document.title;
    document.title = `${projectName}_${versionName}_报告`;
    window.print();
    setTimeout(() => {
      document.title = oldTitle;
    }, 0);
  }

  if (summaryQuery.isLoading || boardQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (summaryQuery.isError || !summaryQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载汇总失败：
        {(summaryQuery.error as Error | undefined)?.message ?? "未知错误"}
      </div>
    );
  }

  const summary = summaryQuery.data;
  const reqs: Requirement[] = boardQuery.data
    ? Object.values(boardQuery.data.requirements_by_status).flat()
    : [];
  const bugs: Task[] = bugsQuery.data ?? [];
  const failedCases: VersionCase[] = failedCasesQuery.data?.items ?? [];

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-end gap-2" data-print-hide>
        <Button
          size="sm"
          variant="outline"
          disabled={regenerateMutation.isPending}
          onClick={() => regenerateMutation.mutate()}
        >
          <RefreshCw
            className={`mr-1 h-3.5 w-3.5 ${
              regenerateMutation.isPending ? "animate-spin" : ""
            }`}
          />
          重算汇总
        </Button>
        <Button size="sm" onClick={handleExportPdf}>
          <Download className="mr-1 h-3.5 w-3.5" />
          导出 PDF
        </Button>
      </div>

      <MetricsCard summary={summary} />
      <BugTableCard bugs={bugs} loading={bugsQuery.isLoading} />
      <FailedCasesCard cases={failedCases} loading={failedCasesQuery.isLoading} />
      <RequirementCoverageCard reqs={reqs} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card 1：指标
// ---------------------------------------------------------------------------
function MetricsCard({
  summary,
}: {
  summary: import("@/types/domain").VersionTestSummary;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">关键指标</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricGroup
          title="计数"
          rows={[
            ["需求", summary.total_requirements],
            ["任务", summary.total_tasks],
            ["用例", summary.total_test_cases],
          ]}
        />
        <MetricGroup
          title="用例执行"
          rows={[
            ["通过", summary.passed],
            ["失败", summary.failed],
            ["阻塞", summary.blocked],
          ]}
        />
        <MetricGroup
          title="Bug"
          rows={[
            ["总数", summary.total_bugs],
            ["P0", summary.p0_bugs],
            ["P1", summary.p1_bugs],
            ["P2", summary.p2_bugs],
            ["P3", summary.p3_bugs],
          ]}
        />
        <MetricGroup
          title="质量指标"
          rows={[
            ["首次通过率", formatPct(summary.first_pass_rate)],
            ["覆盖率", formatPct(summary.test_coverage)],
            ["平均修复时长", formatHours(summary.avg_fix_time_hours)],
          ]}
        />
      </CardContent>
    </Card>
  );
}

function MetricGroup({
  title,
  rows,
}: {
  title: string;
  rows: [string, number | string | null | undefined][];
}) {
  return (
    <div className="rounded border bg-card p-3">
      <div className="mb-2 text-xs font-semibold text-muted-foreground">{title}</div>
      <div className="space-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between">
            <span className="text-muted-foreground">{k}</span>
            <span className="font-medium">{v ?? "—"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card 2：Bug 表
// ---------------------------------------------------------------------------
function BugTableCard({ bugs, loading }: { bugs: Task[]; loading: boolean }) {
  const sorted = [...bugs].sort((a, b) => {
    const sev = (a.severity ?? "Z").localeCompare(b.severity ?? "Z");
    if (sev !== 0) return sev;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          Bug 明细
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            ({sorted.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {loading ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : sorted.length === 0 ? (
          <div className="text-sm text-muted-foreground">没有 bug。</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">标题</th>
                <th className="py-2 pr-2">严重度</th>
                <th className="py-2 pr-2">负责人(dev_id)</th>
                <th className="py-2 pr-2">创建</th>
                <th className="py-2 pr-2">关闭</th>
                <th className="py-2 pr-2">修复时长</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((b) => (
                <tr key={b.id} className="border-b last:border-0">
                  <td className="py-2 pr-2">
                    <a className="hover:underline" href={`/tasks/${b.id}`}>
                      {b.title}
                    </a>
                  </td>
                  <td className="py-2 pr-2">{b.severity ?? "—"}</td>
                  <td className="py-2 pr-2">{b.assignee_dev_id ?? "—"}</td>
                  <td className="py-2 pr-2">{formatDate(b.created_at)}</td>
                  <td className="py-2 pr-2">{formatDate(b.closed_at)}</td>
                  <td className="py-2 pr-2">
                    {formatFixDuration(b.created_at, b.closed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Card 3：失败用例
// ---------------------------------------------------------------------------
function FailedCasesCard({
  cases,
  loading,
}: {
  cases: VersionCase[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          失败用例
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            ({cases.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {loading ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : cases.length === 0 ? (
          <div className="text-sm text-muted-foreground">没有失败用例。</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">用例</th>
                <th className="py-2 pr-2">类型</th>
                <th className="py-2 pr-2">最近一次状态</th>
                <th className="py-2 pr-2">执行时间</th>
                <th className="py-2 pr-2">报告</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="py-2 pr-2">{c.name}</td>
                  <td className="py-2 pr-2">{c.case_type}</td>
                  <td className="py-2 pr-2">{c.latest_run?.status ?? "—"}</td>
                  <td className="py-2 pr-2">
                    {formatDate(c.latest_run?.executed_at)}
                  </td>
                  <td className="py-2 pr-2">
                    {c.latest_run?.report_id ? (
                      <a
                        className="hover:underline"
                        href={`/runs/${c.latest_run.report_id}`}
                      >
                        #{c.latest_run.report_id}
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Card 4：需求覆盖
// ---------------------------------------------------------------------------
function RequirementCoverageCard({ reqs }: { reqs: Requirement[] }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          需求覆盖
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            ({reqs.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {reqs.length === 0 ? (
          <div className="text-sm text-muted-foreground">没有关联需求。</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">需求</th>
                <th className="py-2 pr-2">系统态</th>
                <th className="py-2 pr-2">业务态</th>
              </tr>
            </thead>
            <tbody>
              {reqs.map((r) => (
                <tr key={r.id} className="border-b last:border-0">
                  <td className="py-2 pr-2">{r.title}</td>
                  <td className="py-2 pr-2">{r.system_status ?? "—"}</td>
                  <td className="py-2 pr-2">{r.business_status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function formatPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function formatHours(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)} h`;
}

function formatDate(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

function formatFixDuration(
  createdAt: string | null | undefined,
  closedAt: string | null | undefined,
): string {
  if (!createdAt || !closedAt) return "—";
  try {
    const ms = new Date(closedAt).getTime() - new Date(createdAt).getTime();
    if (ms < 0) return "—";
    const totalMin = Math.round(ms / 60000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  } catch {
    return "—";
  }
}
```

- [ ] **Step 2: typecheck + lint**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run typecheck && npm run lint
```
Expected: 全绿。

- [ ] **Step 3: 手动 UI 冒烟**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run dev &
sleep 3
```
浏览器打开 `http://localhost:5173/projects/1/versions/1/board?tab=report`：
- 4 个 Card 顺序：关键指标 → Bug 明细 → 失败用例 → 需求覆盖
- 指标卡 4 列展示
- Bug 表按 severity asc + created_at desc 排序，修复时长格式 `Xh Ym`
- 失败用例表只显示 `failed/broken/error` 状态用例
- 需求覆盖表展示所有 system_status 桶的合并
- 顶部"重算汇总"点击 → toast + 数据刷新
- 顶部"导出 PDF"点击 → 浏览器打印对话框打开（这一步 _print.css 还没填，预览里 NAV/tab header 仍可见，task 8 修）

`kill %1`。

- [ ] **Step 4: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add frontend/src/pages/versions/tabs/ReportTab.tsx
git commit -m "$(cat <<'EOF'
feat(pm-m4): ReportTab with metrics + bug / failed cases / requirements tables

4 张 Card：指标卡（4 列）+ bug 明细（按 severity 排序 + 修复时长）+ 失败用例
（status=failed,broken,error）+ 需求覆盖表。顶部"重算汇总"和"导出 PDF"
按钮（打印样式 task 8 完善）。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: CasesTab（用例库回流页）

**Files:**
- Modify: `frontend/src/pages/versions/tabs/CasesTab.tsx`

- [ ] **Step 1: 完整覆盖 CasesTab**

```tsx
/**
 * 用例库 tab：本版本绑定的所有自动化用例（跨模块、跨 case_type）。
 *
 * 顶部 toolbar：3 个 Select（module / case_type / status）
 * 主体：扁平表，每行展示 name / module / case_type / 最近一次执行状态
 * 行点击 → 跳转 /runs?case_id=X（已有页面）
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { modulesApi, versionsApi } from "@/lib/api";
import { ALL_CASE_TYPES } from "@/types/domain";
import type { CaseType } from "@/types/domain";

const ANY = "__any__";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "passed", label: "通过" },
  { value: "failed", label: "失败" },
  { value: "broken", label: "broken" },
  { value: "error", label: "error" },
  { value: "skipped", label: "跳过" },
  { value: "pending", label: "未执行" },
];

export function CasesTab({
  projectId,
  versionId,
}: {
  projectId: number;
  versionId: number;
}) {
  const [moduleFilter, setModuleFilter] = useState<string>(ANY);
  const [caseTypeFilter, setCaseTypeFilter] = useState<string>(ANY);
  const [statusFilter, setStatusFilter] = useState<string>(ANY);

  const modulesQuery = useQuery({
    queryKey: ["modules", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: !Number.isNaN(projectId),
  });

  const queryParams = useMemo(() => {
    const p: { module_id?: number; case_type?: CaseType; status?: string } = {};
    if (moduleFilter !== ANY) p.module_id = Number(moduleFilter);
    if (caseTypeFilter !== ANY) p.case_type = caseTypeFilter as CaseType;
    if (statusFilter !== ANY) p.status = statusFilter;
    return p;
  }, [moduleFilter, caseTypeFilter, statusFilter]);

  const casesQuery = useQuery({
    queryKey: ["version-cases", versionId, queryParams],
    queryFn: () => versionsApi.listCases(versionId, queryParams),
    enabled: !Number.isNaN(versionId),
  });

  const items = casesQuery.data?.items ?? [];

  return (
    <div className="space-y-4 p-6">
      <Card data-print-hide>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <FilterBlock label="模块">
            <Select value={moduleFilter} onValueChange={setModuleFilter}>
              <SelectTrigger className="h-9 w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部模块</SelectItem>
                {(modulesQuery.data ?? []).map((m) => (
                  <SelectItem key={m.id} value={String(m.id)}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>
          <FilterBlock label="类型">
            <Select value={caseTypeFilter} onValueChange={setCaseTypeFilter}>
              <SelectTrigger className="h-9 w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部</SelectItem>
                {ALL_CASE_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>
          <FilterBlock label="最近一次状态">
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="h-9 w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部</SelectItem>
                {STATUS_OPTIONS.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>
          <div className="ml-auto text-xs text-muted-foreground">
            共 {casesQuery.data?.total ?? 0} 条
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          {casesQuery.isLoading ? (
            <div className="p-6 text-sm text-muted-foreground">加载中…</div>
          ) : items.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground">
              本版本没有匹配的用例。
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="border-b text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2">名称</th>
                  <th className="px-4 py-2">模块</th>
                  <th className="px-4 py-2">类型</th>
                  <th className="px-4 py-2">最近一次</th>
                  <th className="px-4 py-2">报告</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr
                    key={c.id}
                    className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
                    onClick={() => {
                      window.location.assign(`/runs?case_id=${c.id}`);
                    }}
                  >
                    <td className="px-4 py-2">{c.name}</td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {c.module_name || `#${c.module_id}`}
                    </td>
                    <td className="px-4 py-2">{c.case_type}</td>
                    <td className="px-4 py-2">{c.latest_run?.status ?? "—"}</td>
                    <td className="px-4 py-2">
                      {c.latest_run?.report_id ? (
                        <a
                          className="hover:underline"
                          href={`/runs/${c.latest_run.report_id}`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          #{c.latest_run.report_id}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function FilterBlock({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-[11px] text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}
```

- [ ] **Step 2: typecheck + lint**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run typecheck && npm run lint
```
Expected: 全绿。

- [ ] **Step 3: 手动 UI 冒烟**

启动 dev server，访问 `http://localhost:5173/projects/1/versions/1/board?tab=cases`：
- 三个过滤器联动：选模块 → 表格筛子集；选类型 → 同；选状态 → 同
- "全部 X" 选项使用 `__any__` 哨兵，点击不报 Radix value 错误
- 点行 → 跳转 `/runs?case_id=X`
- 点报告 #N 链接 → 跳转 `/runs/N`，不触发行点击

- [ ] **Step 4: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add frontend/src/pages/versions/tabs/CasesTab.tsx
git commit -m "$(cat <<'EOF'
feat(pm-m4): CasesTab with module / case_type / status filters

本版本绑定用例的扁平列表，跨模块、跨 case_type。三个 Select 过滤器联动；
全选用 __any__ 哨兵兼容 Radix Select。行点击跳 /runs?case_id=X。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: ArchiveTab（summary + release_notes + docs 三件套）

**Files:**
- Modify: `frontend/src/pages/versions/tabs/ArchiveTab.tsx`

- [ ] **Step 1: 完整覆盖 ArchiveTab**

```tsx
/**
 * 归档 tab：仅 version.status ∈ {released, archived} 时由 shell 渲染。
 * 三块卡片：
 *  - Summary 摘要（首次通过率 / bug 总数 / 覆盖率 / 平均修复时长 + generated_at）
 *  - Release Notes 4 段（sql / config / commands / notes）
 *  - Docs 链接：4 类文档（test_plan / requirement_doc / design_doc / ui_prototype）
 */
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { versionSummariesApi, versionsApi } from "@/lib/api";
import type { ProjectVersion, VersionReleaseNotes } from "@/types/domain";

const DOC_GROUPS: Array<{
  key: keyof Pick<
    ProjectVersion,
    "test_plan_items" | "requirement_doc_items" | "design_doc_items" | "ui_prototype_items"
  >;
  label: string;
}> = [
  { key: "test_plan_items", label: "测试计划" },
  { key: "requirement_doc_items", label: "需求文档" },
  { key: "design_doc_items", label: "设计稿" },
  { key: "ui_prototype_items", label: "UI 原型" },
];

export function ArchiveTab({
  projectId,
  versionId,
}: {
  projectId: number;
  versionId: number;
}) {
  const versionQuery = useQuery({
    queryKey: ["version", projectId, versionId],
    queryFn: () => versionsApi.get(projectId, versionId),
  });
  const summaryQuery = useQuery({
    queryKey: ["version-summary", versionId],
    queryFn: () => versionSummariesApi.get(versionId),
  });

  if (versionQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (versionQuery.isError || !versionQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载失败：{(versionQuery.error as Error | undefined)?.message ?? "未知"}
      </div>
    );
  }

  const version = versionQuery.data;
  const summary = summaryQuery.data;
  const notes = parseReleaseNotes(version.release_notes);

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">汇总摘要</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
          <SummaryStat
            label="首次通过率"
            value={formatPct(summary?.first_pass_rate)}
          />
          <SummaryStat label="Bug 总数" value={summary?.total_bugs ?? "—"} />
          <SummaryStat label="覆盖率" value={formatPct(summary?.test_coverage)} />
          <SummaryStat
            label="平均修复时长"
            value={formatHours(summary?.avg_fix_time_hours)}
          />
          {summary?.generated_at ? (
            <div className="col-span-full text-xs text-muted-foreground">
              汇总于 {formatDate(summary.generated_at)}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">发布说明</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {(["sql", "config", "commands", "notes"] as const).map((k) => (
            <div key={k}>
              <div className="mb-1 text-xs font-semibold text-muted-foreground">
                {NOTE_LABELS[k]}
              </div>
              <pre className="whitespace-pre-wrap rounded border bg-muted/30 p-2 text-xs">
                {notes[k] || "（无）"}
              </pre>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">关联文档</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {DOC_GROUPS.map((g) => {
            const items = (version[g.key] ?? []) as Array<{
              id?: number | string;
              name?: string;
              type?: string;
              url?: string;
            }>;
            if (!items.length) return null;
            return (
              <div key={g.key}>
                <div className="mb-1 text-xs font-semibold text-muted-foreground">
                  {g.label}
                </div>
                <ul className="space-y-1">
                  {items.map((item, i) => (
                    <li key={item.id ?? i} className="text-xs">
                      {item.url ? (
                        <a
                          className="text-primary hover:underline"
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {item.name || item.url}
                        </a>
                      ) : (
                        <span>{item.name || "(未命名)"}</span>
                      )}
                      {item.type ? (
                        <span className="ml-2 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                          {item.type}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          {DOC_GROUPS.every(
            (g) => !((version[g.key] ?? []) as unknown[]).length,
          ) ? (
            <div className="text-xs text-muted-foreground">没有挂任何文档。</div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

const NOTE_LABELS: Record<keyof VersionReleaseNotes, string> = {
  sql: "SQL",
  config: "配置变更",
  commands: "常用命令",
  notes: "注意事项",
};

function parseReleaseNotes(raw: string | null | undefined): VersionReleaseNotes {
  const empty: VersionReleaseNotes = { sql: "", config: "", commands: "", notes: "" };
  if (!raw) return empty;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return {
        sql: parsed.sql ?? "",
        config: parsed.config ?? "",
        commands: parsed.commands ?? "",
        notes: parsed.notes ?? "",
      };
    }
  } catch {
    // 旧数据是纯文本，整体当 notes 显示
    return { ...empty, notes: raw };
  }
  return empty;
}

function SummaryStat({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div className="rounded border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function formatPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function formatHours(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)} h`;
}

function formatDate(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}
```

- [ ] **Step 2: typecheck + lint**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run typecheck && npm run lint
```
Expected: 全绿。

- [ ] **Step 3: 手动 UI 冒烟**

后端临时把某个 version 的 `status` 改成 `released`（或者用已是 released 的版本）：

```bash
curl -X PUT 'http://127.0.0.1:54351/api/projects/1/versions/1' \
  -H 'Content-Type: application/json' \
  -d '{"status": "released"}'
```

打开 `http://localhost:5173/projects/1/versions/1/board?tab=archive`：
- shell 顶部的 Tabs 现在多一个"归档" tab
- 点击进入：3 个 Card 顺序：汇总摘要 → 发布说明 → 关联文档
- summary 4 个数；release_notes JSON 解析后 4 段独立 `<pre>`
- docs 列表按类型分组，未挂的类型不显示
- 把 status 改回 `developing` → 归档 tab 消失

- [ ] **Step 4: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add frontend/src/pages/versions/tabs/ArchiveTab.tsx
git commit -m "$(cat <<'EOF'
feat(pm-m4): ArchiveTab summary + release_notes + docs

仅 version.status ∈ {released, archived} 时显示。三 Card：汇总摘要、
4 段 release_notes（兼容旧纯文本数据）、4 类 docs 链接列表。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 打印样式 + AppLayout 标记 + PmWorkspace 链接

**Files:**
- Modify: `frontend/src/pages/versions/_print.css`
- Modify: `frontend/src/components/AppLayout.tsx`
- Modify: `frontend/src/pages/workspace/PmWorkspace.tsx`

- [ ] **Step 1: 填实 _print.css**

完整覆盖 `frontend/src/pages/versions/_print.css`：

```css
/**
 * VersionBoardPage 打印样式（M4）。
 * 把 NAV 侧栏 / 顶部 header / Tabs header / 按钮组在打印时藏掉，
 * 只剩当前 tab 内容。配合 AppLayout / VersionBoardPage 上的 data-attr。
 */
@media print {
  html,
  body {
    background: white !important;
  }

  /* 1) 隐藏 chrome：左边 NAV + 顶部 user header */
  aside,
  header[data-app-header] {
    display: none !important;
  }
  main {
    overflow: visible !important;
  }

  /* 2) 隐藏 tab header 和带 data-print-hide 的按钮组 */
  [data-version-tabs-header],
  [data-print-hide] {
    display: none !important;
  }

  /* 3) tab content 撑满 */
  [data-version-tab-content] {
    height: auto !important;
    overflow: visible !important;
    padding: 0 !important;
  }

  /* 4) 表格 / 卡片避免被切断 */
  table,
  .card,
  [class*="rounded-lg border"] {
    page-break-inside: avoid;
    break-inside: avoid;
  }

  /* 5) 链接打印时显示 URL */
  a[href]::after {
    content: " (" attr(href) ")";
    font-size: 0.85em;
    color: #555;
  }

  /* 6) 去阴影，免得灰边干扰打印版式 */
  .shadow,
  .shadow-sm,
  .shadow-md,
  .shadow-lg {
    box-shadow: none !important;
  }
}
```

- [ ] **Step 2: AppLayout 顶部 header 加 data-attr**

打开 `frontend/src/components/AppLayout.tsx`，找到 line 86 的 `<header className="flex h-12 shrink-0 ...">`。把它改成：

```tsx
          <header
            data-app-header
            className="flex h-12 shrink-0 items-center justify-end gap-2 border-b bg-background px-4"
          >
```

（仅追加 `data-app-header` 属性，其它不变。）

- [ ] **Step 3: PmWorkspace milestone widget 加"测试报告"链接**

打开 `frontend/src/pages/workspace/PmWorkspace.tsx`，搜索 `function VersionMilestoneWidget`。这个 widget 当前用 `<WidgetCard ... viewAllHref={...board}>`。我们要在 widget 卡片里**额外**加一个"📊 测试报告"小链接到 `?tab=report`。

找到 `VersionMilestoneWidget` 函数体内的 `return <WidgetCard ... />` 调用。`WidgetCard` 接受 `footer?: ReactNode` props（先确认，如果没有就改成 children 或包一层）：

先看 `WidgetCard` 的定义（在 `frontend/src/pages/workspace/_shared.tsx`），找 `footer` prop。如果存在，给 milestone widget 传：

```tsx
      footer={
        versionId !== undefined ? (
          <a
            href={`/projects/${projectId}/versions/${versionId}/board?tab=report`}
            className="text-xs text-primary hover:underline"
          >
            📊 测试报告
          </a>
        ) : null
      }
```

如果 `WidgetCard` 没有 `footer` prop，则在 widget card 主标题下方（在 `<div className="text-xs text-muted-foreground">` 提示文案那一行旁边）追加一个内联链接：

```tsx
      {versionId !== undefined ? (
        <a
          href={`/projects/${projectId}/versions/${versionId}/board?tab=report`}
          className="ml-2 text-xs text-primary hover:underline"
        >
          📊 测试报告
        </a>
      ) : null}
```

放在 widget 主体内层最后一行；具体位置要看 PmWorkspace 现状决定，**只要保证页面运行时点这个链接能进 ReportTab 即可**。

> 选哪个写法看 WidgetCard 的实际签名 —— 先 `Read frontend/src/pages/workspace/_shared.tsx` 再决定。代码改完跑 typecheck 兜底。

- [ ] **Step 4: typecheck + lint**

```bash
cd /Users/Apple/Documents/test_automation_platform/frontend && npm run typecheck && npm run lint
```
Expected: 全绿。

- [ ] **Step 5: 端到端打印冒烟**

启动 dev server。访问 `http://localhost:5173/projects/1/versions/1/board?tab=report`：

1. **预览打印**：Cmd+P (Mac) / Ctrl+P (Windows) 打开打印对话框，预览中应当：
   - 左侧 NAV 侧栏不见
   - 顶部 user 切换 header 不见
   - 4 tab 的 header 不见
   - "重算汇总 / 导出 PDF" 按钮组不见
   - 仅 4 张 Card（指标 / Bug / 失败用例 / 需求覆盖）渲染
   - 链接末尾追加 ` (https://...)` URL 注释
2. **导出 PDF**：点页面内"导出 PDF"按钮 → 同样打印对话框 → 选"另存为 PDF" → 文件名默认 `<projectName>_<versionName>_报告.pdf`
3. **PmWorkspace 链接**：访问 `/workspace/pm`，找到"本迭代里程碑"widget，应该看到"📊 测试报告"链接，点击 → 跳到 `?tab=report`

- [ ] **Step 6: Commit**

```bash
cd /Users/Apple/Documents/test_automation_platform
git add frontend/src/pages/versions/_print.css \
        frontend/src/components/AppLayout.tsx \
        frontend/src/pages/workspace/PmWorkspace.tsx
git commit -m "$(cat <<'EOF'
feat(pm-m4): print stylesheet + report shortcut from PmWorkspace

@media print 把 NAV 侧栏、顶部 header、tab header、按钮组都藏掉，
只剩当前 tab 内容；链接打印时追加 URL 注释。AppLayout 顶部 header
加 data-app-header 钩子。PmWorkspace milestone widget 加
"📊 测试报告"链接到 ?tab=report。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## End-to-End 验证（8 task 全部完成后）

1. **后端**：
   ```bash
   curl -s 'http://127.0.0.1:54351/api/project-versions/1/cases' | jq '.data.total'
   curl -s 'http://127.0.0.1:54351/api/project-versions/1/cases?case_type=api&status=failed' | jq '.data.items | length'
   curl -s 'http://127.0.0.1:54351/api/tasks?ids=1,2,3' | jq '. | length'
   ```
   分别返回数字 / 数字 / ≤3。

2. **前端**：
   ```bash
   cd /Users/Apple/Documents/test_automation_platform/frontend && npm run typecheck && npm run lint
   ```
   全绿。

3. **手动 UI**：
   - `/projects/1/versions/1/board` → 4 tab shell；默认 board tab 与 M3 行为完全一致
   - `?tab=report` → 4 张 Card 数据渲染；点"重算汇总"刷新；点"导出 PDF"打印对话框
   - `?tab=cases` → 三个过滤器联动；点行跳 `/runs?case_id=X`
   - 把 version.status 改成 `released` → 多一个"归档" tab，点开看到 summary + release_notes + docs 三块
   - `/workspace/pm` 的"本迭代里程碑"widget 里有"📊 测试报告"链接 → 点击进 `?tab=report`

4. **打印 / PDF**：
   - 在 ReportTab 上 Cmd+P：预览只剩内容；NAV/header/tab-header/按钮组都不见
   - "导出 PDF" 按钮 → 文件名默认 `<projectName>_<versionName>_报告.pdf`

---

## Out of Scope（M4 不做，spec §11 已确认）

- 工时统计 / 燃尽图 / timeline
- AI 自动生成报告草稿
- 服务端 PDF 渲染（weasyprint / reportlab）
- 跨项目版本对比页
- bug 自动归档
- 用例执行历史 timeline 视图
- 移动端响应式
