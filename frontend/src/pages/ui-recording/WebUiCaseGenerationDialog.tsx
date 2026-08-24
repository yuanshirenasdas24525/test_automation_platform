import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Save,
  Send,
  Sparkles,
  Square,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import {
  ApiError,
  aiApi,
  aiModelsApi,
  modulesApi,
  webUiCasesApi,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";
import { FeatureChecklistPanel, PromptPreviewPanel, ConfigPreviewPanel } from "@/components/case/ai-gen-panels";
import { SideDrawer } from "@/components/ui/side-drawer";
import { ChevronDown, ScanSearch, Trash2, LayoutList, ClipboardList, FileText, SlidersHorizontal } from "lucide-react";
import type {
  AiRun,
  WebUiCaseDraft,
} from "@/types/domain";

function messageOf(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "操作失败";
}

function payloadString(run: AiRun, key: string): string {
  const value = run.input_payload?.[key];
  return typeof value === "string" ? value : "";
}

function payloadModuleId(run: AiRun): number | null {
  const value = Number(run.input_payload?.target_module_id);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function findRestorableRun(runs: AiRun[], moduleId: number): AiRun | null {
  const scoped = runs.filter((run) => (
    run.feature === "web_ui_case_gen"
    && payloadModuleId(run) === moduleId
    && Boolean(payloadString(run, "batch_id"))
  ));
  return scoped.find((run) => run.status === "pending" || run.status === "running")
    // listRuns 按创建时间倒序；已取消/失败任务也可能已保存部分草稿。
    ?? scoped[0]
    ?? null;
}

function ToggleRow({
  checked,
  onChange,
  title,
  description,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  title: string;
  description: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-md border p-2.5 text-xs">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 accent-primary"
      />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="mt-0.5 block text-[11px] text-muted-foreground">{description}</span>
      </span>
    </label>
  );
}

export function WebUiCaseGenerationDialog({
  open,
  projectId,
  initialModuleId,
  onOpenChange,
}: {
  open: boolean;
  projectId: number;
  initialModuleId?: number | null;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const initializedScopeRef = useRef("");
  const initializedDraftBatchRef = useRef("");
  const [modelName, setModelName] = useState("");
  const [structureAssertions, setStructureAssertions] = useState(true);
  const [visualAssertions, setVisualAssertions] = useState(true);
  const [visualThreshold, setVisualThreshold] = useState(0.02);
  const [userPrompt, setUserPrompt] = useState("");
  const [runId, setRunId] = useState<number | null>(null);
  const [batchId, setBatchId] = useState("");
  const [selectedDraftIds, setSelectedDraftIds] = useState<number[]>([]);
  const [activeDraftId, setActiveDraftId] = useState<number | null>(null);
  const [dismissedRestore, setDismissedRestore] = useState(false);
  // 左导航切换：用例(配置+草稿) / UI测试要点 / 提示词
  const [wpanel, setWpanel] = useState<"cases" | "checklist" | "prompt" | "config">("cases");
  const [moduleId, setModuleId] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [variablesJson, setVariablesJson] = useState("{}");
  const [stepsJson, setStepsJson] = useState("[]");

  const modelsQuery = useQuery({
    queryKey: ["ai-models", projectId],
    queryFn: () => aiModelsApi.list(projectId),
    enabled: open,
  });
  const modulesQuery = useQuery({
    queryKey: ["modules", "web-ui-generation", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: open,
  });
  const recoveryQuery = useQuery({
    queryKey: ["ai-runs", "web-ui-generation", "restore", projectId, initialModuleId],
    queryFn: () => aiApi.listRuns({
      project_id: projectId,
      feature: "web_ui_case_gen",
      limit: 100,
    }),
    enabled: open && initialModuleId != null,
    staleTime: 0,
    refetchOnMount: "always",
  });
  const runQuery = useQuery({
    queryKey: ["ai-run", "web-ui-generation", runId],
    queryFn: () => aiApi.getRun(runId as number),
    enabled: open && runId != null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "running" ? 1500 : false;
    },
  });
  const draftsQuery = useQuery({
    queryKey: ["web-ui-case-drafts", projectId, batchId],
    queryFn: () => webUiCasesApi.listDrafts({ projectId, batchId }),
    enabled: open && Boolean(batchId),
    refetchInterval: () => (
      runQuery.data?.status === "pending" || runQuery.data?.status === "running" ? 2_000 : false
    ),
  });

  const enabledModels = useMemo(
    () => (modelsQuery.data ?? []).filter((item) => item.enabled),
    [modelsQuery.data],
  );
  const modules = useMemo(() => modulesQuery.data ?? [], [modulesQuery.data]);
  const drafts = useMemo(() => draftsQuery.data ?? [], [draftsQuery.data]);
  const activeDraft = drafts.find((item) => item.id === activeDraftId) ?? null;
  const runStatus = runQuery.data?.status;
  const generating = runStatus === "pending" || runStatus === "running";
  const sourceSelection = runQuery.data?.output_payload?.source_selection as {
    functional_case_ids?: number[];
    page_keys?: string[];
    rationale?: string;
    warnings?: string[];
    budget?: Record<string, unknown>;
  } | undefined;
  const contextBudget = runQuery.data?.output_payload?.context_budget as {
    elements_available?: number;
    elements_included?: number;
    elements_truncated?: boolean;
  } | undefined;
  const droppedReasons = (runQuery.data?.output_payload?.dropped_reasons ?? []) as string[];
  const progress = runQuery.data?.output_payload?.progress as {
    stage?: string;
    message?: string;
    selection_completed?: number;
    selection_total?: number;
    generation_completed?: number;
    generation_total?: number;
    draft_count?: number;
    updated_at?: string;
  } | undefined;
  const progressCompleted = progress?.stage === "source_selection"
    ? Number(progress.selection_completed ?? 0)
    : Number(progress?.generation_completed ?? 0);
  const progressTotal = progress?.stage === "source_selection"
    ? Number(progress.selection_total ?? 0)
    : Number(progress?.generation_total ?? 0);
  const progressPercent = progressTotal > 0
    ? Math.min(100, Math.round((progressCompleted / progressTotal) * 100))
    : 5;
  const progressStageLabel = progress?.stage === "preparing"
    ? "准备事实数据"
    : progress?.stage === "source_selection"
      ? "筛选功能用例"
      : progress?.stage === "generation"
        ? "生成并编译草稿"
        : "等待任务进度";

  useEffect(() => {
    if (!open) return;
    const scopeKey = `${projectId}:${initialModuleId ?? ""}`;
    if (initializedScopeRef.current === scopeKey) return;
    initializedScopeRef.current = scopeKey;
    setModelName("");
    setModuleId(initialModuleId ? String(initialModuleId) : "");
    setRunId(null);
    setBatchId("");
    initializedDraftBatchRef.current = "";
    setSelectedDraftIds([]);
    setActiveDraftId(null);
  }, [initialModuleId, open, projectId]);

  useEffect(() => {
    if (!open || runId != null || initialModuleId == null || !recoveryQuery.data || dismissedRestore) return;
    const restored = findRestorableRun(recoveryQuery.data, initialModuleId);
    if (!restored) return;
    const restoredBatchId = payloadString(restored, "batch_id");
    if (!restoredBatchId) return;

    setRunId(restored.id);
    setBatchId(restoredBatchId);
    initializedDraftBatchRef.current = "";

    const restoredModelName = payloadString(restored, "model_name");
    if (restoredModelName) setModelName(restoredModelName);
    const restoredPrompt = payloadString(restored, "user_prompt");
    setUserPrompt(restoredPrompt);
    if (typeof restored.input_payload?.include_structure_assertions === "boolean") {
      setStructureAssertions(restored.input_payload.include_structure_assertions);
    }
    if (typeof restored.input_payload?.include_visual_assertions === "boolean") {
      setVisualAssertions(restored.input_payload.include_visual_assertions);
    }
    const restoredThreshold = Number(restored.input_payload?.visual_threshold);
    if (Number.isFinite(restoredThreshold)) setVisualThreshold(restoredThreshold);
  }, [initialModuleId, open, recoveryQuery.data, runId]);

  useEffect(() => {
    if (modelName || enabledModels.length === 0) return;
    const preferred = enabledModels.find((item) => item.is_default) ?? enabledModels[0];
    setModelName(preferred.name);
  }, [enabledModels, modelName]);

  useEffect(() => {
    if (moduleId || modules.length === 0) return;
    setModuleId(String(modules[0].id));
  }, [moduleId, modules]);

  useEffect(() => {
    if (drafts.length === 0 || !batchId || initializedDraftBatchRef.current === batchId) return;
    initializedDraftBatchRef.current = batchId;
    setSelectedDraftIds(drafts.filter((item) => item.status === "pending").map((item) => item.id));
    setActiveDraftId(drafts[0].id);
  }, [batchId, drafts]);

  useEffect(() => {
    if (!activeDraft) return;
    setDraftTitle(activeDraft.title);
    setVariablesJson(JSON.stringify(activeDraft.variables ?? {}, null, 2));
    setStepsJson(JSON.stringify(activeDraft.steps ?? [], null, 2));
  }, [activeDraft]);

  useEffect(() => {
    if (runStatus !== "failed") return;
    toast.error(runQuery.data?.error || "Web UI 用例生成失败");
  }, [runQuery.data?.error, runStatus]);

  const generateMutation = useMutation({
    mutationFn: (gapOnly: boolean) => webUiCasesApi.generate({
      project_id: projectId,
      target_module_id: Number(initialModuleId),
      model_name: modelName,
      source_mode: "auto",
      functional_case_ids: [],
      page_keys: [],
      executable_only: true,
      include_structure_assertions: structureAssertions,
      include_visual_assertions: visualAssertions,
      visual_threshold: visualThreshold,
      user_prompt: userPrompt,
      gap_only: gapOnly,
    }),
    onSuccess: (result, gapOnly) => {
      setRunId(result.ai_run_id);
      setBatchId(result.batch_id);
      initializedDraftBatchRef.current = "";
      void queryClient.invalidateQueries({
        queryKey: ["ai-runs", "web-ui-generation", "restore", projectId, initialModuleId],
      });
      toast.success(
        gapOnly
          ? "查缺补漏任务已提交，仅对未覆盖的功能用例生成草稿"
          : "生成任务已提交，结果会先进入待评审草稿",
      );
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const cancelMutation = useMutation({
    mutationFn: () => {
      if (runId == null) throw new Error("当前没有可终止的生成任务");
      return aiApi.cancelRun(runId);
    },
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ai-run", "web-ui-generation", runId] }),
        queryClient.invalidateQueries({ queryKey: queryKeys.tasksInProgress() }),
      ]);
      toast.success(result.message || "生成任务已终止，已完成草稿会保留");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!activeDraft) throw new Error("请选择草稿");
      let variables: Record<string, unknown>;
      let steps: WebUiCaseDraft["steps"];
      try {
        variables = JSON.parse(variablesJson) as Record<string, unknown>;
        steps = JSON.parse(stepsJson) as WebUiCaseDraft["steps"];
      } catch {
        throw new Error("变量或步骤 JSON 格式不正确");
      }
      if (!variables || Array.isArray(variables) || typeof variables !== "object") {
        throw new Error("变量必须是 JSON 对象");
      }
      if (!Array.isArray(steps)) throw new Error("步骤必须是 JSON 数组");
      return webUiCasesApi.updateDraft(activeDraft.id, {
        title: draftTitle.trim(),
        variables,
        steps,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["web-ui-case-drafts", projectId, batchId] });
      toast.success("草稿已保存");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const rejectMutation = useMutation({
    mutationFn: (draftId: number) => webUiCasesApi.rejectDraft(draftId, "人工评审拒绝"),
    onSuccess: async (_, draftId) => {
      setSelectedDraftIds((items) => items.filter((id) => id !== draftId));
      await queryClient.invalidateQueries({ queryKey: ["web-ui-case-drafts", projectId, batchId] });
      toast.success("草稿已拒绝");
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const commitMutation = useMutation({
    mutationFn: () => webUiCasesApi.commitDrafts({
      draft_ids: selectedDraftIds,
      module_id: Number(moduleId),
    }),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["web-ui-case-drafts", projectId, batchId] }),
        queryClient.invalidateQueries({ queryKey: ["content", projectId] }),
        // 写入后刷新父页面的用例列表，否则「Web 用例(N)」要手动刷新浏览器才更新。
        queryClient.invalidateQueries({ queryKey: ["automation-cases"] }),
      ]);
      toast.success(`已写入 ${result.created_case_ids.length} 条 Web UI 用例${result.skipped.length ? `，跳过 ${result.skipped.length} 条` : ""}`);
      setSelectedDraftIds([]);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const submitGeneration = (gapOnly = false) => {
    if (!modelName) return toast.error("请先配置并选择一个可用 AI 模型");
    if (!initialModuleId) return toast.error("请先进入要生成用例的具体模块");
    generateMutation.mutate(gapOnly);
  };

  /** 清空生成缓存：不再恢复上一批草稿，并刷新用例列表/草稿/运行缓存。 */
  const handleClearCache = () => {
    setRunId(null);
    setBatchId("");
    initializedDraftBatchRef.current = "";
    setSelectedDraftIds([]);
    setActiveDraftId(null);
    setDismissedRestore(true);
    void queryClient.invalidateQueries({ queryKey: ["ai-runs", "web-ui-generation", "restore"] });
    void queryClient.invalidateQueries({ queryKey: ["web-ui-case-drafts"] });
    void queryClient.invalidateQueries({ queryKey: ["ai-run", "web-ui-generation"] });
    void queryClient.invalidateQueries({ queryKey: ["automation-cases"] });
    void queryClient.invalidateQueries({ queryKey: ["content"] });
    toast.success("已清空生成缓存，不再恢复上一批草稿，列表已刷新");
  };

  const toggleNumber = (items: number[], value: number, checked: boolean) => (
    checked ? [...items, value] : items.filter((item) => item !== value)
  );

  return (
    <SideDrawer
      open={open}
      onClose={() => onOpenChange(false)}
      storageKey="web-ui-gen-drawer-width"
      defaultWidth={1120}
      minWidth={900}
      title={
        <>
          <Sparkles className="h-[17px] w-[17px] text-violet-600" />
          AI 生成 Web UI 自动化用例
        </>
      }
      headerExtra={
        <Button variant="outline" size="sm" onClick={handleClearCache} title="不再恢复上一批草稿并刷新列表">
          <Trash2 className="h-3.5 w-3.5" />清空缓存
        </Button>
      }
    >
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* 左栏：手风琴导航 —「用例」可向下展开配置，「UI测试要点/提示词」点了在右侧显示 */}
        <div className="flex w-[340px] shrink-0 flex-col overflow-y-auto border-r bg-muted/20">
          <button
            type="button"
            onClick={() => setWpanel("cases")}
            className={cn(
              "flex items-center gap-2.5 border-b border-l-2 px-4 py-3 text-left text-sm font-medium transition-colors",
              wpanel === "cases"
                ? "border-l-primary bg-primary/5 text-primary"
                : "border-l-transparent text-muted-foreground hover:bg-muted/60",
            )}
          >
            <LayoutList className="h-4 w-4 shrink-0" />
            <span className="flex-1">用例</span>
            <ChevronDown className={cn("h-4 w-4 shrink-0 transition-transform", wpanel === "cases" ? "rotate-180" : "")} />
          </button>
          {wpanel === "cases" ? (
          <section className="border-b bg-background p-5">
            <div className="space-y-5">
              <div>
                <Label>AI 模型</Label>
                <Select value={modelName} onValueChange={setModelName}>
                  <SelectTrigger className="mt-1.5"><SelectValue placeholder="选择模型" /></SelectTrigger>
                  <SelectContent>
                    {enabledModels.map((item) => (
                      <SelectItem key={item.name} value={item.name}>
                        {item.name} · {item.provider}/{item.model}{item.is_default ? "（默认）" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="rounded-lg border border-violet-200 bg-violet-50/70 p-3 text-xs text-violet-950">
                <p className="font-medium">自动选择与检索</p>
                <ol className="mt-2 list-decimal space-y-1.5 pl-4 text-[11px] leading-5">
                  <li>本地先过滤纯接口、安全、性能和文档类功能用例。</li>
                  <li>AI 从有限候选中选择适合自动化的业务流程和页面。</li>
                  <li>系统再按所选用例检索元素，并用元素库真实定位器编译。</li>
                  <li>需要验证码、缺定位器或缺断言的结果不会进入可执行草稿。</li>
                </ol>
              </div>

              <div>
                <Label htmlFor="visual-threshold">视觉差异阈值</Label>
                <Input id="visual-threshold" type="number" min={0} max={1} step={0.01} disabled={!visualAssertions} value={visualThreshold} onChange={(event) => setVisualThreshold(Number(event.target.value))} className="mt-1.5" />
              </div>

              <div className="space-y-2">
                <ToggleRow checked={structureAssertions} onChange={setStructureAssertions} title="生成关键结构断言" description="对页面标题、关键区域或操作结果生成可见性/文本断言。" />
                <ToggleRow checked={visualAssertions} onChange={setVisualAssertions} title="生成视觉回归断言（可选）" description="使用已录制截图做基线；需固定视口，动态区域后续可编辑 masks。" />
              </div>

              <div>
                <Label htmlFor="web-ui-prompt">业务范围补充（可选）</Label>
                <Textarea id="web-ui-prompt" value={userPrompt} onChange={(event) => setUserPrompt(event.target.value)} placeholder="例如：优先项目创建、搜索和编辑；不要生成删除或停用流程。" className="mt-1.5 min-h-20" />
              </div>


              <div className="flex gap-2">
                <Button className="min-w-0 flex-1" disabled={generateMutation.isPending || generating} onClick={() => submitGeneration(false)}>
                  {generateMutation.isPending || generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                  {generating ? "AI 正在分批生成…" : "一键生成可执行草稿"}
                </Button>
                {generating ? (
                  <Button
                    variant="outline"
                    className="shrink-0 text-red-600 hover:bg-red-50 hover:text-red-700"
                    disabled={cancelMutation.isPending}
                    onClick={() => cancelMutation.mutate()}
                  >
                    {cancelMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
                    终止
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    className="shrink-0"
                    disabled={generateMutation.isPending}
                    onClick={() => submitGeneration(true)}
                    title="只对本模块内还没有对应 Web 用例的功能用例生成，跳过已覆盖的"
                  >
                    <ScanSearch className="h-3.5 w-3.5" />
                    查缺补漏
                  </Button>
                )}
              </div>
              <p className="text-[11px] leading-5 text-muted-foreground">
                任务会在后台持续运行，可以关闭弹窗或切换页面；再次进入当前模块时会自动恢复任务状态和最近一批草稿。
              </p>
            </div>
          </section>
          ) : null}
          {/* 用例分类预览 / 提示词 —— 不展开，点了在右侧显示 */}
          <button
            type="button"
            onClick={() => setWpanel("checklist")}
            className={cn(
              "flex items-center gap-2.5 border-b border-l-2 px-4 py-3 text-left text-sm font-medium transition-colors",
              wpanel === "checklist"
                ? "border-l-primary bg-primary/5 text-primary"
                : "border-l-transparent text-muted-foreground hover:bg-muted/60",
            )}
          >
            <ClipboardList className="h-4 w-4 shrink-0" />
            <span className="flex-1">用例分类预览</span>
          </button>
          <button
            type="button"
            onClick={() => setWpanel("prompt")}
            className={cn(
              "flex items-center gap-2.5 border-b border-l-2 px-4 py-3 text-left text-sm font-medium transition-colors",
              wpanel === "prompt"
                ? "border-l-primary bg-primary/5 text-primary"
                : "border-l-transparent text-muted-foreground hover:bg-muted/60",
            )}
          >
            <FileText className="h-4 w-4 shrink-0" />
            <span className="flex-1">提示词</span>
          </button>
          <button
            type="button"
            onClick={() => setWpanel("config")}
            className={cn(
              "flex items-center gap-2.5 border-b border-l-2 px-4 py-3 text-left text-sm font-medium transition-colors",
              wpanel === "config"
                ? "border-l-primary bg-primary/5 text-primary"
                : "border-l-transparent text-muted-foreground hover:bg-muted/60",
            )}
          >
            <SlidersHorizontal className="h-4 w-4 shrink-0" />
            <span className="flex-1">配置预览</span>
          </button>
        </div>
        {/* 右栏内容区：用例=草稿列表 / UI测试要点 / 提示词 */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {wpanel === "config" ? (
            <div className="min-h-0 flex-1 p-5">
              <ConfigPreviewPanel projectId={projectId} category="web" modelName={modelName} />
            </div>
          ) : wpanel === "checklist" ? (
            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              <FeatureChecklistPanel moduleId={initialModuleId ?? null} modelName={modelName} requirementText={userPrompt} caseSignature="" mode="web" />
            </div>
          ) : wpanel === "prompt" ? (
            <div className="min-h-0 flex-1 p-5">
              <PromptPreviewPanel moduleId={initialModuleId ?? null} mode="web" coverage="standard" dimensions="" requirementText={userPrompt} />
            </div>
          ) : (
          <section className="flex min-h-0 flex-col">
            {!batchId ? (
              <div className="flex flex-1 flex-col items-center justify-center px-8 text-center text-muted-foreground">
                <Sparkles className="mb-4 h-10 w-10 text-violet-300" />
                <p className="font-medium text-foreground">无需手工挑选功能用例和页面</p>
                <p className="mt-2 max-w-lg text-sm">一次点击会先筛选功能用例与页面，再检索元素库并编译真实定位器；未通过可执行门禁的结果会被自动丢弃并说明原因。</p>
              </div>
            ) : generating ? (
              <div className="min-h-0 flex-1 overflow-y-auto p-8">
                <div className="mx-auto max-w-3xl space-y-5">
                  <div className="rounded-xl border border-violet-200 bg-violet-50/60 p-5">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <p className="flex items-center gap-2 font-semibold text-violet-950">
                          <Loader2 className="h-4 w-4 animate-spin text-violet-600" />
                          {progressStageLabel}
                        </p>
                        <p className="mt-1 text-sm text-violet-800">
                          {progress?.message || "后台任务已启动，正在等待首个批次进度"}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-full bg-white px-3 py-1 text-xs font-medium text-violet-700 ring-1 ring-violet-200">
                        {progressTotal > 0 ? `${progressCompleted}/${progressTotal}` : "准备中"}
                      </span>
                    </div>
                    <div className="mt-4 h-2 overflow-hidden rounded-full bg-violet-100">
                      <div
                        className="h-full rounded-full bg-violet-600 transition-[width] duration-500"
                        style={{ width: `${progressPercent}%` }}
                      />
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
                      <div className="rounded-lg bg-white p-3 ring-1 ring-violet-100">
                        <span className="text-muted-foreground">已完成筛选</span>
                        <strong className="mt-1 block text-base text-foreground">{progress?.selection_completed ?? 0}/{progress?.selection_total ?? 0}</strong>
                      </div>
                      <div className="rounded-lg bg-white p-3 ring-1 ring-violet-100">
                        <span className="text-muted-foreground">已完成生成批次</span>
                        <strong className="mt-1 block text-base text-foreground">{progress?.generation_completed ?? 0}/{progress?.generation_total ?? 0}</strong>
                      </div>
                      <div className="rounded-lg bg-white p-3 ring-1 ring-violet-100">
                        <span className="text-muted-foreground">已保存草稿</span>
                        <strong className="mt-1 block text-base text-foreground">{drafts.length || progress?.draft_count || 0}</strong>
                      </div>
                    </div>
                  </div>

                  {drafts.length > 0 ? (
                    <div className="rounded-xl border bg-background">
                      <div className="border-b px-4 py-3">
                        <p className="text-sm font-semibold">已完成的草稿会立即保留</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">后续批次继续运行时，可以关闭此页面；全部结束后再统一评审入库。</p>
                      </div>
                      <div className="divide-y">
                        {drafts.map((draft) => (
                          <div key={draft.id} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
                            <span className="min-w-0 truncate">{draft.title}</span>
                            <span className="shrink-0 text-xs text-muted-foreground">{draft.steps.length} 步</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <p className="text-center text-sm text-muted-foreground">首个生成批次完成后，草稿会立即出现在这里。</p>
                  )}
                </div>
              </div>
            ) : runStatus === "failed" ? (
              <div className="m-auto max-w-xl rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700">
                <div className="flex items-center gap-2 font-medium"><XCircle className="h-4 w-4" />生成失败</div>
                <p className="mt-2 break-words">{runQuery.data?.error || "未知错误"}</p>
              </div>
            ) : runStatus === "cancelled" ? (
              <div className="m-auto max-w-xl rounded-lg border border-amber-200 bg-amber-50 p-5 text-sm text-amber-800">
                <div className="flex items-center gap-2 font-medium"><Square className="h-4 w-4" />生成任务已终止</div>
                <p className="mt-2">已完成的 {drafts.length} 条草稿已保留，可以重新生成或稍后继续处理。</p>
              </div>
            ) : (
              <>
                <div className="flex shrink-0 items-center justify-between border-b px-5 py-3">
                  <div>
                    <p className="text-sm font-semibold">待评审草稿 · {drafts.length} 条</p>
                    <p className="text-[11px] text-muted-foreground">勾选通过项并选择目标模块后批量入库</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Select value={moduleId} onValueChange={setModuleId}>
                      <SelectTrigger className="h-8 w-52 text-xs"><SelectValue placeholder="选择目标模块" /></SelectTrigger>
                      <SelectContent>{modules.map((item) => <SelectItem key={item.id} value={String(item.id)}>{item.name}</SelectItem>)}</SelectContent>
                    </Select>
                    <Button size="sm" disabled={!moduleId || selectedDraftIds.length === 0 || commitMutation.isPending} onClick={() => commitMutation.mutate()}>
                      {commitMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                      通过并入库 ({selectedDraftIds.length})
                    </Button>
                  </div>
                </div>
                {sourceSelection ? (
                  <div className="shrink-0 border-b bg-muted/25 px-5 py-3 text-xs">
                    <div className="flex flex-wrap gap-x-5 gap-y-1">
                      <span>AI 已选功能用例 <strong>{sourceSelection.functional_case_ids?.length ?? 0}</strong> 条</span>
                      <span>关联页面 <strong>{sourceSelection.page_keys?.length ?? 0}</strong> 个</span>
                      <span>
                        详细元素 <strong>{contextBudget?.elements_included ?? 0}</strong>
                        / {contextBudget?.elements_available ?? 0}
                        {contextBudget?.elements_truncated ? "（已按相关度裁剪）" : ""}
                      </span>
                    </div>
                    {sourceSelection.rationale ? <p className="mt-1 text-muted-foreground">{sourceSelection.rationale}</p> : null}
                    {(sourceSelection.warnings?.length || droppedReasons.length) ? (
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-amber-700">
                        {[...(sourceSelection.warnings ?? []), ...droppedReasons].slice(0, 6).map((item) => <li key={item}>{item}</li>)}
                      </ul>
                    ) : null}
                  </div>
                ) : null}
                <div className="grid min-h-0 flex-1 grid-cols-[330px_minmax(0,1fr)]">
                  <div className="min-h-0 overflow-y-auto border-r p-3">
                    <div className="space-y-2">
                      {drafts.map((draft) => (
                        <button
                          type="button"
                          key={draft.id}
                          onClick={() => setActiveDraftId(draft.id)}
                          className={cn(
                            "w-full rounded-lg border p-3 text-left transition hover:border-primary/50",
                            activeDraftId === draft.id && "border-primary bg-primary/5",
                            draft.status !== "pending" && "opacity-60",
                          )}
                        >
                          <div className="flex items-start gap-2">
                            <input
                              type="checkbox"
                              disabled={draft.status !== "pending"}
                              checked={selectedDraftIds.includes(draft.id)}
                              onClick={(event) => event.stopPropagation()}
                              onChange={(event) => setSelectedDraftIds(toggleNumber(selectedDraftIds, draft.id, event.target.checked))}
                              className="mt-0.5 h-4 w-4 accent-primary"
                            />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm font-medium">{draft.title}</span>
                              <span className="mt-1 flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                                <span>{draft.steps.length} 步</span>
                                <span>· 可信度 {Math.round(draft.confidence * 100)}%</span>
                                {draft.visual_assertion ? <span className="text-violet-600">· 含视觉断言</span> : null}
                              </span>
                            </span>
                            {draft.manual_reasons.length ? <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" /> : <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />}
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="min-h-0 overflow-y-auto p-5">
                    {activeDraft ? (
                      <div className="space-y-4">
                        <div className="flex items-start justify-between gap-4">
                          <div className="min-w-0 flex-1">
                            <Label htmlFor="draft-title">用例名称</Label>
                            <Input id="draft-title" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} disabled={activeDraft.status !== "pending"} className="mt-1.5" />
                          </div>
                          {activeDraft.status === "pending" ? (
                            <Button variant="outline" size="sm" className="mt-6 text-red-600" disabled={rejectMutation.isPending} onClick={() => rejectMutation.mutate(activeDraft.id)}>
                              <XCircle className="h-4 w-4" />拒绝
                            </Button>
                          ) : <span className="mt-6 rounded-full bg-muted px-2 py-1 text-xs">{activeDraft.status}</span>}
                        </div>

                        {activeDraft.manual_reasons.length ? (
                          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                            <p className="font-medium">需人工处理，入库后默认停用</p>
                            <ul className="mt-1 list-disc space-y-1 pl-4">{activeDraft.manual_reasons.map((item) => <li key={item}>{item}</li>)}</ul>
                          </div>
                        ) : null}
                        {activeDraft.warnings.length ? (
                          <div className="rounded-lg border bg-muted/30 p-3 text-xs">
                            <p className="font-medium">编译提示</p>
                            <ul className="mt-1 list-disc space-y-1 pl-4 text-muted-foreground">{activeDraft.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
                          </div>
                        ) : null}

                        <div className="grid grid-cols-3 gap-2 text-xs">
                          <div className="rounded-md border p-2"><span className="text-muted-foreground">来源元素</span><strong className="mt-1 block">{activeDraft.evidence.element_ids?.length ?? 0}</strong></div>
                          <div className="rounded-md border p-2"><span className="text-muted-foreground">覆盖页面</span><strong className="mt-1 block">{activeDraft.evidence.page_keys?.length ?? 0}</strong></div>
                          <div className="rounded-md border p-2"><span className="text-muted-foreground">关联功能用例</span><strong className="mt-1 block">{activeDraft.functional_case_id ? `#${activeDraft.functional_case_id}` : "仅元素库"}</strong></div>
                        </div>

                        <div>
                          <Label htmlFor="draft-variables">动态变量（JSON）</Label>
                          <Textarea id="draft-variables" value={variablesJson} onChange={(event) => setVariablesJson(event.target.value)} disabled={activeDraft.status !== "pending"} className="mt-1.5 min-h-28 font-mono text-xs" />
                        </div>
                        <div>
                          <Label htmlFor="draft-steps">执行步骤（可在评审时调整 JSON）</Label>
                          <Textarea id="draft-steps" value={stepsJson} onChange={(event) => setStepsJson(event.target.value)} disabled={activeDraft.status !== "pending"} className="mt-1.5 min-h-64 font-mono text-xs" />
                        </div>
                        {activeDraft.status === "pending" ? (
                          <div className="flex justify-end">
                            <Button variant="outline" disabled={!draftTitle.trim() || updateMutation.isPending} onClick={() => updateMutation.mutate()}>
                              {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                              保存草稿调整
                            </Button>
                          </div>
                        ) : null}
                      </div>
                    ) : <p className="text-sm text-muted-foreground">请选择一条草稿查看详情。</p>}
                  </div>
                </div>
              </>
            )}
          </section>
          )}
        </div>
      </div>
    </SideDrawer>
  );
}
