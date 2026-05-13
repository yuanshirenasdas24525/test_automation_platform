import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  GitFork,
  Loader2,
  Pencil,
  Plus,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { AttachmentList } from "@/components/attachments/AttachmentList";
import { PriorityBadge } from "@/components/badges/PriorityBadge";
import { RequirementStatusBadge } from "@/components/badges/RequirementStatusBadge";
import { RichTextEditor } from "@/components/editor/RichTextEditor";
import { AssigneePicker } from "@/components/pickers/AssigneePicker";
import { AiAnalysisLauncherDialog } from "./requirements/dialogs/AiAnalysisLauncherDialog";
import { AnalysisDocumentListDialog } from "./requirements/dialogs/AnalysisDocumentListDialog";
import { RequirementDetailDrawer } from "./requirements/RequirementDetailDrawer";
import { cn } from "@/lib/utils";
import { useUserId } from "@/lib/current-user";
import {
  ApiError,
  aiApi,
  modulesApi,
  projectsApi,
  requirementsApi,
  versionsApi,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  AiRun,
  Requirement,
  RequirementSystemStatus,
} from "@/types/domain";
import { ALL_REQUIREMENT_SYSTEM_STATUSES } from "@/types/domain";

/**
 * 需求管理页（项目级）。
 *
 * 两个核心入口：
 *   - 手工新建：弹 dialog，填 title/description/acceptance_criteria
 *   - AI 解析：粘贴 PRD/需求文本 → 调 /api/ai/requirement_parse 异步生成
 *     → 完成后列表自动刷新（轮询 ai_run 状态 + invalidate）
 *
 * 一条需求支持 draft → approved → archived 三态切换。
 * AI 生成的会带 source=ai_generated 徽章 + 链回到 ai_run 详情（debug 用）。
 */

const SYSTEM_STATUS_LABEL_CN: Record<RequirementSystemStatus, string> = {
  approved: "已立项",
  developing: "开发中",
  pm_review: "产品体验",
  testing: "测试中",
  ready_to_release: "待发版",
  done: "已完成",
};

const PRIORITY_OPTIONS = [
  { value: "0", label: "紧急" },
  { value: "1", label: "高" },
  { value: "2", label: "中" },
  { value: "3", label: "低" },
] as const;

function formatPlannedRange(req: Requirement): string {
  const s = req.planned_start_at?.slice(0, 10);
  const e = req.planned_end_at?.slice(0, 10);
  if (!s && !e) return "—";
  return `${s ?? "—"} ~ ${e ?? "—"}`;
}

export function RequirementsPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [moduleFilter, setModuleFilter] = useState<string>("all");
  const [priorityFilter, setPriorityFilter] = useState<string>("all");
  const [systemStatusFilter, setSystemStatusFilter] = useState<string>("all");
  const [versionFilter, setVersionFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<
    | { mode: "create" }
    | { mode: "edit"; req: Requirement }
    | null
  >(null);
  const [aiOpen, setAiOpen] = useState(false);
  const [splittingParent, setSplittingParent] = useState<Requirement | null>(null);
  const [aiLauncherFor, setAiLauncherFor] = useState<Requirement | null>(null);
  const [docsListFor, setDocsListFor] = useState<Requirement | null>(null);
  const [detailReq, setDetailReq] = useState<Requirement | null>(null);

  const projectQuery = useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => projectsApi.get(projectId),
    enabled: Number.isFinite(projectId),
  });

  const modulesQuery = useQuery({
    queryKey: ["modules", "picker", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: Number.isFinite(projectId),
    staleTime: 30_000,
  });

  const versionsQuery = useQuery({
    queryKey: ["versions", projectId],
    queryFn: () => versionsApi.list(projectId),
    enabled: Number.isFinite(projectId),
    staleTime: 30_000,
  });

  const listFilters = {
    module_id: moduleFilter === "all" ? undefined : Number(moduleFilter),
    system_status:
      systemStatusFilter === "all"
        ? undefined
        : (systemStatusFilter as RequirementSystemStatus),
    version_id: versionFilter === "all" ? undefined : Number(versionFilter),
    tree: true,
  };

  const listQuery = useQuery({
    queryKey: ["requirements", projectId, "tree", listFilters],
    queryFn: () => requirementsApi.list(projectId, listFilters),
    enabled: Number.isFinite(projectId),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "操作失败";
    toast.error(msg);
  };

  const removeMutation = useMutation({
    mutationFn: (rid: number) => requirementsApi.remove(rid),
    onSuccess: () => {
      toast.success("已删除");
      invalidate();
    },
    onError: handleError,
  });

  const items = listQuery.data ?? [];

  // 优先级筛选放前端：树形结果里命中父级才显示；命中子级则保留整条父级链。
  const visibleRoots = useMemo(() => {
    if (priorityFilter === "all") return items;
    const target = Number(priorityFilter);
    return items.filter((r) => {
      if (r.priority === target) return true;
      return (r.children ?? []).some((c) => c.priority === target);
    });
  }, [items, priorityFilter]);

  const totalCount = useMemo(
    () =>
      visibleRoots.reduce(
        (sum, r) => sum + 1 + (r.children?.length ?? 0),
        0,
      ),
    [visibleRoots],
  );

  const toggleExpand = (rid: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(rid)) next.delete(rid);
      else next.add(rid);
      return next;
    });
  };

  if (!Number.isFinite(projectId)) {
    return <div className="p-8 text-sm text-destructive">非法的项目 ID。</div>;
  }

  const modules = modulesQuery.data ?? [];
  const versions = versionsQuery.data ?? [];

  const moduleNames = useMemo(() => {
    const m = new Map<number, string>();
    for (const mod of modules) m.set(mod.id, mod.name);
    return m;
  }, [modules]);

  const versionNames = useMemo(() => {
    const m = new Map<number, string>();
    for (const v of versions) m.set(v.id, v.display_name || v.version_name);
    return m;
  }, [versions]);

  return (
    <div className="space-y-4 p-6">
      {/* 顶栏 */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            onClick={() => navigate(`/projects/${projectId}`)}
            title="返回项目详情"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-muted-foreground text-sm">需求 ·</span>
            <span className="truncate font-medium">
              {projectQuery.data?.name ?? "…"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditing({ mode: "create" })}>
            <Plus className="h-4 w-4" />
            新建需求
          </Button>
          <Button size="sm" onClick={() => setAiOpen(true)}>
            <Sparkles className="h-4 w-4" />
            AI 解析需求
          </Button>
        </div>
      </div>

      {/* 过滤栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <FilterSelect
          label="模块"
          value={moduleFilter}
          onChange={setModuleFilter}
          width="w-40"
          options={[
            { value: "all", label: "全部模块" },
            ...modules.map((m) => ({ value: String(m.id), label: m.name })),
          ]}
        />
        <FilterSelect
          label="优先级"
          value={priorityFilter}
          onChange={setPriorityFilter}
          width="w-32"
          options={[
            { value: "all", label: "全部" },
            ...PRIORITY_OPTIONS,
          ]}
        />
        <FilterSelect
          label="状态"
          value={systemStatusFilter}
          onChange={setSystemStatusFilter}
          width="w-32"
          options={[
            { value: "all", label: "全部" },
            ...ALL_REQUIREMENT_SYSTEM_STATUSES.map((s) => ({
              value: s,
              label: SYSTEM_STATUS_LABEL_CN[s],
            })),
          ]}
        />
        <FilterSelect
          label="迭代"
          value={versionFilter}
          onChange={setVersionFilter}
          width="w-40"
          options={[
            { value: "all", label: "全部迭代" },
            ...versions.map((v) => ({
              value: String(v.id),
              label: v.display_name || v.version_name,
            })),
          ]}
        />
        <span className="ml-2 text-xs text-muted-foreground">共 {totalCount} 条</span>
      </div>

      {/* 树形表格 */}
      {listQuery.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : visibleRoots.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            还没有需求 —— 用 AI 解析 PRD 一键导入，或手工新建
          </CardContent>
        </Card>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium w-24">编号</th>
                <th className="px-3 py-2 text-left font-medium">名称</th>
                <th className="px-3 py-2 text-left font-medium w-20">优先级</th>
                <th className="px-3 py-2 text-left font-medium w-32">迭代</th>
                <th className="px-3 py-2 text-left font-medium w-28">状态</th>
                <th className="px-3 py-2 text-left font-medium w-48">起止时间</th>
                <th className="px-3 py-2 text-right font-medium w-44">操作</th>
              </tr>
            </thead>
            <tbody>
              {visibleRoots.map((req) => (
                <RequirementTreeRows
                  key={req.id}
                  req={req}
                  expanded={expanded}
                  onToggle={toggleExpand}
                  versions={versions}
                  onEdit={(r) => setEditing({ mode: "edit", req: r })}
                  onDelete={(r) => {
                    const isChild = !!r.parent_requirement_id;
                    const msg = isChild
                      ? `删除子需求"${r.title}"？`
                      : `删除需求"${r.title}"？同时会删除其子需求。`;
                    if (confirm(msg))
                      removeMutation.mutate(r.id);
                  }}
                  onSplit={(r) => setSplittingParent(r)}
                  onAiAnalyze={(r) => setAiLauncherFor(r)}
                  onOpenDocs={(r) => setDocsListFor(r)}
                  onViewDetail={(r) => setDetailReq(r)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <RequirementDialog
        state={editing}
        projectId={projectId}
        onClose={() => setEditing(null)}
        onDone={() => {
          invalidate();
          setEditing(null);
        }}
        onCreated={(req) => {
          invalidate();
          setEditing({ mode: "edit", req });
        }}
        onError={handleError}
      />

      <AiParseDialog
        open={aiOpen}
        projectId={projectId}
        onClose={() => setAiOpen(false)}
        onDone={() => {
          invalidate();
          setAiOpen(false);
        }}
      />

      <SplitRequirementDialog
        parent={splittingParent}
        onClose={() => setSplittingParent(null)}
        onDone={() => {
          invalidate();
          setSplittingParent(null);
        }}
        onError={handleError}
      />

      <AiAnalysisLauncherDialog
        open={!!aiLauncherFor}
        requirement={aiLauncherFor}
        onClose={() => setAiLauncherFor(null)}
        onTriggered={() => {
          // 触发后直接打开文档列表，方便用户观察生成进度
          if (aiLauncherFor) setDocsListFor(aiLauncherFor);
          setAiLauncherFor(null);
        }}
      />

      <AnalysisDocumentListDialog
        open={!!docsListFor}
        requirement={docsListFor}
        onClose={() => setDocsListFor(null)}
      />

      <RequirementDetailDrawer
        req={detailReq}
        open={!!detailReq}
        onClose={() => setDetailReq(null)}
        onEdit={(r) => {
          setDetailReq(null);
          setEditing({ mode: "edit", req: r });
        }}
        onViewRequirement={(reqId) => {
          requirementsApi.get(reqId).then((r) => {
            setDetailReq(null);
            setTimeout(() => setDetailReq(r), 100);
          }).catch(handleError);
        }}
        moduleNames={moduleNames}
        versionNames={versionNames}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 筛选下拉（toolbar 小封装）
// ---------------------------------------------------------------------------
function FilterSelect({
  label,
  value,
  onChange,
  options,
  width = "w-32",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: ReadonlyArray<{ value: string; label: string }>;
  width?: string;
}) {
  return (
    <div className="flex items-center gap-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className={cn("h-8 text-xs", width)}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value} className="text-xs">
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 树形渲染：父行 + 可选展开子行
// ---------------------------------------------------------------------------
function RequirementTreeRows({
  req,
  expanded,
  onToggle,
  versions,
  onEdit,
  onDelete,
  onSplit,
  onAiAnalyze,
  onOpenDocs,
  onViewDetail,
}: {
  req: Requirement;
  expanded: Set<number>;
  onToggle: (id: number) => void;
  versions: { id: number; version_name: string; display_name?: string | null }[];
  onEdit: (r: Requirement) => void;
  onDelete: (r: Requirement) => void;
  onSplit: (r: Requirement) => void;
  onAiAnalyze: (r: Requirement) => void;
  onOpenDocs: (r: Requirement) => void;
  onViewDetail: (r: Requirement) => void;
}) {
  const children = req.children ?? [];
  const isOpen = expanded.has(req.id);

  return (
    <>
      <RequirementTableRow
        req={req}
        depth={0}
        hasChildren={children.length > 0}
        isOpen={isOpen}
        onToggle={() => onToggle(req.id)}
        versions={versions}
        onEdit={() => onEdit(req)}
        onDelete={() => onDelete(req)}
        onSplit={() => onSplit(req)}
        onAiAnalyze={() => onAiAnalyze(req)}
        onOpenDocs={() => onOpenDocs(req)}
        onViewDetail={() => onViewDetail(req)}
      />
      {isOpen
        ? children.map((c) => (
            <RequirementTableRow
              key={c.id}
              req={c}
              depth={1}
              hasChildren={false}
              isOpen={false}
              onToggle={() => {}}
              versions={versions}
              onEdit={() => onEdit(c)}
              onDelete={() => onDelete(c)}
              onSplit={null}
              onAiAnalyze={() => onAiAnalyze(c)}
              onOpenDocs={() => onOpenDocs(c)}
              onViewDetail={() => onViewDetail(c)}
            />
          ))
        : null}
    </>
  );
}

function RequirementTableRow({
  req,
  depth,
  hasChildren,
  isOpen,
  onToggle,
  versions,
  onEdit,
  onDelete,
  onSplit,
  onAiAnalyze,
  onOpenDocs,
  onViewDetail,
}: {
  req: Requirement;
  depth: number;
  hasChildren: boolean;
  isOpen: boolean;
  onToggle: () => void;
  versions: { id: number; version_name: string; display_name?: string | null }[];
  onEdit: () => void;
  onDelete: () => void;
  onSplit: (() => void) | null;
  onAiAnalyze: () => void;
  onOpenDocs: () => void;
  onViewDetail: () => void;
}) {
  const version = versions.find((v) => v.id === req.version_id);
  return (
    <tr className="border-t hover:bg-accent/30">
      <td className="px-3 py-2 align-top text-xs text-muted-foreground">
        <button
          type="button"
          onClick={onViewDetail}
          className="font-mono text-muted-foreground hover:text-foreground hover:underline cursor-pointer"
        >
          REQ-{req.id}
        </button>
      </td>
      <td className="px-3 py-2 align-top">
        <div
          className="flex items-start gap-1"
          style={{ paddingLeft: depth * 20 }}
        >
          {hasChildren ? (
            <button
              type="button"
              onClick={onToggle}
              className="mt-0.5 text-muted-foreground hover:text-foreground"
              aria-label="展开子需求"
            >
              {isOpen ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </button>
          ) : (
            <span className="inline-block w-3.5" />
          )}
          <button
            type="button"
            onClick={onViewDetail}
            className="min-w-0 flex-1 text-left cursor-pointer"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium hover:text-primary hover:underline">
                  {req.title}
                </span>
                {req.source === "ai_generated" ? (
                  <span className="inline-flex items-center gap-0.5 rounded bg-violet-50 px-1 py-0.5 text-[10px] text-violet-700 ring-1 ring-inset ring-violet-200">
                    <Sparkles className="h-2.5 w-2.5" />
                    AI
                  </span>
                ) : null}
                {req.tags?.slice(0, 3).map((t) => (
                  <span
                    key={t}
                    className="rounded bg-secondary px-1 py-0.5 text-[10px] text-secondary-foreground"
                  >
                    {t}
                  </span>
                ))}
              </div>
              {req.description ? (
                <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                  {stripHtml(req.description)}
                </div>
              ) : null}
            </div>
          </button>
        </div>
      </td>
      <td className="px-3 py-2 align-top">
        <PriorityBadge priority={req.priority} />
      </td>
      <td className="px-3 py-2 align-top text-xs text-muted-foreground">
        {version ? version.display_name || version.version_name : "—"}
      </td>
      <td className="px-3 py-2 align-top">
        {req.system_status ? (
          <RequirementStatusBadge status={req.system_status} />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-3 py-2 align-top text-xs text-muted-foreground">
        {formatPlannedRange(req)}
      </td>
      <td className="px-3 py-2 align-top text-right">
        <div className="inline-flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-violet-600 hover:text-violet-700"
            onClick={onAiAnalyze}
            title="AI 分析此需求"
          >
            <Bot className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onOpenDocs}
            title="查看 AI 分析文档"
          >
            <FileText className="h-3.5 w-3.5" />
          </Button>
          {onSplit ? (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={onSplit}
              title="拆分子需求"
            >
              <GitFork className="h-3.5 w-3.5" />
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onEdit}
            title="编辑"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive"
            onClick={onDelete}
            title="删除"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// 手工创建/编辑对话框（TAPD 风格大表单）
// ---------------------------------------------------------------------------
function emptyAssignees() {
  return { dev: [] as number[], test: [] as number[], pm: [] as number[], ui: [] as number[] };
}

function isoDateInput(v: string | null | undefined): string {
  if (!v) return "";
  return v.slice(0, 10);
}

function dateToIso(d: string): string | null {
  if (!d) return null;
  return d + "T00:00:00+08:00";
}

function stripHtml(html: string): string {
  if (!html) return "";
  const doc = new DOMParser().parseFromString(html, "text/html");
  return (doc.body.textContent || "").replace(/\s+/g, " ").trim();
}

function RequirementDialog({
  state,
  projectId,
  onClose,
  onDone,
  onCreated,
  onError,
}: {
  state: { mode: "create" } | { mode: "edit"; req: Requirement } | null;
  projectId: number;
  onClose: () => void;
  onDone: () => void;
  onCreated: (req: Requirement) => void;
  onError: (e: unknown) => void;
}) {
  const isEdit = state?.mode === "edit";
  const initial = state?.mode === "edit" ? state.req : null;

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [criteriaText, setCriteriaText] = useState("");
  const [priority, setPriority] = useState<number>(2);
  const [tagsText, setTagsText] = useState("");
  const [moduleId, setModuleId] = useState<string>("none");
  const [systemStatus, setSystemStatus] = useState<string>("none");
  const [versionId, setVersionId] = useState<string>("none");
  const [plannedStart, setPlannedStart] = useState<string>("");
  const [plannedEnd, setPlannedEnd] = useState<string>("");
  const [dependsOnText, setDependsOnText] = useState<string>("");
  const [assignees, setAssignees] = useState(emptyAssignees());
  const userId = useUserId();

  const modulesQuery = useQuery({
    queryKey: ["modules", "picker", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: !!state,
    staleTime: 30_000,
  });
  const versionsQuery = useQuery({
    queryKey: ["versions", projectId],
    queryFn: () => versionsApi.list(projectId),
    enabled: !!state,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (!state) return;
    if (initial) {
      setTitle(initial.title);
      setDescription(initial.description || "");
      setCriteriaText((initial.acceptance_criteria || []).join("\n"));
      setPriority(initial.priority ?? 2);
      setTagsText((initial.tags || []).join(","));
      setModuleId(initial.module_id != null ? String(initial.module_id) : "none");
      setSystemStatus(initial.system_status ?? "none");
      setVersionId(initial.version_id != null ? String(initial.version_id) : "none");
      setPlannedStart(isoDateInput(initial.planned_start_at));
      setPlannedEnd(isoDateInput(initial.planned_end_at));
      setDependsOnText((initial.depends_on ?? []).join(","));
      setAssignees(
        initial.assignees
          ? {
              dev: initial.assignees.dev ?? [],
              test: initial.assignees.test ?? [],
              pm: initial.assignees.pm ?? [],
              ui: initial.assignees.ui ?? [],
            }
          : emptyAssignees(),
      );
    } else {
      setTitle("");
      setDescription("");
      setCriteriaText("");
      setPriority(2);
      setTagsText("");
      setModuleId("none");
      setSystemStatus("none");
      setVersionId("none");
      setPlannedStart("");
      setPlannedEnd("");
      setDependsOnText("");
      setAssignees(emptyAssignees());
    }
  }, [state, initial]);

  const buildPayload = () => {
    return {
      title: title.trim(),
      description: description.trim() || null,
      acceptance_criteria: criteriaText
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      priority,
      tags: tagsText
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
      depends_on: dependsOnText
        .split(/[,，\s]+/)
        .map((s) => Number(s.trim()))
        .filter((n) => Number.isFinite(n) && n > 0),
      module_id: moduleId === "none" ? null : Number(moduleId),
      version_id: versionId === "none" ? null : Number(versionId),
      planned_start_at: dateToIso(plannedStart),
      planned_end_at: dateToIso(plannedEnd),
      assignees,
    };
  };

  const createMutation = useMutation({
    mutationFn: () =>
      requirementsApi.create({
        project_id: projectId,
        ...buildPayload(),
      }),
    onSuccess: (data: Requirement) => {
      toast.success("需求已创建，可编辑完整信息和附件");
      onCreated(data);
    },
    onError,
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!initial) return Promise.reject(new Error("invalid"));
      const payload = buildPayload();
      const updatePayload: Record<string, unknown> = { ...payload };
      if (systemStatus !== "none") {
        updatePayload.system_status = systemStatus;
      }
      if (userId) {
        updatePayload.edited_by_id = userId;
      }
      return requirementsApi.update(initial.id, updatePayload);
    },
    onSuccess: () => {
      toast.success("已更新");
      onDone();
    },
    onError,
  });

  const submit = () => {
    if (!title.trim()) {
      toast.error("请填写标题");
      return;
    }
    if (plannedStart && plannedEnd && plannedStart > plannedEnd) {
      toast.error("起止时间不合法");
      return;
    }
    if (isEdit) updateMutation.mutate();
    else createMutation.mutate();
  };

  if (!state) return null;
  const submitting = createMutation.isPending || updateMutation.isPending;
  const modules = modulesQuery.data ?? [];
  const versions = versionsQuery.data ?? [];

  return (
    <Dialog open={!!state} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="max-w-[960px] max-h-[90vh] overflow-y-auto"
      >
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑需求" : "新建需求"}</DialogTitle>
          <DialogDescription>
            一条需求 = 一个可被测试覆盖的功能点
            {initial?.parent_requirement_id ? (
              <span className="ml-2 rounded bg-violet-50 px-1.5 py-0.5 text-xs text-violet-700">
                子需求（父 REQ-{initial.parent_requirement_id}）
              </span>
            ) : null}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {/* 1. 基本信息 */}
          <FormSection title="基本信息">
            <div className="space-y-1 md:col-span-2">
              <Label className="text-xs">标题 *</Label>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                autoFocus
                placeholder="一句话讲清这条需求做什么"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">模块</Label>
              <Select value={moduleId} onValueChange={setModuleId}>
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder="未指定" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none" className="text-xs">未指定</SelectItem>
                  {modules.map((m) => (
                    <SelectItem key={m.id} value={String(m.id)} className="text-xs">
                      {m.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">优先级</Label>
              <Select
                value={String(priority)}
                onValueChange={(v) => setPriority(Number(v))}
              >
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRIORITY_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value} className="text-xs">
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">状态</Label>
              <Select value={systemStatus} onValueChange={setSystemStatus}>
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder="未指定" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none" className="text-xs">未指定（由 Task 自动派生）</SelectItem>
                  {ALL_REQUIREMENT_SYSTEM_STATUSES.map((s) => (
                    <SelectItem key={s} value={s} className="text-xs">
                      {SYSTEM_STATUS_LABEL_CN[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">关联迭代</Label>
              <Select value={versionId} onValueChange={setVersionId}>
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder="未指定" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none" className="text-xs">未指定</SelectItem>
                  {versions.map((v) => (
                    <SelectItem key={v.id} value={String(v.id)} className="text-xs">
                      {v.display_name || v.version_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">标签（逗号分隔）</Label>
              <Input
                value={tagsText}
                onChange={(e) => setTagsText(e.target.value)}
                placeholder="登录,权限"
              />
            </div>
          </FormSection>

          {/* 2. 时间 */}
          <FormSection title="时间">
            <div className="space-y-1">
              <Label className="text-xs">预计开始</Label>
              <Input
                type="date"
                value={plannedStart}
                onChange={(e) => setPlannedStart(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">预计完成</Label>
              <Input
                type="date"
                value={plannedEnd}
                onChange={(e) => setPlannedEnd(e.target.value)}
              />
            </div>
          </FormSection>

          {/* 3. 描述 */}
          <FormSection title="描述" cols={1}>
            <div className="md:col-span-2">
              <RichTextEditor value={description} onChange={setDescription} height={400} toolbar="full" />
            </div>
          </FormSection>

          {/* 4. 验收标准 + 依赖 */}
          <FormSection title="验收 & 依赖">
            <div className="space-y-1 md:col-span-2">
              <Label className="text-xs">验收标准（每行一条，支持 Markdown）</Label>
              <Textarea
                rows={4}
                value={criteriaText}
                onChange={(e) => setCriteriaText(e.target.value)}
                placeholder="未登录用户访问 /admin 时跳转到 /login&#10;已登录普通用户访问 /admin 时返回 403"
              />
            </div>
            <div className="space-y-1 md:col-span-2">
              <Label className="text-xs">依赖需求</Label>
              <div className="flex items-center gap-2">
                <Input
                  value={dependsOnText}
                  onChange={(e) => setDependsOnText(e.target.value)}
                  placeholder="例如：12, 18"
                  className="flex-1"
                />
                <DependencyPickerButton
                  projectId={projectId}
                  currentIds={dependsOnText
                    .split(/[,，\s]+/)
                    .map((s) => Number(s.trim()))
                    .filter((n) => Number.isFinite(n) && n > 0)}
                  excludeId={initial?.id}
                  onSelect={(ids) => setDependsOnText(ids.join(", "))}
                />
              </div>
            </div>
          </FormSection>

          {/* 5. 协作（4 角色 assignee） */}
          <FormSection title="协作">
            <AssigneeRow
              label="开发"
              role="dev"
              value={assignees.dev}
              onChange={(v) => setAssignees((a) => ({ ...a, dev: v }))}
            />
            <AssigneeRow
              label="测试"
              role="test"
              value={assignees.test}
              onChange={(v) => setAssignees((a) => ({ ...a, test: v }))}
            />
            <AssigneeRow
              label="产品"
              role="pm"
              value={assignees.pm}
              onChange={(v) => setAssignees((a) => ({ ...a, pm: v }))}
            />
            <AssigneeRow
              label="UI"
              role="ui"
              value={assignees.ui}
              onChange={(v) => setAssignees((a) => ({ ...a, ui: v }))}
            />
          </FormSection>

          {/* 6. 附件（仅 edit 模式） */}
          {isEdit && initial ? (
            <FormSection title="附件" cols={1}>
              <div className="md:col-span-2">
                <AttachmentList requirementId={initial.id} />
              </div>
            </FormSection>
          ) : (
            <FormSection title="附件" cols={1}>
              <div className="md:col-span-2 rounded border border-dashed bg-muted/30 p-3 text-xs text-muted-foreground">
                保存后即可在编辑视图里上传附件。
              </div>
            </FormSection>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {isEdit ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FormSection({
  title,
  children,
  cols = 2,
}: {
  title: string;
  children: React.ReactNode;
  cols?: 1 | 2;
}) {
  return (
    <div className="space-y-2">
      <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <div
        className={cn(
          "grid gap-3",
          cols === 2 ? "md:grid-cols-2" : "grid-cols-1",
        )}
      >
        {children}
      </div>
    </div>
  );
}

function AssigneeRow({
  label,
  role,
  value,
  onChange,
}: {
  label: string;
  role: "dev" | "test" | "pm" | "ui";
  value: number[];
  onChange: (v: number[]) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <AssigneePicker role={role} value={value} onChange={onChange} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// AI 解析对话框 V2（支持分析模式、文件上传、上下文信息展示）
// ---------------------------------------------------------------------------
function AiParseDialog({
  open,
  projectId,
  onClose,
  onDone,
}: {
  open: boolean;
  projectId: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const [text, setText] = useState("");
  const [analysisMode, setAnalysisMode] = useState<string>("standard");
  const [inputMode, setInputMode] = useState<"text" | "file">("text");
  const [aiRunId, setAiRunId] = useState<number | null>(null);

  const submitMutation = useMutation({
    mutationFn: () => {
      const payload: any = {
        project_id: projectId,
        analysis_mode: analysisMode,
      };
      if (inputMode === "text") {
        payload.text = text.trim();
      }
      return aiApi.submitRequirementParse(payload);
    },
    onSuccess: (res) => {
      setAiRunId(res.ai_run_id);
      toast.success(`AI 任务已提交（${analysisMode === "multi_model" ? "多模型" : analysisMode === "deep" ? "深度" : analysisMode === "quick" ? "快速" : "标准"}分析）`);
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "提交失败";
      toast.error(msg);
    },
  });

  // 轮询
  const runQuery = useQuery({
    queryKey: queryKeys.aiRun(aiRunId ?? -1),
    queryFn: () => aiApi.getRun(aiRunId!),
    enabled: aiRunId !== null,
    refetchInterval: (query) => {
      const status = (query.state.data as AiRun | undefined)?.status;
      if (status === "success" || status === "failed" || status === "cancelled") {
        return false;
      }
      return 2000;
    },
  });

  const run = runQuery.data;
  const isRunning =
    run?.status === "pending" || run?.status === "running" || submitMutation.isPending;

  const reset = () => {
    setText("");
    setAiRunId(null);
    setAnalysisMode("standard");
    setInputMode("text");
  };

  useEffect(() => {
    if (run?.status === "success") {
      onDone();
    }
  }, [run?.status, onDone]);

  const modeLabels: Record<string, string> = {
    quick: "快速扫描（轻量模型，秒级完成）",
    standard: "标准分析（平衡质量与速度）",
    deep: "深度分析（旗舰模型，全面提取）",
    multi_model: "多模型集成（2-3 个模型交叉验证，最准确）",
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          reset();
          onClose();
        }
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-600" />
            AI 解析需求
          </DialogTitle>
          <DialogDescription>
            粘贴 PRD / 用户故事 / 需求描述，AI 会拆成可测试的需求点
            {analysisMode === "deep" ? "（含项目上下文分析）" : ""}
          </DialogDescription>
        </DialogHeader>

        {!aiRunId ? (
          <div className="space-y-3">
            {/* 分析模式选择 */}
            <div className="space-y-1">
              <Label className="text-xs">分析模式</Label>
              <Select value={analysisMode} onValueChange={setAnalysisMode}>
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="quick">{modeLabels.quick}</SelectItem>
                  <SelectItem value="standard">{modeLabels.standard}</SelectItem>
                  <SelectItem value="deep">{modeLabels.deep}</SelectItem>
                  <SelectItem value="multi_model">{modeLabels.multi_model}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 输入方式 */}
            <div className="space-y-1">
              <Label className="text-xs">输入方式</Label>
              <div className="flex gap-2">
                <Button
                  variant={inputMode === "text" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setInputMode("text")}
                >
                  粘贴文本
                </Button>
                <Button
                  variant={inputMode === "file" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setInputMode("file")}
                >
                  上传文档
                </Button>
              </div>
            </div>

            {inputMode === "text" ? (
              <>
                <Textarea
                  rows={12}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="例如：&#10;用户管理模块需要支持创建 / 编辑 / 删除 / 锁定四种操作。&#10;创建用户时需要校验邮箱格式 + 密码强度。&#10;锁定后的用户登录失败次数清零。&#10;删除用户为软删除，30 天内可恢复。"
                />
                <p className="text-xs text-muted-foreground">
                  建议至少给一段完整的需求段落（≥ 10 个字）
                </p>
              </>
            ) : (
              <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">
                文件上传功能在后续版本中提供（配置 file_path 路径即可）
                <br />
                <span className="text-xs">支持 PDF / DOCX / MD / TXT 格式</span>
              </div>
            )}

            {analysisMode === "multi_model" && (
              <div className="rounded bg-violet-50 p-2 text-xs text-violet-800">
                多模型集成将同时调用 2-3 个不同模型分析同一份需求，投票聚合结果，置信度更高。
                成本约为标准模式的 2-3 倍。
              </div>
            )}
          </div>
        ) : (
          <AiRunProgress run={run} analysisMode={analysisMode} />
        )}

        <DialogFooter>
          {!aiRunId ? (
            <>
              <Button variant="outline" onClick={onClose}>
                取消
              </Button>
              <Button
                onClick={() => submitMutation.mutate()}
                disabled={inputMode === "text" ? text.trim().length < 10 : false || submitMutation.isPending}
              >
                {submitMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4" />
                )}
                开始解析
              </Button>
            </>
          ) : (
            <>
              {isRunning ? (
                <Button
                  variant="outline"
                  onClick={() => aiApi.cancelRun(aiRunId).catch(() => {})}
                >
                  取消任务
                </Button>
              ) : (
                <Button variant="outline" onClick={reset}>
                  再来一次
                </Button>
              )}
              <Button
                onClick={() => {
                  reset();
                  onClose();
                }}
              >
                关闭
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AiRunProgress({
  run,
  analysisMode,
}: {
  run?: AiRun | null;
  analysisMode?: string;
}) {
  if (!run) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载任务状态...
      </div>
    );
  }

  if (run.status === "failed") {
    return (
      <div className="space-y-2 rounded border border-destructive/40 bg-destructive/5 p-3 text-sm">
        <div className="font-medium text-destructive">AI 任务失败</div>
        <pre className="whitespace-pre-wrap text-xs text-destructive">
          {run.error}
        </pre>
      </div>
    );
  }

  if (run.status === "cancelled") {
    return <div className="text-sm text-muted-foreground">任务已取消</div>;
  }

  if (run.status !== "success") {
    const modeHint =
      analysisMode === "deep"
        ? "深度分析中，请耐心等待"
        : analysisMode === "multi_model"
          ? "多模型并行分析中（2-3 个模型）"
          : "分析中，通常 10-30 秒";
    return (
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        {modeHint}...
      </div>
    );
  }

  const out = run.output_payload as
    | {
        summary?: string;
        created_count?: number;
        requirements?: any[];
        context_items_count?: number;
        matched_contexts_count?: number;
        analysis_notes?: { topic: string; note: string }[];
        analysis_mode?: string;
      }
    | undefined;
  const requirements = out?.requirements ?? [];
  const modeLabel =
    out?.analysis_mode === "multi_model"
      ? "多模型"
      : out?.analysis_mode === "deep"
        ? "深度"
        : out?.analysis_mode === "quick"
          ? "快速"
          : "标准";

  return (
    <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
      {/* 摘要 */}
      <div className="rounded bg-emerald-50 p-3 text-sm text-emerald-900 ring-1 ring-emerald-200">
        ✅ 已生成 <strong>{out?.created_count ?? 0}</strong> 条需求
        {out?.context_items_count != null && out.context_items_count > 0 ? (
          <span className="ml-1">
            ，提取 <strong>{out.context_items_count}</strong> 条项目知识
          </span>
        ) : null}
        {out?.matched_contexts_count != null && out.matched_contexts_count > 0 ? (
          <span className="ml-1">
            ，匹配 <strong>{out.matched_contexts_count}</strong> 条历史上下文
          </span>
        ) : null}
        <span className="ml-2 text-xs">（{modeLabel}分析）</span>
        {out?.summary ? (
          <>
            <br />
            <span className="text-xs text-emerald-700">{out.summary}</span>
          </>
        ) : null}
      </div>

      {/* Token & 成本 */}
      <div className="text-xs text-muted-foreground flex flex-wrap gap-x-3 gap-y-1">
        <span>Token：{run.tokens_in ?? 0} in / {run.tokens_out ?? 0} out</span>
        {run.cost_usd != null ? (
          <span>成本 ${run.cost_usd.toFixed(4)}</span>
        ) : null}
        <span>
          {run.provider} · {run.model}
        </span>
      </div>

      {/* 分析注释 */}
      {out?.analysis_notes?.length ? (
        <div className="rounded bg-violet-50 p-2 text-xs ring-1 ring-violet-200">
          <div className="font-medium text-violet-800 mb-1">交叉分析</div>
          {out.analysis_notes.map((n, i) => (
            <div key={i} className="text-violet-700">
              {n.topic ? <strong>{n.topic}：</strong> : null}
              {n.note}
            </div>
          ))}
        </div>
      ) : null}

      {/* 需求列表 */}
      <div className="space-y-2">
        <div className="text-xs font-medium text-muted-foreground">生成的需求条目</div>
        {requirements.map((r: any, i: number) => (
          <div key={i} className="rounded border bg-card p-2">
            <div className="flex items-center gap-2">
              {r._confidence === "high" ? (
                <span title="多模型一致通过" className="text-xs text-emerald-500">✦</span>
              ) : r._confidence === "medium" ? (
                <span title="部分模型通过" className="text-xs text-amber-500">◇</span>
              ) : null}
              <span className="font-medium text-sm">{r.title}</span>
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                P{r.priority ?? 2}
              </span>
            </div>
            {r.description ? (
              <p className="mt-1 text-xs text-muted-foreground">{r.description}</p>
            ) : null}
            {r.tags?.length ? (
              <div className="mt-1 flex gap-1">
                {r.tags.map((t: string) => (
                  <span key={t} className="rounded bg-secondary px-1 py-0.5 text-[10px]">
                    {t}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 子需求拆分对话框
// ---------------------------------------------------------------------------
function SplitRequirementDialog({
  parent,
  onClose,
  onDone,
  onError,
}: {
  parent: Requirement | null;
  onClose: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [text, setText] = useState("");

  useEffect(() => {
    if (parent) setText("");
  }, [parent]);

  const splitMutation = useMutation({
    mutationFn: () => {
      if (!parent) return Promise.reject(new Error("invalid"));
      const lines = text
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      if (lines.length === 0) {
        return Promise.reject(new Error("至少写一条子需求标题"));
      }
      return requirementsApi.split(
        parent.id,
        lines.map((title) => ({ title })),
      );
    },
    onSuccess: (created) => {
      toast.success(`已创建 ${created.length} 条子需求`);
      onDone();
    },
    onError,
  });

  if (!parent) return null;
  const lineCount = text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean).length;

  return (
    <Dialog open={!!parent} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>拆分子需求</DialogTitle>
          <DialogDescription>
            父需求：<span className="font-medium">{parent.title}</span>
            <br />
            一行一条，子需求会继承父需求的模块 / 迭代 / 优先级。
          </DialogDescription>
        </DialogHeader>
        <Textarea
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"接口：新增登录\nUI：登录页表单\n测试：登录用例覆盖"}
          autoFocus
        />
        <div className="text-xs text-muted-foreground">将创建 {lineCount} 条子需求</div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={() => splitMutation.mutate()}
            disabled={lineCount === 0 || splitMutation.isPending}
          >
            {splitMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            创建子需求
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 依赖需求选择器
// ---------------------------------------------------------------------------
function DependencyPickerButton({
  projectId,
  currentIds,
  excludeId,
  onSelect,
}: {
  projectId: number;
  currentIds: number[];
  excludeId?: number;
  onSelect: (ids: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set(currentIds));

  const reqsQuery = useQuery({
    queryKey: ["requirements", "picker", projectId],
    queryFn: () => requirementsApi.list(projectId, { tree: true }),
    enabled: open,
  });

  const allReqs = useMemo(() => {
    function flatten(list: Requirement[]): Requirement[] {
      return list.flatMap((r) => [r, ...flatten(r.children ?? [])]);
    }
    return flatten(reqsQuery.data ?? []).filter(
      (r) => excludeId == null || r.id !== excludeId,
    );
  }, [reqsQuery.data, excludeId]);

  const filtered = useMemo(
    () =>
      search.trim()
        ? allReqs.filter(
            (r) =>
              r.title.includes(search) || String(r.id).includes(search),
          )
        : allReqs,
    [allReqs, search],
  );

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const apply = () => {
    onSelect([...selected]);
    setOpen(false);
  };

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => {
          setSelected(new Set(currentIds));
          setSearch("");
          setOpen(true);
        }}
      >
        <Search className="mr-1 h-3 w-3" />
        选择需求
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>选择依赖需求</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              autoFocus
              placeholder="搜索标题或 ID…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="max-h-64 space-y-0.5 overflow-y-auto">
              {filtered.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  没有匹配的需求
                </p>
              ) : (
                filtered.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => toggle(r.id)}
                    className="flex w-full items-center gap-3 rounded px-3 py-2 text-left text-sm hover:bg-accent"
                  >
                    <span
                      className={
                        selected.has(r.id)
                          ? "flex h-4 w-4 items-center justify-center rounded bg-primary text-[10px] text-primary-foreground"
                          : "h-4 w-4 rounded border"
                      }
                    >
                      {selected.has(r.id) && <Check className="h-3 w-3" />}
                    </span>
                    <span className="shrink-0 font-mono text-xs text-muted-foreground">
                      REQ-{r.id}
                    </span>
                    <span className="truncate">{r.title}</span>
                  </button>
                ))
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              取消
            </Button>
            <Button onClick={apply}>确定 ({selected.size})</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
