/**
 * 建 Bug / 编辑 Bug 弹窗。
 *
 * 创建模式：versionId / parentTaskId 控制关联方式
 * 编辑模式：传 editingBug 触发，使用 tasksApi.update
 */
import { useEffect, useState } from "react";
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
import { RichTextEditor } from "@/components/editor/RichTextEditor";
import { tasksApi, versionsApi, usersApi } from "@/lib/api";
import { useUserId } from "@/lib/current-user";
import { ALL_BUG_SEVERITIES } from "@/types/domain";
import type { BugSeverity, VersionPickerItem, Task, User } from "@/types/domain";

const formSchema = z.object({
  title: z.string().min(1, "标题必填").max(255),
  severity: z.enum(ALL_BUG_SEVERITIES),
  description: z.string().max(4000).optional(),
  reproduce_steps: z.string().max(4000).optional(),
  related_case_id: z.number().int().positive().optional().nullable(),
  estimated_hours: z.number().min(0).max(999).optional().nullable(),
  assignee_dev_id: z.number().int().positive().optional().nullable(),
});

type FormValues = z.infer<typeof formSchema>;

interface CreateBugModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  versionId?: number | null;
  parentTaskId?: number | null;
  relatedCaseId?: number | null;
  defaultTitle?: string;
  createdById?: number | null;
  /** 编辑模式：传入已有 Bug */
  editingBug?: Task | null;
  onEdited?: () => void;
}

export function CreateBugModal({
  open,
  onOpenChange,
  versionId,
  parentTaskId,
  relatedCaseId,
  defaultTitle,
  createdById,
  editingBug,
  onEdited,
}: CreateBugModalProps) {
  const fallbackUserId = useUserId();
  const userId = createdById ?? fallbackUserId;
  const queryClient = useQueryClient();
  const isEdit = !!editingBug;

  // ── 版本选择器 ──
  const [pickedVersionId, setPickedVersionId] = useState<number | null>(null);
  const effectiveVersionId = versionId ?? pickedVersionId;

  const versionsQuery = useQuery({
    queryKey: ["versions", "picker"],
    queryFn: () => versionsApi.picker(),
    enabled: open && versionId == null && parentTaskId == null && !isEdit,
  });
  const versions: VersionPickerItem[] = versionsQuery.data ?? [];

  const effectiveParentId = parentTaskId ?? null;

  // ── 用户列表（处理人选择器） ──
  const usersQuery = useQuery({
    queryKey: ["users", { is_active: true }],
    queryFn: () => usersApi.list({ is_active: true }),
    enabled: open,
  });
  const users: User[] = usersQuery.data ?? [];

  useEffect(() => {
    if (open) {
      setPickedVersionId(versionId ?? null);
    }
  }, [open, versionId]);

  // ── 表单 ──
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      title: "",
      severity: "P2",
      description: "",
      reproduce_steps: "",
      related_case_id: null,
      estimated_hours: null,
      assignee_dev_id: null,
    },
  });

  useEffect(() => {
    if (open) {
      if (editingBug) {
          const meta = (editingBug as unknown as Record<string, unknown>).metadata as Record<string, unknown> | undefined;
        form.reset({
          title: editingBug.title,
          severity: editingBug.severity ?? "P2",
          description: editingBug.description ?? "",
          reproduce_steps: (meta?.reproduce_steps as string) ?? "",
          related_case_id: editingBug.related_case_id ?? null,
          estimated_hours: editingBug.estimated_hours ?? null,
          assignee_dev_id: editingBug.assignee_dev_id ?? null,
        });
      } else {
        form.reset({
          title: defaultTitle ?? "",
          severity: "P2",
          description: "",
          reproduce_steps: "",
          related_case_id: relatedCaseId ?? null,
          estimated_hours: null,
          assignee_dev_id: null,
        });
      }
    }
  }, [open, editingBug, defaultTitle, relatedCaseId, form]);

  // ── 提交（创建） ──
  const createMutation = useMutation({
    mutationFn: (values: FormValues) => {
      if (userId == null) {
        return Promise.reject(new Error("当前没有用户"));
      }

      const useVersion = effectiveVersionId != null;
      const useParent = !useVersion && effectiveParentId != null;

      if (!isEdit && !useVersion && !useParent) {
        return Promise.reject(new Error("请先选择版本迭代"));
      }

      const metadata: Record<string, unknown> = {};
      if (values.reproduce_steps && values.reproduce_steps.trim()) {
        metadata.reproduce_steps = values.reproduce_steps.trim();
      }

      return tasksApi.fromTestFailure({
        version_id: effectiveVersionId ?? null,
        parent_task_id: effectiveParentId ?? null,
        severity: values.severity,
        title: values.title.trim(),
        created_by_id: userId,
        related_case_id: values.related_case_id ?? null,
        description: values.description?.trim() || null,
        assignee_dev_id: values.assignee_dev_id ?? null,
        estimated_hours: values.estimated_hours ?? null,
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

  // ── 提交（编辑） ──
  const editMutation = useMutation({
    mutationFn: (values: FormValues) => {
      if (!editingBug) return Promise.reject(new Error("缺少编辑目标"));
      const metadata: Record<string, unknown> = {};
      if (values.reproduce_steps && values.reproduce_steps.trim()) {
        metadata.reproduce_steps = values.reproduce_steps.trim();
      }
      return tasksApi.update(editingBug.id, {
        title: values.title.trim(),
        severity: values.severity,
        description: values.description?.trim() || null,
        assignee_dev_id: values.assignee_dev_id ?? null,
        related_case_id: values.related_case_id ?? null,
        estimated_hours: values.estimated_hours ?? null,
        metadata: Object.keys(metadata).length > 0 ? metadata : null,
      });
    },
    onSuccess: () => {
      toast.success("Bug 已更新");
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      onEdited?.();
      onOpenChange(false);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const submitMutation = isEdit ? editMutation : createMutation;

  // ── 显示逻辑 ──
  const showVersionPicker = versionId == null && parentTaskId == null && !isEdit;
  const hasPreselected = versionId != null || parentTaskId != null;

  const sourceLabel = effectiveVersionId
    ? `版本 #${effectiveVersionId}`
    : effectiveParentId
      ? `parent 任务 #${effectiveParentId}`
      : "(未选)";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑 Bug" : "建 Bug"}</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit((v) => submitMutation.mutate(v))}
          className="space-y-3"
        >
          {/* 版本选择器（仅创建模式） */}
          {showVersionPicker ? (
            <div>
              <Label>关联版本迭代</Label>
              <Select
                value={pickedVersionId ? String(pickedVersionId) : ""}
                onValueChange={(v) => setPickedVersionId(Number(v))}
                disabled={versionsQuery.isLoading}
              >
                <SelectTrigger className="h-9">
                  <SelectValue
                    placeholder={
                      versionsQuery.isLoading
                        ? "加载版本列表…"
                        : versions.length === 0
                          ? "暂无版本"
                          : "选择版本迭代"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {versions.map((v) => (
                    <SelectItem key={v.id} value={String(v.id)}>
                      {v.project_name} / {v.version_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="mt-1 text-[11px] text-muted-foreground">
                bug 会关联到该版本迭代
              </div>
            </div>
          ) : null}

          {hasPreselected ? (
            <div className="rounded border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
              {versionId != null
                ? `已关联版本 #${versionId}`
                : `已关联 parent 任务 #${parentTaskId}`}
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
                form.setValue("severity", v as BugSeverity, { shouldValidate: true })
              }
            >
              <SelectTrigger className="h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ALL_BUG_SEVERITIES.map((s) => (
                  <SelectItem key={s} value={s}>{s}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 处理人 */}
          <div>
            <Label>开发负责人</Label>
            <Select
              value={form.watch("assignee_dev_id") ? String(form.watch("assignee_dev_id")) : ""}
              onValueChange={(v) =>
                form.setValue("assignee_dev_id", v ? Number(v) : null, { shouldValidate: true })
              }
            >
              <SelectTrigger className="h-9">
                <SelectValue placeholder="选择开发负责人" />
              </SelectTrigger>
              <SelectContent>
                {users.map((u) => (
                  <SelectItem key={u.id} value={String(u.id)}>
                    {u.full_name || u.username}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>关联用例 ID</Label>
              <Input
                type="number"
                placeholder="可选"
                {...form.register("related_case_id", { valueAsNumber: true })}
              />
            </div>
            <div>
              <Label>预估工时 (h)</Label>
              <Input
                type="number"
                step="0.5"
                placeholder="可选"
                {...form.register("estimated_hours", { valueAsNumber: true })}
              />
            </div>
          </div>

          <div>
            <Label htmlFor="bug-desc">描述</Label>
            <RichTextEditor
              value={form.watch("description") ?? ""}
              onChange={(html) => form.setValue("description", html, { shouldValidate: true })}
              height={160}
              toolbar="minimal"
              placeholder="可选：bug 现象 / 影响范围"
            />
          </div>

          <div>
            <Label htmlFor="bug-repro">复现步骤</Label>
            <RichTextEditor
              value={form.watch("reproduce_steps") ?? ""}
              onChange={(html) => form.setValue("reproduce_steps", html, { shouldValidate: true })}
              height={160}
              toolbar="minimal"
              placeholder="可选：1. 打开 X；2. 点击 Y"
            />
          </div>

          <div className="rounded border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            来源：{sourceLabel}
            {!isEdit && userId == null ? " · 当前未选用户" : ""}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={submitMutation.isPending}>
              {submitMutation.isPending ? "保存中…" : isEdit ? "保存" : "提交"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
