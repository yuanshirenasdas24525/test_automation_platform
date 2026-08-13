import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui 官方的 class 合并工具 —— 让 Tailwind 的条件 / 冲突类名合并更稳。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
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
