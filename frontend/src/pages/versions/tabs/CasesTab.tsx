/**
 * 用例库 tab：本版本绑定的所有自动化用例（跨模块、跨 case_type）。
 *
 * 顶部 toolbar：3 个 Select（module / case_type / status）
 * 主体：扁平表，每行展示 name / module / case_type / 最近一次执行状态
 * 行点击 → 跳转 /runs?case_id=X（已有页面）
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { modulesApi, versionsApi } from "@/lib/api";
import { AUTOMATED_CASE_TYPES } from "@/types/domain";
import type { CaseType } from "@/types/domain";

const ANY = "__any__";

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "passed", label: "通过" },
  { value: "failed", label: "失败" },
  { value: "broken", label: "broken" },
  { value: "error", label: "error" },
  { value: "skipped", label: "跳过" },
  { value: "pending", label: "未执行" },
];

export function CasesTab({
  projectId,
  versionId,
}: {
  projectId: number;
  versionId: number;
}) {
  const navigate = useNavigate();
  const [moduleFilter, setModuleFilter] = useState<string>(ANY);
  const [caseTypeFilter, setCaseTypeFilter] = useState<string>(ANY);
  const [statusFilter, setStatusFilter] = useState<string>(ANY);

  const modulesQuery = useQuery({
    queryKey: ["modules", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: !Number.isNaN(projectId),
  });

  const queryParams = useMemo(() => {
    const p: { module_id?: number; case_type?: CaseType; status?: string } = {};
    if (moduleFilter !== ANY) p.module_id = Number(moduleFilter);
    if (caseTypeFilter !== ANY) p.case_type = caseTypeFilter as CaseType;
    if (statusFilter !== ANY) p.status = statusFilter;
    return p;
  }, [moduleFilter, caseTypeFilter, statusFilter]);

  const casesQuery = useQuery({
    queryKey: ["version-cases", versionId, queryParams],
    queryFn: () => versionsApi.listCases(versionId, queryParams),
    enabled: !Number.isNaN(versionId),
  });

  const items = casesQuery.data?.items ?? [];

  return (
    <div className="space-y-4 p-6">
      <Card data-print-hide>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <FilterBlock label="模块">
            <Select value={moduleFilter} onValueChange={setModuleFilter}>
              <SelectTrigger className="h-9 w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部模块</SelectItem>
                {(modulesQuery.data ?? []).map((m) => (
                  <SelectItem key={m.id} value={String(m.id)}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>
          <FilterBlock label="类型">
            <Select value={caseTypeFilter} onValueChange={setCaseTypeFilter}>
              <SelectTrigger className="h-9 w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部</SelectItem>
                {AUTOMATED_CASE_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>
          <FilterBlock label="最近一次状态">
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="h-9 w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>全部</SelectItem>
                {STATUS_OPTIONS.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FilterBlock>
          <div className="ml-auto text-xs text-muted-foreground">
            共 {casesQuery.data?.total ?? 0} 条
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          {casesQuery.isLoading ? (
            <div className="p-6 text-sm text-muted-foreground">加载中…</div>
          ) : items.length === 0 ? (
            <div className="p-6 text-sm text-muted-foreground">
              本版本没有匹配的用例。
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="border-b text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2">名称</th>
                  <th className="px-4 py-2">模块</th>
                  <th className="px-4 py-2">类型</th>
                  <th className="px-4 py-2">最近一次</th>
                  <th className="px-4 py-2">报告</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr
                    key={c.id}
                    className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
                    onClick={() => {
                      navigate(`/runs?case_id=${c.id}`);
                    }}
                  >
                    <td className="px-4 py-2">{c.name}</td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {c.module_name || `#${c.module_id}`}
                    </td>
                    <td className="px-4 py-2">{c.case_type}</td>
                    <td className="px-4 py-2">{c.latest_run?.status ?? "—"}</td>
                    <td className="px-4 py-2">
                      {c.latest_run?.report_id ? (
                        <Link
                          className="hover:underline"
                          to={`/runs/${c.latest_run.report_id}`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          #{c.latest_run.report_id}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function FilterBlock({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-[11px] text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}
