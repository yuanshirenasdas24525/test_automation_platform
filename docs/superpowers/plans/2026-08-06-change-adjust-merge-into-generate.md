# 变更调整汇入「生成用例」流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「变更调整」产出的 ops（增/改/删）汇入现有「生成用例」的"生成详情 → 审阅列表 → 写入 → 生成历史"整套流程，三类动作在同一审阅列表按 `action` 分别 create/update/delete，并自动进「生成历史」可恢复。

**Architecture:** 变更调整只负责产出 ops + 契约，把它们作为 `points`（带 `action`/`target_case_id`）载入 `AiGenerateDialog` 的 generate 视图，复用其审阅/写入/历史机制。draft schema 的 point/case 各加两字段；写入循环按 `action` 分支；变更调整规划改为创建一条 `api_case_gen`+`stage=outline` 的 `AiRun`，天然进现有生成历史。

**Tech Stack:** FastAPI + SQLAlchemy（后端）、React 19 + TS（前端，无测试框架 → typecheck/lint/build + 手动验证）。后端 pytest 在 `tests/`。

**依赖前提（重要）:** 本计划的前端任务嫁接在**用户尚未提交的「生成用例/生成历史」在制品**上（`FunctionalCasesPage.tsx` 的 `AiGenerateDialog`、draft schema、写入循环）。执行前端任务前，**强烈建议先让用户把这块 WIP 提交**，以获得稳定基线 + 干净提交；否则需外科式提交且易受其后续改动影响。

---

## 已核实的整合点（勿臆测，均已读实际代码）

- `frontend/src/types/domain.ts`
  - `AiOutlinePoint { title: string; category: string }`（593 行）
  - `AiGeneratedCase { name; ...; compiled_case?; method?; path?; preconditions; steps; expected; duplicate?; after? }`（511 行）
  - `AiGenerateDraft { ...; points: AiOutlinePoint[]; pickedPoints: number[]; cases: AiGeneratedCase[]; picked: number[]; writtenNames: string[]; generationRunId?; apiContract?; stage }`（~2527 行，定义在 FunctionalCasesPage 内的 `type AiGenerateDraft`）
- `frontend/src/lib/api.ts`：`casesApi.create(body, sessionId?)` / `casesApi.update(id, body, sessionId?, historyBatchId?)` / `casesApi.remove(id, sessionId?, historyBatchId?)`（524 行起）；`automationCasesApi.renumber(moduleId, {enable, caseType})`；`changeAdjustApi.preview(...)`（Task 之前已加）。
- `FunctionalCasesPage.tsx`：
  - `AiGenerateDialog`（2658 行）持有生成态：`points/setPoints`（2710）、`pickedPoints/setPickedPoints`（2711）、`cases`、`picked`、`writtenNames`、`generationRunId/setGenerationRunId`（2709）、`apiContract`、`setView`（"input"|"outline"|"generate"|"history"）。
  - 载入草稿：`setPoints(draft.points)`、`setPickedPoints(...)`、`setGenerationRunId(draft.generationRunId)`、`setView("generate")`（2986-2997）。
  - **写入循环在 3528-3577**：遍历 `chosen`（勾选的 `AiGeneratedCase[]`），`basePayload = c.compiled_case ? {...c.compiled_case, module_id} : toInterfaceCase(...)`，`casesApi.create(payload)`，成功后 `automationCasesApi.renumber(...)`。
  - 「变更调整」Tab 现由 `ModuleOutlinePanel`（`components/case/module-outline-drawer.tsx`）渲染，`view==="outline"`。
- 后端 `server/api/functional_cases.py`：生成历史基于 `AiRun(feature=AI_FEATURE_API_CASE_GEN, input_payload.stage=="outline")`；`_generation_history_draft/summary`（1954/1999）；`output_payload.draft` 存审阅态快照（含 `points/cases/picked/writtenNames/apiContract/digest`）。
- 后端 `server/services/change_plan_service.py`：现有 `plan_preview` 产出 `{plan_id, ops, warnings}`，落 `AiRun(feature="change_plan")`。`plan_apply` 直接写库（本计划中退居后备/移除）。

---

## Task 1（后端）: plan_preview 改为产出「生成历史」兼容的 outline run

让变更调整规划落一条 `api_case_gen`+`stage=outline` 的 `AiRun`，`draft.points` = ops（带 `action`/`target_case_id`/`endpoint`），使其进现有生成历史，并可被前端 generate 视图载入。

**Files:**
- Modify: `server/services/change_plan_service.py`
- Test: `tests/services/test_change_plan_service.py`

- [ ] **Step 1: 失败测试**

```python
def test_plan_preview_builds_generation_history_draft(monkeypatch):
    # monkeypatch chat_markdown 返回固定 ops，构造真实 db（沿用现有 db fixture）；
    # 调 plan_preview，断言：
    #   返回 dict 含 generation_run_id（= 落库的 AiRun.id）
    #   该 AiRun.feature == AI_FEATURE_API_CASE_GEN 且 input_payload["stage"]=="outline"
    #   output_payload["draft"]["points"] 每项含 title/action/target_case_id
    ...
```
> 实现前读 `tests/services/test_change_plan_service.py` 现有 fixture 与 monkeypatch 方式照抄。

- [ ] **Step 2: 跑测试确认失败** `python -m pytest tests/services/test_change_plan_service.py::test_plan_preview_builds_generation_history_draft -v`

- [ ] **Step 3: 实现** —— 在 `plan_preview` 里，把落库改为 `feature=AI_FEATURE_API_CASE_GEN`（`from database.models.ai_run import` 引入），`input_payload` 增加 `stage="outline"` 与 `module_id/mode`，`output_payload` 增加 `draft`（用现有生成历史 draft 结构 + points 带 action）：

```python
from database.models.ai_run import AI_FEATURE_API_CASE_GEN
# points：ops → outline point（带 action/target/endpoint）
draft_points = [
    {"title": o["title"], "category": ACTION_CATEGORY.get(o["action"], ""),
     "action": o["action"], "target_case_id": o["target_case_id"],
     "endpoint": o["endpoint"]}
    for o in ops
]
draft = {
    "version": 1, "mode": "interface", "stage": "outline",
    "text": change_text, "docUrls": "", "modelName": model_name,
    "digest": "", "apiContract": ingest.contract,
    "points": draft_points, "pickedPoints": list(range(len(draft_points))),
    "genQueue": draft_points, "cursor": 0, "failedBatches": [],
    "cases": [], "picked": [], "writtenNames": [],
}
run = AiRun(
    feature=AI_FEATURE_API_CASE_GEN, status=AI_RUN_STATUS_SUCCESS,
    project_id=module.project_id, provider=cfg.provider, model=cfg.model,
    tokens_in=tin, tokens_out=tout,
    input_payload={"module_id": module.id, "mode": "interface", "stage": "outline",
                   "model_name": model_name, "change_text": change_text},
    output_payload={"draft": draft, "points": draft_points,
                    "api_contract": ingest.contract, "ops": ops,
                    "contract_hash": contract_hash(ingest.contract)},
    operator=operator, started_at=datetime.now(), ended_at=datetime.now(),
)
db.session.add(run); db.session.flush()
warnings = list(ingest.warnings)
needs_contract = any(o["action"] in ("add", "modify") for o in ops)
if needs_contract and not (ingest.contract.get("operations") or []):
    warnings.append("检测到新增/修改用例，但未解析到结构化 OpenAPI 契约……（保留现有文案）")
return {"plan_id": run.id, "generation_run_id": run.id, "ops": ops, "warnings": warnings}
```
其中 `ACTION_CATEGORY = {"add": "新增", "modify": "修改", "delete": "删除"}`（模块级常量）。

- [ ] **Step 4: 跑测试通过**；同时确认 `_get_generation_history_run` 的校验（feature/stage/module_id）对该 run 放行——读 `functional_cases.py:2020` 附近，若它要求 `input_payload.module_id`，我们已写入。
- [ ] **Step 5: 提交** `git add server/services/change_plan_service.py tests/services/test_change_plan_service.py && git commit -m "feat(change-adjust): 规划产出生成历史兼容的 outline run（points 带 action）"`

## Task 2（前端类型）: 给 point/case 加 action 与 target_case_id

**Files:** Modify `frontend/src/types/domain.ts`

- [ ] **Step 1** 扩展类型（可选字段，向后兼容普通生成用例）：

```typescript
export interface AiOutlinePoint {
  title: string;
  category: string;
  action?: "add" | "modify" | "delete";      // 变更调整专用；普通生成缺省
  target_case_id?: number | null;            // modify/delete 指向现有用例
  endpoint?: { method: string; path: string } | null;
}
// AiGeneratedCase 追加：
//   action?: "add" | "modify" | "delete";
//   target_case_id?: number | null;
```

- [ ] **Step 2** `npm run typecheck` 通过。**不提交**（controller 外科式提交；domain.ts 有用户在制品）。报告新增行供 controller 暂存。

## Task 3（前端合流入口）: 规划调整 → 把 ops 载入 generate 视图

把 `ModuleOutlinePanel` 的"规划调整"改为：调用 `changeAdjustApi.preview` 拿到 `{generation_run_id, ops, warnings}` 后，**不再在本面板内审阅**，而是把 ops 作为 points 交给父级 `AiGenerateDialog` 载入 generate 视图。

**Files:** Modify `frontend/src/components/case/module-outline-drawer.tsx`、`frontend/src/pages/FunctionalCasesPage.tsx`（`AiGenerateDialog` 渲染 `ModuleOutlinePanel` 处 + 载入函数）

- [ ] **Step 1** 给 `ModuleOutlinePanel` 新增回调 prop `onPlanned(plan: { generationRunId: number; points: AiOutlinePoint[]; apiContract: Record<string,unknown>; warnings: string[] })`。preview 成功后：把 ops 映射成 `AiOutlinePoint[]`（带 action/target_case_id/endpoint），调用 `onPlanned(...)`；移除面板内 ops 审阅/apply UI（`ChangeOpsList` 等）——审阅改由 generate 视图承担。
- [ ] **Step 2** 在 `AiGenerateDialog`（FunctionalCasesPage）里，渲染 `ModuleOutlinePanel` 处传入 `onPlanned`：其实现 `setPoints(points)`、`setPickedPoints(new Set(points.map((_,i)=>i)))`、`setGenerationRunId(generationRunId)`、`setApiContract(apiContract)`、warnings 存入现有 warning 展示、`setView("generate")`。这样后续复用 generate 视图的"逐批生成 + 审阅 + 写入"。
- [ ] **Step 3** `npm run typecheck && npm run lint && npm run build`。**不提交**（module-outline-drawer 全属我方可整文件交；FunctionalCasesPage 需外科式）。

## Task 4（前端生成）: 详情生成只对 add/modify；delete 造删除卡片

generate 视图逐批生成详情时，需把 `action`/`target_case_id` 从 point 透传到生成出的 `AiGeneratedCase`，且 delete point 不送 AI。

**Files:** Modify `FunctionalCasesPage.tsx`（批生成逻辑，约 3199/3295/3331 一带 `pickedPoints`→`genQueue`→cases）

- [ ] **Step 1** 生成前：把 `points` 分流——`delete` point 直接产出一个合成 `AiGeneratedCase`（`{name: point.title, action:"delete", target_case_id: point.target_case_id, preconditions:[], steps:[], expected:[]}`，无 compiled_case），不进 AI 批；`add/modify` point 进批（`ai_generate_batch`），生成结果回填时带上该 point 的 `action`/`target_case_id`（按顺序/标题对齐）。
- [ ] **Step 2** typecheck/lint/build。**不提交**。

## Task 5（前端写入分支）: 写入循环按 action 分 create/update/delete

**Files:** Modify `FunctionalCasesPage.tsx:3528-3577`（写入循环）

- [ ] **Step 1** 在循环内按 `c.action` 分支（缺省 add）：

```typescript
const action = c.action ?? "add";
try {
  if (action === "delete") {
    if (c.target_case_id != null) { await casesApi.remove(c.target_case_id); createdIds.push(c.target_case_id); createdCases.push(c); }
  } else if (action === "modify" && c.target_case_id != null) {
    await casesApi.update(c.target_case_id, payload); createdIds.push(c.target_case_id); createdCases.push(c);
    if (!blocked) runnableIds.push(c.target_case_id);
  } else {
    const res = await casesApi.create(payload);
    createdIds.push(res.id); createdCases.push(c);
    if (blocked) manualAdjustmentCount += 1; else runnableIds.push(res.id);
  }
} catch (error) {
  failedCases.push({ name: c.name, message: errorMessage(error) });
}
```
（`payload` 构造对 delete 分支不需要；把 payload 构造移进 add/modify 分支或保持但 delete 不用。）写入后照旧 `automationCasesApi.renumber(...)`。

- [ ] **Step 2** typecheck/lint/build。**不提交**。

## Task 6（前端审阅行）: 每行显示 action 徽标；delete 行只读

**Files:** Modify `FunctionalCasesPage.tsx`（审阅列表用例行渲染组件）

- [ ] **Step 1** 用例行标题旁加徽标：`c.action` 为 modify=琥珀「改」、delete=红「删」、add/缺省=绿「新增」；delete 行不显示步骤/预期（无详情），仍可勾选确认。读实际行组件位置后就地加。
- [ ] **Step 2** typecheck/lint/build。**不提交**。

## Task 7（收敛）: 退役面板内旧 apply 路径

**Files:** Modify `frontend/src/components/case/module-outline-drawer.tsx`；`server/services/change_plan_service.py`（`plan_apply` 保留但不再被前端调用，或标注 deprecated）；路由 `/change_plan/apply` 可保留（后备）。

- [ ] **Step 1** 移除面板内 `applyMut` + 调整大纲审阅 UI（已被 generate 视图取代）。保留 `changeAdjustApi.preview` 与 `apply`（apply 暂留后备）。
- [ ] **Step 2** typecheck/lint/build。**不提交**。

## Task 8: 端到端手动验证

- [ ] 重启后端；硬刷新前端。
- [ ] 「变更调整」填变更 + 传 `echo_test.openapi.json`（含 add；再另测一个模块已有用例的 modify/delete）→ 规划调整 → **自动切到生成用例审阅视图**，points 带 action。
- [ ] 逐批生成 → 审阅列表出现带「新增/修改/删除」徽标的用例，delete 行只读。
- [ ] 勾选 → 写入 → add 新建、modify 覆盖对应用例、delete 删除对应用例；统一编号连续不重复。
- [ ] 打开「生成历史」→ 出现这次变更调整记录，可 restore 回审阅态。

---

## 备注 / 风险
- 前端 Task 3-7 均编辑用户未提交的 `FunctionalCasesPage.tsx`（`AiGenerateDialog`）；**建议先让用户提交该 WIP** 再执行，以便干净提交与稳定基线。
- generate 视图内部（批生成、校验、compiled_case）是用户在制品，实现每个前端 Task 的第一步应先精读对应函数当前实现再改。
- 无契约时 add/modify 生成失败沿用生成用例既有"红色候选/需人工调整"提示（#1 决策：保持要求契约）。
