import { useSyncExternalStore } from "react";
import type { CaseType, FunctionalCase, TestCaseDetail, TestStepDraft } from "@/types/domain";

/** 步骤平台组：粘贴目标类型校验用。 */
export type StepPlatformGroup = "web" | "app" | "api";

/** 用例快照：自动化用例是 TestCaseDetail，功能用例是 FunctionalCase。 */
export type CaseSnapshot = TestCaseDetail | FunctionalCase;

/**
 * 剪贴板同一时刻只持有一批同类内容：一批用例 或 一个步骤。
 * - case：snapshots 是复制源的完整快照；sourceIds 是被复制行的 id（用于常驻高亮，参考 Excel 蚁行线）。
 * - step：snapshot 是复制的步骤；ownerToken 标识哪个编辑器实例复制的（只在该编辑器里高亮）。
 */
export type CopyClipboardItem =
  | { kind: "case"; caseType: CaseType; snapshots: CaseSnapshot[]; sourceIds: number[] }
  | { kind: "step"; platformGroup: StepPlatformGroup; snapshot: TestStepDraft; ownerToken: string };

let current: CopyClipboardItem | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

// 我方刚触发的复制（case/step）会在 keydown 里 preventDefault，一般不产生原生 copy 事件；
// 但为稳妥起见用一个短暂的抑制标记，避免我方复制被下面的原生 copy 监听误清空。
let suppressNextNativeClear = false;

/**
 * 模块级单例：跨对话框、跨列表页共享，只存在于内存，刷新页面即清空。
 * 刻意不写 localStorage / navigator.clipboard —— 纯应用内剪贴板。
 */
export const copyClipboard = {
  get(): CopyClipboardItem | null {
    return current;
  },
  set(item: CopyClipboardItem | null) {
    // 我方设置剪贴板时，抑制紧随其后的一次原生 copy 清除
    suppressNextNativeClear = true;
    if (typeof window !== "undefined") {
      window.setTimeout(() => {
        suppressNextNativeClear = false;
      }, 0);
    }
    current = item;
    emit();
  },
  clear() {
    if (current === null) return;
    current = null;
    emit();
  },
  subscribe(cb: () => void): () => void {
    listeners.add(cb);
    return () => {
      listeners.delete(cb);
    };
  },
};

// 用户复制了「其它内容」（非用例/步骤的原生文本复制）时，清空我方剪贴板高亮 —— 参考 Excel：
// 复制别的东西，蚁行线就消失。我方 case/step 复制走 copyClipboard.set，会置抑制标记跳过这里。
if (typeof document !== "undefined") {
  document.addEventListener("copy", () => {
    if (suppressNextNativeClear) return;
    copyClipboard.clear();
  });
}

/** 订阅剪贴板变化（供 UI 反映「有没有可粘贴内容」/ 高亮）。 */
export function useCopyClipboard(): CopyClipboardItem | null {
  return useSyncExternalStore(copyClipboard.subscribe, copyClipboard.get, copyClipboard.get);
}
