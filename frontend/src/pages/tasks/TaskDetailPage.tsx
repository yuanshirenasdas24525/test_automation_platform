/**
 * /tasks/:id —— 任务详情页。
 *
 * - 头部展示标题 / type / status / severity（仅 bug）
 * - status 直接用下拉切到任意合法态，调 tasksApi.update + invalidate
 * - 元信息列：requirement / parent_task / 开发/测试/创建人 / 时间 / 工时
 * - 描述：纯文本展示（先不渲染 markdown）
 * - 子任务：tasksApi.list({ parent_task_id })，列表可点
 */
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import DOMPurify from "dompurify";
import { ArrowLeft, Trash2, Sparkles, Undo2, RefreshCw, Loader2 } from "lucide-react";

const sanitizeHtml = (html: string): string => {
  try {
    return DOMPurify.sanitize(html);
  } catch {
    return html.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
};

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { tasksApi, usersApi, bugFixApi, aiApi } from "@/lib/api";
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

  const rollbackMutation = useMutation({
    mutationFn: () =>
      bugFixApi.rollback(taskId, taskQuery.data?.fix_ai_run_id ?? 0),
    onSuccess: (res) => {
      toast.success(res.message ?? "已回滚");
      queryClient.invalidateQueries({ queryKey: ["task", taskId] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const [fixingAiRunId, setFixingAiRunId] = useState<number | null>(null);

  const fixStatusQuery = useQuery({
    queryKey: ["ai-run", fixingAiRunId],
    queryFn: () => aiApi.getRun(fixingAiRunId!),
    enabled: fixingAiRunId != null,
    refetchInterval: 2000,
  });

  const fixRunStatus = fixStatusQuery.data?.status;
  const fixRunError = fixStatusQuery.data?.error;
  if (fixRunStatus === "success" || fixRunStatus === "failed" || fixRunStatus === "cancelled") {
    if (fixStatusQuery.data && fixRunStatus === "success") {
      toast.success("AI 修复完成");
    } else if (fixRunStatus === "failed") {
      toast.error(`AI 修复失败：${fixRunError || "未知错误"}`);
    }
    queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    queryClient.invalidateQueries({ queryKey: ["tasks"] });
    setTimeout(() => setFixingAiRunId(null), 0);
  }

  const retryFixMutation = useMutation({
    mutationFn: (agentName: string) =>
      bugFixApi.fixBug(taskId, agentName),
    onSuccess: (res) => {
      toast.success("AI 修复任务已启动");
      setFixingAiRunId(res.ai_run_id);
      queryClient.invalidateQueries({ queryKey: ["task", taskId] });
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
            <div
              className="prose prose-sm max-w-none break-words text-sm [&_ul]:list-disc [&_ol]:list-decimal [&_li]:ml-4"
              dangerouslySetInnerHTML={{ __html: sanitizeHtml(task.description || "") }}
            />
          ) : (
            <div className="text-sm text-muted-foreground">无描述。</div>
          )}
        </CardContent>
      </Card>

      {/* 复现步骤 */}
      {task.metadata?.reproduce_steps ? (
        <Card>
          <div className="border-b px-4 py-3 text-sm font-semibold">复现步骤</div>
          <CardContent className="p-4">
            <div
              className="prose prose-sm max-w-none break-words text-sm [&_ul]:list-disc [&_ol]:list-decimal [&_li]:ml-4"
              dangerouslySetInnerHTML={{ __html: sanitizeHtml(String(task.metadata.reproduce_steps)) }}
            />
          </CardContent>
        </Card>
      ) : null}

      {/* AI 修复结果 */}
      {task.type === "bug" && (task.fix_description || task.fix_commit_sha || task.fix_suggestion || fixingAiRunId) ? (
        <Card className="border-violet-200">
          <div className="flex items-center gap-2 border-b border-violet-100 px-4 py-3">
            <Sparkles className="h-4 w-4 text-violet-500" />
            <span className="text-sm font-semibold text-violet-700">
              AI 修复结果
            </span>
            {task.fix_agent_used ? (
              <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-600">
                {task.fix_agent_used}
              </span>
            ) : null}
            {fixingAiRunId ? (
              <span className="ml-auto inline-flex items-center gap-1 text-xs text-violet-600">
                <Loader2 className="h-3 w-3 animate-spin" />
                修复中...
              </span>
            ) : null}
            {task.fix_commit_branch ? (
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto h-7 text-xs text-orange-600 hover:text-orange-700 hover:bg-orange-50"
                onClick={() => {
                  if (window.confirm("确认回滚该 AI 修复？（将删除远程分支）")) {
                    rollbackMutation.mutate();
                  }
                }}
                disabled={rollbackMutation.isPending}
              >
                <Undo2 className="mr-1 h-3 w-3" />
                回滚修复
              </Button>
            ) : null}
          </div>
          <CardContent className="space-y-3 p-4">
            {task.fix_description ? (
              <div>
                <div className="mb-1 text-xs text-muted-foreground">修复说明</div>
                <div
                  className="prose prose-sm max-w-none break-words rounded bg-muted/50 p-2 text-sm [&_ul]:list-disc [&_ol]:list-decimal [&_li]:ml-4"
                  dangerouslySetInnerHTML={{ __html: sanitizeHtml(task.fix_description || "") }}
                />
              </div>
            ) : null}
            {task.fix_commit_sha ? (
              <div className="flex items-center gap-4 text-sm">
                <div>
                  <div className="text-xs text-muted-foreground">Commit</div>
                  <code className="text-xs">{task.fix_commit_sha?.slice(0, 12)}</code>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">分支</div>
                  <code className="text-xs">{task.fix_commit_branch}</code>
                </div>
              </div>
            ) : null}
            {task.fix_suggestion ? (
              <div>
                <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                  <span>修复建议（未应用，需手动处理）</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 text-xs text-violet-600 hover:text-violet-700 hover:bg-violet-50"
                    disabled={retryFixMutation.isPending}
                    onClick={() =>
                      retryFixMutation.mutate(task.fix_agent_used || "opencode")
                    }
                  >
                    <RefreshCw className="mr-1 h-3 w-3" />
                    重试修复
                  </Button>
                </div>
                <pre className="whitespace-pre-wrap break-words rounded bg-amber-50 p-2 text-sm text-amber-800">
                  {task.fix_suggestion}
                </pre>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

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
