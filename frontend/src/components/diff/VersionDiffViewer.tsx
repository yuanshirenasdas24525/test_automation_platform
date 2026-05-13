import { useMemo } from "react";
import DiffMatchPatch from "diff-match-patch";

/**
 * 行级 diff —— diff_main + diff_linesToChars 让插入/删除按整行对齐，
 * 比字符级 diff 更适合 markdown 文档比较。
 */
function buildLineDiff(before: string, after: string) {
  const dmp = new DiffMatchPatch();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const a = (dmp as any).diff_linesToChars_(before, after);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const diffs = (dmp as any).diff_main(a.chars1, a.chars2, false) as [
    number,
    string,
  ][];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (dmp as any).diff_charsToLines_(diffs, a.lineArray);
  return diffs;
}

interface Props {
  before: string;
  after: string;
  beforeLabel?: string;
  afterLabel?: string;
}

export function VersionDiffViewer({
  before,
  after,
  beforeLabel,
  afterLabel,
}: Props) {
  const diffs = useMemo(() => buildLineDiff(before, after), [before, after]);

  const stats = useMemo(() => {
    let added = 0;
    let removed = 0;
    for (const [op, text] of diffs) {
      const lines = text.split("\n").filter((l) => l.length > 0).length;
      if (op === 1) added += lines;
      if (op === -1) removed += lines;
    }
    return { added, removed };
  }, [diffs]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <div>
          {beforeLabel ?? "旧版本"} → {afterLabel ?? "新版本"}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-emerald-600">+{stats.added} 行新增</span>
          <span className="text-rose-600">−{stats.removed} 行删除</span>
        </div>
      </div>
      <pre className="max-h-[60vh] overflow-auto rounded border bg-muted/30 p-3 text-xs leading-5">
        {diffs.map(([op, text], idx) => {
          const lines = text.split("\n");
          // text 拆 lines 后末尾可能多一个空 string，过滤
          return lines.map((line, li) => {
            const isLast = li === lines.length - 1 && line === "";
            if (isLast) return null;
            if (op === 1) {
              return (
                <div
                  key={`${idx}-${li}`}
                  className="bg-emerald-100/60 text-emerald-900"
                >
                  + {line}
                </div>
              );
            }
            if (op === -1) {
              return (
                <div
                  key={`${idx}-${li}`}
                  className="bg-rose-100/60 text-rose-900"
                >
                  − {line}
                </div>
              );
            }
            return (
              <div key={`${idx}-${li}`} className="text-foreground/80">
                {"  "}
                {line}
              </div>
            );
          });
        })}
      </pre>
    </div>
  );
}
