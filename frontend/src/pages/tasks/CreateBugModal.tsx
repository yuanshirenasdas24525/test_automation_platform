/**
 * 建 Bug 弹窗。支持两种关联方式：
 *   1. 版本模式（推荐）：选择版本迭代，后端自动取该版本下第一个需求作为 requirement_id
 *   2. parent 任务模式（兼容）：选择 dev 任务作为 parent
 *
 * 调用方可通过 versionId / parentTaskId prop 预选，此时不展示选择器。
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
import { tasksApi, versionsApi } from "@/lib/api";
import { useUserId } from "@/lib/current-user";
import { ALL_BUG_SEVERITIES } from "@/types/domain";
import type { BugSeverity, VersionPickerItem } from "@/types/domain";

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
  /** 预选版本 id。传入时隐藏版本选择器。 */
  versionId?: number | null;
  /** 预选父 dev 任务（兼容旧逻辑）。传 null 时显示选择器。 */
  parentTaskId?: number | null;
  relatedCaseId?: number | null;
  defaultTitle?: string;
  createdById?: number | null;
}

export function CreateBugModal({
  open,
  onOpenChange,
  versionId,
  parentTaskId,
  relatedCaseId,
  defaultTitle,
  createdById,
}: CreateBugModalProps) {
  const fallbackUserId = useUserId();
  const userId = createdById ?? fallbackUserId;
  const queryClient = useQueryClient();

  // ── 版本选择器 ──
  const [pickedVersionId, setPickedVersionId] = useState<number | null>(null);
  const effectiveVersionId = versionId ?? pickedVersionId;

  const versionsQuery = useQuery({
    queryKey: ["versions", "picker"],
    queryFn: () => versionsApi.picker(),
    enabled: open && versionId == null && parentTaskId == null,
  });
  const versions: VersionPickerItem[] = versionsQuery.data ?? [];

  const effectiveParentId = parentTaskId ?? null;

  useEffect(() => {
    if (open) {
      setPickedVersionId(versionId ?? null);
    }
  }, [open, versionId]);

  // ── 表单 ──
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      title: defaultTitle ?? "",
      severity: "P2",
      description: "",
      reproduce_steps: "",
    },
  });

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

  // ── 提交 ──
  const submit = useMutation({
    mutationFn: (values: FormValues) => {
      if (userId == null) {
        return Promise.reject(new Error("当前没有用户，先在右上角选一个"));
      }

      const useVersion = effectiveVersionId != null;
      const useParent = !useVersion && effectiveParentId != null;

      if (!useVersion && !useParent) {
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

  // ── 决定是否显示选择器 ──
  // 默认模式：显示版本选择器。若已预选版本或 parent 任务则不显示。
  const showVersionPicker = versionId == null && parentTaskId == null;
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
          <DialogTitle>建 Bug</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit((v) => submit.mutate(v))}
          className="space-y-3"
        >
          {/* ── 版本选择器（默认模式） ── */}
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
                bug 会关联到该版本迭代，自动归属该版本下的需求。
              </div>
            </div>
          ) : null}

          {/* ── 预选提示 ── */}
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
              placeholder="可选：1. 打开 X；2. 点击 Y；3. ..."
            />
            <div className="mt-1 text-[11px] text-muted-foreground">
              会落到 metadata.reproduce_steps，方便后续按字段查询。
            </div>
          </div>
          <div className="rounded border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            来源：{sourceLabel}
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
