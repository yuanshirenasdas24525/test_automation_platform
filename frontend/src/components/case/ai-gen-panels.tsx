/**
 * AI 生成抽屉的两个透明度面板（第一步：加进现有抽屉，不动生成逻辑）：
 *  - FeatureChecklistPanel（#2）：当前功能「该测什么」+ 每个要点覆盖了几条。
 *  - PromptPreviewPanel（#1）：本次已渲染的提示词 + 生成流程（只读）。
 * 都是按需触发（点按钮才请求），失败不阻断主流程。
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ClipboardCheck, Loader2, ScanSearch, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { functionalCasesApi, errorMessage } from "@/lib/api";

const COV_META: Record<string, { label: string; cls: string }> = {
  covered: { label: "已覆盖", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  thin: { label: "偏薄", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  none: { label: "缺", cls: "bg-red-500/15 text-red-600 dark:text-red-400" },
};

/** #2 功能测试要点 Checklist */
export function FeatureChecklistPanel({
  moduleId,
  modelName,
  requirementText,
}: {
  moduleId: number | null;
  modelName: string;
  requirementText: string;
}) {
  const m = useMutation({
    mutationFn: () =>
      functionalCasesApi.aiFeatureChecklist({
        module_id: moduleId as number,
        model_name: modelName,
        requirement_text: requirementText.trim(),
      }),
  });
  const data = m.data;

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-2 flex items-center gap-2">
        <ClipboardCheck className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">该测什么 · 功能测试要点</span>
        {data ? (
          <span className="ml-auto text-xs text-muted-foreground">
            {data.summary.covered}/{data.summary.total} 已覆盖
            {data.summary.gaps > 0 ? <span className="text-amber-600 dark:text-amber-400"> · {data.summary.gaps} 项有缺口</span> : null}
          </span>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto h-7"
            disabled={!moduleId || !modelName || m.isPending}
            onClick={() => m.mutate()}
            title={!modelName ? "先选一个 AI 模型" : "根据需求归纳该功能该测哪些方面 + 统计覆盖"}
          >
            {m.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ClipboardCheck className="h-3.5 w-3.5" />}
            {m.isPending ? "分析中…" : "分析要点"}
          </Button>
        )}
      </div>

      {m.isError ? (
        <p className="text-xs text-red-600 dark:text-red-400">分析失败：{errorMessage(m.error)}</p>
      ) : null}
      {data?.warning ? <p className="text-xs text-amber-600 dark:text-amber-400">{data.warning}</p> : null}

      {data && data.aspects.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {data.aspects.map((a) => {
            const meta = COV_META[a.coverage] ?? COV_META.none;
            return (
              <li
                key={a.aspect}
                className={cn(
                  "rounded-md border px-2.5 py-1.5",
                  a.coverage === "none" && "border-red-500/30 bg-red-500/5",
                  a.coverage === "thin" && "border-amber-500/30 bg-amber-500/5",
                )}
                title={a.what_to_test}
              >
                <div className="flex items-center gap-2">
                  <span className="flex-1 truncate text-xs font-medium">{a.aspect}</span>
                  <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums", meta.cls)}>
                    {a.coverage === "none" ? "缺" : `${a.covered_count} 条 · ${meta.label}`}
                  </span>
                </div>
                {a.what_to_test ? (
                  <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">{a.what_to_test}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {data && data.aspects.length === 0 && !data.warning ? (
        <p className="text-xs text-muted-foreground">未归纳出测试要点，可补充需求后重试。</p>
      ) : null}
    </div>
  );
}

/** #1 提示词 / 流程预览（只读） */
export function PromptPreviewPanel({
  moduleId,
  mode,
  coverage,
  dimensions,
  requirementText,
}: {
  moduleId: number | null;
  mode: "functional" | "interface";
  coverage: string;
  dimensions: string;
  requirementText: string;
}) {
  const [tab, setTab] = useState<"outline" | "batch">("outline");
  const m = useMutation({
    mutationFn: () =>
      functionalCasesApi.aiPromptPreview({
        module_id: moduleId as number,
        mode,
        coverage,
        dimensions,
        requirement_text: requirementText.trim(),
      }),
  });
  const data = m.data;

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-2 flex items-center gap-2">
        <ScanSearch className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">AI 怎么生成 · 提示词与流程</span>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto h-7"
          disabled={!moduleId || m.isPending}
          onClick={() => m.mutate()}
        >
          {m.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ScanSearch className="h-3.5 w-3.5" />}
          {data ? "刷新" : "查看"}
        </Button>
      </div>

      {m.isError ? (
        <p className="text-xs text-red-600 dark:text-red-400">预览失败：{errorMessage(m.error)}</p>
      ) : null}

      {data ? (
        <div className="flex flex-col gap-2">
          {/* 流程 */}
          <ol className="flex flex-col gap-1">
            {data.flow.map((f) => (
              <li key={f.step} className="flex gap-2 text-[11px] leading-snug">
                <ChevronRight className="mt-0.5 h-3 w-3 flex-none text-primary" />
                <span>
                  <b className="font-medium">{f.step}</b>
                  <span className="text-muted-foreground"> — {f.desc}</span>
                </span>
              </li>
            ))}
          </ol>
          {/* 提示词（只读） */}
          <div className="flex items-center gap-1 text-xs">
            <button
              onClick={() => setTab("outline")}
              className={cn("rounded px-2 py-0.5", tab === "outline" ? "bg-muted font-medium" : "text-muted-foreground")}
            >
              大纲提示词
            </button>
            <button
              onClick={() => setTab("batch")}
              className={cn("rounded px-2 py-0.5", tab === "batch" ? "bg-muted font-medium" : "text-muted-foreground")}
            >
              用例提示词
            </button>
            <span className="ml-auto font-mono text-[10px] text-muted-foreground">
              {tab === "outline" ? data.outline.template : data.batch.template}
            </span>
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-2 text-[10.5px] leading-relaxed text-muted-foreground">
            {tab === "outline" ? data.outline.prompt : data.batch.prompt}
          </pre>
          <p className="text-[10px] text-muted-foreground">只读预览——这是本次实际发给模型的提示词（动态部分如本批测试点会在生成时填入）。</p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">点「查看」显示本次实际使用的提示词和生成流程，黑盒变白盒。</p>
      )}
    </div>
  );
}
