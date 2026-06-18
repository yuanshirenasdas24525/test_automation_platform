/**
 * Bug tab：版本下所有 bug 列表 + 创建入口。
 *
 * 顶部 toolbar："创建 Bug" 按钮 + severity / status 筛选
 * 主体：Bug 列表表格（标题、严重度、状态、需求、开发负责人、创建时间）
 *
 * 数据来源：tasksApi.list({ type: "bug", version_id, status })
 * severity 做前端筛选（后端任务 API 暂不按 severity 过滤）
 */
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bug, Loader2, Plus, Sparkles, Pencil } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { tasksApi, aiApi } from "@/lib/api";
import { ALL_BUG_SEVERITIES, ALL_TASK_STATUSES } from "@/types/domain";
import type { Task, TaskStatus } from "@/types/domain";
import { CreateBugModal } from "@/pages/tasks/CreateBugModal";
import { BugFixDialog } from "@/pages/versions/tabs/BugFixDialog";
import { toast } from "sonner";

const ANY = "__any__";

function severityClass(s: string | null | undefined): string {
  switch (s) {
    case "P0":
      return "font-semibold text-red-600";
    case "P1":
      return "font-semibold text-orange-500";
    case "P2":
      return "text-amber-600";
    case "P3":
      return "text-muted-foreground";
    default:
      return "";
  }
}

function statusBadge(status: Task["status"]): {
  label: string;
  className: string;
} {
  switch (status) {
    case "pending":
      return { label: "待处理", className: "bg-gray-100 text-gray-600" };
    case "dev_doing":
      return { label: "修复中", className: "bg-blue-100 text-blue-700" };
    case "dev_done":
      return { label: "已修复", className: "bg-emerald-100 text-emerald-700" };
    case "test_doing":
      return { label: "测试中", className: "bg-violet-100 text-violet-700" };
    case "passed":
      return { label: "通过", className: "bg-green-100 text-green-700" };
    case "failed":
      return { label: "失败", className: "bg-red-100 text-red-700" };
    case "closed":
      return { label: "关闭", className: "bg-gray-100 text-gray-500" };
    default:
      return { label: status, className: "" };
  }
}

export function BugTab({
  projectId,
  versionId,
}: {
  projectId: number;
  versionId: number;
}) {
  void projectId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [severityFilter, setSeverityFilter] = useState<string>(ANY);
  const [statusFilter, setStatusFilter] = useState<string>(ANY);
  const [createOpen, setCreateOpen] = useState(false);
  const [fixTarget, setFixTarget] = useState<Task | null>(null);
  const [fixingAiRunId, setFixingAiRunId] = useState<number | null>(null);
  const [editBug, setEditBug] = useState<Task | null>(null);

  // 轮询 AI 修复任务状态
  const fixStatusQuery = useQuery({
    queryKey: ["ai-run", fixingAiRunId],
    queryFn: () => aiApi.getRun(fixingAiRunId!),
    enabled: fixingAiRunId != null,
    refetchInterval: 2000,
  });

  // 监听修复任务状态变化
  const fixRunStatus = fixStatusQuery.data?.status;
  const fixRunError = fixStatusQuery.data?.error;
  if (fixRunStatus === "success" || fixRunStatus === "failed" || fixRunStatus === "cancelled") {
    if (fixStatusQuery.data && fixRunStatus === "success") {
      toast.success("AI 修复完成，Bug 已标记为已修复");
    } else if (fixRunStatus === "failed") {
      toast.error(`AI 修复失败：${fixRunError || "未知错误"}`);
    }
    queryClient.invalidateQueries({ queryKey: ["tasks", "version-bugs"] });
    queryClient.invalidateQueries({ queryKey: ["tasks"] });
    if (fixTarget) {
      queryClient.invalidateQueries({ queryKey: ["task", fixTarget.id] });
    }
    // 延迟重置状态让 effect 只触发一次
    setTimeout(() => setFixingAiRunId(null), 0);
  }

  const query = useQuery({
    queryKey: ["tasks", "version-bugs", versionId, statusFilter],
    queryFn: () =>
      tasksApi.list({
        type: "bug",
        version_id: versionId,
        ...(statusFilter !== ANY
          ? { status: statusFilter as TaskStatus }
          : {}),
      }),
    enabled: !Number.isNaN(versionId),
  });

  const allBugs = useMemo<Task[]>(() => query.data ?? [], [query.data]);

  const bugs = useMemo(() => {
    if (severityFilter === ANY) return allBugs;
    return allBugs.filter((b) => b.severity === severityFilter);
  }, [allBugs, severityFilter]);

  const sorted = useMemo(
    () =>
      [...bugs].sort((a, b) => {
        const sev =
          (a.severity ?? "Z").localeCompare(b.severity ?? "Z");
        if (sev !== 0) return sev;
        return (b.created_at ?? "").localeCompare(a.created_at ?? "");
      }),
    [bugs],
  );

  return (
    <div className="space-y-4 p-6">
      <Card data-print-hide>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" />
            创建 Bug
          </Button>

          <FilterBlock label="严重度">
            <Select
              value={severityFilter}
              onValueChange={setSeverityFilter}
            >
              <SelectTrigger className="h-9 w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部</SelectItem>
                {ALL_BUG_SEVERITIES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>

          <FilterBlock label="状态">
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="h-9 w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部</SelectItem>
                {ALL_TASK_STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {statusBadge(s as TaskStatus).label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>

          <div className="ml-auto text-xs text-muted-foreground">
            共 {sorted.length} 条
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          {query.isLoading ? (
            <div className="p-6 text-sm text-muted-foreground">加载中…</div>
          ) : sorted.length === 0 ? (
            <div className="flex flex-col items-center gap-3 p-10 text-sm text-muted-foreground">
              <Bug className="h-8 w-8 opacity-40" />
              <div>该版本暂无 Bug</div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCreateOpen(true)}
              >
                <Plus className="mr-1 h-3.5 w-3.5" />
                创建第一个 Bug
              </Button>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="border-b text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2">标题</th>
                  <th className="px-4 py-2">严重度</th>
                  <th className="px-4 py-2">状态</th>
                  <th className="px-4 py-2">需求</th>
                  <th className="px-4 py-2">开发负责人</th>
                  <th className="px-4 py-2">创建时间</th>
                  <th className="px-4 py-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((b) => (
                  <tr
                    key={b.id}
                    className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
                    onClick={() => navigate(`/tasks/${b.id}`)}
                  >
                    <td className="px-4 py-2">
                      <Link
                        className="hover:underline"
                        to={`/tasks/${b.id}`}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {b.title}
                      </Link>
                    </td>
                    <td className={`px-4 py-2 ${severityClass(b.severity)}`}>
                      {b.severity ?? "—"}
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                          statusBadge(b.status).className
                        }`}
                      >
                        {statusBadge(b.status).label}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      #{b.requirement_id}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {b.assignee_dev_id ? `#${b.assignee_dev_id}` : "—"}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {formatDate(b.created_at)}
                    </td>
                    <td className="px-4 py-2">
                      {fixRunStatus === "running" && fixTarget?.id === b.id ? (
                        <span className="inline-flex items-center gap-1 text-xs text-blue-600">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          修复中...
                        </span>
                      ) : b.fix_description || b.fix_suggestion ? (
                        <span className="text-xs text-emerald-600">
                          <Sparkles className="mr-1 inline h-3 w-3" />
                          {b.fix_description ? "AI已修复" : "AI已分析"}
                        </span>
                      ) : b.status !== "dev_done" && b.status !== "closed" ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-violet-600 hover:text-violet-700 hover:bg-violet-50"
                          onClick={(e) => {
                            e.stopPropagation();
                            setFixTarget(b);
                          }}
                        >
                          <Sparkles className="mr-1 h-3 w-3" />
                          AI修复
                        </Button>
                      ) : null}
                      {b.status !== "closed" ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 ml-1"
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditBug(b);
                          }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <CreateBugModal
        open={createOpen}
        onOpenChange={setCreateOpen}
        versionId={versionId}
      />

      <BugFixDialog
        open={!!fixTarget}
        onOpenChange={(v) => !v && setFixTarget(null)}
        bug={fixTarget}
        onTriggered={(aiRunId) => {
          toast.success("AI 修复任务已启动");
          setFixingAiRunId(aiRunId);
          queryClient.invalidateQueries({ queryKey: ["tasks", "version-bugs"] });
        }}
      />

      {/* 编辑 Bug */}
      <CreateBugModal
        open={!!editBug}
        onOpenChange={(v) => { if (!v) setEditBug(null); }}
        editingBug={editBug}
        onEdited={() => {
          setEditBug(null);
          queryClient.invalidateQueries({ queryKey: ["tasks", "version-bugs"] });
        }}
      />
    </div>
  );
}

function FilterBlock({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-[11px] text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

function formatDate(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return s;
  }
}
