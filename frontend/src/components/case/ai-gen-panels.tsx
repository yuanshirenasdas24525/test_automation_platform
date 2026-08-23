/**
 * AI 生成抽屉的两个透明度面板（第一步：加进现有抽屉，不动生成逻辑）：
 *  - FeatureChecklistPanel（#2）：当前功能「该测什么」+ 每个要点覆盖了几条。
 *  - PromptPreviewPanel（#1）：本次已渲染的提示词 + 生成流程（只读）。
 * 都是按需触发（点按钮才请求），失败不阻断主流程。
 */
import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ClipboardCheck, Loader2, ScanSearch, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { functionalCasesApi } from "@/lib/api";

const errMsg = (err: unknown) => (err instanceof Error ? err.message : String(err));

const COV_META: Record<string, { label: string; cls: string }> = {
  covered: { label: "已覆盖", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" },
  thin: { label: "偏薄", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  none: { label: "缺", cls: "bg-red-500/15 text-red-600 dark:text-red-400" },
};

type ChecklistAspect = {
  aspect: string;
  what_to_test: string;
  gap_hint: string;
  covered_cases: string[];
  covered_case_ids: number[];
  covered_count: number;
  coverage: "covered" | "thin" | "none";
};
type ChecklistData = { aspects: ChecklistAspect[]; summary: { total: number; covered: number; gaps: number } };
const CK_KEY = (mid: number, mode: string) => `feature-checklist:v2:${mode}:${mid}`;

/** #2 功能测试要点 Checklist —— 结果按模块缓存到 localStorage，打开即显示；
 * 用例有增删（caseSignature 变化）时提示可重新分析，其余时间保留上次结果。 */
export function FeatureChecklistPanel({
  moduleId,
  modelName,
  requirementText,
  caseSignature,
  mode = "functional",
  onFilterAspect,
  onSupplement,
}: {
  moduleId: number | null;
  modelName: string;
  requirementText: string;
  /** functional=功能要点；interface=接口要点；web/android/ios=UI 要点。 */
  mode?: "functional" | "interface" | "web" | "android" | "ios";
  /** 当前模块用例的签名（增删会变），用于判断缓存的分析是否过期。 */
  caseSignature: string;
  /** 点某个要点 → 用它覆盖的用例 id 去筛主列表。缺省则要点不可点。 */
  onFilterAspect?: (caseIds: number[]) => void;
  /** 一键补充：针对某个偏薄/缺的要点，聚焦生成补充大纲。 */
  onSupplement?: (aspect: string, hint: string) => void;
}) {
  const [data, setData] = useState<ChecklistData | null>(null);
  const [savedSig, setSavedSig] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () =>
      functionalCasesApi.aiFeatureChecklist({
        module_id: moduleId as number,
        model_name: modelName,
        requirement_text: requirementText.trim(),
        mode,
      }),
  });
  const { reset } = m;

  // 切模块：加载该模块的缓存结果（没有则空），不再每次都要重新点分析
  useEffect(() => {
    reset();
    if (!moduleId) {
      setData(null);
      setSavedSig(null);
      return;
    }
    try {
      const raw = localStorage.getItem(CK_KEY(moduleId, mode));
      if (raw) {
        const c = JSON.parse(raw) as ChecklistData & { signature?: string };
        setData({ aspects: c.aspects ?? [], summary: c.summary });
        setSavedSig(c.signature ?? null);
        return;
      }
    } catch {
      /* ignore */
    }
    setData(null);
    setSavedSig(null);
  }, [moduleId, mode, reset]);

  const analyze = () =>
    m.mutate(undefined, {
      onSuccess: (res) => {
        const payload: ChecklistData = { aspects: res.aspects, summary: res.summary };
        setData(payload);
        setSavedSig(caseSignature);
        try {
          if (moduleId)
            localStorage.setItem(CK_KEY(moduleId, mode), JSON.stringify({ ...payload, signature: caseSignature, ts: Date.now() }));
        } catch {
          /* ignore */
        }
      },
    });

  const stale = !!data && savedSig !== null && savedSig !== caseSignature;

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="mb-2 flex items-center gap-2">
        <ClipboardCheck className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">该测什么 · {mode === "interface" ? "接口测试要点" : mode === "functional" ? "功能测试要点" : "UI 测试要点"}</span>
        {data ? (
          <>
            <span className="ml-auto text-xs text-muted-foreground">
              {data.summary.covered}/{data.summary.total} 已覆盖
              {data.summary.gaps > 0 ? <span className="text-amber-600 dark:text-amber-400"> · {data.summary.gaps} 项有缺口</span> : null}
            </span>
            <button
              className="text-xs text-primary hover:underline disabled:opacity-50"
              disabled={!moduleId || !modelName || m.isPending}
              onClick={analyze}
              title="重新分析要点与覆盖"
            >
              {m.isPending ? "分析中…" : "重新分析"}
            </button>
          </>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto h-7"
            disabled={!moduleId || !modelName || m.isPending}
            onClick={analyze}
            title={!modelName ? "先选一个 AI 模型" : "根据需求归纳该功能该测哪些方面 + 统计覆盖"}
          >
            {m.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ClipboardCheck className="h-3.5 w-3.5" />}
            {m.isPending ? "分析中…" : "分析要点"}
          </Button>
        )}
      </div>

      {stale ? (
        <p className="mb-1.5 rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-600 dark:text-amber-400">
          用例有增删，覆盖统计可能已过期，建议「重新分析」。
        </p>
      ) : null}
      {m.isError ? (
        <p className="text-xs text-red-600 dark:text-red-400">分析失败：{errMsg(m.error)}</p>
      ) : null}

      {data && data.aspects.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {data.aspects.map((a) => {
            const meta = COV_META[a.coverage] ?? COV_META.none;
            const clickable = !!onFilterAspect && a.covered_count > 0;
            return (
              <li
                key={a.aspect}
                className={cn(
                  "rounded-md border px-2.5 py-1.5",
                  a.coverage === "none" && "border-red-500/30 bg-red-500/5",
                  a.coverage === "thin" && "border-amber-500/30 bg-amber-500/5",
                  clickable && "cursor-pointer hover:border-primary/50 hover:bg-primary/5",
                )}
                title={clickable ? `点击筛选该要点覆盖的 ${a.covered_count} 条用例` : a.what_to_test}
                onClick={clickable ? () => onFilterAspect?.(a.covered_case_ids) : undefined}
              >
                <div className="flex items-center gap-2">
                  <span className="flex-1 truncate text-xs font-medium">{a.aspect}</span>
                  {clickable ? <span className="text-[10px] text-primary">筛选 ›</span> : null}
                  <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium tabular-nums", meta.cls)}>
                    {a.coverage === "none" ? "缺" : `${a.covered_count} 条 · ${meta.label}`}
                  </span>
                </div>
                {a.what_to_test ? (
                  <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">{a.what_to_test}</p>
                ) : null}
                {a.coverage !== "covered" ? (
                  <div className="mt-1.5 flex items-start gap-2 rounded bg-amber-500/10 px-2 py-1.5">
                    <span className="flex-1 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
                      <b className="font-medium">缺口：</b>
                      {a.gap_hint || "该方面覆盖偏少，建议补充更多正/反/边界分支。"}
                    </span>
                    {onSupplement ? (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onSupplement(a.aspect, a.gap_hint || a.what_to_test);
                        }}
                        className="shrink-0 rounded bg-primary px-2 py-0.5 text-[11px] font-medium text-primary-foreground hover:opacity-90"
                      >
                        一键补充 ›
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {data && data.aspects.length === 0 ? (
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
  const { mutate } = m;
  // 默认自动加载（选到该面板 / 换模块或配置即拉），不用点「查看」
  useEffect(() => {
    if (moduleId) mutate();
  }, [moduleId, mode, coverage, dimensions, mutate]);
  const data = m.data;

  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border bg-card p-3">
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
        <p className="text-xs text-red-600 dark:text-red-400">预览失败：{errMsg(m.error)}</p>
      ) : null}

      {data ? (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
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
          <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/40 p-2 text-[10.5px] leading-relaxed text-muted-foreground">
            {tab === "outline" ? data.outline.prompt : data.batch.prompt}
          </pre>
          <p className="text-[10px] text-muted-foreground">只读预览——这是本次实际发给模型的提示词（动态部分如本批测试点会在生成时填入）。</p>
        </div>
      ) : m.isPending ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在加载本次提示词与流程…</p>
      ) : (
        <p className="text-xs text-muted-foreground">点「查看」显示本次实际使用的提示词和生成流程，黑盒变白盒。</p>
      )}
    </div>
  );
}
