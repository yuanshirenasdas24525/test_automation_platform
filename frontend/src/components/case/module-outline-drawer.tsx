/**
 * 模块大纲面板 —— 可嵌入（融进「AI 生成用例」抽屉的 Tab 里）。
 *
 * - 常态：展示模块长期保存的测试点（覆盖 / 缺口）。
 * - 刷新对齐：大纲 ↔ 当前用例 diff → 预览 → 应用。
 * - AI 重新规划：基于现有大纲 + 本次变更增量补点 → 同一 diff 预览 → 应用。
 * - 清理未覆盖：删掉没有关联用例的测试点，只保留同步自真实用例的点。
 *
 * 设计见 docs/module_outline_design.md。
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";

import { moduleOutlineApi, aiModelsApi, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  OutlineAlignChange,
  OutlineAlignPreview,
  OutlineReplanPreview,
} from "@/types/domain";

type Preview =
  | { source: "align"; data: OutlineAlignPreview }
  | { source: "replan"; data: OutlineReplanPreview };

const OP_LABEL: Record<string, string> = {
  added: "新增",
  linked: "关联",
  renamed: "改名",
  orphaned: "失联",
};

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "操作失败";
}

/** 可嵌入的模块大纲面板（不含抽屉外壳）。 */
export function ModuleOutlinePanel({
  moduleId,
  mode = "interface",
  onApplied,
}: {
  moduleId: number | null;
  mode?: string;
  onApplied?: () => void;
}) {
  const qc = useQueryClient();
  const [digestOpen, setDigestOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [replanOpen, setReplanOpen] = useState(false);
  const [changeText, setChangeText] = useState("");
  const [incremental, setIncremental] = useState(true);
  const [modelName, setModelName] = useState("");

  const outlineKey = ["module-outline", moduleId] as const;
  const outlineQuery = useQuery({
    queryKey: outlineKey,
    queryFn: () => moduleOutlineApi.get(moduleId as number),
    enabled: moduleId != null,
  });
  const outline = outlineQuery.data ?? null;

  const modelsQuery = useQuery({
    queryKey: ["ai-models"],
    queryFn: () => aiModelsApi.list(),
    enabled: replanOpen,
  });
  const models = useMemo(() => (modelsQuery.data ?? []).filter((m) => m.enabled), [modelsQuery.data]);
  const effectiveModel = modelName || models.find((m) => m.is_default)?.name || models[0]?.name || "";

  const refresh = () => qc.invalidateQueries({ queryKey: outlineKey });

  const previewMut = useMutation({
    mutationFn: () => moduleOutlineApi.alignPreview(moduleId as number, mode),
    onSuccess: (data) => {
      setReplanOpen(false);
      setPreview({ source: "align", data });
      if (data.changes.length === 0) toast.info("大纲已和当前用例一致，无需变更");
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const replanMut = useMutation({
    mutationFn: () =>
      moduleOutlineApi.replanPreview({
        moduleId: moduleId as number,
        modelName: effectiveModel,
        changeText,
        mode,
        incremental,
      }),
    onSuccess: (data) => {
      setReplanOpen(false);
      setPreview({ source: "replan", data });
      if (data.changes.length === 0) toast.info("没有需要新增的测试点");
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const applyMut = useMutation({
    mutationFn: () => {
      if (!preview) throw new Error("no preview");
      if (preview.source === "align") return moduleOutlineApi.applyAlign(moduleId as number, mode);
      return moduleOutlineApi.replanApply({
        moduleId: moduleId as number,
        mode,
        digest: preview.data.digest,
        points: preview.data.points,
      });
    },
    onSuccess: () => {
      setPreview(null);
      refresh();
      onApplied?.();
      toast.success("大纲已更新");
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const purgeMut = useMutation({
    mutationFn: () => moduleOutlineApi.purgeGaps(moduleId as number, mode),
    onSuccess: (d) => {
      refresh();
      toast.success(`已清理 ${d.removed} 个未覆盖测试点`);
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const points = outline?.points ?? [];
  const firstAlign = preview?.source === "align" && !preview.data.has_outline;

  return (
    <div className="flex flex-col gap-3">
      {/* 工具条 */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => { setReplanOpen(false); previewMut.mutate(); }}
          disabled={moduleId == null || previewMut.isPending}
          className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-[12.5px] font-medium hover:bg-accent disabled:opacity-50"
        >
          {previewMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          刷新对齐
        </button>
        <button
          onClick={() => { setPreview(null); setReplanOpen((v) => !v); }}
          disabled={moduleId == null}
          className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 px-3 py-1.5 text-[12.5px] text-primary hover:bg-accent disabled:opacity-50"
        >
          <Sparkles className="h-3.5 w-3.5" />
          AI 重新规划
        </button>
        <button
          onClick={() => {
            if (window.confirm("清理所有没有关联用例的测试点？只保留同步自真实用例的点。")) purgeMut.mutate();
          }}
          disabled={moduleId == null || purgeMut.isPending || (outline?.gap_count ?? 0) === 0}
          title="删掉没有对应用例的缺口点"
          className="inline-flex items-center gap-1.5 rounded-md border border-input px-3 py-1.5 text-[12.5px] text-muted-foreground hover:bg-accent disabled:opacity-50"
        >
          {purgeMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
          清理未覆盖
        </button>
        {outline?.last_aligned_at ? (
          <span className="ml-auto text-[11px] text-muted-foreground">
            上次对齐 {new Date(outline.last_aligned_at).toLocaleString()}
          </span>
        ) : null}
      </div>

      {/* digest 折叠 */}
      {outline?.digest ? (
        <div className="rounded-md border px-3 py-2">
          <button onClick={() => setDigestOpen((v) => !v)} className="flex w-full items-center justify-between text-xs text-muted-foreground">
            <span>需求摘要 digest</span>
            {digestOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
          {digestOpen ? (
            <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-secondary-foreground">{outline.digest}</p>
          ) : null}
        </div>
      ) : null}

      {/* AI 重新规划表单 */}
      {replanOpen ? (
        <div className="rounded-md border p-3">
          <ReplanForm
            changeText={changeText}
            setChangeText={setChangeText}
            incremental={incremental}
            setIncremental={setIncremental}
            models={models}
            modelName={effectiveModel}
            setModelName={setModelName}
            existingCount={points.length}
            running={replanMut.isPending}
            onSubmit={() => {
              if (!effectiveModel) { toast.error("请先在 AI 模型配置里启用一个模型"); return; }
              if (!changeText.trim()) { toast.error("请填写本次变更 / 新增需求"); return; }
              replanMut.mutate();
            }}
            onCancel={() => setReplanOpen(false)}
          />
        </div>
      ) : null}

      {/* diff 预览 */}
      {preview ? (
        <div className="rounded-md border p-3">
          {firstAlign ? (
            <p className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800">
              首次对齐：将根据现有 {preview.data.changes.length} 条用例建立初始大纲。
            </p>
          ) : null}
          <AlignDiff changes={preview.data.changes} />
          <div className="mt-3 flex items-center justify-end gap-2">
            <button onClick={() => setPreview(null)} className="rounded-md border border-input px-3.5 py-1.5 text-[13px] hover:bg-accent">
              取消
            </button>
            <button
              onClick={() => applyMut.mutate()}
              disabled={applyMut.isPending || preview.data.changes.length === 0}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-[13px] font-medium text-primary-foreground disabled:opacity-50"
            >
              {applyMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              应用变更
            </button>
          </div>
        </div>
      ) : null}

      {/* 大纲列表 */}
      {outlineQuery.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 加载大纲…
        </div>
      ) : !outline || points.length === 0 ? (
        <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
          该模块暂无大纲。点「刷新对齐」按现有用例建立，或用右侧「生成用例」规划后由关联落库。
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>测试点（{points.length}）</span>
            <span className="flex gap-2">
              <span className="text-green-700">覆盖 {outline.covered_count}</span>
              <span className="text-red-700">缺口 {outline.gap_count}</span>
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            {points.map((p) => (
              <div
                key={p.id}
                className={cn(
                  "flex items-center gap-2 rounded-md border px-2.5 py-2",
                  p.status === "gap" && "border-red-300 bg-red-50",
                )}
              >
                {p.status === "covered" ? (
                  <CircleCheck className="h-[15px] w-[15px] shrink-0 text-emerald-500" />
                ) : (
                  <AlertCircle className="h-[15px] w-[15px] shrink-0 text-red-500" />
                )}
                <span className={cn("flex-1 text-[12.5px]", p.status === "gap" && "text-red-800")}>{p.title}</span>
                {p.linked_case_id ? (
                  <span className="text-[11px] text-primary">#{p.linked_case_id}</span>
                ) : (
                  <span className="text-[11px] text-red-600">未覆盖</span>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ReplanForm(props: {
  changeText: string;
  setChangeText: (v: string) => void;
  incremental: boolean;
  setIncremental: (v: boolean) => void;
  models: { name: string; model: string }[];
  modelName: string;
  setModelName: (v: string) => void;
  existingCount: number;
  running: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const { changeText, setChangeText, incremental, setIncremental, models, modelName, setModelName, existingCount, running, onSubmit, onCancel } = props;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">基于现有大纲增量规划，只描述这次新增 / 变更的功能即可。</p>

      <div>
        <div className="mb-1.5 text-xs text-secondary-foreground">规划方式</div>
        <div className="flex gap-2">
          <button
            onClick={() => setIncremental(true)}
            className={cn("flex-1 rounded-md border px-2.5 py-2 text-left", incremental ? "border-primary/50 bg-accent" : "border-input")}
          >
            <div className={cn("text-[12.5px] font-medium", incremental && "text-primary")}>增量（推荐）</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">保留现有点，只补变更</div>
          </button>
          <button
            onClick={() => setIncremental(false)}
            className={cn("flex-1 rounded-md border px-2.5 py-2 text-left", !incremental ? "border-primary/50 bg-accent" : "border-input")}
          >
            <div className="text-[12.5px]">全量重来</div>
            <div className="mt-0.5 text-[11px] text-muted-foreground">按 change 全新规划</div>
          </button>
        </div>
      </div>

      <div>
        <div className="mb-1.5 text-xs text-secondary-foreground">本次变更 / 新增需求</div>
        <textarea
          value={changeText}
          onChange={(e) => setChangeText(e.target.value)}
          rows={4}
          placeholder="例：新增“手机验证码登录”；登录失败超过 5 次锁定账号 15 分钟…"
          className="w-full resize-none rounded-md border border-input bg-background px-2.5 py-2 text-xs outline-none focus:ring-1 focus:ring-ring"
        />
      </div>

      <div>
        <div className="mb-1.5 text-xs text-secondary-foreground">模型</div>
        <select
          value={modelName}
          onChange={(e) => setModelName(e.target.value)}
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none focus:ring-1 focus:ring-ring"
        >
          {models.length === 0 ? <option value="">（无可用模型）</option> : null}
          {models.map((m) => (
            <option key={m.name} value={m.name}>{m.name}（{m.model}）</option>
          ))}
        </select>
      </div>

      <p className="text-[11px] text-muted-foreground">现有 {existingCount} 个测试点会作为上下文给 AI，产出的增量以 diff 预览后再应用。</p>

      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-md border border-input px-3.5 py-1.5 text-[13px] hover:bg-accent">取消</button>
        <button
          onClick={onSubmit}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-[13px] font-medium text-primary-foreground disabled:opacity-50"
        >
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
          规划变更
        </button>
      </div>
    </div>
  );
}

function AlignDiff({ changes }: { changes: OutlineAlignChange[] }) {
  if (changes.length === 0) {
    return <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">无变更。</div>;
  }
  const added = changes.filter((c) => c.op === "added" || c.op === "linked").length;
  const renamed = changes.filter((c) => c.op === "renamed").length;
  const orphaned = changes.filter((c) => c.op === "orphaned").length;
  return (
    <>
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-medium">变更预览</span>
        <span className="text-muted-foreground">+{added} · ~{renamed} · −{orphaned}</span>
      </div>
      <div className="overflow-hidden rounded-md border font-mono text-[12px]">
        {changes.map((c, i) => (
          <DiffRow key={i} c={c} />
        ))}
      </div>
    </>
  );
}

function DiffRow({ c }: { c: OutlineAlignChange }) {
  const map: Record<string, { bg: string; fg: string; sign: string }> = {
    added: { bg: "bg-green-50", fg: "text-green-800", sign: "+" },
    linked: { bg: "bg-green-50", fg: "text-green-800", sign: "+" },
    renamed: { bg: "bg-amber-50", fg: "text-amber-800", sign: "~" },
    orphaned: { bg: "bg-red-50", fg: "text-red-800", sign: "−" },
    unchanged: { bg: "", fg: "text-secondary-foreground", sign: " " },
  };
  const s = map[c.op] ?? map.unchanged;
  return (
    <div className={cn("flex items-center gap-2 px-3 py-1.5", s.bg, s.fg)}>
      <span className="w-3.5 text-center">{s.sign}</span>
      <span className={cn("flex-1", c.op === "orphaned" && "line-through")}>
        {c.op === "renamed" && c.old_title ? (
          <>
            {c.title}
            <span className="ml-1 text-muted-foreground line-through">（旧：{c.old_title}）</span>
          </>
        ) : (
          c.title
        )}
      </span>
      <span className="text-[10px] opacity-80">
        {OP_LABEL[c.op] ?? ""}
        {c.linked_case_id ? ` · #${c.linked_case_id}` : c.op === "orphaned" ? " · 用例已删" : ""}
      </span>
    </div>
  );
}
