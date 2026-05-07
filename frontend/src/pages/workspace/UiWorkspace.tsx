/**
 * 设计工作台。
 *
 * - 走查任务 → tasksApi.list({ type: 'ui_review', assignee_dev_id: userId })
 *   注：当前 Task 模型 ui_review 类型也复用 assignee_dev_id 字段（设计稿审核人也算"做事的人"）
 * - 设计稿资产 → 选 project + version，列出该 version 的 design_doc_items
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { projectsApi, tasksApi, versionsApi } from "@/lib/api";
import { TaskRow, WidgetCard } from "@/pages/workspace/_shared";
import type { DocItem, Task } from "@/types/domain";

export function UiWorkspace({ userId }: { userId: number }) {
  const reviewTasksQuery = useQuery({
    queryKey: ["tasks", "ui-review", userId],
    queryFn: () =>
      tasksApi.list({ type: "ui_review", assignee_dev_id: userId }),
  });

  const projectsQuery = useQuery({
    queryKey: ["projects", "all"],
    queryFn: () => projectsApi.list(),
  });
  const projects = projectsQuery.data ?? [];

  const [projectId, setProjectId] = useState<number | undefined>(undefined);
  const [versionId, setVersionId] = useState<number | undefined>(undefined);
  const effectiveProjectId =
    projectId ?? (projects.length > 0 ? projects[0].id : undefined);

  const versionsQuery = useQuery({
    queryKey: ["versions", effectiveProjectId],
    queryFn: () => versionsApi.list(effectiveProjectId as number),
    enabled: effectiveProjectId !== undefined,
  });
  const versions = versionsQuery.data ?? [];
  const effectiveVersionId =
    versionId ?? (versions.length > 0 ? versions[0].id : undefined);

  const versionDetailQuery = useQuery({
    queryKey: ["version", effectiveProjectId, effectiveVersionId],
    queryFn: () =>
      versionsApi.get(
        effectiveProjectId as number,
        effectiveVersionId as number,
      ),
    enabled:
      effectiveProjectId !== undefined && effectiveVersionId !== undefined,
  });

  return (
    <>
      <WidgetCard<Task>
        title="走查任务"
        items={reviewTasksQuery.data}
        isLoading={reviewTasksQuery.isLoading}
        isError={reviewTasksQuery.isError}
        errorMessage={(reviewTasksQuery.error as Error | undefined)?.message}
        renderItem={(task) => <TaskRow task={task} />}
        emptyText="暂无走查任务。"
        viewAllHref={`/tasks?type=ui_review&assignee_dev_id=${userId}`}
      />

      <div className="col-span-full flex flex-wrap items-center gap-3 rounded-md border bg-background px-4 py-3 text-sm">
        <span className="text-muted-foreground">项目：</span>
        <Select
          value={effectiveProjectId ? String(effectiveProjectId) : ""}
          onValueChange={(v) => {
            setProjectId(Number(v));
            setVersionId(undefined);
          }}
          disabled={projectsQuery.isLoading || projects.length === 0}
        >
          <SelectTrigger className="h-8 w-[200px]">
            <SelectValue placeholder="选择项目" />
          </SelectTrigger>
          <SelectContent>
            {projects.map((p) => (
              <SelectItem key={p.id} value={String(p.id)}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-muted-foreground">版本：</span>
        <Select
          value={effectiveVersionId ? String(effectiveVersionId) : ""}
          onValueChange={(v) => setVersionId(Number(v))}
          disabled={versionsQuery.isLoading || versions.length === 0}
        >
          <SelectTrigger className="h-8 w-[200px]">
            <SelectValue
              placeholder={
                versions.length === 0 ? "项目暂无版本" : "选择版本"
              }
            />
          </SelectTrigger>
          <SelectContent>
            {versions.map((v) => (
              <SelectItem key={v.id} value={String(v.id)}>
                {v.display_name || v.version_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <WidgetCard<DocItem>
        title="设计稿资产"
        items={
          versionDetailQuery.data
            ? versionDetailQuery.data.design_doc_items
            : undefined
        }
        isLoading={
          effectiveVersionId !== undefined && versionDetailQuery.isLoading
        }
        isError={versionDetailQuery.isError}
        errorMessage={(versionDetailQuery.error as Error | undefined)?.message}
        renderItem={(item) => <DesignDocRow item={item} />}
        emptyText={
          effectiveVersionId === undefined
            ? "请先选择项目 + 版本。"
            : "当前版本没有设计稿。"
        }
        previewLimit={8}
      />
    </>
  );
}

function DesignDocRow({ item }: { item: DocItem }) {
  const linkable = item.type === "link" && item.url;
  const inner = (
    <>
      <span className="min-w-0 truncate font-medium">{item.name}</span>
      <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {item.type}
      </span>
    </>
  );

  if (linkable) {
    return (
      <a
        href={item.url ?? undefined}
        target="_blank"
        rel="noreferrer"
        className="flex items-center justify-between gap-2 px-4 py-2 text-sm hover:bg-accent/40"
      >
        {inner}
      </a>
    );
  }

  return (
    <div className="flex items-center justify-between gap-2 px-4 py-2 text-sm">
      {inner}
    </div>
  );
}
