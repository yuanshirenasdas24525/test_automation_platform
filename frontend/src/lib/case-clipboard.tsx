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
