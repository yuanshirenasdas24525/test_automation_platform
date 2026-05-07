/**
 * 运维工作台。
 *
 * - 环境探活 → devicesApi.list()，按 status 分组展示
 * - 本周发版 → 选 project，filter versions where status in ['testing', 'ready_to_release']
 *   注：后端 versions.status 没有"本周"概念，这里就当"待发版"展示
 * - 上线公告 → 列出所有 status='released' 版本的 release_notes
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { devicesApi, projectsApi, versionsApi } from "@/lib/api";
import type { Device } from "@/lib/api";
import { WidgetCard } from "@/pages/workspace/_shared";
import { cn } from "@/lib/utils";
import type { ProjectVersion } from "@/types/domain";

export function OpsWorkspace() {
  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: () => devicesApi.list(),
  });

  const projectsQuery = useQuery({
    queryKey: ["projects", "all"],
    queryFn: () => projectsApi.list(),
  });
  const projects = projectsQuery.data ?? [];

  const [projectId, setProjectId] = useState<number | undefined>(undefined);
  const effectiveProjectId =
    projectId ?? (projects.length > 0 ? projects[0].id : undefined);

  const versionsQuery = useQuery({
    queryKey: ["versions", effectiveProjectId],
    queryFn: () => versionsApi.list(effectiveProjectId as number),
    enabled: effectiveProjectId !== undefined,
  });
  const versions = versionsQuery.data ?? [];

  const upcomingReleases = useMemo(
    () =>
      versions.filter(
        (v) => v.status === "testing" || v.status === "ready_to_release",
      ),
    [versions],
  );
  const released = useMemo(
    () => versions.filter((v) => v.status === "released"),
    [versions],
  );

  return (
    <>
      <WidgetCard<Device>
        title="环境探活"
        items={devicesQuery.data}
        isLoading={devicesQuery.isLoading}
        isError={devicesQuery.isError}
        errorMessage={(devicesQuery.error as Error | undefined)?.message}
        renderItem={(d) => <DeviceRow d={d} />}
        emptyText="设备池为空。"
        viewAllHref="/devices"
        previewLimit={6}
      />

      <div className="col-span-full flex flex-wrap items-center gap-3 rounded-md border bg-background px-4 py-3 text-sm">
        <span className="text-muted-foreground">项目：</span>
        <Select
          value={effectiveProjectId ? String(effectiveProjectId) : ""}
          onValueChange={(v) => setProjectId(Number(v))}
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
      </div>

      <WidgetCard<ProjectVersion>
        title="本周发版"
        items={
          effectiveProjectId === undefined ? undefined : upcomingReleases
        }
        isLoading={
          effectiveProjectId !== undefined && versionsQuery.isLoading
        }
        isError={versionsQuery.isError}
        errorMessage={(versionsQuery.error as Error | undefined)?.message}
        renderItem={(v) => <ReleaseRow v={v} />}
        emptyText={
          effectiveProjectId === undefined
            ? "请先选择项目。"
            : "项目暂无待发版的版本。"
        }
        subtitle="status ∈ {testing, ready_to_release}"
      />

      <WidgetCard<ProjectVersion>
        title="上线公告"
        items={effectiveProjectId === undefined ? undefined : released}
        isLoading={
          effectiveProjectId !== undefined && versionsQuery.isLoading
        }
        isError={versionsQuery.isError}
        errorMessage={(versionsQuery.error as Error | undefined)?.message}
        renderItem={(v) => <ReleaseNoteRow v={v} />}
        emptyText={
          effectiveProjectId === undefined
            ? "请先选择项目。"
            : "暂无已发版的版本。"
        }
        previewLimit={5}
      />
    </>
  );
}

function DeviceRow({ d }: { d: Device }) {
  return (
    <div className="flex items-center justify-between gap-2 px-4 py-2 text-sm">
      <div className="min-w-0">
        <div className="truncate font-medium">
          {d.device_name || d.brand || d.udid}
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {d.platform} · {d.pool}
        </div>
      </div>
      <span
        className={cn(
          "shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
          d.status === "idle"
            ? "bg-green-100 text-green-800"
            : d.status === "busy"
              ? "bg-blue-100 text-blue-800"
              : "bg-red-100 text-red-800",
        )}
      >
        {d.status}
      </span>
    </div>
  );
}

function ReleaseRow({ v }: { v: ProjectVersion }) {
  return (
    <Link
      to={`/projects/${v.project_id}/versions/${v.id}`}
      className="flex items-center justify-between gap-2 px-4 py-2 text-sm hover:bg-accent/40"
    >
      <span className="min-w-0 truncate font-medium">
        {v.display_name || v.version_name}
      </span>
      <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {v.status}
      </span>
    </Link>
  );
}

function ReleaseNoteRow({ v }: { v: ProjectVersion }) {
  return (
    <div className="px-4 py-2 text-sm">
      <div className="truncate font-medium">
        {v.display_name || v.version_name}
      </div>
      {v.release_notes ? (
        <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
          {v.release_notes}
        </div>
      ) : (
        <div className="mt-0.5 text-xs text-muted-foreground">无公告内容。</div>
      )}
    </div>
  );
}
