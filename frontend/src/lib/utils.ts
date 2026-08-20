import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui 官方的 class 合并工具 —— 让 Tailwind 的条件 / 冲突类名合并更稳。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * 判断点击目标是否属于应自行保留焦点的交互控件。
 *
 * 用例列表和步骤编辑器会把非表单区域聚焦到外层容器，以接收复制、粘贴快捷键；
 * 如果点击来自输入框、按钮或 Radix 等组件生成的可聚焦节点，外层就不能再次抢焦点。
 */
export function isInteractiveClickTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return target.closest([
    "input",
    "textarea",
    "select",
    "option",
    "button",
    "a[href]",
    "label",
    '[contenteditable]:not([contenteditable="false"])',
    '[tabindex]:not([tabindex="-1"])',
    '[role="button"]',
    '[role="checkbox"]',
    '[role="combobox"]',
    '[role="link"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="radio"]',
    '[role="slider"]',
    '[role="spinbutton"]',
    '[role="switch"]',
    '[role="textbox"]',
  ].join(",")) !== null;
}

/**
 * 把富文本 HTML 还原成纯文本（用于列表预览，避免 `<h1><strong>…` 之类标签漏到界面上）。
 * 描述可能是正常 HTML，也可能是被转义存储的 HTML（&lt;p&gt;…）——
 * 反复「解码实体 + 去标签」直到稳定，确保两种情况都还原成纯文本。
 */
export function stripHtml(html: string | null | undefined): string {
  if (!html) return "";
  let text = html;
  for (let i = 0; i < 3; i++) {
    const doc = new DOMParser().parseFromString(text, "text/html");
    const stripped = (doc.body.textContent || "").replace(/<[^>]+>/g, "");
    if (stripped === text) break;
    text = stripped;
  }
  return text.replace(/\s+/g, " ").trim();
}
