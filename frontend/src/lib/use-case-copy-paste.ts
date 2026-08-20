import { useCallback, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";
import { casesApi, functionalCasesApi } from "@/lib/api";
import { copyClipboard, useCopyClipboard, type CaseSnapshot } from "@/lib/case-clipboard";
import {
  buildCaseCopyPayload,
  buildFunctionalCopyPayload,
  canPasteCase,
  dedupeCopyName,
} from "@/lib/copy-clone";
import type { CaseType, FunctionalCase, TestCaseDetail } from "@/types/domain";

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
  const containerRef = useRef<HTMLDivElement>(null);
  const lastPaste = useRef<{ createdIds: number[] } | null>(null);
  const [busy, setBusy] = useState(false);

  // 功能用例的内容(functional_spec)在独立 JSON 列，必须走 functionalCasesApi；
  // 自动化用例(api/web/android/ios)走通用 casesApi。reorder 两者共用 /api/reorder。
  const isFunctional = caseType === "functional";

  const fetchSnapshot = useCallback(
    (id: number): Promise<CaseSnapshot> =>
      isFunctional ? functionalCasesApi.get(id) : casesApi.get(id),
    [isFunctional],
  );

  const createCopy = useCallback(
    (snapshot: CaseSnapshot, targetModuleId: number, name: string) =>
      isFunctional
        ? functionalCasesApi.create(
            buildFunctionalCopyPayload(snapshot as FunctionalCase, targetModuleId, name),
            sessionId ?? undefined,
          )
        : casesApi.create(
            buildCaseCopyPayload(snapshot as TestCaseDetail, targetModuleId, name),
            sessionId ?? undefined,
          ),
    [isFunctional, sessionId],
  );

  const removeCopy = useCallback(
    (id: number) =>
      isFunctional
        ? functionalCasesApi.remove(id, sessionId ?? undefined)
        : casesApi.remove(id, sessionId ?? undefined),
    [isFunctional, sessionId],
  );

  // 常驻高亮：跟随剪贴板状态（参考 Excel 蚁行线），不再用定时器自动消失。
  // 复制别的用例 → 高亮跟着走；复制其它文本 / 离开页面 → 剪贴板清空 → 高亮消失。
  const clip = useCopyClipboard();
  const markedIds = useMemo<Set<number>>(() => {
    if (clip && clip.kind === "case" && clip.caseType === caseType) {
      return new Set(clip.sourceIds);
    }
    return new Set();
  }, [clip, caseType]);

  const doCopy = useCallback(async () => {
    const ids =
      selected.size > 0
        ? cases.filter((c) => selected.has(c.id)).map((c) => c.id)
        : activeCaseId != null
          ? [activeCaseId]
          : [];
    if (ids.length === 0) return;
    try {
      const snapshots = await Promise.all(ids.map((id) => fetchSnapshot(id)));
      copyClipboard.set({ kind: "case", caseType, snapshots, sourceIds: ids });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "复制失败");
    }
  }, [selected, cases, activeCaseId, caseType, fetchSnapshot]);

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
      const existing = new Set(cases.map((c) => c.name));
      const createdIds: number[] = [];
      for (const snapshot of item.snapshots) {
        const name = dedupeCopyName(snapshot.name, existing);
        existing.add(name);
        const res = await createCopy(snapshot, moduleId, name);
        createdIds.push(res.id);
      }
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
  }, [moduleId, busy, caseType, cases, activeCaseId, createCopy, onAfterChange]);

  const undoPaste = useCallback(async () => {
    const rec = lastPaste.current;
    if (!rec || busy) return;
    setBusy(true);
    try {
      // allSettled：某条已被其它操作删掉(404)也不阻断其余撤销，避免半途失败留下残副本
      const results = await Promise.allSettled(rec.createdIds.map((id) => removeCopy(id)));
      lastPaste.current = null;
      onAfterChange();
      const failed = results.filter((r) => r.status === "rejected").length;
      if (failed > 0) {
        toast.error(`撤销部分失败：${failed} 条未删除`);
      } else {
        toast.success("已撤销粘贴");
      }
    } finally {
      setBusy(false);
    }
  }, [busy, removeCopy, onAfterChange]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (!(e.metaKey || e.ctrlKey)) return;
      const key = e.key.toLowerCase();
      if (key !== "c" && key !== "v" && key !== "z") return;
      const el = document.activeElement as HTMLElement | null;
      const inEditable =
        !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      if (key === "c") {
        // 输入框 / textarea / contenteditable 内一律让位原生复制
        // （window.getSelection() 看不到表单控件内部选区，不能靠它判断）
        if (inEditable) return;
        // 非编辑区若有选中文本，也让位原生复制
        if ((window.getSelection()?.toString() ?? "").length > 0) return;
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
    markedIds,
    containerRef,
    containerProps: {
      ref: containerRef,
      tabIndex: -1,
      className: "outline-none",
      onKeyDown,
      // preventScroll：聚焦容器不要把它滚进视口，否则点行/按钮时页面会跳动
      onClick: () => containerRef.current?.focus({ preventScroll: true }),
    },
  };
}
