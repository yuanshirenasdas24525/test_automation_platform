/**
 * 归档 tab：仅 version.status ∈ {released, archived} 时由 shell 渲染。
 * 三块卡片：
 *  - Summary 摘要（首次通过率 / bug 总数 / 覆盖率 / 平均修复时长 + generated_at）
 *  - Release Notes 4 段（sql / config / commands / notes）
 *  - Docs 链接：4 类文档（test_plan / requirement_doc / design_doc / ui_prototype）
 */
import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { versionSummariesApi, versionsApi } from "@/lib/api";
import type { ProjectVersion, VersionReleaseNotes } from "@/types/domain";

const DOC_GROUPS: Array<{
  key: keyof Pick<
    ProjectVersion,
    "test_plan_items" | "requirement_doc_items" | "design_doc_items" | "ui_prototype_items"
  >;
  label: string;
}> = [
  { key: "test_plan_items", label: "测试计划" },
  { key: "requirement_doc_items", label: "需求文档" },
  { key: "design_doc_items", label: "设计稿" },
  { key: "ui_prototype_items", label: "UI 原型" },
];

export function ArchiveTab({
  projectId,
  versionId,
}: {
  projectId: number;
  versionId: number;
}) {
  const versionQuery = useQuery({
    queryKey: ["version", projectId, versionId],
    queryFn: () => versionsApi.get(projectId, versionId),
  });
  const summaryQuery = useQuery({
    queryKey: ["version-summary", versionId],
    queryFn: () => versionSummariesApi.get(versionId),
  });

  if (versionQuery.isLoading) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }
  if (versionQuery.isError || !versionQuery.data) {
    return (
      <div className="p-6 text-sm text-destructive">
        加载失败：{(versionQuery.error as Error | undefined)?.message ?? "未知"}
      </div>
    );
  }

  const version = versionQuery.data;
  const summary = summaryQuery.data;
  const notes = parseReleaseNotes(version.release_notes);

  return (
    <div className="space-y-4 p-6">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">汇总摘要</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
          <SummaryStat
            label="首次通过率"
            value={formatPct(summary?.first_pass_rate)}
          />
          <SummaryStat label="Bug 总数" value={summary?.total_bugs ?? "—"} />
          <SummaryStat label="覆盖率" value={formatPct(summary?.test_coverage)} />
          <SummaryStat
            label="平均修复时长"
            value={formatHours(summary?.avg_fix_time_hours)}
          />
          {summary?.generated_at ? (
            <div className="col-span-full text-xs text-muted-foreground">
              汇总于 {formatDate(summary.generated_at)}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">发布说明</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {(["sql", "config", "commands", "notes"] as const).map((k) => (
            <div key={k}>
              <div className="mb-1 text-xs font-semibold text-muted-foreground">
                {NOTE_LABELS[k]}
              </div>
              <pre className="whitespace-pre-wrap rounded border bg-muted/30 p-2 text-xs">
                {notes[k] || "（无）"}
              </pre>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">关联文档</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {DOC_GROUPS.map((g) => {
            const items = (version[g.key] ?? []) as Array<{
              id?: number | string;
              name?: string;
              type?: string;
              url?: string;
            }>;
            if (!items.length) return null;
            return (
              <div key={g.key}>
                <div className="mb-1 text-xs font-semibold text-muted-foreground">
                  {g.label}
                </div>
                <ul className="space-y-1">
                  {items.map((item, i) => (
                    <li key={item.id ?? i} className="text-xs">
                      {item.url ? (
                        <a
                          className="text-primary hover:underline"
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {item.name || item.url}
                        </a>
                      ) : (
                        <span>{item.name || "(未命名)"}</span>
                      )}
                      {item.type ? (
                        <span className="ml-2 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                          {item.type}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          {DOC_GROUPS.every(
            (g) => !((version[g.key] ?? []) as unknown[]).length,
          ) ? (
            <div className="text-xs text-muted-foreground">没有挂任何文档。</div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

const NOTE_LABELS: Record<keyof VersionReleaseNotes, string> = {
  sql: "SQL",
  config: "配置变更",
  commands: "常用命令",
  notes: "注意事项",
};

function parseReleaseNotes(raw: string | null | undefined): VersionReleaseNotes {
  const empty: VersionReleaseNotes = { sql: "", config: "", commands: "", notes: "" };
  if (!raw) return empty;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return {
        sql: parsed.sql ?? "",
        config: parsed.config ?? "",
        commands: parsed.commands ?? "",
        notes: parsed.notes ?? "",
      };
    }
  } catch {
    // 旧数据是纯文本，整体当 notes 显示
    return { ...empty, notes: raw };
  }
  return empty;
}

function SummaryStat({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div className="rounded border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function formatPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function formatHours(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(1)} h`;
}

function formatDate(s: string | null | undefined): string {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}
