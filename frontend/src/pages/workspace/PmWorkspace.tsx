/**
 * 产品工作台。
 *
 * 三个 widget 都依赖一个 PM 当前关注的 project（顶部选择器），
 * 因为 /api/requirements 必须带 project_id；版本选择只服务于"本迭代里程碑" widget。
 *
 * - 需求池 → requirementsApi.list(pid, { assignee_pm_id: me, business_status: 'approved' })
 * - 待验收 → requirementsApi.list(pid, { assignee_pm_id: me, system_status: 'ready_to_release' })
 *           每行 inline Accept 按钮 → requirementsApi.accept(id, { pm_id: me })
 * - 本迭代里程碑 → versionsApi.board(vid)，显示 4 列 system_status 计数 + 链到 board 详情页
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  projectsApi,
  requirementsApi,
  versionsApi,
} from "@/lib/api";
import { WidgetCard } from "@/pages/workspace/_shared";
import type {
  Requirement,
  RequirementSystemStatus,
} from "@/types/domain";
import { ALL_REQUIREMENT_SYSTEM_STATUSES } from "@/types/domain";

const SYSTEM_STATUS_LABELS: Record<RequirementSystemStatus, string> = {
  approved: "已立项",
  developing: "开发中",
  testing: "测试中",
  ready_to_release: "待发版",
};

export function PmWorkspace({ userId }: { userId: number }) {
  const projectsQuery = useQuery({
    queryKey: ["projects", "all"],
    queryFn: () => projectsApi.list(),
  });

  const [projectId, setProjectId] = useState<number | undefined>(undefined);
  const [versionId, setVersionId] = useState<number | undefined>(undefined);

  // 项目列表加载完后，自动选第一个，避免空态
  const projects = projectsQuery.data ?? [];
  const effectiveProjectId =
    projectId ?? (projects.length > 0 ? projects[0].id : undefined);

  return (
    <>
      <div className="col-span-full flex flex-wrap items-center gap-3 rounded-md border bg-background px-4 py-3 text-sm">
        <span className="text-muted-foreground">项目：</span>
        <Select
          value={effectiveProjectId ? String(effectiveProjectId) : ""}
          onValueChange={(v) => {
            setProjectId(Number(v));
            setVersionId(undefined);
          }}
          disabled={projectsQuery.isLoading}
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
        {effectiveProjectId ? (
          <VersionPickerInline
            projectId={effectiveProjectId}
            value={versionId}
            onChange={setVersionId}
          />
        ) : null}
      </div>

      {effectiveProjectId ? (
        <>
          <RequirementPoolWidget
            projectId={effectiveProjectId}
            userId={userId}
          />
          <PendingAcceptWidget
            projectId={effectiveProjectId}
            userId={userId}
          />
          <VersionMilestoneWidget
            projectId={effectiveProjectId}
            versionId={versionId}
          />
        </>
      ) : (
        <div className="col-span-full rounded-md border bg-background p-6 text-sm text-muted-foreground">
          {projectsQuery.isLoading ? "加载项目列表…" : "还没有项目，先去新建一个。"}
        </div>
      )}
    </>
  );
}

function VersionPickerInline({
  projectId,
  value,
  onChange,
}: {
  projectId: number;
  value: number | undefined;
  onChange: (v: number | undefined) => void;
}) {
  const versionsQuery = useQuery({
    queryKey: ["versions", projectId],
    queryFn: () => versionsApi.list(projectId),
  });
  const versions = versionsQuery.data ?? [];

  return (
    <>
      <span className="text-muted-foreground">版本：</span>
      <Select
        value={value ? String(value) : ""}
        onValueChange={(v) => onChange(v ? Number(v) : undefined)}
        disabled={versionsQuery.isLoading || versions.length === 0}
      >
        <SelectTrigger className="h-8 w-[200px]">
          <SelectValue
            placeholder={
              versionsQuery.isLoading
                ? "加载…"
                : versions.length === 0
                  ? "项目暂无版本"
                  : "选择版本"
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
    </>
  );
}

function RequirementPoolWidget({
  projectId,
  userId,
}: {
  projectId: number;
  userId: number;
}) {
  const query = useQuery({
    queryKey: ["requirements", projectId, "pool", userId],
    queryFn: () =>
      requirementsApi.list(projectId, {
        assignee_pm_id: userId,
        business_status: "approved",
      }),
  });

  return (
    <WidgetCard<Requirement>
      title="需求池"
      items={query.data}
      isLoading={query.isLoading}
      isError={query.isError}
      errorMessage={(query.error as Error | undefined)?.message}
      renderItem={(req) => <RequirementRow req={req} />}
      emptyText="没有立项中的需求。"
      subtitle="business_status = approved"
      viewAllHref={`/projects/${projectId}/requirements`}
    />
  );
}

function PendingAcceptWidget({
  projectId,
  userId,
}: {
  projectId: number;
  userId: number;
}) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["requirements", projectId, "ready_to_release", userId],
    queryFn: () =>
      requirementsApi.list(projectId, {
        assignee_pm_id: userId,
        system_status: "ready_to_release",
      }),
  });

  const acceptMutation = useMutation({
    mutationFn: (id: number) =>
      requirementsApi.accept(id, { pm_id: userId }),
    onSuccess: () => {
      toast.success("已验收");
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <WidgetCard<Requirement>
      title="待验收"
      items={query.data}
      isLoading={query.isLoading}
      isError={query.isError}
      errorMessage={(query.error as Error | undefined)?.message}
      renderItem={(req) => (
        <div className="flex items-center justify-between gap-2 px-4 py-2 text-sm">
          <Link
            to={`/projects/${req.project_id}/requirements`}
            className="min-w-0 flex-1 truncate font-medium hover:underline"
          >
            {req.title}
          </Link>
          <Button
            size="sm"
            variant="outline"
            disabled={acceptMutation.isPending}
            onClick={(e) => {
              e.stopPropagation();
              acceptMutation.mutate(req.id);
            }}
          >
            验收
          </Button>
        </div>
      )}
      emptyText="没有等待验收的需求。"
      subtitle="system_status = ready_to_release"
    />
  );
}

function VersionMilestoneWidget({
  projectId,
  versionId,
}: {
  projectId: number;
  versionId: number | undefined;
}) {
  const query = useQuery({
    queryKey: ["version-board", versionId],
    queryFn: () => versionsApi.board(versionId as number),
    enabled: versionId !== undefined,
  });

  const counts = useMemo(() => {
    if (!query.data) return null;
    return ALL_REQUIREMENT_SYSTEM_STATUSES.map((status) => ({
      status,
      count: (query.data!.requirements_by_status[status] ?? []).length,
    }));
  }, [query.data]);

  return (
    <WidgetCard
      title="本迭代里程碑"
      items={counts ?? undefined}
      isLoading={versionId !== undefined && query.isLoading}
      isError={query.isError}
      errorMessage={(query.error as Error | undefined)?.message}
      subtitle={
        versionId !== undefined ? (
          <Link
            to={`/projects/${projectId}/versions/${versionId}/board?tab=report`}
            className="text-primary hover:underline"
          >
            📊 测试报告
          </Link>
        ) : null
      }
      renderItem={(row) => (
        <div className="flex items-center justify-between gap-2 px-4 py-2 text-sm">
          <span>{SYSTEM_STATUS_LABELS[row.status]}</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            {row.count}
          </span>
        </div>
      )}
      emptyText={versionId === undefined ? "请选择一个版本。" : "看板为空。"}
      viewAllHref={
        versionId !== undefined
          ? `/projects/${projectId}/versions/${versionId}/board`
          : undefined
      }
    />
  );
}

function RequirementRow({ req }: { req: Requirement }) {
  return (
    <Link
      to={`/projects/${req.project_id}/requirements`}
      className="block px-4 py-2 text-sm hover:bg-accent/40"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate font-medium">{req.title}</span>
        {req.system_status ? (
          <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            {req.system_status}
          </span>
        ) : null}
      </div>
      {req.description ? (
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {req.description}
        </div>
      ) : null}
    </Link>
  );
}
