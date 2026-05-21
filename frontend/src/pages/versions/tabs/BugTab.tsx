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
import { useQuery } from "@tanstack/react-query";
import { Bug, Plus } from "lucide-react";
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
import { tasksApi } from "@/lib/api";
import { ALL_BUG_SEVERITIES, ALL_TASK_STATUSES } from "@/types/domain";
import type { Task, TaskStatus } from "@/types/domain";
import { CreateBugModal } from "@/pages/tasks/CreateBugModal";

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
  const [severityFilter, setSeverityFilter] = useState<string>(ANY);
  const [statusFilter, setStatusFilter] = useState<string>(ANY);
  const [createOpen, setCreateOpen] = useState(false);

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

  const allBugs: Task[] = query.data ?? [];

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
        parentTaskId={null}
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
