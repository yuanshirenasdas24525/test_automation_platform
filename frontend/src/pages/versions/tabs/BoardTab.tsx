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
import { usersApi, versionSummariesApi, versionsApi } from "@/lib/api";
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
  User,
  VersionTaskBucket,
} from "@/types/domain";

const STATUS_LABELS: Record<RequirementSystemStatus, string> = {
  approved: "已立项",
  developing: "开发中",
  pm_review: "产品体验",
  testing: "测试中",
  ready_to_release: "待发版",
  done: "已完成",
};

const STATUS_TONES: Record<RequirementSystemStatus, string> = {
  approved: "border-blue-200 bg-blue-50/40",
  developing: "border-amber-200 bg-amber-50/40",
  pm_review: "border-purple-200 bg-purple-50/40",
  testing: "border-violet-200 bg-violet-50/40",
  ready_to_release: "border-emerald-200 bg-emerald-50/40",
  done: "border-teal-200 bg-teal-50/40",
};

const TASK_TYPE_LABELS: Record<TaskType, string> = {
  dev: "开发任务",
  test: "测试任务",
  ui_review: "走查任务",
  pm_review: "产品体验",
  bug: "Bug",
};

export function BoardTab({ projectId, versionId }: { projectId: number; versionId: number }) {
  const queryClient = useQueryClient();

  const boardQuery = useQuery({
    queryKey: ["version-board", versionId],
    queryFn: () => versionsApi.board(versionId),
    enabled: !Number.isNaN(versionId),
  });

  // 用户字典：BoardTab 卡片显示 dev/test/pm/ui 4 角色头像时按 id 取名字
  const usersQuery = useQuery({
    queryKey: ["users", "active"],
    queryFn: () => usersApi.list({ is_active: true }),
    staleTime: 60_000,
  });
  const userMap = (usersQuery.data ?? []).reduce<Record<number, User>>(
    (acc, u) => {
      acc[u.id] = u;
      return acc;
    },
    {},
  );

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
                userMap={userMap}
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
  userMap,
}: {
  status: RequirementSystemStatus;
  items: Requirement[];
  projectId: number;
  userMap: Record<number, User>;
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
          items.map((req) => (
            <RequirementCard
              key={req.id}
              req={req}
              projectId={projectId}
              userMap={userMap}
            />
          ))
        )}
      </div>
    </div>
  );
}

const ROLE_LABEL: Record<"dev" | "test" | "pm" | "ui", string> = {
  dev: "开发",
  test: "测试",
  pm: "产品",
  ui: "UI",
};
const ROLE_COLOR: Record<"dev" | "test" | "pm" | "ui", string> = {
  dev: "bg-amber-100 text-amber-800 ring-amber-200",
  test: "bg-violet-100 text-violet-800 ring-violet-200",
  pm: "bg-blue-100 text-blue-800 ring-blue-200",
  ui: "bg-pink-100 text-pink-800 ring-pink-200",
};

function AssigneeAvatar({
  role,
  userIds,
  userMap,
}: {
  role: "dev" | "test" | "pm" | "ui";
  userIds: number[];
  userMap: Record<number, User>;
}) {
  if (userIds.length === 0) {
    return (
      <div
        className="inline-flex h-5 items-center gap-0.5 rounded-full bg-slate-100 px-1.5 text-[10px] text-slate-400 ring-1 ring-inset ring-slate-200"
        title={`${ROLE_LABEL[role]}：未指派`}
      >
        <span className="font-semibold">{ROLE_LABEL[role]}</span>
        <span>·</span>
        <span>未指派</span>
      </div>
    );
  }
  const names = userIds.map((uid) => {
    const u = userMap[uid];
    return u?.full_name || u?.username || `#${uid}`;
  });
  const primary = names[0];
  const extra = names.length - 1;
  return (
    <div
      className={cn(
        "inline-flex h-5 items-center gap-0.5 rounded-full px-1.5 text-[10px] ring-1 ring-inset",
        ROLE_COLOR[role],
      )}
      title={`${ROLE_LABEL[role]}：${names.join(", ")}`}
    >
      <span className="font-semibold">{ROLE_LABEL[role]}</span>
      <span>·</span>
      <span className="max-w-[60px] truncate">{primary}</span>
      {extra > 0 ? <span>+{extra}</span> : null}
    </div>
  );
}

function RequirementCard({
  req,
  projectId,
  userMap,
}: {
  req: Requirement;
  projectId: number;
  userMap: Record<number, User>;
}) {
  const assignees = req.assignees ?? { dev: [], test: [], pm: [], ui: [] };
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
      <div className="mt-2 flex flex-wrap gap-1 border-t pt-1.5">
        {(["dev", "test", "pm", "ui"] as const).map((role) => (
          <AssigneeAvatar
            key={role}
            role={role}
            userIds={assignees[role] ?? []}
            userMap={userMap}
          />
        ))}
      </div>
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
