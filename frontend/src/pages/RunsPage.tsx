import { useEffect, useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Bug,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Loader2,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ApiError,
  aiApi,
  aiModelsApi,
  reportsApi,
  tasksOverviewApi,
  type ReportAnalysisOutput,
  type TestReportDetail,
  type TestReportSummary,
  type TestStepReportItem,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";
import { CreateBugModal } from "@/pages/tasks/CreateBugModal";
import type { AiRun, InProgressTask } from "@/types/domain";

/**
 * 执行记录页。
 *
 * 顶部过滤：分类 Tab（全部 / api / web / app）+ 项目 ID + 状态下拉。
 * 列表：倒序展示最近的 TestReport，点「详情」弹对话框看步骤级结果。
 */

const STATUSES = [
  { value: "all", label: "全部" },
  { value: "success", label: "成功" },
  { value: "fail", label: "失败" },
  { value: "running", label: "运行中" },
] as const;

const CATEGORIES = [
  { value: "all", label: "全部" },
  { value: "api", label: "API" },
  { value: "web", label: "Web" },
  { value: "app", label: "App" },
] as const;

const PAGE_SIZE = 25;

export function RunsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const category = searchParams.get("category") ?? "all";
  const status = searchParams.get("status") ?? "all";
  const projectIdStr = searchParams.get("project_id") ?? "";
  const projectId = projectIdStr ? Number(projectIdStr) : undefined;
  const page = Number(searchParams.get("page") ?? "1") || 1;

  const setQS = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "" || v === "all") next.delete(k);
      else next.set(k, v);
    }
    setSearchParams(next, { replace: true });
  };

  const listQuery = useQuery({
    queryKey: queryKeys.reports({
      category,
      status,
      projectId: projectId ?? null,
      page,
    }),
    queryFn: () =>
      reportsApi.list({
        category: category !== "all" ? category : undefined,
        status: status !== "all" ? status : undefined,
        project_id: projectId,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    // 运行中的任务要持续刷，开 10s 轮询
    refetchInterval: 10_000,
  });

  const queryClient = useQueryClient();

  // 进行中任务（执行类 + AI 类）
  const inProgressQuery = useQuery<InProgressTask[]>({
    queryKey: queryKeys.tasksInProgress(),
    queryFn: () => tasksOverviewApi.getInProgress(),
    refetchInterval: 5_000,
    staleTime: 4_000,
  });

  const inProgressTasks = inProgressQuery.data ?? [];

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "操作失败";
    toast.error(msg);
  };

  const deleteMutation = useMutation({
    mutationFn: (id: number) => reportsApi.remove(id),
    onSuccess: () => {
      toast.success("已删除");
      queryClient.invalidateQueries({ queryKey: ["reports"] });
      setPendingDelete(null);
    },
    onError: handleError,
  });

  const [detailId, setDetailId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<TestReportSummary | null>(
    null,
  );

  const reports = listQuery.data?.data ?? [];
  const total = listQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">执行记录</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            每次运行（项目 / 模块 / 用例）都会在这里留一条记录。页面每 10s 自动刷新。
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => listQuery.refetch()}
          disabled={listQuery.isFetching}
        >
          {listQuery.isFetching ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          刷新
        </Button>
      </div>

      {/* 过滤区 */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <Label className="text-xs">分类</Label>
          <Tabs value={category} onValueChange={(v) => setQS({ category: v, page: "1" })}>
            <TabsList>
              {CATEGORIES.map((c) => (
                <TabsTrigger key={c.value} value={c.value}>
                  {c.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">状态</Label>
          <Select
            value={status}
            onValueChange={(v) => setQS({ status: v, page: "1" })}
          >
            <SelectTrigger className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUSES.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs" htmlFor="pid">
            项目 ID
          </Label>
          <Input
            id="pid"
            type="number"
            inputMode="numeric"
            className="w-32"
            placeholder="留空=全部"
            value={projectIdStr}
            onChange={(e) => setQS({ project_id: e.target.value, page: "1" })}
          />
        </div>
      </div>

      {/* 进行中任务卡片 */}
      {inProgressTasks.length > 0 ? (
        <Card className="border-blue-200 bg-blue-50/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-3">
              <Play className="h-4 w-4 text-blue-600" />
              <h2 className="text-sm font-semibold text-blue-800">
                进行中的任务 · {inProgressTasks.length}
              </h2>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {inProgressTasks.map((task) => {
                const isRunning = task.status === "running";
                return (
                  <Link
                    key={`${task.type_key}-${task.id}`}
                    to={task.detail_url || "#"}
                    className="flex items-center gap-2.5 rounded-lg bg-background p-2.5 text-sm shadow-sm hover:shadow transition-shadow"
                  >
                    {isRunning ? (
                      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-blue-600" />
                    ) : (
                      <Loader2 className="h-3.5 w-3.5 shrink-0 text-amber-500" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium">{task.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {task.type_label}
                        {task.project_name ? ` · ${task.project_name}` : ""}
                      </div>
                    </div>
                    <StatusBadge status={task.status} />
                  </Link>
                );
              })}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* 列表 */}
      {listQuery.isLoading ? (
        <ListSkeleton />
      ) : listQuery.isError ? (
        <ErrorBox
          message={
            listQuery.error instanceof Error
              ? listQuery.error.message
              : "加载失败"
          }
          onRetry={() => listQuery.refetch()}
        />
      ) : reports.length === 0 ? (
        <EmptyState />
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="grid grid-cols-[80px_1fr_120px_110px_130px_180px_100px] gap-3 border-b px-4 py-2 text-xs text-muted-foreground">
              <span>#</span>
              <span>项目 / 场景</span>
              <span>分类</span>
              <span>状态</span>
              <span>通过率</span>
              <span>开始时间</span>
              <span className="text-right">操作</span>
            </div>
            <div className="divide-y">
              {reports.map((r) => (
                <ReportRow
                  key={r.id}
                  report={r}
                  onOpen={() => setDetailId(r.id)}
                  onDelete={() => setPendingDelete(r)}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 分页 */}
      {totalPages > 1 ? (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            共 {total} 条 · 第 {page} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setQS({ page: String(page - 1) })}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setQS({ page: String(page + 1) })}
            >
              下一页
            </Button>
          </div>
        </div>
      ) : null}

      {/* Dialogs */}
      <DetailDialog
        reportId={detailId}
        onClose={() => setDetailId(null)}
      />

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(v) => !v && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除执行记录</DialogTitle>
            <DialogDescription>
              确认删除报告 #{pendingDelete?.id}？关联的步骤记录也会一起删除，不可恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingDelete(null)}
              disabled={deleteMutation.isPending}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() =>
                pendingDelete && deleteMutation.mutate(pendingDelete.id)
              }
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单行
// ---------------------------------------------------------------------------
function ReportRow({
  report,
  onOpen,
  onDelete,
}: {
  report: TestReportSummary;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const passRate =
    report.total_count > 0
      ? Math.round((report.pass_count / report.total_count) * 100)
      : null;

  return (
    <div className="grid grid-cols-[80px_1fr_120px_110px_130px_180px_100px] items-center gap-3 px-4 py-3 text-sm">
      <span className="font-mono text-xs text-muted-foreground">
        #{report.id}
      </span>
      <div className="min-w-0">
        <div className="truncate font-medium">
          {report.project_name ?? `项目 #${report.project_id ?? "-"}`}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {report.scene_name || report.summary || "—"}
        </div>
      </div>
      <span className="text-xs uppercase text-muted-foreground">
        {report.category ?? "—"}
      </span>
      <StatusBadge status={report.status} />
      <span className="text-xs">
        {passRate === null ? "—" : `${passRate}% (${report.pass_count}/${report.total_count})`}
      </span>
      <span className="text-xs text-muted-foreground">
        {formatTime(report.start_time)}
      </span>
      <div className="flex items-center justify-end gap-1">
        <Button variant="ghost" size="sm" onClick={onOpen}>
          详情
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-destructive"
          onClick={onDelete}
          title="删除"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string | null }) {
  const s = (status ?? "").toLowerCase();
  if (s === "success" || s === "pass" || s === "passed") {
    return (
      <span className="inline-flex items-center gap-1 text-green-600">
        <CheckCircle2 className="h-3.5 w-3.5" />
        成功
      </span>
    );
  }
  if (s === "fail" || s === "failed" || s === "error") {
    return (
      <span className="inline-flex items-center gap-1 text-destructive">
        <XCircle className="h-3.5 w-3.5" />
        失败
      </span>
    );
  }
  if (s === "running") {
    return (
      <span className="inline-flex items-center gap-1 text-blue-600">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        运行中
      </span>
    );
  }
  if (s === "pending") {
    return (
      <span className="inline-flex items-center gap-1 text-amber-600">
        <Loader2 className="h-3.5 w-3.5" />
        等待中
      </span>
    );
  }
  return <span className="text-muted-foreground">{status ?? "—"}</span>;
}

// ---------------------------------------------------------------------------
// 详情对话框
// ---------------------------------------------------------------------------
function DetailDialog({
  reportId,
  onClose,
}: {
  reportId: number | null;
  onClose: () => void;
}) {
  const [analysisRunId, setAnalysisRunId] = useState<number | null>(null);
  const [analysisPreview, setAnalysisPreview] = useState<ReportAnalysisOutput | null>(null);
  const [analysisStage, setAnalysisStage] = useState<"idle" | "collecting" | "rules" | "ai" | "done" | "failed">("idle");
  useEffect(() => {
    setAnalysisRunId(null);
    setAnalysisPreview(null);
    setAnalysisStage("idle");
  }, [reportId]);

  const query = useQuery({
    queryKey: reportId ? queryKeys.report(reportId) : ["report", "none"],
    queryFn: () => reportsApi.get(reportId!),
    enabled: reportId !== null,
  });
  const modelsQuery = useQuery({
    queryKey: ["ai-models"],
    queryFn: () => aiModelsApi.list(),
    enabled: reportId !== null,
    staleTime: 60_000,
  });
  const analysisQuery = useQuery({
    queryKey: analysisRunId ? queryKeys.aiRun(analysisRunId) : ["ai-run", "none"],
    queryFn: () => aiApi.getRun(analysisRunId!),
    enabled: analysisRunId !== null,
    refetchInterval: (q) => {
      const status = (q.state.data as AiRun | undefined)?.status;
      return status === "success" || status === "failed" || status === "cancelled"
        ? false
        : 2_000;
    },
  });
  const submitAnalysis = useMutation({
    mutationFn: async () => {
      if (reportId === null) throw new Error("报告不存在");
      setAnalysisStage("collecting");
      const preview = await reportsApi.analysisPreview(reportId);
      setAnalysisPreview(preview);
      setAnalysisStage("rules");
      const firstModel = (modelsQuery.data ?? []).find((m) => m.enabled)?.name;
      if (!firstModel) return null;
      setAnalysisStage("ai");
      return reportsApi.analyze(reportId, { model_name: firstModel });
    },
    onSuccess: (res) => {
      if (res) {
        setAnalysisRunId(res.ai_run_id);
        toast.success("规则诊断已完成，正在生成 AI 汇总");
      } else {
        setAnalysisStage("done");
        toast.success("规则诊断已完成");
      }
    },
    onError: (err) => {
      setAnalysisStage("failed");
      toast.error(err instanceof Error ? err.message : "提交分析失败");
    },
  });
  useEffect(() => {
    const status = analysisQuery.data?.status;
    if (status === "success") setAnalysisStage("done");
    if (status === "failed" || status === "cancelled") setAnalysisStage("failed");
  }, [analysisQuery.data?.status]);

  return (
    <Dialog open={reportId !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>报告详情 #{reportId ?? ""}</DialogTitle>
          <DialogDescription>
            步骤级执行结果。Allure 链接（如果有）会单独给出。
          </DialogDescription>
        </DialogHeader>

        {query.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-full" />
          </div>
        ) : query.isError ? (
          <div className="text-sm text-destructive">
            加载失败：
            {query.error instanceof Error ? query.error.message : "未知错误"}
          </div>
        ) : query.data ? (
          <DetailBody
            detail={query.data}
            analysisRun={analysisQuery.data ?? null}
            analysisPreview={analysisPreview}
            analysisStage={analysisStage}
            analysisLoading={submitAnalysis.isPending || analysisQuery.isLoading}
            onStartAnalysis={() => submitAnalysis.mutate()}
          />
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DetailBody({
  detail,
  analysisRun,
  analysisPreview,
  analysisStage,
  analysisLoading,
  onStartAnalysis,
}: {
  detail: TestReportDetail;
  analysisRun: AiRun | null;
  analysisPreview: ReportAnalysisOutput | null;
  analysisStage: "idle" | "collecting" | "rules" | "ai" | "done" | "failed";
  analysisLoading: boolean;
  onStartAnalysis: () => void;
}) {
  const meta: [string, string][] = [
    ["项目", detail.project_name ?? `#${detail.project_id ?? "-"}`],
    ["分类", detail.category ?? "—"],
    ["状态", detail.status ?? "—"],
    ["执行人", detail.executor ?? "—"],
    [
      "通过率",
      detail.total_count > 0
        ? `${detail.pass_count}/${detail.total_count} · 失败 ${detail.fail_count} · 错误 ${detail.error_count} · 跳过 ${detail.skip_count}`
        : "—",
    ],
    [
      "时长",
      detail.duration !== null ? `${detail.duration.toFixed(2)} 秒` : "—",
    ],
    ["开始时间", formatTime(detail.start_time) ?? "—"],
    ["结束时间", formatTime(detail.end_time) ?? "—"],
  ];

  return (
    <div className="space-y-4">
      {/* 元信息 */}
      <Card>
        <CardContent className="grid grid-cols-2 gap-2 p-4 text-sm">
          {meta.map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <span className="w-20 text-muted-foreground">{k}</span>
              <span className="flex-1 truncate">{v}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Allure 报告链接 */}
      <div className="flex flex-wrap items-center gap-2">
        {detail.allure_url ? (
          <a
            href={detail.allure_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            打开 Allure 报告
          </a>
        ) : null}
        <Button
          size="sm"
          variant="outline"
          disabled={analysisLoading || detail.status === "running"}
          onClick={onStartAnalysis}
        >
          {analysisLoading ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
          )}
          AI 分析
        </Button>
      </div>

      <AnalysisPanel run={analysisRun} preview={analysisPreview} stage={analysisStage} />

      {/* 错误摘要 */}
      {detail.summary ? (
        <div className="rounded border border-destructive/30 bg-destructive/5 p-3 text-xs">
          <div className="mb-1 font-semibold text-destructive">错误摘要</div>
          <pre className="whitespace-pre-wrap font-mono">{detail.summary}</pre>
        </div>
      ) : null}

      {/* 步骤列表 */}
      <StepsList steps={detail.steps} />
    </div>
  );
}

function AnalysisPanel({
  run,
  preview,
  stage,
}: {
  run: AiRun | null;
  preview: ReportAnalysisOutput | null;
  stage: "idle" | "collecting" | "rules" | "ai" | "done" | "failed";
}) {
  if (!run && !preview && stage === "idle") return null;
  if (!preview && (stage === "collecting" || run?.status === "pending" || run?.status === "running")) {
    return (
      <div className="rounded border bg-muted/30 p-3 text-sm text-muted-foreground">
        <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
        正在收集报告并运行规则诊断。
      </div>
    );
  }
  if (run?.status === "failed") {
    return (
      <div className="rounded border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
        分析失败：{run.error || "未知错误"}
      </div>
    );
  }

  const aiOutput = run?.status === "success" ? (run.output_payload as ReportAnalysisOutput | null) : null;
  const output = aiOutput ?? preview;
  if (!output) return null;
  const cases = output.cases ?? [];
  const suggestions = cases.flatMap((item) =>
    (item.suggestions ?? []).map((suggestion) => ({ ...suggestion, caseName: item.name, classification: item.classification })),
  );
  const high = suggestions.filter((s) => s.apply_mode === "high_confidence");
  const review = suggestions.filter((s) => s.apply_mode === "need_review");
  const manual = suggestions.filter((s) => s.apply_mode === "manual_required");

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <AnalysisStageBar stage={stage} hasAiSummary={Boolean(aiOutput?.ai_summary)} />
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="font-medium">执行结果体检</span>
          <span className="text-xs text-muted-foreground">用例 {output.summary.total_cases}</span>
          <span className="text-xs text-muted-foreground">建议 {output.summary.total_suggestions}</span>
          <span className="text-xs text-emerald-700">高置信 {high.length}</span>
          <span className="text-xs text-amber-700">需审核 {review.length}</span>
          <span className="text-xs text-red-700">需人工 {manual.length}</span>
        </div>
        {aiOutput?.ai_summary ? (
          <pre className="max-h-52 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-3 text-xs leading-relaxed">
            {aiOutput.ai_summary}
          </pre>
        ) : aiOutput?.ai_error ? (
          <div className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
            AI 汇总不可用，已展示规则分析：{aiOutput.ai_error}
          </div>
        ) : stage === "ai" || run?.status === "pending" || run?.status === "running" ? (
          <div className="rounded border bg-muted/30 p-2 text-xs text-muted-foreground">
            <Loader2 className="mr-1.5 inline h-3.5 w-3.5 animate-spin" />
            规则诊断已展示，AI 正在生成中文汇总。
          </div>
        ) : null}
        {suggestions.length === 0 ? (
          <div className="rounded border border-dashed p-3 text-center text-xs text-muted-foreground">
            暂未发现明显的提取、断言或参数问题。
          </div>
        ) : (
          <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
            {suggestions.slice(0, 80).map((s, index) => (
              <div key={`${s.step_report_id ?? index}-${index}`} className="rounded border p-2 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-muted px-1.5 py-0.5">{categoryLabel(s.category)}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5">{modeLabel(s.apply_mode)}</span>
                  <span className="min-w-0 flex-1 truncate font-medium">{s.title}</span>
                  <span className="text-muted-foreground">{Math.round((s.confidence ?? 0) * 100)}%</span>
                </div>
                <div className="mt-1 text-muted-foreground">
                  {s.caseName}
                  {s.step_name ? ` · ${s.step_name}` : ""}
                </div>
                {s.evidence ? <div className="mt-1 break-all">{s.evidence}</div> : null}
                <pre className="mt-1 max-h-24 overflow-auto rounded bg-muted/40 p-1.5 font-mono">
                  {JSON.stringify(s.action, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AnalysisStageBar({
  stage,
  hasAiSummary,
}: {
  stage: "idle" | "collecting" | "rules" | "ai" | "done" | "failed";
  hasAiSummary: boolean;
}) {
  const steps = [
    { key: "collecting", label: "收集报告" },
    { key: "rules", label: "规则诊断" },
    { key: "ai", label: "AI 汇总" },
    { key: "done", label: "完成" },
  ] as const;
  const indexMap: Record<typeof stage, number> = {
    idle: -1,
    collecting: 0,
    rules: 1,
    ai: 2,
    done: 3,
    failed: hasAiSummary ? 3 : 1,
  };
  const current = indexMap[stage];
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {steps.map((item, index) => {
        const active = index <= current;
        const running = index === current && stage !== "done" && stage !== "failed";
        return (
          <span
            key={item.key}
            className={cn(
              "inline-flex items-center gap-1 rounded border px-2 py-0.5",
              active ? "border-primary/30 bg-primary/10 text-primary" : "bg-muted/30 text-muted-foreground",
            )}
          >
            {running ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            {item.label}
          </span>
        );
      })}
    </div>
  );
}

function categoryLabel(category: string): string {
  const map: Record<string, string> = {
    missing_extraction: "提取",
    missing_assertion: "断言",
    parameter_error: "参数",
    sql_assertion_needed: "SQL",
    function_needed: "Function",
    environment_issue: "环境",
    api_defect: "接口",
  };
  return map[category] ?? category;
}

function modeLabel(mode: string): string {
  const map: Record<string, string> = {
    high_confidence: "高置信",
    need_review: "需审核",
    manual_required: "需人工",
  };
  return map[mode] ?? mode;
}

function StepsList({ steps }: { steps: TestReportDetail["steps"] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [bugStep, setBugStep] = useState<TestStepReportItem | null>(null);
  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const summary = useMemo(() => {
    const byStatus = new Map<string, number>();
    for (const s of steps) {
      const k = (s.status ?? "unknown").toLowerCase();
      byStatus.set(k, (byStatus.get(k) ?? 0) + 1);
    }
    return byStatus;
  }, [steps]);

  if (steps.length === 0) {
    return (
      <div className="rounded border border-dashed p-4 text-center text-xs text-muted-foreground">
        暂无步骤记录（任务可能还没跑完，或者跑的是 v1 场景）。
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-3 text-xs text-muted-foreground">
        <span>共 {steps.length} 步</span>
        {Array.from(summary.entries()).map(([k, v]) => (
          <span key={k}>
            {k} · {v}
          </span>
        ))}
      </div>
      <Card>
        <CardContent className="divide-y p-0">
          {steps.map((s) => {
            const open = expanded.has(s.id);
            const isFailed =
              s.status &&
              ["failed", "broken", "error"].includes(s.status.toLowerCase());
            return (
              <div key={s.id} className="px-3 py-2 text-sm">
                <div className="flex w-full items-center gap-2">
                  <button
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    onClick={() => toggle(s.id)}
                  >
                    {open ? (
                      <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <StatusBadge status={s.status} />
                    <span className="min-w-0 flex-1 truncate">
                      {s.step_name || `#${s.id}`}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {s.action ?? s.step_type ?? "—"}
                    </span>
                  </button>
                  {isFailed ? (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 shrink-0"
                      onClick={(e) => {
                        e.stopPropagation();
                        setBugStep(s);
                      }}
                    >
                      <Bug className="mr-1 h-3.5 w-3.5" /> 建 Bug
                    </Button>
                  ) : null}
                </div>
                {open ? (
                  <div className="mt-2 space-y-1 rounded bg-muted/40 p-2 text-xs">
                    <Line k="target" v={s.target} />
                    <Line
                      k="status_code"
                      v={s.status_code !== null ? String(s.status_code) : null}
                    />
                    <Line
                      k="duration"
                      v={s.duration !== null ? `${s.duration.toFixed(2)} s` : null}
                    />
                    <Line k="error" v={s.error_message} />
                    <Line k="create_time" v={formatTime(s.create_time)} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </CardContent>
      </Card>
      <CreateBugModal
        open={bugStep !== null}
        onOpenChange={(o) => !o && setBugStep(null)}
        parentTaskId={null}
        relatedCaseId={bugStep?.case_id ?? null}
        defaultTitle={bugStep ? `失败步骤：${bugStep.step_name ?? `#${bugStep.id}`}` : ""}
      />
    </div>
  );
}

function Line({ k, v }: { k: string; v: string | null | undefined }) {
  if (!v) return null;
  return (
    <div className="flex gap-2">
      <span className="w-24 shrink-0 text-muted-foreground">{k}</span>
      <span className="flex-1 break-all font-mono">{v}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 空态 / 骨架 / 错误 / 工具函数
// ---------------------------------------------------------------------------
function EmptyState() {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center text-sm text-muted-foreground">
        还没有符合条件的执行记录。去项目页跑一条用例再回来看看。
      </CardContent>
    </Card>
  );
}

function ListSkeleton() {
  return (
    <Card>
      <CardContent className="divide-y p-0">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-4 py-3">
            <Skeleton className="h-4 w-10" />
            <Skeleton className="h-4 flex-1" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-20" />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function ErrorBox({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="border-destructive/50">
      <CardContent className="flex flex-col items-start gap-3 py-6">
        <div className="text-sm text-destructive">加载失败：{message}</div>
        <Button onClick={onRetry} variant="outline" size="sm">
          重试
        </Button>
      </CardContent>
    </Card>
  );
}

function formatTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  // ISO 转「MM-DD HH:mm:ss」本地时区，列表里更好看
  try {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return iso;
  }
}
