/**
 * 测试报告里"快捷建 bug"的弹窗。受控用法：
 *   <CreateBugModal
 *     open={open}
 *     onOpenChange={setOpen}
 *     parentTaskId={task.id}
 *     relatedCaseId={step.case_id}
 *     defaultTitle={`步骤失败：${step.name}`}
 *   />
 *
 * 提交直接调 POST /api/tasks/from-test-failure，让后端自动从 parent_task 继承
 * requirement_id / assignee_dev_id，前端只需要传严重度、标题和 reproduce_steps。
 */
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import { tasksApi } from "@/lib/api";
import { useUserId } from "@/lib/current-user";
import { ALL_BUG_SEVERITIES } from "@/types/domain";
import type { BugSeverity } from "@/types/domain";

const formSchema = z.object({
  title: z.string().min(1, "标题必填").max(255),
  severity: z.enum(ALL_BUG_SEVERITIES),
  description: z.string().max(4000).optional(),
  reproduce_steps: z.string().max(4000).optional(),
});

type FormValues = z.infer<typeof formSchema>;

interface CreateBugModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 父 dev 任务。传 null 时弹窗顶部会渲染 parent picker 让用户挑。 */
  parentTaskId: number | null;
  relatedCaseId?: number | null;
  defaultTitle?: string;
  /** 不传时从 useUserId() 取；无登录用户会校验失败。 */
  createdById?: number | null;
}

export function CreateBugModal({
  open,
  onOpenChange,
  parentTaskId,
  relatedCaseId,
  defaultTitle,
  createdById,
}: CreateBugModalProps) {
  const fallbackUserId = useUserId();
  const userId = createdById ?? fallbackUserId;
  const queryClient = useQueryClient();

  // parent picker（仅在 parentTaskId 为 null 时启用）：拉所有 type=dev 的活动任务给用户选
  const [pickedParentId, setPickedParentId] = useState<number | null>(null);
  useEffect(() => {
    if (open) setPickedParentId(parentTaskId);
  }, [open, parentTaskId]);

  const candidatesQuery = useQuery({
    queryKey: ["tasks", "dev-candidates"],
    queryFn: () => tasksApi.list({ type: "dev" }),
    enabled: open && parentTaskId === null,
  });
  const candidates = useMemo(
    () =>
      (candidatesQuery.data ?? []).filter(
        (t) => t.status !== "closed" && t.status !== "passed",
      ),
    [candidatesQuery.data],
  );

  const effectiveParentId = parentTaskId ?? pickedParentId;

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      title: defaultTitle ?? "",
      severity: "P2",
      description: "",
      reproduce_steps: "",
    },
  });

  // open 重置：每次打开重新初始化（关键，否则上次的 title 会粘住）
  useEffect(() => {
    if (open) {
      form.reset({
        title: defaultTitle ?? "",
        severity: "P2",
        description: "",
        reproduce_steps: "",
      });
    }
  }, [open, defaultTitle, form]);

  const submit = useMutation({
    mutationFn: (values: FormValues) => {
      if (userId == null) {
        return Promise.reject(new Error("当前没有用户，先在右上角选一个"));
      }
      if (effectiveParentId == null) {
        return Promise.reject(new Error("请先选择 parent 任务"));
      }
      const metadata: Record<string, unknown> = {};
      if (values.reproduce_steps && values.reproduce_steps.trim()) {
        metadata.reproduce_steps = values.reproduce_steps.trim();
      }
      return tasksApi.fromTestFailure({
        parent_task_id: effectiveParentId,
        severity: values.severity,
        title: values.title.trim(),
        created_by_id: userId,
        related_case_id: relatedCaseId ?? null,
        description: values.description?.trim() || null,
        metadata: Object.keys(metadata).length > 0 ? metadata : null,
      });
    },
    onSuccess: (task) => {
      toast.success(`已建 bug #${task.id}`);
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onOpenChange(false);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>建 Bug</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit((v) => submit.mutate(v))}
          className="space-y-3"
        >
          {parentTaskId === null ? (
            <div>
              <Label>父任务（dev）</Label>
              <Select
                value={pickedParentId ? String(pickedParentId) : ""}
                onValueChange={(v) => setPickedParentId(Number(v))}
                disabled={candidatesQuery.isLoading}
              >
                <SelectTrigger className="h-9">
                  <SelectValue
                    placeholder={
                      candidatesQuery.isLoading
                        ? "加载候选任务…"
                        : candidates.length === 0
                          ? "暂无活动 dev 任务"
                          : "选一个 dev 任务作为 parent"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {candidates.map((t) => (
                    <SelectItem key={t.id} value={String(t.id)}>
                      #{t.id} · {t.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="mt-1 text-[11px] text-muted-foreground">
                bug 会从这个 parent 继承 requirement_id 和 dev 负责人。
              </div>
            </div>
          ) : null}
          <div>
            <Label htmlFor="bug-title">标题</Label>
            <Input id="bug-title" autoFocus {...form.register("title")} />
            {form.formState.errors.title ? (
              <div className="mt-1 text-xs text-destructive">
                {form.formState.errors.title.message}
              </div>
            ) : null}
          </div>
          <div>
            <Label>严重度</Label>
            <Select
              value={form.watch("severity")}
              onValueChange={(v) =>
                form.setValue("severity", v as BugSeverity, {
                  shouldValidate: true,
                })
              }
            >
              <SelectTrigger className="h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ALL_BUG_SEVERITIES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="bug-desc">描述</Label>
            <Textarea
              id="bug-desc"
              rows={3}
              placeholder="可选：bug 现象 / 影响范围"
              {...form.register("description")}
            />
          </div>
          <div>
            <Label htmlFor="bug-repro">复现步骤</Label>
            <Textarea
              id="bug-repro"
              rows={3}
              placeholder="可选：1. 打开 X；2. 点击 Y；3. ..."
              {...form.register("reproduce_steps")}
            />
            <div className="mt-1 text-[11px] text-muted-foreground">
              会落到 metadata.reproduce_steps，方便后续按字段查询。
            </div>
          </div>
          <div className="rounded border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            来源：parent_task{" "}
            {effectiveParentId ? `#${effectiveParentId}` : "(未选)"}
            {relatedCaseId ? ` · case #${relatedCaseId}` : ""}
            {userId == null ? " · 当前未选用户，提交会失败" : ""}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              取消
            </Button>
            <Button type="submit" disabled={submit.isPending}>
              {submit.isPending ? "提交中…" : "提交"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
