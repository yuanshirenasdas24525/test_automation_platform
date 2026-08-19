# 用例 / 步骤复制功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给平台加「Ctrl+C 复制 / Ctrl+V 粘贴 / Ctrl+Z 撤销粘贴」能力，覆盖 functional/api/web/android/ios 全部用例类型，支持用例级（列表页，含多选）与步骤级（用例编辑器，单选），复制成功有边缘流动光效。

**Architecture:** 纯前端实现。一个内存单例剪贴板（`case-clipboard.tsx`）+ 一组可单测的纯函数（`copy-clone.ts`）+ 一个流动光效 CSS/hook（`index.css` + `use-copy-flash.ts`）。用例级逻辑封装进共享 hook `use-case-copy-paste.ts`，两个列表页共用；步骤级逻辑内嵌进 `step-editor.tsx`。用例复制走「`casesApi.get` 读详情 → 改名组装 `TestCaseCreate` → `casesApi.create` → `casesApi.reorder` 排到锚点下方」，不新增后端接口。

**Tech Stack:** React 19 + TypeScript strict + Tailwind + shadcn/ui。校验门：`npm run typecheck`（`tsc -b --noEmit`）+ `npm run lint`（eslint `--max-warnings 0`）。本项目**无前端测试框架**，功能验证靠 dev server 手动 e2e（见最后一个 Task）。

---

## 项目须知（trap）

- CLAUDE.md：改前端后想从后端 54351 端口看效果需 `npm run build`；开发直接看用 `npm run dev`（5173）。
- 所有命令在 `frontend/` 目录下跑。
- 无 ruff/black，无 vitest/jest；每个 Task 的验证 = `npm run typecheck` + `npm run lint` 通过（lint 是 `--max-warnings 0`，严）。
- 路径别名 `@/* → frontend/src/*`。
- 领域类型在 `src/types/domain.ts`：`CaseType`、`TestStepDraft`、`TestCaseCreate`、`TestCaseDetail`。
- API 客户端在 `src/lib/api.ts`：`casesApi.get(id) → TestCaseDetail`、`casesApi.create(body, sessionId) → {id}`、`casesApi.reorder(items)`、`casesApi.remove(id, sessionId)`。`ReorderItem` 形如 `{ id, type: "case", new_order }`。

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `frontend/src/lib/case-clipboard.tsx` | 内存单例剪贴板 + `useCopyClipboard()` | 新增 |
| `frontend/src/lib/copy-clone.ts` | 纯函数：平台组推导 / 类型匹配 / 改名去重 / 组装 payload | 新增 |
| `frontend/src/lib/use-copy-flash.ts` | 光效触发 hook（Set + 自动清除） | 新增 |
| `frontend/src/index.css` | `.copy-flash` 流动光效 keyframes | 改 |
| `frontend/src/lib/use-case-copy-paste.ts` | 用例级复制/粘贴/撤销共享 hook | 新增 |
| `frontend/src/components/case/step-editor.tsx` | 步骤级 active + 键盘 + 复制/粘贴/撤销 + 光效 | 改 |
| `frontend/src/pages/AutomationCasesPage.tsx` | 接 hook；`ApiCaseTable` 行 active/flash/click | 改 |
| `frontend/src/pages/FunctionalCasesPage.tsx` | 接 hook；`CaseRow` active/flash/click | 改 |

---

## Task 1: 内存单例剪贴板

**Files:**
- Create: `frontend/src/lib/case-clipboard.tsx`

- [ ] **Step 1: 写文件**

```tsx
import { useSyncExternalStore } from "react";
import type { CaseType, TestCaseDetail, TestStepDraft } from "@/types/domain";

/** 步骤平台组：粘贴目标类型校验用。 */
export type StepPlatformGroup = "web" | "app" | "api";

/** 剪贴板同一时刻只持有一批同类内容：一批用例 或 一个步骤。 */
export type CopyClipboardItem =
  | { kind: "case"; caseType: CaseType; snapshots: TestCaseDetail[] }
  | { kind: "step"; platformGroup: StepPlatformGroup; snapshot: TestStepDraft };

let current: CopyClipboardItem | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

/**
 * 模块级单例：跨对话框、跨列表页共享，只存在于内存，刷新页面即清空。
 * 刻意不写 localStorage / navigator.clipboard —— 纯应用内剪贴板。
 */
export const copyClipboard = {
  get(): CopyClipboardItem | null {
    return current;
  },
  set(item: CopyClipboardItem | null) {
    current = item;
    emit();
  },
  subscribe(cb: () => void): () => void {
    listeners.add(cb);
    return () => {
      listeners.delete(cb);
    };
  },
};

/** 订阅剪贴板变化（供 UI 反映「有没有可粘贴内容」，可选使用）。 */
export function useCopyClipboard(): CopyClipboardItem | null {
  return useSyncExternalStore(copyClipboard.subscribe, copyClipboard.get, copyClipboard.get);
}
```

- [ ] **Step 2: 校验**

Run: `cd frontend && npm run typecheck`
Expected: PASS（无新增报错）

- [ ] **Step 3: 提交**

```bash
git add frontend/src/lib/case-clipboard.tsx
git commit -m "feat(copy): 内存单例剪贴板"
```

---

## Task 2: 复制/粘贴纯函数

**Files:**
- Create: `frontend/src/lib/copy-clone.ts`

- [ ] **Step 1: 写文件**

```ts
import type { CaseType, TestCaseCreate, TestCaseDetail } from "@/types/domain";
import type { StepPlatformGroup } from "@/lib/case-clipboard";

/**
 * 步骤 step_type → 平台组。通用步骤（sleep/assert 等）返回 null，
 * 交给 resolveCopyStepGroup 按来源用例归组。
 */
export function stepPlatformGroupOf(stepType: string): StepPlatformGroup | null {
  if (stepType.startsWith("web_")) return "web";
  if (stepType.startsWith("app_")) return "app";
  if (stepType === "http_request") return "api";
  return null;
}

/** 用例 case_type → 步骤平台组。api / mixed / functional 归 api。 */
export function caseGroupOf(caseType: CaseType): StepPlatformGroup {
  if (caseType === "web") return "web";
  if (caseType === "android" || caseType === "ios") return "app";
  return "api";
}

/** 复制某步骤时确定它的平台组：优先按 step_type，通用步骤回退到来源用例组。 */
export function resolveCopyStepGroup(stepType: string, caseType: CaseType): StepPlatformGroup {
  return stepPlatformGroupOf(stepType) ?? caseGroupOf(caseType);
}

/**
 * 剪贴板里的步骤能否粘贴进当前 category 的用例。
 * mixed 用例接受任意组；其余按组严格匹配。
 */
export function canPasteStep(group: StepPlatformGroup, caseType: CaseType): boolean {
  if (caseType === "mixed") return true;
  return caseGroupOf(caseType) === group;
}

/** 剪贴板里的用例能否粘贴进当前列表：case_type 严格相等（各类型互相隔离）。 */
export function canPasteCase(clipCaseType: CaseType, listCaseType: CaseType): boolean {
  return clipCaseType === listCaseType;
}

/** 生成不与现有名冲突的副本名："X_副本"、"X_副本2"、"X_副本3"… */
export function dedupeCopyName(baseName: string, existing: Set<string>): string {
  const root = `${baseName}_副本`;
  if (!existing.has(root)) return root;
  let n = 2;
  while (existing.has(`${root}${n}`)) n += 1;
  return `${root}${n}`;
}

/**
 * 由用例详情快照组装「新建副本」的 payload：
 * 去掉 id / sort_order（排序另由 reorder 处理），重置每个 step 的 id，改名。
 * case_type / priority / tags / variables / description 等随 detail 原样保留。
 */
export function buildCaseCopyPayload(
  detail: TestCaseDetail,
  moduleId: number,
  name: string,
): TestCaseCreate {
  const { id: _id, sort_order: _sortOrder, steps, ...rest } = detail;
  return {
    ...rest,
    module_id: moduleId,
    name,
    steps: (steps ?? []).map((s, idx) => ({ ...s, id: null, step_order: idx })),
  };
}
```

- [ ] **Step 2: 校验类型**

Run: `cd frontend && npm run typecheck`
Expected: PASS

- [ ] **Step 3: 纯函数自检（一次性 node 脚本，可选但推荐）**

写临时文件 `frontend/scratch-copy-clone-check.mjs`（校验后删除）：

```js
// 用 tsx/esbuild 直接跑 TS 麻烦，这里只手工复刻纯逻辑做 sanity check。
function dedupeCopyName(baseName, existing) {
  const root = `${baseName}_副本`;
  if (!existing.has(root)) return root;
  let n = 2;
  while (existing.has(`${root}${n}`)) n += 1;
  return `${root}${n}`;
}
console.assert(dedupeCopyName("登录", new Set()) === "登录_副本", "空集");
console.assert(dedupeCopyName("登录", new Set(["登录_副本"])) === "登录_副本2", "撞一次");
console.assert(
  dedupeCopyName("登录", new Set(["登录_副本", "登录_副本2"])) === "登录_副本3",
  "撞两次",
);
console.log("dedupeCopyName OK");
```

Run: `cd frontend && node scratch-copy-clone-check.mjs && rm scratch-copy-clone-check.mjs`
Expected: 打印 `dedupeCopyName OK`，无 assert 失败

- [ ] **Step 4: 提交**

```bash
git add frontend/src/lib/copy-clone.ts
git commit -m "feat(copy): 复制/粘贴纯函数(平台组/类型匹配/改名/组装payload)"
```

---

## Task 3: 流动光效 CSS + 触发 hook

**Files:**
- Modify: `frontend/src/index.css`（在文件末尾追加）
- Create: `frontend/src/lib/use-copy-flash.ts`

- [ ] **Step 1: 在 `frontend/src/index.css` 末尾追加光效样式**

```css
/* ------------------------------------------------------------------ */
/* 复制成功：边缘流动光效（绕行一圈，单次）。给目标元素加 .copy-flash。 */
/* ------------------------------------------------------------------ */
@property --copy-flow-angle {
  syntax: "<angle>";
  initial-value: 0deg;
  inherits: false;
}

@keyframes copy-flow-spin {
  to {
    --copy-flow-angle: 360deg;
  }
}

.copy-flash {
  position: relative;
}

.copy-flash::after {
  content: "";
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  padding: 2px;
  background: conic-gradient(
    from var(--copy-flow-angle),
    transparent 0deg,
    transparent 250deg,
    hsl(var(--primary) / 0.9) 320deg,
    #ffffff 350deg,
    transparent 360deg
  );
  /* 只保留边框那一圈：内容区用 mask 掏空 */
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  mask-composite: exclude;
  animation: copy-flow-spin 1.15s linear 1;
  pointer-events: none;
  z-index: 5;
}

@media (prefers-reduced-motion: reduce) {
  .copy-flash::after {
    animation: none;
    background: hsl(var(--primary) / 0.6);
  }
}
```

- [ ] **Step 2: 写 `frontend/src/lib/use-copy-flash.ts`**

```ts
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 复制光效触发器：trigger(id) 给该 id 打上 flash 标记，durationMs 后自动清除。
 * flashing 集合驱动行上的 .copy-flash class。支持一次触发多个（多选复制）。
 */
export function useCopyFlash<T>(durationMs = 1200) {
  const [flashing, setFlashing] = useState<Set<T>>(new Set());
  const timers = useRef<Map<T, number>>(new Map());

  const trigger = useCallback(
    (ids: T | T[]) => {
      const arr = Array.isArray(ids) ? ids : [ids];
      setFlashing((prev) => {
        const next = new Set(prev);
        for (const id of arr) next.add(id);
        return next;
      });
      for (const id of arr) {
        const existing = timers.current.get(id);
        if (existing) window.clearTimeout(existing);
        const t = window.setTimeout(() => {
          setFlashing((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
          timers.current.delete(id);
        }, durationMs);
        timers.current.set(id, t);
      }
    },
    [durationMs],
  );

  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const t of map.values()) window.clearTimeout(t);
      map.clear();
    };
  }, []);

  return { flashing, trigger };
}
```

- [ ] **Step 3: 校验**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add frontend/src/index.css frontend/src/lib/use-copy-flash.ts
git commit -m "feat(copy): 边缘流动光效 CSS + useCopyFlash hook"
```

---

## Task 4: 步骤级复制/粘贴/撤销（step-editor.tsx）

**Files:**
- Modify: `frontend/src/components/case/step-editor.tsx`

现状（已确认）：`StepEditor({ projectId, category: CaseType, value: TestStepDraft[], onChange })`；内部 `setStep/removeStep/moveStep/reorderStep/addStep` 都是 `onChange(arr.map((s,idx)=>({...s, step_order:idx})))` 的受控更新；行组件 `StepRow`（`key={i}`，outer div 在 ~L1053）；`platformOf`/`isAppFamily` 已存在。文件已 `import * as React`，并从 `sonner` 用 toast? —— 若没有先加 `import { toast } from "sonner";`（先 grep 确认）。

- [ ] **Step 1: 顶部补充 import**

在 step-editor.tsx 已有 import 区加入（若 `toast` 已导入则跳过该行）：

```ts
import { toast } from "sonner";
import { copyClipboard } from "@/lib/case-clipboard";
import { canPasteStep, resolveCopyStepGroup } from "@/lib/copy-clone";
import { useCopyFlash } from "@/lib/use-copy-flash";
import type { TestStepDraft } from "@/types/domain";
```

（`TestStepDraft` 大概率已导入，重复导入 eslint 会报，先确认。）

- [ ] **Step 2: 在 `StepEditor` 组件体内、`return` 之前，加状态与句柄**

紧挨 `const [dragIdx, setDragIdx] = React.useState<number | null>(null);` 之后加：

```ts
  // ---- 复制/粘贴/撤销 ----
  const [activeStepIndex, setActiveStepIndex] = React.useState<number | null>(null);
  const { flashing: stepFlash, trigger: triggerStepFlash } = useCopyFlash<number>();
  const containerRef = React.useRef<HTMLDivElement>(null);
  // 记录最近一次粘贴（仅单级撤销）；任何其它改动都会清空它。
  const lastPaste = React.useRef<{ index: number } | null>(null);

  const doCopyStep = () => {
    if (activeStepIndex == null) return;
    const step = value[activeStepIndex];
    if (!step) return;
    const group = resolveCopyStepGroup(step.step_type, category);
    copyClipboard.set({
      kind: "step",
      platformGroup: group,
      snapshot: structuredClone(step),
    });
    triggerStepFlash(activeStepIndex);
  };

  const doPasteStep = () => {
    const item = copyClipboard.get();
    if (!item || item.kind !== "step") return;
    if (!canPasteStep(item.platformGroup, category)) {
      toast.error("不能跨类型粘贴步骤");
      return;
    }
    const insertAt = activeStepIndex == null ? 0 : activeStepIndex + 1;
    const cloned: TestStepDraft = { ...structuredClone(item.snapshot), id: null };
    const arr = value.slice();
    arr.splice(insertAt, 0, cloned);
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
    lastPaste.current = { index: insertAt };
    setActiveStepIndex(insertAt);
  };

  const undoPasteStep = () => {
    const rec = lastPaste.current;
    if (!rec) return;
    if (rec.index < 0 || rec.index >= value.length) {
      lastPaste.current = null;
      return;
    }
    const arr = value.slice();
    arr.splice(rec.index, 1);
    onChange(arr.map((s, idx) => ({ ...s, step_order: idx })));
    lastPaste.current = null;
    setActiveStepIndex(null);
  };

  const onEditorKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!(e.metaKey || e.ctrlKey)) return;
    const key = e.key.toLowerCase();
    if (key !== "c" && key !== "v" && key !== "z") return;
    const el = document.activeElement as HTMLElement | null;
    const inEditable =
      !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    if (key === "c") {
      // 输入框内且有选中文本 → 让原生复制文本
      const hasText = (window.getSelection()?.toString() ?? "").length > 0;
      if (inEditable && hasText) return;
      if (activeStepIndex == null) return;
      e.preventDefault();
      doCopyStep();
    } else if (key === "v") {
      if (inEditable) return; // 输入框内交给原生粘贴
      e.preventDefault();
      doPasteStep();
    } else if (key === "z") {
      if (inEditable) return;
      if (!lastPaste.current) return;
      e.preventDefault();
      undoPasteStep();
    }
  };
```

- [ ] **Step 3: 每个会改动列表的句柄开头清空 lastPaste**

在 `setStep`、`removeStep`、`moveStep`、`reorderStep`、`addStep` 五个函数体**第一行**各加：

```ts
    lastPaste.current = null;
```

- [ ] **Step 4: 给最外层容器接键盘与聚焦**

把 `return ( <div className="space-y-3">` 改成：

```tsx
  return (
    <div
      ref={containerRef}
      className="space-y-3 outline-none"
      tabIndex={-1}
      onKeyDown={onEditorKeyDown}
      onClick={() => containerRef.current?.focus()}
    >
```

（`tabIndex={-1}` + onClick focus：点空白区也能让容器拿到焦点，从而支持「无选中 Ctrl+V 插到第一条」。）

- [ ] **Step 5: 给 `StepRow` 传 active/flash/onActivate**

在 `value.map((step, i) => (<StepRow ... />))` 里加三个 props：

```tsx
              active={activeStepIndex === i}
              flash={stepFlash.has(i)}
              onActivate={() => {
                setActiveStepIndex(i);
                containerRef.current?.focus();
              }}
```

- [ ] **Step 6: `StepRow` 接收并应用这三个 prop**

在 `StepRow` 参数解构里加 `active, flash, onActivate`，在其类型块里加：

```ts
  active: boolean;
  flash: boolean;
  onActivate: () => void;
```

把 StepRow 的 outer `<div className={cn(...)}>`（~L1053）改为：

```tsx
    <div
      className={cn(
        "rounded-md border bg-card p-2 transition-colors",
        step.skip && "opacity-60",
        isDragging && "opacity-50 ring-2 ring-primary/30",
        isDropTarget && "border-primary/40 bg-primary/5",
        active && "ring-2 ring-primary/60",
        flash && "copy-flash",
      )}
      onClick={onActivate}
      onDragOver={handleRowDragOver}
      onDrop={handleRowDrop}
    >
```

（`onClick={onActivate}` 放 outer div：点行任意处即选中该步骤；内部按钮点击照常冒泡到这里，无副作用。）

- [ ] **Step 7: 校验**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add frontend/src/components/case/step-editor.tsx
git commit -m "feat(copy): 步骤级复制/粘贴/撤销 + 选中态 + 光效"
```

---

## Task 5: 用例级复制/粘贴/撤销共享 hook

**Files:**
- Create: `frontend/src/lib/use-case-copy-paste.ts`

两个列表页共用。hook 管理 activeCaseId、flash、剪贴板读写、以及粘贴/撤销的异步编排（create + reorder + delete）。用例复制源优先取 `selected` 多选集合，否则取 activeCaseId。

- [ ] **Step 1: 写文件**

```ts
import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";
import { casesApi } from "@/lib/api";
import { copyClipboard } from "@/lib/case-clipboard";
import { buildCaseCopyPayload, canPasteCase, dedupeCopyName } from "@/lib/copy-clone";
import { useCopyFlash } from "@/lib/use-copy-flash";
import type { CaseType } from "@/types/domain";

/** 列表页传进来的当前页用例（已按显示顺序）。 */
export interface CopyPasteCaseRow {
  id: number;
  name: string;
}

export interface UseCaseCopyPasteArgs {
  caseType: CaseType;
  moduleId: number | null;
  sessionId: string | null;
  cases: CopyPasteCaseRow[];
  /** 现有 checkbox 多选集合，作为复制源（非空优先）。 */
  selected: Set<number>;
  /** 变更后刷新列表（invalidate/refetch）。 */
  onAfterChange: () => void;
}

export function useCaseCopyPaste({
  caseType,
  moduleId,
  sessionId,
  cases,
  selected,
  onAfterChange,
}: UseCaseCopyPasteArgs) {
  const [activeCaseId, setActiveCaseId] = useState<number | null>(null);
  const { flashing, trigger } = useCopyFlash<number>();
  const containerRef = useRef<HTMLDivElement>(null);
  const lastPaste = useRef<{ createdIds: number[] } | null>(null);
  const [busy, setBusy] = useState(false);

  const doCopy = useCallback(async () => {
    // 复制源：多选非空则用多选，否则用 activeCaseId 单条。
    const ids =
      selected.size > 0
        ? cases.filter((c) => selected.has(c.id)).map((c) => c.id)
        : activeCaseId != null
          ? [activeCaseId]
          : [];
    if (ids.length === 0) return;
    try {
      const snapshots = await Promise.all(ids.map((id) => casesApi.get(id)));
      copyClipboard.set({ kind: "case", caseType, snapshots });
      trigger(ids);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "复制失败");
    }
  }, [selected, cases, activeCaseId, caseType, trigger]);

  const doPaste = useCallback(async () => {
    if (moduleId == null || busy) return;
    const item = copyClipboard.get();
    if (!item || item.kind !== "case") return;
    if (!canPasteCase(item.caseType, caseType)) {
      toast.error("不能跨类型粘贴用例");
      return;
    }
    setBusy(true);
    try {
      // 名称去重：基于当前页已有名 + 本批已产生名。
      const existing = new Set(cases.map((c) => c.name));
      const createdIds: number[] = [];
      for (const detail of item.snapshots) {
        const name = dedupeCopyName(detail.name, existing);
        existing.add(name);
        const res = await casesApi.create(
          buildCaseCopyPayload(detail, moduleId, name),
          sessionId ?? undefined,
        );
        createdIds.push(res.id);
      }
      // 排序：把新用例插到锚点(activeCaseId)之后；无锚点则置顶。
      const currentIds = cases.map((c) => c.id);
      const anchorIdx = activeCaseId == null ? -1 : currentIds.indexOf(activeCaseId);
      const orderedIds =
        anchorIdx < 0
          ? [...createdIds, ...currentIds]
          : [
              ...currentIds.slice(0, anchorIdx + 1),
              ...createdIds,
              ...currentIds.slice(anchorIdx + 1),
            ];
      await casesApi.reorder(
        orderedIds.map((id, order) => ({ id, type: "case" as const, new_order: order })),
      );
      lastPaste.current = { createdIds };
      onAfterChange();
      toast.success(`已粘贴 ${createdIds.length} 条用例`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "粘贴失败");
    } finally {
      setBusy(false);
    }
  }, [moduleId, busy, caseType, cases, activeCaseId, sessionId, onAfterChange]);

  const undoPaste = useCallback(async () => {
    const rec = lastPaste.current;
    if (!rec || busy) return;
    setBusy(true);
    try {
      for (const id of rec.createdIds) {
        await casesApi.remove(id, sessionId ?? undefined);
      }
      lastPaste.current = null;
      onAfterChange();
      toast.success("已撤销粘贴");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "撤销失败");
    } finally {
      setBusy(false);
    }
  }, [busy, sessionId, onAfterChange]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      const key = e.key.toLowerCase();
      if (key !== "c" && key !== "v" && key !== "z") return;
      const el = document.activeElement as HTMLElement | null;
      const inEditable =
        !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      if (key === "c") {
        const hasText = (window.getSelection()?.toString() ?? "").length > 0;
        if (inEditable && hasText) return;
        if (selected.size === 0 && activeCaseId == null) return;
        e.preventDefault();
        void doCopy();
      } else if (key === "v") {
        if (inEditable) return;
        e.preventDefault();
        void doPaste();
      } else if (key === "z") {
        if (inEditable) return;
        if (!lastPaste.current) return;
        e.preventDefault();
        void undoPaste();
      }
    },
    [selected, activeCaseId, doCopy, doPaste, undoPaste],
  );

  return {
    activeCaseId,
    setActiveCaseId,
    flashing,
    containerRef,
    containerProps: {
      ref: containerRef,
      tabIndex: -1,
      className: "outline-none",
      onKeyDown,
      onClick: () => containerRef.current?.focus(),
    },
  };
}
```

- [ ] **Step 2: 校验**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: PASS。若 `ReorderItem` 的 `type` 字段是字面量联合类型，`type: "case" as const` 应满足；若报错，改为 `import type { ReorderItem } from "@/lib/api"` 并按其定义调整（先 grep `export interface ReorderItem` / `export type ReorderItem`）。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/lib/use-case-copy-paste.ts
git commit -m "feat(copy): 用例级复制/粘贴/撤销共享 hook"
```

---

## Task 6: 接入 AutomationCasesPage（api/web/android/ios）

**Files:**
- Modify: `frontend/src/pages/AutomationCasesPage.tsx`

现状：页面组件里已有 `selected: Set<number>`、`caseType`、`moduleId`、`editSession`（sessionId）、`invalidate()`、`cases`（当前页数组）。`ApiCaseTable` 在 ~L2094，run 模式行是 `<div key={row.id} className={cn("grid ... hover:bg-muted/30", ...)}>`（~L2154）。

- [ ] **Step 1: import hook**

在 import 区加：

```ts
import { useCaseCopyPaste } from "@/lib/use-case-copy-paste";
```

- [ ] **Step 2: 在页面组件体内实例化 hook**

在 `invalidate` 定义之后（能拿到 `cases`/`selected`/`caseType`/`moduleId`/`editSession`）加：

```ts
  const copyPaste = useCaseCopyPaste({
    caseType,
    moduleId,
    sessionId: editSession,
    cases: cases.map((c) => ({ id: c.id, name: c.name })),
    selected,
    onAfterChange: invalidate,
  });
```

（若 `caseType` 的类型是 `AutomationTabCaseType` 而非 `CaseType`，它是 CaseType 的子集，可直接传；若 TS 报窄化问题，用 `caseType as CaseType`。）

- [ ] **Step 3: 用容器包住 `<ApiCaseTable/>` 并接键盘**

把渲染 `<ApiCaseTable .../>` 的外层加一个 `<div {...copyPaste.containerProps}>` 包裹，并给 `ApiCaseTable` 传三个新 props：

```tsx
                <div {...copyPaste.containerProps}>
                  <ApiCaseTable
                    cases={cases}
                    /* …现有 props 保持不变… */
                    activeCaseId={copyPaste.activeCaseId}
                    flashingIds={copyPaste.flashing}
                    onRowActivate={copyPaste.setActiveCaseId}
                  />
                </div>
```

- [ ] **Step 4: `ApiCaseTable` 接收新 props**

在 `ApiCaseTable({ ... })` 解构里加 `activeCaseId, flashingIds, onRowActivate`，类型块加：

```ts
  activeCaseId: number | null;
  flashingIds: Set<number>;
  onRowActivate: (id: number) => void;
```

- [ ] **Step 5: run 模式行加 active/flash/click**

把 run 模式的 `<div key={row.id} className={cn("grid items-center gap-2 border-b px-3 py-2.5 text-sm last:border-b-0 hover:bg-muted/30", manualAdjustment.pending && "bg-red-50/50", gridClass)}>` 改为：

```tsx
      <div
        key={row.id}
        onClick={() => onRowActivate(row.id)}
        className={cn(
          "grid items-center gap-2 border-b px-3 py-2.5 text-sm last:border-b-0 hover:bg-muted/30",
          manualAdjustment.pending && "bg-red-50/50",
          activeCaseId === row.id && "bg-primary/5 ring-2 ring-inset ring-primary/50",
          flashingIds.has(row.id) && "copy-flash",
          gridClass,
        )}
      >
```

（quickEdit 模式的 `QuickEditRow` 暂不接 active——快速编辑行全是输入框，复制场景以 run 模式为主；保持最小改动。）

- [ ] **Step 6: 校验**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add frontend/src/pages/AutomationCasesPage.tsx
git commit -m "feat(copy): AutomationCasesPage 接入用例复制/粘贴(api/web/android/ios)"
```

---

## Task 7: 接入 FunctionalCasesPage（functional）

**Files:**
- Modify: `frontend/src/pages/FunctionalCasesPage.tsx`

现状：已有 `selected: Set<number>`、`invalidateAll()`、`cases`、`moduleId`、`sessionId`（若无独立 sessionId 变量则传 `null`）。表格子组件里每行渲染 `<CaseRow row={c} selected={...} onEdit .../>`（~L1385）。functional 用例 case_type = `"functional"`，无 steps，`buildCaseCopyPayload` 会给 `steps: []`，正确。

- [ ] **Step 1: import hook**

```ts
import { useCaseCopyPaste } from "@/lib/use-case-copy-paste";
```

- [ ] **Step 2: 实例化 hook（页面组件体内，能拿到 cases/selected/moduleId）**

```ts
  const copyPaste = useCaseCopyPaste({
    caseType: "functional",
    moduleId,
    sessionId: sessionId ?? null,
    cases: cases.map((c) => ({ id: c.id, name: c.name })),
    selected,
    onAfterChange: invalidateAll,
  });
```

（`sessionId` 变量名以文件实际为准；无则传 `null`。`moduleId` 若是 `number | null` 直接传。）

- [ ] **Step 3: 容器包住表格 + 透传 props**

在渲染表格子组件处，外层包 `<div {...copyPaste.containerProps}>`，并把 `activeCaseId`/`flashingIds`/`onRowActivate` 透传给表格子组件，再由它传给每个 `CaseRow`。

表格子组件参数解构与类型块加：

```ts
  activeCaseId: number | null;
  flashingIds: Set<number>;
  onRowActivate: (id: number) => void;
```

`<CaseRow .../>` 调用处加：

```tsx
                  active={activeCaseId === c.id}
                  flash={flashingIds.has(c.id)}
                  onActivate={() => onRowActivate(c.id)}
```

- [ ] **Step 4: `CaseRow` 接收并应用**

`CaseRow` 参数解构加 `active, flash, onActivate`，类型块加：

```ts
  active: boolean;
  flash: boolean;
  onActivate: () => void;
```

在 `CaseRow` 的最外层 `<tr ...>` 上加 `onClick={onActivate}` 与 class：

```tsx
      onClick={onActivate}
      className={cn(
        /* …CaseRow 原有 className… */,
        active && "bg-primary/5 ring-2 ring-inset ring-primary/50",
        flash && "copy-flash",
      )}
```

（若 `CaseRow` 的 `<tr>` 原本没有 `className`/`cn`，import `cn`（`@/lib/utils`）并新增 className。`.copy-flash::after` 在 `<tr>` 上定位需要 `<tr>` 是定位上下文；若光效在 table-row 上不显示，退而在该行第一个 `<td>` 外包一层或给 `<tr>` 加 `class="relative"` —— 见 Task 8 手动验证时确认，必要时改挂到行内容 wrapper。）

- [ ] **Step 5: 校验**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/FunctionalCasesPage.tsx
git commit -m "feat(copy): FunctionalCasesPage 接入用例复制/粘贴(functional)"
```

---

## Task 8: 整体验证（手动 e2e 矩阵）

**Files:** 无（只跑与验证）

- [ ] **Step 1: 全量类型 + lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: 两者都 PASS

- [ ] **Step 2: 起 dev server**

Run: `cd frontend && npm run dev`
Expected: Vite 起在 5173；后端 54351 已在跑（`python server/main.py`）

- [ ] **Step 3: 用例级验证矩阵（逐类型：api / web / android / ios / functional）**

对每种类型走一遍：
1. 进对应用例列表（run 模式）。
2. 点一条用例行 → 出现选中高亮（ring）。
3. `Ctrl+C`（Mac `Cmd+C`）→ 该行边缘出现流动光效绕一圈。
4. 点另一条用例行 → `Ctrl+V` → 在其下方出现「原名_副本」，列表顺序正确。
5. `Ctrl+Z` → 刚粘贴的副本被删除，列表恢复。
6. 不点任何行，直接点列表空白区域使其聚焦 → `Ctrl+V` → 副本插到第一条。
7. 勾选多条（checkbox）→ `Ctrl+C` → 多行同时绕光 → `Ctrl+V` → 按序插入多条副本。
8. 未选中任何行且区域未聚焦 → `Ctrl+V` → 无反应（no-op）。

- [ ] **Step 4: 跨类型隔离验证**

在 API 列表 `Ctrl+C` 一条 → 切到 WEB 列表 → `Ctrl+V` → toast「不能跨类型粘贴用例」，列表不变。

- [ ] **Step 5: 步骤级验证（在 CaseDialog 编辑器里）**

1. 打开一个 web 用例编辑器 → 点一个步骤行 → 选中高亮。
2. `Ctrl+C` → 该步骤行边缘流动光效。
3. 点另一步骤 → `Ctrl+V` → 其下方插入步骤副本，#序号连续。
4. `Ctrl+Z` → 副本步骤被移除。
5. 无选中步骤时点编辑器空白 → `Ctrl+V` → 插到第一条。
6. 复制 web 步骤 → 打开一个 android 用例编辑器 → `Ctrl+V` → toast「不能跨类型粘贴步骤」。
7. 在步骤名输入框里选中文字 `Ctrl+C` → 走原生文本复制（不触发步骤复制）。

- [ ] **Step 6: 步骤跨用例共享验证**

在 web 用例A复制一个步骤 → 关闭A → 打开另一个 web 用例B → `Ctrl+V` → 步骤成功粘入B。

- [ ] **Step 7: 光效降级验证（可选）**

系统开启「减少动态效果」后复制 → 光效降级为静态描边（不绕行动画），无报错。

- [ ] **Step 8: 收尾提交（若手动验证中有微调）**

```bash
git add -A
git commit -m "test(copy): 手动 e2e 验证通过 + 微调"
```

---

## Self-Review 备注（已核对 spec 覆盖）

- 全类型覆盖：Task 6（api/web/android/ios）+ Task 7（functional）+ Task 4（步骤，web/app/api 组）。
- 对称粘贴：Task 4（步骤→步骤）、Task 5/6/7（用例→用例）。
- 多选复制（用例）：Task 5 `doCopy` 取 `selected` 优先。
- Ctrl+Z 撤销：Task 4（步骤 splice）、Task 5（删除新建用例）。
- 不能跨类型：`canPasteCase`（严格相等）、`canPasteStep`（组匹配，mixed 放行）。
- 无选中 Ctrl+V 无效 / 区域聚焦插第一条：键盘 handler 的 guard + `insertAt/anchorIdx` 逻辑。
- 光效一次性 + reduced-motion 降级：Task 3 CSS。
- 全局共享剪贴板：Task 1 模块单例。
- 跨平台快捷键：所有 handler 用 `e.metaKey || e.ctrlKey`。
