import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Loader2,
  RefreshCw,
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
  reportsApi,
  type TestReportDetail,
  type TestReportSummary,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";

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
  const query = useQuery({
    queryKey: reportId ? queryKeys.report(reportId) : ["report", "none"],
    queryFn: () => reportsApi.get(reportId!),
    enabled: reportId !== null,
  });

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
          <DetailBody detail={query.data} />
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

function DetailBody({ detail }: { detail: TestReportDetail }) {
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

function StepsList({ steps }: { steps: TestReportDetail["steps"] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
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
            return (
              <div key={s.id} className="px-3 py-2 text-sm">
                <button
                  className="flex w-full items-center gap-2 text-left"
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
