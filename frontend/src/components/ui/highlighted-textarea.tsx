import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * 一个带轻量语法高亮的 Textarea，主要是给用例表单里的 headers / params / extract / assertion /
 * sql_query 这几个字段用的，让 `${var}`、`$.json.path`、`function:xxx`、`sql:select ...` 这些
 * 特殊语法在输入时一眼能看出来。
 *
 * 实现思路：textarea 本身 text 设成 transparent，caret 保留，后面垫一层 <pre>，按 token 上色。
 * 两层 padding/font/line-height/border 必须严格一致，否则光标会对不上字符。
 */

type Token = { text: string; className?: string };

// 几个需要高亮的 token。顺序就是匹配优先级，基本上互不重叠：
//   ${...}                         → 蓝色（变量占位符，来自 extra_pool 的替换）
//   $.something[0].x / $[0].x      → 紫色（JSONPath，用于 extract_data / assertion 左值）
//   function:xxx(args?)            → 绿色（helper：随机时间、签名、转换等）
//   sql:select ... (到行尾)        → 橙色（断言里的 sql: 前缀）
const HIGHLIGHT_RE =
  /(\$\{[^}\n]*\})|(\$(?:\.[A-Za-z0-9_[\]]+|\[[^\]\n]*\])+)|(\bfunction:[A-Za-z_][\w.]*(?:\([^)\n]*\))?)|(\bsql:[^\n]*)/g;

function tokenize(text: string): Token[] {
  if (!text) return [];
  const tokens: Token[] = [];
  let lastIndex = 0;
  // Reset lastIndex explicitly — HIGHLIGHT_RE 是模块级单例，每次用前要清掉上次的 state
  HIGHLIGHT_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = HIGHLIGHT_RE.exec(text)) !== null) {
    if (m.index > lastIndex) {
      tokens.push({ text: text.slice(lastIndex, m.index) });
    }
    let cls = "";
    if (m[1]) cls = "text-sky-600 font-medium";
    else if (m[2]) cls = "text-fuchsia-600 font-medium";
    else if (m[3]) cls = "text-emerald-600 font-medium";
    else if (m[4]) cls = "text-amber-600 font-medium";
    tokens.push({ text: m[0], className: cls });
    lastIndex = m.index + m[0].length;
    // 防御性：零宽匹配时要手动推进，不然会死循环
    if (m[0].length === 0) HIGHLIGHT_RE.lastIndex++;
  }
  if (lastIndex < text.length) {
    tokens.push({ text: text.slice(lastIndex) });
  }
  return tokens;
}

// 两层共享的排版：把 pre 和 textarea 的字号 / 行高 / 字体 / 内边距 / 边框粗细都绑死到这里，
// 改样式时务必两层同步修改，否则会错位。
const SHARED_TYPO =
  "block w-full rounded-md border px-3 py-2 text-sm font-mono leading-6 " +
  "whitespace-pre-wrap break-words";

export interface HighlightedTextareaProps
  extends Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  /** 覆盖到最外层容器，用于外部控制尺寸。textarea 默认跟随外层宽度。 */
  containerClassName?: string;
  /** 失效态时的样式提示（比如 JSON 校验失败），由调用方决定；这里只负责呈现 */
  invalid?: boolean;
}

export const HighlightedTextarea = React.forwardRef<
  HTMLTextAreaElement,
  HighlightedTextareaProps
>(function HighlightedTextarea(
  {
    className,
    containerClassName,
    value,
    onChange,
    invalid,
    rows = 3,
    spellCheck = false,
    ...props
  },
  ref,
) {
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const preRef = React.useRef<HTMLPreElement>(null);
  React.useImperativeHandle(ref, () => textareaRef.current as HTMLTextAreaElement);

  const safeValue = value ?? "";
  const tokens = React.useMemo(() => tokenize(String(safeValue)), [safeValue]);

  // pre 的滚动要跟 textarea 同步，长文本才不会错位
  const syncScroll = React.useCallback(() => {
    const ta = textareaRef.current;
    const pre = preRef.current;
    if (!ta || !pre) return;
    pre.scrollTop = ta.scrollTop;
    pre.scrollLeft = ta.scrollLeft;
  }, []);

  return (
    <div className={cn("relative", containerClassName)}>
      <pre
        ref={preRef}
        aria-hidden="true"
        className={cn(
          SHARED_TYPO,
          "pointer-events-none absolute inset-0 m-0 overflow-hidden",
          // 边框透明但占位，保证 pre 的内容框和 textarea 的内容框宽度一致
          "border-transparent bg-transparent text-foreground",
        )}
      >
        {tokens.map((t, i) =>
          t.className ? (
            <span key={i} className={t.className}>
              {t.text}
            </span>
          ) : (
            <React.Fragment key={i}>{t.text}</React.Fragment>
          ),
        )}
        {/* 末尾留一个空白，处理以 \n 结尾时 pre 不换行的 quirk */}
        {"\n"}
      </pre>
      <textarea
        ref={textareaRef}
        value={safeValue}
        onChange={onChange}
        onScroll={syncScroll}
        rows={rows}
        spellCheck={spellCheck}
        className={cn(
          SHARED_TYPO,
          "relative resize-y bg-transparent text-transparent caret-foreground",
          "placeholder:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          invalid
            ? "border-destructive focus-visible:ring-destructive"
            : "border-input",
          // 选中态 — 让选中背景稍微淡一点，不然会把 pre 里的高亮字完全盖住
          "selection:bg-primary/25",
          className,
        )}
        {...props}
      />
    </div>
  );
});

HighlightedTextarea.displayName = "HighlightedTextarea";
