/**
 * /tasks —— 跨需求/角色查任务的全量页。
 *
 * 过滤参数全部通过 querystring 双绑（react-router useSearchParams），url 直接复制
 * 出去也能复用同一筛选；从工作台 widget 跳过来时 viewAllHref 直接拼好对应的 qs。
 */
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Loader2 } from "lucide-react";
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
import { tasksApi, usersApi, aiApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  ALL_TASK_STATUSES,
  ALL_TASK_TYPES,
} from "@/types/domain";
import type {
  Task,
  TaskListFilters,
  TaskStatus,
  TaskType,
  User,
} from "@/types/domain";
import { BugFixDialog } from "@/pages/versions/tabs/BugFixDialog";

const ANY = "__any__"; // shadcn Select 不支持空字符串值，用 sentinel 表示"任意"

export function TaskListPage() {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [fixTarget, setFixTarget] = useState<Task | null>(null);
  const [fixingAiRunId, setFixingAiRunId] = useState<number | null>(null);

  // 轮询 AI 修复任务状态
  const fixStatusQuery = useQuery({
    queryKey: ["ai-run", fixingAiRunId],
    queryFn: () => aiApi.getRun(fixingAiRunId!),
    enabled: fixingAiRunId != null,
    refetchInterval: 2000,
  });

  // 监听修复完成/失败
  const fixRunStatus = fixStatusQuery.data?.status;
  const fixRunError = fixStatusQuery.data?.error;
  if (fixRunStatus === "success" || fixRunStatus === "failed" || fixRunStatus === "cancelled") {
    if (fixStatusQuery.data && fixRunStatus === "success") {
      toast.success("AI 修复完成");
    } else if (fixRunStatus === "failed") {
      toast.error(`AI 修复失败：${fixRunError || "未知错误"}`);
    }
    queryClient.invalidateQueries({ queryKey: ["tasks", "list"] });
    setTimeout(() => setFixingAiRunId(null), 0);
  }

  const filters: TaskListFilters = useMemo(() => {
    const f: TaskListFilters = {};
    const ad = params.get("assignee_dev_id");
    if (ad) f.assignee_dev_id = Number(ad);
    const at = params.get("assignee_test_id");
    if (at) f.assignee_test_id = Number(at);
    const cb = params.get("created_by_id");
    if (cb) f.created_by_id = Number(cb);
    const rid = params.get("requirement_id");
    if (rid) f.requirement_id = Number(rid);
    const t = params.get("type");
    if (t) f.type = t as TaskType;
    const s = params.get("status");
    if (s) f.status = s as TaskStatus;
    const ca = params.get("closed_at_after");
    if (ca) f.closed_at_after = ca;
    return f;
  }, [params]);

  const tasksQuery = useQuery({
    queryKey: ["tasks", "list", filters],
    queryFn: () => tasksApi.list(filters),
  });

  const usersQuery = useQuery({
    queryKey: ["users", { is_active: true }],
    queryFn: () => usersApi.list({ is_active: true }),
  });
  const users = usersQuery.data ?? [];
  const userById = useMemo(() => {
    const m = new Map<number, User>();
    users.forEach((u) => m.set(u.id, u));
    return m;
  }, [users]);

  const updateParam = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params);
    if (value && value !== ANY) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const tasks = tasksQuery.data ?? [];

  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">任务</h1>
        <p className="text-sm text-muted-foreground">
          按 url 参数过滤；查询字符串里的过滤项可被工作台 widget 直接拼接。
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 rounded-md border bg-background p-4 md:grid-cols-3 lg:grid-cols-6">
        <FilterUserSelect
          label="开发负责人"
          users={users}
          value={params.get("assignee_dev_id") ?? ""}
          onChange={(v) => updateParam("assignee_dev_id", v)}
        />
        <FilterUserSelect
          label="测试负责人"
          users={users}
          value={params.get("assignee_test_id") ?? ""}
          onChange={(v) => updateParam("assignee_test_id", v)}
        />
        <FilterUserSelect
          label="创建人"
          users={users}
          value={params.get("created_by_id") ?? ""}
          onChange={(v) => updateParam("created_by_id", v)}
        />
        <div>
          <Label className="text-xs">类型</Label>
          <Select
            value={params.get("type") ?? ANY}
            onValueChange={(v) => updateParam("type", v === ANY ? undefined : v)}
          >
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>全部</SelectItem>
              {ALL_TASK_TYPES.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="text-xs">状态</Label>
          <Select
            value={params.get("status") ?? ANY}
            onValueChange={(v) => updateParam("status", v === ANY ? undefined : v)}
          >
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>全部</SelectItem>
              {ALL_TASK_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="text-xs">需求 ID</Label>
          <Input
            className="h-8"
            type="number"
            placeholder="例如 12"
            value={params.get("requirement_id") ?? ""}
            onChange={(e) =>
              updateParam("requirement_id", e.target.value || undefined)
            }
          />
        </div>
        <div className="col-span-2 md:col-span-3 lg:col-span-6 flex items-end">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setParams(new URLSearchParams(), { replace: true })}
          >
            清空所有筛选
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded-md border bg-background">
        {tasksQuery.isLoading ? (
          <div className="px-4 py-6 text-sm text-muted-foreground">加载中…</div>
        ) : tasksQuery.isError ? (
          <div className="px-4 py-6 text-sm text-destructive">
            加载失败：{(tasksQuery.error as Error).message}
          </div>
        ) : tasks.length === 0 ? (
          <div className="px-4 py-6 text-sm text-muted-foreground">
            没有匹配的任务。
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-2 text-left">标题</th>
                <th className="px-4 py-2 text-left">类型</th>
                <th className="px-4 py-2 text-left">状态</th>
                <th className="px-4 py-2 text-left">严重度</th>
                <th className="px-4 py-2 text-left">开发</th>
                <th className="px-4 py-2 text-left">测试</th>
                <th className="px-4 py-2 text-left">创建于</th>
                <th className="px-4 py-2 text-left">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {tasks.map((task) => (
                <tr key={task.id} className="hover:bg-accent/40">
                  <td className="px-4 py-2">
                    <Link
                      to={`/tasks/${task.id}`}
                      className="font-medium hover:underline"
                    >
                      {task.title}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-xs">{task.type}</td>
                  <td className="px-4 py-2">
                    <StatusBadge status={task.status} />
                  </td>
                  <td className="px-4 py-2 text-xs">{task.severity ?? "—"}</td>
                  <td className="px-4 py-2 text-xs">
                    {assigneeLabel(task.assignee_dev_id, userById)}
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {assigneeLabel(task.assignee_test_id, userById)}
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {formatDate(task.created_at)}
                  </td>
                  <td className="px-4 py-2">
                    {fixRunStatus === "running" && fixTarget?.id === task.id ? (
                      <span className="inline-flex items-center gap-1 text-xs text-blue-600">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        修复中...
                      </span>
                    ) : task.fix_description || task.fix_suggestion ? (
                      <span className="text-xs text-emerald-600">
                        <Sparkles className="mr-1 inline h-3 w-3" />
                        {task.fix_description ? "AI已修复" : "AI已分析"}
                      </span>
                    ) : task.type === "bug" &&
                    task.status !== "dev_done" &&
                    task.status !== "closed" ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-violet-600 hover:text-violet-700 hover:bg-violet-50"
                        onClick={(e) => {
                          e.stopPropagation();
                          setFixTarget(task);
                        }}
                      >
                        <Sparkles className="mr-1 h-3 w-3" />
                        AI修复
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <BugFixDialog
        open={!!fixTarget}
        onOpenChange={(v) => !v && setFixTarget(null)}
        bug={fixTarget}
        onTriggered={(aiRunId) => {
          toast.success("AI 修复任务已启动");
          setFixingAiRunId(aiRunId);
          queryClient.invalidateQueries({ queryKey: ["tasks", "list"] });
        }}
      />
    </div>
  );
}

function FilterUserSelect({
  label,
  users,
  value,
  onChange,
}: {
  label: string;
  users: User[];
  value: string;
  onChange: (value: string | undefined) => void;
}) {
  return (
    <div>
      <Label className="text-xs">{label}</Label>
      <Select
        value={value || ANY}
        onValueChange={(v) => onChange(v === ANY ? undefined : v)}
      >
        <SelectTrigger className="h-8">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>全部</SelectItem>
          {users.map((u) => (
            <SelectItem key={u.id} value={String(u.id)}>
              {u.full_name || u.username}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function StatusBadge({ status }: { status: Task["status"] }) {
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
        status === "passed" || status === "closed"
          ? "bg-green-100 text-green-800"
          : status === "failed"
            ? "bg-red-100 text-red-800"
            : status === "dev_doing" || status === "test_doing"
              ? "bg-blue-100 text-blue-800"
              : status === "dev_done"
                ? "bg-amber-100 text-amber-800"
                : "bg-muted text-muted-foreground",
      )}
    >
      {status}
    </span>
  );
}

function assigneeLabel(id: number | null | undefined, m: Map<number, User>) {
  if (id == null) return "—";
  const u = m.get(id);
  return u ? u.full_name || u.username : `#${id}`;
}

function formatDate(s: string | null | undefined) {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}
