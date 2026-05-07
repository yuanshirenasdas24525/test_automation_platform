/**
 * 测试报告 tab：4 张 Card 垂直堆叠 + 顶部"重算汇总 / 导出 PDF"按钮。
 *
 * 数据来源：
 *  - version-summary    指标 + payload（含 bug_ids）
 *  - version-board      requirements_by_status 平铺成需求覆盖表
 *  - tasks?ids=bug_ids  bug 明细（一次拉回，避免 N+1）
 *  - version cases      失败用例表（status=failed,broken,error）
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { tasksApi, versionSummariesApi, versionsApi } from "@/lib/api";
import type {
  Requirement,
  Task,
  VersionCase,
  VersionTestSummary,
} from "@/types/domain";

export function ReportTab({
  projectId,
  versionId,
  projectName,
  versionName,
}: {
  projectId: number;
  versionId: number;
  projectName: string;
  versionName: string;
}) {
  void projectId;
  const queryClient = useQueryClient();

  const summaryQuery = useQuery({
    queryKey: ["version-summary", versionId],
    queryFn: () => versionSummariesApi.get(versionId),
  });
  const boardQuery = useQuery({
    queryKey: ["version-board", versionId],
    queryFn: () => versionsApi.board(versionId),
  });
  const failedCasesQuery = useQuery({
    queryKey: ["version-cases", versionId, "failed"],
    queryFn: () =>
      versionsApi.listCases(versionId, { status: "failed,broken,error" }),
  });

  const bugIds = summaryQuery.data?.payload.bug_ids ?? [];
  const bugIdsKey = [...bugIds].sort((a, b) => a - b).join(",");
  const bugsQuery = useQuery({
    queryKey: ["tasks", "by-ids", bugIdsKey],
    queryFn: () => tasksApi.list({ ids: bugIds }),
    enabled: bugIds.length > 0,
  });

  const regenerateMutation = useMutation({
    mutationFn: () => versionSummariesApi.regenerate(versionId),
    onSuccess: () => {
      toast.success("已重算汇总");
      queryClient.invalidateQueries({ queryKey: ["version-summary", versionId] });
      queryClient.invalidateQueries({ queryKey: ["version-board", versionId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  function handleExportPdf() {
    const oldTitle = document.title;
    document.title = `${projectName}_${versionName}_报告`;
    const restore = () => {
      document.title = oldTitle;
      window.removeEventListener("afterprint", restore);
    };
    window.addEventListener("afterprint", restore);
    window.print();
  }

  if (summaryQuery.isLoading || boardQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (summaryQuery.isError || !summaryQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载汇总失败：
        {(summaryQuery.error as Error | undefined)?.message ?? "未知错误"}
      </div>
    );
  }

  const summary = summaryQuery.data;
  const reqs: Requirement[] = boardQuery.data
    ? Object.values(boardQuery.data.requirements_by_status).flat()
    : [];
  const bugs: Task[] = bugsQuery.data ?? [];
  const failedCases: VersionCase[] = failedCasesQuery.data?.items ?? [];

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-end gap-2" data-print-hide>
        <Button
          size="sm"
          variant="outline"
          disabled={regenerateMutation.isPending}
          onClick={() => regenerateMutation.mutate()}
        >
          <RefreshCw
            className={`mr-1 h-3.5 w-3.5 ${
              regenerateMutation.isPending ? "animate-spin" : ""
            }`}
          />
          重算汇总
        </Button>
        <Button size="sm" onClick={handleExportPdf}>
          <Download className="mr-1 h-3.5 w-3.5" />
          导出 PDF
        </Button>
      </div>

      <MetricsCard summary={summary} />
      <BugTableCard bugs={bugs} loading={bugsQuery.isLoading} />
      <FailedCasesCard cases={failedCases} loading={failedCasesQuery.isLoading} />
      <RequirementCoverageCard reqs={reqs} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card 1：指标
// ---------------------------------------------------------------------------
function MetricsCard({
  summary,
}: {
  summary: VersionTestSummary;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">关键指标</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricGroup
          title="计数"
          rows={[
            ["需求", summary.total_requirements],
            ["任务", summary.total_tasks],
            ["用例", summary.total_test_cases],
          ]}
        />
        <MetricGroup
          title="用例执行"
          rows={[
            ["通过", summary.passed],
            ["失败", summary.failed],
            ["阻塞", summary.blocked],
          ]}
        />
        <MetricGroup
          title="Bug"
          rows={[
            ["总数", summary.total_bugs],
            ["P0", summary.p0_bugs],
            ["P1", summary.p1_bugs],
            ["P2", summary.p2_bugs],
            ["P3", summary.p3_bugs],
          ]}
        />
        <MetricGroup
          title="质量指标"
          rows={[
            ["首次通过率", formatPct(summary.first_pass_rate)],
            ["覆盖率", formatPct(summary.test_coverage)],
            ["平均修复时长", formatHours(summary.avg_fix_time_hours)],
          ]}
        />
      </CardContent>
    </Card>
  );
}

function MetricGroup({
  title,
  rows,
}: {
  title: string;
  rows: [string, number | string | null | undefined][];
}) {
  return (
    <div className="rounded border bg-card p-3">
      <div className="mb-2 text-xs font-semibold text-muted-foreground">{title}</div>
      <div className="space-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between">
            <span className="text-muted-foreground">{k}</span>
            <span className="font-medium">{v ?? "—"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card 2：Bug 表
// ---------------------------------------------------------------------------
function BugTableCard({ bugs, loading }: { bugs: Task[]; loading: boolean }) {
  const sorted = [...bugs].sort((a, b) => {
    const sev = (a.severity ?? "Z").localeCompare(b.severity ?? "Z");
    if (sev !== 0) return sev;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          Bug 明细
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            ({sorted.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {loading ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : sorted.length === 0 ? (
          <div className="text-sm text-muted-foreground">没有 bug。</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">标题</th>
                <th className="py-2 pr-2">严重度</th>
                <th className="py-2 pr-2">负责人(dev_id)</th>
                <th className="py-2 pr-2">创建</th>
                <th className="py-2 pr-2">关闭</th>
                <th className="py-2 pr-2">修复时长</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((b) => (
                <tr key={b.id} className="border-b last:border-0">
                  <td className="py-2 pr-2">
                    <Link className="hover:underline" to={`/tasks/${b.id}`}>
                      {b.title}
                    </Link>
                  </td>
                  <td className="py-2 pr-2">{b.severity ?? "—"}</td>
                  <td className="py-2 pr-2">{b.assignee_dev_id ?? "—"}</td>
                  <td className="py-2 pr-2">{formatDate(b.created_at)}</td>
                  <td className="py-2 pr-2">{formatDate(b.closed_at)}</td>
                  <td className="py-2 pr-2">
                    {formatFixDuration(b.created_at, b.closed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Card 3：失败用例
// ---------------------------------------------------------------------------
function FailedCasesCard({
  cases,
  loading,
}: {
  cases: VersionCase[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          失败用例
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            ({cases.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {loading ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : cases.length === 0 ? (
          <div className="text-sm text-muted-foreground">没有失败用例。</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">用例</th>
                <th className="py-2 pr-2">类型</th>
                <th className="py-2 pr-2">最近一次状态</th>
                <th className="py-2 pr-2">执行时间</th>
                <th className="py-2 pr-2">报告</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="py-2 pr-2">{c.name}</td>
                  <td className="py-2 pr-2">{c.case_type}</td>
                  <td className="py-2 pr-2">{c.latest_run?.status ?? "—"}</td>
                  <td className="py-2 pr-2">
                    {formatDate(c.latest_run?.executed_at)}
                  </td>
                  <td className="py-2 pr-2">
                    {c.latest_run?.report_id ? (
                      <Link
                        className="hover:underline"
                        to={`/runs/${c.latest_run.report_id}`}
                      >
                        #{c.latest_run.report_id}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Card 4：需求覆盖
// ---------------------------------------------------------------------------
function RequirementCoverageCard({ reqs }: { reqs: Requirement[] }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          需求覆盖
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            ({reqs.length})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {reqs.length === 0 ? (
          <div className="text-sm text-muted-foreground">没有关联需求。</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-2">需求</th>
                <th className="py-2 pr-2">系统态</th>
                <th className="py-2 pr-2">业务态</th>
              </tr>
            </thead>
            <tbody>
              {reqs.map((r) => (
                <tr key={r.id} className="border-b last:border-0">
                  <td className="py-2 pr-2">{r.title}</td>
                  <td className="py-2 pr-2">{r.system_status ?? "—"}</td>
                  <td className="py-2 pr-2">{r.business_status ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function formatPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function formatHours(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)} h`;
}

function formatDate(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

function formatFixDuration(
  createdAt: string | null | undefined,
  closedAt: string | null | undefined,
): string {
  if (!createdAt || !closedAt) return "—";
  try {
    const ms = new Date(closedAt).getTime() - new Date(createdAt).getTime();
    if (ms < 0) return "—";
    const totalMin = Math.round(ms / 60000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  } catch {
    return "—";
  }
}
