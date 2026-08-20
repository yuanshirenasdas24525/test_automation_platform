import { Braces } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// 用例 JSON 字段校验 + 「校验 JSON」按钮
// ---------------------------------------------------------------------------
// 单请求编辑器(CaseDialog)和多步骤编辑器(step-editor)共用这一份逻辑，避免两边
// 各写一遍解析/占位符处理。
//
// 运行期会 `rep_expr` 把 ${var} 替换成参数池里的值，为了让 JSON.parse 不挂，
// 这里先把 ${...} 暂时替换成一个合法的 JSON 字面量再校验。
//
// 历史坑：原来用 '"__placeholder__"'（带引号）替换 —— 假定用户写的是
//   { "x": ${var} }     ← 裸用作 value
// 但实际上常见且推荐的写法是字符串内嵌：
//   { "x": "Bearer ${token}" }
// 替换之后变成 `{ "x": "Bearer "__placeholder__"" }`，JSON.parse 直接挂。
// 用户因此被迫把 ${var} 改成 $.{var} / $.var 才能保存，丢失了真正的变量替换语义。
//
// 现在改用临时 token：字符串内嵌占位符直接替换 token，裸值占位符替换成
// 带引号的 token。这样 JSON.parse 和 JSON.stringify 都能跑，格式化后再恢复原文。
type PlaceholderToken = {
  token: string;
  original: string;
  inString: boolean;
};

function isInsideJsonString(text: string, index: number): boolean {
  let inString = false;
  let escaped = false;
  for (let i = 0; i < index; i += 1) {
    const ch = text[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\") {
      escaped = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
    }
  }
  return inString;
}

function maskPlaceholdersForParse(text: string): {
  candidate: string;
  placeholders: PlaceholderToken[];
} {
  const placeholders: PlaceholderToken[] = [];
  const candidate = text.replace(/\$\{[^}\n]*\}/g, (original, offset: number) => {
    const inString = isInsideJsonString(text, offset);
    const token = `__JSON_PLACEHOLDER_${placeholders.length}__`;
    placeholders.push({ token, original, inString });
    return inString ? token : `"${token}"`;
  });
  return { candidate, placeholders };
}

function restorePlaceholders(text: string, placeholders: PlaceholderToken[]): string {
  return placeholders.reduce((next, item) => {
    if (!item.inString) {
      return next.replaceAll(`"${item.token}"`, item.original);
    }
    return next.replaceAll(item.token, item.original);
  }, text);
}

export type JsonCheck =
  | { state: "empty" }
  | { state: "ok"; pretty: string; compact: string; parsed: unknown }
  | { state: "error"; message: string };

export function toFieldText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function checkJson(text: unknown): JsonCheck {
  const s = toFieldText(text).trim();
  if (!s) return { state: "empty" };
  const { candidate, placeholders } = maskPlaceholdersForParse(s);
  try {
    const parsed = JSON.parse(candidate);
    // pretty / compact：先对临时 token 做格式化，再把 `${var}` 恢复回去。裸占位符仍
    // 保持裸值，字符串内嵌占位符仍保持字符串的一部分，避免改变运行时替换语义。
    let pretty = s;
    let compact = s;
    try {
      pretty = restorePlaceholders(JSON.stringify(parsed, null, 2), placeholders);
      compact = restorePlaceholders(JSON.stringify(parsed), placeholders);
    } catch {
      pretty = s;
      compact = s;
    }
    return { state: "ok", pretty, compact, parsed };
  } catch (e) {
    return { state: "error", message: (e as Error).message || "JSON 解析失败" };
  }
}

/** 提取参数：只要求整体是 JSON 对象；值可以是 JSONPath / function，也可以是常量兜底值。 */
export function checkExtract(text: string | undefined | null): JsonCheck {
  const base = checkJson(text);
  if (base.state !== "ok") return base;
  if (typeof base.parsed !== "object" || base.parsed === null || Array.isArray(base.parsed)) {
    return { state: "error", message: "提取参数必须是一个 JSON 对象" };
  }
  for (const k of Object.keys(base.parsed as Record<string, unknown>)) {
    if (!k.trim()) {
      return { state: "error", message: "提取参数的 key 不能为空" };
    }
  }
  return base;
}

/** 断言：只要求整体是 JSON 对象；key 可用 JSONPath / sql，也可用 status_code 等响应字段名。 */
export function checkAssertion(text: string | undefined | null): JsonCheck {
  const base = checkJson(text);
  if (base.state !== "ok") return base;
  if (typeof base.parsed !== "object" || base.parsed === null || Array.isArray(base.parsed)) {
    return { state: "error", message: "断言必须是一个 JSON 对象" };
  }
  for (const k of Object.keys(base.parsed as Record<string, unknown>)) {
    if (!k.trim()) {
      return { state: "error", message: "断言的 key 不能为空" };
    }
  }
  return base;
}

/** Headers：必须是对象，key/value 都是字符串。 */
export function checkHeaders(text: string | undefined | null): JsonCheck {
  const base = checkJson(text);
  if (base.state !== "ok") return base;
  if (typeof base.parsed !== "object" || base.parsed === null || Array.isArray(base.parsed)) {
    return { state: "error", message: "请求头必须是一个 JSON 对象" };
  }
  return base;
}

/**
 * 「校验 JSON」按钮：点一次格式化，再点一次压缩成一行（无多余空格），如此往复。
 * 校验失败弹错误 toast，内容为空则提示。
 */
export function JsonValidateButton({
  value,
  onChange,
  check,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  check?: (text: string) => JsonCheck;
  className?: string;
}) {
  const handleToggle = () => {
    const fn = check ?? checkJson;
    const r = fn(value);
    if (r.state === "empty") {
      toast.info("内容为空");
      return;
    }
    if (r.state === "error") {
      toast.error("JSON 格式错误：" + r.message);
      return;
    }
    // 当前已是格式化(pretty)形态 → 压缩成一行；否则(含压缩形态/手写形态) → 格式化。
    if (value.trim() === r.pretty.trim()) {
      if (r.compact === r.pretty.trim()) {
        toast.success("JSON 格式正确");
      } else {
        onChange(r.compact);
        toast.success("已压缩为一行");
      }
    } else {
      onChange(r.pretty);
      toast.success("已格式化");
    }
  };

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn(
        "h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground",
        className,
      )}
      onClick={handleToggle}
    >
      <Braces className="h-3 w-3" />
      校验 JSON
    </Button>
  );
}
