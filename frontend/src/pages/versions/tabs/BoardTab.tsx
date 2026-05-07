/**
 * 看板 tab：4 列 system_status 需求分桶 + 任务计数侧栏。
 * 由 VersionBoardPage 在 ?tab=board（默认）时渲染。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { versionSummariesApi, versionsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  ALL_BUG_SEVERITIES,
  ALL_REQUIREMENT_SYSTEM_STATUSES,
  ALL_TASK_TYPES,
} from "@/types/domain";
import type {
  Requirement,
  RequirementSystemStatus,
  TaskType,
  VersionTaskBucket,
} from "@/types/domain";

const STATUS_LABELS: Record<RequirementSystemStatus, string> = {
  approved: "已立项",
  developing: "开发中",
  testing: "测试中",
  ready_to_release: "待发版",
};

const STATUS_TONES: Record<RequirementSystemStatus, string> = {
  approved: "border-blue-200 bg-blue-50/40",
  developing: "border-amber-200 bg-amber-50/40",
  testing: "border-violet-200 bg-violet-50/40",
  ready_to_release: "border-emerald-200 bg-emerald-50/40",
};

const TASK_TYPE_LABELS: Record<TaskType, string> = {
  dev: "开发任务",
  test: "测试任务",
  ui_review: "走查任务",
  bug: "Bug",
};

export function BoardTab({ projectId, versionId }: { projectId: number; versionId: number }) {
  const queryClient = useQueryClient();

  const boardQuery = useQuery({
    queryKey: ["version-board", versionId],
    queryFn: () => versionsApi.board(versionId),
    enabled: !Number.isNaN(versionId),
  });

  const regenerateMutation = useMutation({
    mutationFn: () => versionSummariesApi.regenerate(versionId),
    onSuccess: () => {
      toast.success("已重算汇总");
      queryClient.invalidateQueries({ queryKey: ["version-board", versionId] });
      queryClient.invalidateQueries({ queryKey: ["version-summary", versionId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  if (boardQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (boardQuery.isError || !boardQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载失败：
        {(boardQuery.error as Error | undefined)?.message ?? "version 不存在"}
      </div>
    );
  }

  const board = boardQuery.data;

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-end" data-print-hide>
        <Button
          size="sm"
          variant="outline"
          disabled={regenerateMutation.isPending}
          onClick={() => regenerateMutation.mutate()}
        >
          <RefreshCw
            className={cn(
              "mr-1 h-3.5 w-3.5",
              regenerateMutation.isPending && "animate-spin",
            )}
          />
          重算汇总
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {ALL_REQUIREMENT_SYSTEM_STATUSES.map((status) => {
            const items = board.requirements_by_status[status] ?? [];
            return (
              <RequirementColumn
                key={status}
                status={status}
                items={items}
                projectId={projectId}
              />
            );
          })}
        </div>

        <div className="space-y-3">
          {ALL_TASK_TYPES.map((type) => {
            const bucket: VersionTaskBucket = board.task_counts_by_type[type] ?? {
              total: 0,
              by_status: {},
            };
            return <TaskTypeCard key={type} type={type} bucket={bucket} />;
          })}
          {board.requirements_by_status.unassigned &&
          board.requirements_by_status.unassigned.length > 0 ? (
            <Card className="border-dashed">
              <CardContent className="p-3 text-xs text-muted-foreground">
                还有 {board.requirements_by_status.unassigned.length} 条需求没设
                system_status，已挂在 unassigned 桶里。
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function RequirementColumn({
  status,
  items,
  projectId,
}: {
  status: RequirementSystemStatus;
  items: Requirement[];
  projectId: number;
}) {
  return (
    <div
      className={cn(
        "flex min-h-[140px] flex-col rounded-md border p-3",
        STATUS_TONES[status],
      )}
    >
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="font-semibold">{STATUS_LABELS[status]}</span>
        <span className="rounded bg-background/70 px-1.5 py-0.5 text-xs text-muted-foreground">
          {items.length}
        </span>
      </div>
      <div className="space-y-2">
        {items.length === 0 ? (
          <div className="text-xs text-muted-foreground">空</div>
        ) : (
          items.map((req) => <RequirementCard key={req.id} req={req} projectId={projectId} />)
        )}
      </div>
    </div>
  );
}

function RequirementCard({
  req,
  projectId,
}: {
  req: Requirement;
  projectId: number;
}) {
  return (
    <Link
      to={`/projects/${projectId}/requirements`}
      className="block rounded border bg-background p-2 text-xs shadow-sm hover:border-primary/40"
    >
      <div className="line-clamp-2 font-medium">{req.title}</div>
      {req.description ? (
        <div className="mt-1 line-clamp-2 text-muted-foreground">
          {req.description}
        </div>
      ) : null}
      {req.business_status ? (
        <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
          biz: {req.business_status}
        </div>
      ) : null}
    </Link>
  );
}

function TaskTypeCard({
  type,
  bucket,
}: {
  type: TaskType;
  bucket: VersionTaskBucket;
}) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="flex items-center justify-between text-sm">
          <span className="font-semibold">{TASK_TYPE_LABELS[type]}</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            {bucket.total}
          </span>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1 text-[11px]">
          {Object.entries(bucket.by_status).map(([status, count]) => (
            <div key={status} className="flex items-center justify-between">
              <span className="text-muted-foreground">{status}</span>
              <span>{count}</span>
            </div>
          ))}
        </div>
        {type === "bug" && bucket.by_severity ? (
          <div className="mt-2 border-t pt-2 text-[11px]">
            <div className="mb-1 text-muted-foreground">严重度</div>
            <div className="grid grid-cols-4 gap-1 text-center">
              {ALL_BUG_SEVERITIES.map((sev) => (
                <div
                  key={sev}
                  className="rounded bg-muted px-1 py-0.5"
                  title={`${sev}: ${bucket.by_severity?.[sev] ?? 0}`}
                >
                  <div className="text-[10px] text-muted-foreground">{sev}</div>
                  <div className="font-semibold">
                    {bucket.by_severity?.[sev] ?? 0}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
