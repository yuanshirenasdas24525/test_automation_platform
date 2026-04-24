import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui 官方的 class 合并工具 —— 让 Tailwind 的条件 / 冲突类名合并更稳。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
