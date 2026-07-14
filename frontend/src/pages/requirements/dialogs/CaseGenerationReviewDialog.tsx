/**
 * M7 AI 生成用例 + 草稿评审（ReviewDialog）。
 *
 * 一个对话框走完整个飞轮：
 *   触发生成（模型 × 条数 × 场景配比）→ 轮询草稿 → 勾选/行内编辑 →
 *   批量采纳入库（后端计算 edit_ratio）/ 拒绝（带原因,回填 prompt 反例）
 *
 * 评审信号（采纳率/拒因/编辑距离）是 AI 质量飞轮的数据来源——
 * 所以拒绝时强烈引导填写原因。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, ChevronDown, ChevronRight, Loader2, Sparkles, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { aiCaseGenerationApi, aiModelsApi, ApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  AiCaseDraft,
  CaseGenerationScenarioMix,
  Requirement,
} from "@/types/domain";

interface Props {
  open: boolean;
  requirement: Requirement | null;
  onClose: () => void;
}

const SCENARIO_MIX_OPTIONS: Array<{ value: CaseGenerationScenarioMix; label: string }> = [
  { value: "positive_only", label: "只要正向（happy path）" },
  { value: "positive_and_negative", label: "正向 + 常见异常（约 2:1）" },
  { value: "all_scenarios", label: "全场景（正向+异常+边界+安全）" },
];

export function CaseGenerationReviewDialog({ open, requirement, onClose }: Props) {
  const qc = useQueryClient();
  const projectId = requirement?.project_id;

  // ── 触发生成 ──────────────────────────────────────────────
  const modelsQuery = useQuery({
    queryKey: projectId ? queryKeys.aiModels(projectId) : ["ai-models", "none"],
    queryFn: () => aiModelsApi.list(projectId as number),
    enabled: open && projectId != null,
  });
  const enabledModels = useMemo(
    () => (modelsQuery.data ?? []).filter((m) => m.enabled),
    [modelsQuery.data],
  );
  const [modelName, setModelName] = useState("");
  const [count, setCount] = useState(8);
  const [mix, setMix] = useState<CaseGenerationScenarioMix>("positive_and_negative");
  const [userPrompt, setUserPrompt] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (open) {
      const def = enabledModels.find((m) => m.is_default) ?? enabledModels[0];
      setModelName((prev) => prev || def?.name || "");
    } else {
      setGenerating(false);
    }
  }, [open, enabledModels]);

  // ── 草稿列表（生成期间 3s 轮询）───────────────────────────
  const draftsQuery = useQuery({
    queryKey: ["ai-case-drafts", requirement?.id],
    queryFn: () =>
      aiCaseGenerationApi.listDrafts({ requirement_id: requirement!.id, status: "pending" }),
    enabled: open && requirement != null,
    refetchInterval: generating ? 3000 : false,
  });
  const drafts = useMemo(() => draftsQuery.data ?? [], [draftsQuery.data]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const prevCountRef = useRef(0);

  // 生成期间草稿数量涨了 → 到货,停轮询
  useEffect(() => {
    if (generating && drafts.length > prevCountRef.current) setGenerating(false);
    prevCountRef.current = drafts.length;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drafts.length]);

  const invalidateDrafts = () =>
    qc.invalidateQueries({ queryKey: ["ai-case-drafts", requirement?.id] });

  const handleError = (e: unknown) =>
    toast.error(e instanceof ApiError ? e.message : "操作失败,请重试");

  const triggerMutation = useMutation({
    mutationFn: () =>
      aiCaseGenerationApi.trigger({
        requirement_ids: [requirement!.id],
        model_names: [modelName],
        count_per_requirement: count,
        scenario_mix: mix,
        user_prompt: userPrompt.trim() || undefined,
      }),
    onSuccess: () => {
      setGenerating(true);
      toast.success("生成任务已提交,草稿会陆续出现在下方列表");
    },
    onError: handleError,
  });

  const commitMutation = useMutation({
    mutationFn: (ids: number[]) => aiCaseGenerationApi.commit({ draft_ids: ids }),
    onSuccess: (res) => {
      toast.success(
        `已采纳入库 ${res.created_case_ids.length} 条` +
          (res.skipped.length ? `,跳过 ${res.skipped.length} 条` : ""),
      );
      setPicked(new Set());
      void invalidateDrafts();
    },
    onError: handleError,
  });

  const togglePick = (id: number) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allPicked = drafts.length > 0 && drafts.every((d) => picked.has(d.id));

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-3xl flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-600" />
            AI 生成用例：{requirement?.title}
          </DialogTitle>
          <DialogDescription>
            生成 → 评审勾选 → 采纳入库。被采纳的草稿会成为后续生成的风格样例,拒绝原因会让 AI 避开同类问题。
          </DialogDescription>
        </DialogHeader>

        {/* 触发区 */}
        <div className="grid grid-cols-2 gap-3 rounded-md border p-3 md:grid-cols-4">
          <div className="col-span-2 space-y-1 md:col-span-1">
            <Label className="text-xs">模型</Label>
            <Select value={modelName} onValueChange={setModelName}>
              <SelectTrigger className="h-8">
                <SelectValue placeholder="选择模型" />
              </SelectTrigger>
              <SelectContent>
                {enabledModels.map((m) => (
                  <SelectItem key={m.name} value={m.name}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">条数</Label>
            <Input
              type="number"
              min={1}
              max={30}
              value={count}
              onChange={(e) => setCount(Math.min(30, Math.max(1, Number(e.target.value) || 1)))}
              className="h-8"
            />
          </div>
          <div className="col-span-2 space-y-1">
            <Label className="text-xs">场景配比</Label>
            <Select value={mix} onValueChange={(v) => setMix(v as CaseGenerationScenarioMix)}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SCENARIO_MIX_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="col-span-2 space-y-1 md:col-span-3">
            <Label className="text-xs">补充要求（可选）</Label>
            <Input
              value={userPrompt}
              onChange={(e) => setUserPrompt(e.target.value)}
              placeholder="如：重点覆盖权限相关场景"
              className="h-8"
            />
          </div>
          <div className="flex items-end">
            <Button
              size="sm"
              className="w-full"
              disabled={!modelName || triggerMutation.isPending || generating}
              onClick={() => triggerMutation.mutate()}
            >
              {triggerMutation.isPending || generating ? (
                <>
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                  生成中…
                </>
              ) : (
                "生成草稿"
              )}
            </Button>
          </div>
        </div>

        {/* 草稿列表 */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {draftsQuery.isLoading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">加载中…</div>
          ) : drafts.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              暂无待评审草稿。选好模型点「生成草稿」开始。
            </div>
          ) : (
            <div className="space-y-2 py-1">
              <label className="flex items-center gap-2 px-1 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={allPicked}
                  onChange={() =>
                    setPicked(allPicked ? new Set() : new Set(drafts.map((d) => d.id)))
                  }
                />
                全选（{picked.size}/{drafts.length}）
              </label>
              {drafts.map((d) => (
                <DraftCard
                  key={d.id}
                  draft={d}
                  picked={picked.has(d.id)}
                  onTogglePick={() => togglePick(d.id)}
                  onChanged={invalidateDrafts}
                  onError={handleError}
                />
              ))}
            </div>
          )}
        </div>

        <DialogFooter className="border-t pt-3">
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
          <Button
            disabled={picked.size === 0 || commitMutation.isPending}
            onClick={() => commitMutation.mutate([...picked])}
          >
            {commitMutation.isPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Check className="mr-1 h-3.5 w-3.5" />
            )}
            采纳入库（{picked.size}）
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 单条草稿卡片：展开详情 / 行内编辑 / 拒绝（带原因）
// ---------------------------------------------------------------------------
function DraftCard({
  draft,
  picked,
  onTogglePick,
  onChanged,
  onError,
}: {
  draft: AiCaseDraft;
  picked: boolean;
  onTogglePick: () => void;
  onChanged: () => void;
  onError: (e: unknown) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [title, setTitle] = useState(draft.title);
  const [steps, setSteps] = useState(draft.steps_text ?? "");
  const [expected, setExpected] = useState(draft.expected ?? "");

  const saveMutation = useMutation({
    mutationFn: () =>
      aiCaseGenerationApi.updateDraft(draft.id, {
        title: title.trim() || draft.title,
        steps_text: steps,
        expected,
      }),
    onSuccess: () => {
      toast.success("草稿已更新");
      setEditing(false);
      onChanged();
    },
    onError,
  });

  const rejectMutation = useMutation({
    mutationFn: () => aiCaseGenerationApi.rejectDraft(draft.id, reason),
    onSuccess: () => {
      toast.success(reason.trim() ? "已拒绝（原因将用于改进后续生成）" : "已拒绝");
      onChanged();
    },
    onError,
  });

  return (
    <div className="rounded-md border px-3 py-2">
      <div className="flex items-start gap-2">
        <input type="checkbox" checked={picked} onChange={onTogglePick} className="mt-1" />
        <button
          type="button"
          className="mt-0.5 text-muted-foreground"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <div className="min-w-0 flex-1 cursor-pointer" onClick={() => setExpanded((v) => !v)}>
          <span className="text-sm font-medium">{draft.title}</span>
          <span className="ml-2 text-[10px] text-muted-foreground">
            P{draft.priority} · {draft.model_label ?? "?"}
            {draft.needs_ui_detail ? " · 需补 UI 细节" : ""}
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-red-500 hover:text-red-600"
          title="拒绝此草稿"
          onClick={() => setRejecting((v) => !v)}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {rejecting ? (
        <div className="mt-2 flex items-center gap-2 pl-6">
          <Input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="拒绝原因（会让 AI 下次避开同类问题,强烈建议填写）"
            className="h-8 flex-1 text-xs"
          />
          <Button
            size="sm"
            variant="destructive"
            className="h-8"
            disabled={rejectMutation.isPending}
            onClick={() => rejectMutation.mutate()}
          >
            确认拒绝
          </Button>
        </div>
      ) : null}

      {expanded ? (
        <div className="mt-2 space-y-2 pl-6 text-xs">
          {editing ? (
            <>
              <div className="space-y-1">
                <Label className="text-[11px]">标题</Label>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} className="h-8" />
              </div>
              <div className="space-y-1">
                <Label className="text-[11px]">步骤（1. 2. 3. 编号）</Label>
                <Textarea rows={4} value={steps} onChange={(e) => setSteps(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label className="text-[11px]">预期结果</Label>
                <Textarea rows={3} value={expected} onChange={(e) => setExpected(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <Button size="sm" className="h-7" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                  保存修改
                </Button>
                <Button size="sm" variant="outline" className="h-7" onClick={() => setEditing(false)}>
                  取消
                </Button>
              </div>
            </>
          ) : (
            <>
              {draft.preconditions ? (
                <div>
                  <span className="font-medium text-foreground/70">前置：</span>
                  {draft.preconditions}
                </div>
              ) : null}
              {draft.steps_text ? (
                <pre className="whitespace-pre-wrap font-sans text-muted-foreground">{draft.steps_text}</pre>
              ) : null}
              {draft.expected ? (
                <div>
                  <span className="font-medium text-foreground/70">预期：</span>
                  <pre className="inline whitespace-pre-wrap font-sans">{draft.expected}</pre>
                </div>
              ) : null}
              <Button size="sm" variant="outline" className="h-7" onClick={() => setEditing(true)}>
                编辑
              </Button>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
