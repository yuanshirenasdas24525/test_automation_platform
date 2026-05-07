/**
 * /projects/:pid/versions/:vid/board —— 版本视图中心（4 tab）。
 *
 * Tabs：
 *   - board    （默认） 4 列需求看板 + 任务计数
 *   - report   测试报告 + 一键导出 PDF
 *   - cases    本版本绑定用例的扁平列表（用例库回流）
 *   - archive  归档页（仅 status ∈ {released, archived} 时显示）
 *
 * Tab 状态用 useSearchParams('tab')；URL 即真理。
 */
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { projectsApi, versionsApi } from "@/lib/api";

import { ArchiveTab } from "./tabs/ArchiveTab";
import { BoardTab } from "./tabs/BoardTab";
import { CasesTab } from "./tabs/CasesTab";
import { ReportTab } from "./tabs/ReportTab";

import "./_print.css";

const ARCHIVED_STATUSES = new Set(["released", "archived"]);

export function VersionBoardPage() {
  const params = useParams<{ id: string; vid: string }>();
  const projectId = Number(params.id);
  const versionId = Number(params.vid);
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") || "board";

  const versionQuery = useQuery({
    queryKey: ["version", projectId, versionId],
    queryFn: () => versionsApi.get(projectId, versionId),
    enabled: !Number.isNaN(versionId) && !Number.isNaN(projectId),
  });

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: !Number.isNaN(projectId),
  });

  if (Number.isNaN(versionId) || Number.isNaN(projectId)) {
    return <div className="p-6 text-sm text-destructive">非法 url 参数。</div>;
  }
  if (versionQuery.isLoading || projectQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (versionQuery.isError || !versionQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载失败：
        {(versionQuery.error as Error | undefined)?.message ?? "版本不存在"}
      </div>
    );
  }

  const version = versionQuery.data;
  const project = projectQuery.data;
  const showArchive = ARCHIVED_STATUSES.has(version.status);

  const handleTabChange = (next: string) => {
    const nextSearch = new URLSearchParams(search);
    nextSearch.set("tab", next);
    setSearch(nextSearch, { replace: true });
  };

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between gap-2" data-print-hide>
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
          <ArrowLeft className="mr-1 h-3.5 w-3.5" /> 返回
        </Button>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 p-4 text-sm">
          <div>
            <div className="text-xs text-muted-foreground">项目</div>
            <div className="font-semibold">{project?.name ?? `#${projectId}`}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">版本</div>
            <div className="font-semibold">
              {version.display_name || version.version_name}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">状态</div>
            <div className="font-medium">{version.status}</div>
          </div>
          {version.planned_start_at ? (
            <div>
              <div className="text-xs text-muted-foreground">计划开始</div>
              <div>{formatDate(version.planned_start_at)}</div>
            </div>
          ) : null}
          {version.planned_end_at ? (
            <div>
              <div className="text-xs text-muted-foreground">计划结束</div>
              <div>{formatDate(version.planned_end_at)}</div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Tabs value={tab} onValueChange={handleTabChange}>
        <TabsList data-version-tabs-header>
          <TabsTrigger value="board">看板</TabsTrigger>
          <TabsTrigger value="report">测试报告</TabsTrigger>
          <TabsTrigger value="cases">用例库</TabsTrigger>
          {showArchive ? <TabsTrigger value="archive">归档</TabsTrigger> : null}
        </TabsList>

        <TabsContent value="board" data-version-tab-content>
          <BoardTab projectId={projectId} versionId={versionId} />
        </TabsContent>
        <TabsContent value="report" data-version-tab-content>
          <ReportTab
            projectId={projectId}
            versionId={versionId}
            projectName={project?.name ?? `项目${projectId}`}
            versionName={version.display_name || version.version_name}
          />
        </TabsContent>
        <TabsContent value="cases" data-version-tab-content>
          <CasesTab projectId={projectId} versionId={versionId} />
        </TabsContent>
        {showArchive ? (
          <TabsContent value="archive" data-version-tab-content>
            <ArchiveTab projectId={projectId} versionId={versionId} />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  );
}

function formatDate(s: string | null | undefined) {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleDateString();
  } catch {
    return s;
  }
}
