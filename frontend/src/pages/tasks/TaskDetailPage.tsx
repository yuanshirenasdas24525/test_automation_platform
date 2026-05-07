/**
 * /tasks/:id —— 任务详情页。
 *
 * - 头部展示标题 / type / status / severity（仅 bug）
 * - status 直接用下拉切到任意合法态，调 tasksApi.update + invalidate
 * - 元信息列：requirement / parent_task / 开发/测试/创建人 / 时间 / 工时
 * - 描述：纯文本展示（先不渲染 markdown）
 * - 子任务：tasksApi.list({ parent_task_id })，列表可点
 */
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { tasksApi, usersApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  ALL_BUG_SEVERITIES,
  ALL_TASK_STATUSES,
} from "@/types/domain";
import type { BugSeverity, TaskStatus, User } from "@/types/domain";

export function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const taskId = Number(params.id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const taskQuery = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => tasksApi.get(taskId),
    enabled: !Number.isNaN(taskId),
  });

  const subtasksQuery = useQuery({
    queryKey: ["tasks", "children", taskId],
    queryFn: () => tasksApi.list({ parent_task_id: taskId }),
    enabled: !Number.isNaN(taskId),
  });

  const usersQuery = useQuery({
    queryKey: ["users", { is_active: true }],
    queryFn: () => usersApi.list({ is_active: true }),
  });
  const users = usersQuery.data ?? [];
  const userById = new Map<number, User>(users.map((u) => [u.id, u]));

  const updateStatus = useMutation({
    mutationFn: (status: TaskStatus) =>
      tasksApi.update(taskId, { status }),
    onSuccess: () => {
      toast.success("状态已更新");
      queryClient.invalidateQueries({ queryKey: ["task", taskId] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const updateSeverity = useMutation({
    mutationFn: (severity: BugSeverity) =>
      tasksApi.update(taskId, { severity }),
    onSuccess: () => {
      toast.success("严重度已更新");
      queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const removeMutation = useMutation({
    mutationFn: () => tasksApi.remove(taskId),
    onSuccess: () => {
      toast.success("已删除");
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      navigate("/tasks");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  if (Number.isNaN(taskId)) {
    return (
      <div className="p-6 text-sm text-destructive">非法 task id</div>
    );
  }
  if (taskQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (taskQuery.isError || !taskQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载失败：{(taskQuery.error as Error | undefined)?.message ?? "task 不存在"}
      </div>
    );
  }
  const task = taskQuery.data;
  const subtasks = subtasksQuery.data ?? [];

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between gap-2">
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
          <ArrowLeft className="mr-1 h-3.5 w-3.5" /> 返回
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive hover:text-destructive"
          disabled={removeMutation.isPending}
          onClick={() => {
            if (window.confirm("确认删除该任务？")) removeMutation.mutate();
          }}
        >
          <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
        </Button>
      </div>

      <Card>
        <div className="border-b px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="break-words text-xl font-semibold">{task.title}</h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>#{task.id}</span>
                <span>·</span>
                <span>type = {task.type}</span>
                {task.requirement_id ? (
                  <>
                    <span>·</span>
                    <Link
                      to={`/tasks?requirement_id=${task.requirement_id}`}
                      className="hover:underline"
                    >
                      req #{task.requirement_id}
                    </Link>
                  </>
                ) : null}
                {task.parent_task_id ? (
                  <>
                    <span>·</span>
                    <Link
                      to={`/tasks/${task.parent_task_id}`}
                      className="hover:underline"
                    >
                      parent #{task.parent_task_id}
                    </Link>
                  </>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Select
                value={task.status}
                onValueChange={(v) => updateStatus.mutate(v as TaskStatus)}
                disabled={updateStatus.isPending}
              >
                <SelectTrigger className="h-8 w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ALL_TASK_STATUSES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {task.type === "bug" ? (
                <Select
                  value={task.severity ?? ""}
                  onValueChange={(v) => updateSeverity.mutate(v as BugSeverity)}
                  disabled={updateSeverity.isPending}
                >
                  <SelectTrigger className="h-8 w-[100px]">
                    <SelectValue placeholder="严重度" />
                  </SelectTrigger>
                  <SelectContent>
                    {ALL_BUG_SEVERITIES.map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
            </div>
          </div>
        </div>

        <CardContent className="grid grid-cols-2 gap-x-6 gap-y-3 p-4 text-sm md:grid-cols-3">
          <Meta label="开发负责人" value={assigneeLabel(task.assignee_dev_id, userById)} />
          <Meta label="测试负责人" value={assigneeLabel(task.assignee_test_id, userById)} />
          <Meta label="创建人" value={assigneeLabel(task.created_by_id, userById)} />
          <Meta label="预估工时" value={formatHours(task.estimated_hours)} />
          <Meta label="实际工时" value={formatHours(task.actual_hours)} />
          <Meta
            label="关联用例"
            value={
              task.related_case_id ? `case #${task.related_case_id}` : "—"
            }
          />
          <Meta label="创建于" value={formatDate(task.created_at)} />
          <Meta label="更新于" value={formatDate(task.updated_at)} />
          <Meta label="关闭于" value={formatDate(task.closed_at)} />
        </CardContent>
      </Card>

      <Card>
        <div className="border-b px-4 py-3 text-sm font-semibold">描述</div>
        <CardContent className="p-4">
          {task.description ? (
            <pre className="whitespace-pre-wrap break-words text-sm">
              {task.description}
            </pre>
          ) : (
            <div className="text-sm text-muted-foreground">无描述。</div>
          )}
        </CardContent>
      </Card>

      <Card>
        <div className="border-b px-4 py-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">子任务</span>
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {subtasks.length}
            </span>
          </div>
        </div>
        <CardContent className="p-0">
          {subtasksQuery.isLoading ? (
            <div className="px-4 py-4 text-xs text-muted-foreground">
              加载中…
            </div>
          ) : subtasks.length === 0 ? (
            <div className="px-4 py-4 text-xs text-muted-foreground">
              没有子任务。
            </div>
          ) : (
            <ul className="divide-y">
              {subtasks.map((s) => (
                <li key={s.id}>
                  <Link
                    to={`/tasks/${s.id}`}
                    className="block px-4 py-2 text-sm hover:bg-accent/40"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-medium">
                        {s.title}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
                          s.status === "passed" || s.status === "closed"
                            ? "bg-green-100 text-green-800"
                            : s.status === "failed"
                              ? "bg-red-100 text-red-800"
                              : "bg-muted text-muted-foreground",
                        )}
                      >
                        {s.status}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {s.type}
                      {s.severity ? ` · ${s.severity}` : ""}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 break-words">{value}</div>
    </div>
  );
}

function assigneeLabel(
  id: number | null | undefined,
  m: Map<number, Pick<User, "full_name" | "username">>,
) {
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

function formatHours(h: number | null | undefined) {
  if (h == null) return "—";
  return `${h} h`;
}
