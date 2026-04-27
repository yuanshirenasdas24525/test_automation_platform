import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  ClipboardList,
  Download,
  Folder,
  FolderPlus,
  History,
  Info,
  Loader2,
  MinusCircle,
  MoreHorizontal,
  Pencil,
  Plus,
  ShieldOff,
  Trash2,
  Upload,
  XCircle,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { cn } from "@/lib/utils";
import {
  ApiError,
  contentApi,
  functionalCasesApi,
  modulesApi,
  projectsApi,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  ContentNode,
  FunctionalBatchItem,
  FunctionalBatchSummary,
  FunctionalCase,
  FunctionalRunStatus,
  FunctionalSpec,
} from "@/types/domain";

/**
 * 功能用例（人工执行）管理页。
 *
 * 核心功能：
 *  - 文件管理器风格：项目 → 模块 → 子模块，进入模块后看用例列表（和 ProjectDetailPage 一致的交互）
 *  - 用例编辑：name / description / preconditions / steps / expected / priority / tags
 *  - 单点勾结果：Mark 按钮弹小对话框选 PASS/FAIL/BLOCKED/N.A. + 备注
 *  - 测试模式：toggle 进入后每行出现选择框，底部固定栏批量标记选中用例（一个 batch_id）
 *  - 状态过滤：按"最近一次执行状态"筛选（含 pending = 没勾过）
 *  - Excel 导入 / 导出 / 批次概览面板
 *
 * 设计选择：
 *  - 模块树：复用 contentApi.list(..., "functional")，但只取 type==="module" 的节点
 *  - 用例列表：用 functionalCasesApi.list（带 status filter + 分页 + functional_spec + latest_run）
 *  - 模块管理（建/重命名/删/移动）：本页支持基本的"新建/重命名"，复杂操作（移动模块）回 ProjectDetailPage
 *    —— 因为 functional 用例里嵌套层级一般不深，避免重复实现
 *  - 测试模式 batch_id 由后端生成（functionalCasesApi.newBatchId），UI 进入时一次拿到
 *  - 测试模式 + 状态过滤可叠加：先按"未执行"过滤出待勾用例，再批量勾
 */

// ---------------------------------------------------------------------------
// 状态常量 / 工具
// ---------------------------------------------------------------------------

/** 状态标签 + 视觉样式（颜色用 token，不写死 hex）。 */
const STATUS_META: Record<
  FunctionalRunStatus,
  { label: string; tone: string; ring: string; icon: React.ComponentType<{ className?: string }> }
> = {
  passed: {
    label: "通过",
    tone: "text-emerald-700 bg-emerald-50",
    ring: "ring-emerald-200",
    icon: CheckCircle2,
  },
  failed: {
    label: "失败",
    tone: "text-red-700 bg-red-50",
    ring: "ring-red-200",
    icon: XCircle,
  },
  blocked: {
    label: "阻塞",
    tone: "text-amber-700 bg-amber-50",
    ring: "ring-amber-200",
    icon: ShieldOff,
  },
  na: {
    label: "N.A.",
    tone: "text-slate-700 bg-slate-100",
    ring: "ring-slate-200",
    icon: MinusCircle,
  },
  pending: {
    label: "待执行",
    tone: "text-slate-600 bg-muted",
    ring: "ring-slate-200",
    icon: CircleHelp,
  },
};

/** 自动化 RunStatus 子集（不含 pending；勾结果只能勾这四个）。 */
const MARKABLE_STATUSES: Exclude<FunctionalRunStatus, "pending">[] = [
  "passed",
  "failed",
  "blocked",
  "na",
];

const PAGE_SIZE = 50;

/** 从 ?status= 解析多选；空 / "all" → undefined（不过滤）。 */
function parseStatusParam(raw: string | null): FunctionalRunStatus[] | undefined {
  if (!raw || raw === "all") return undefined;
  const parts = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean) as FunctionalRunStatus[];
  return parts.length ? parts : undefined;
}

function formatTime(iso?: string | null) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// zod schemas
// ---------------------------------------------------------------------------
const moduleSchema = z.object({
  name: z.string().trim().min(1, "请输入模块名").max(50, "最多 50 字"),
});
type ModuleFormValues = z.infer<typeof moduleSchema>;

/** 功能用例编辑表单。functional_spec 三段（preconditions/steps/expected）这里用文本框，提交前再切行。 */
const caseSchema = z.object({
  name: z.string().trim().min(1, "请输入用例名").max(100, "最多 100 字"),
  description: z.string().max(500).optional(),
  /** 多行文本，提交前 split("\n") */
  preconditions: z.string().optional(),
  steps: z.string().optional(),
  expected: z.string().optional(),
  priority: z.coerce.number().int().min(0).max(5).optional(),
  /** 逗号分隔，提交前 split(","). */
  tags: z.string().optional(),
  skip: z.boolean().optional(),
});
type CaseFormValues = z.infer<typeof caseSchema>;

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------
export function FunctionalCasesPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = parseStatusParam(searchParams.get("status"));
  const page = Number(searchParams.get("page") ?? "1") || 1;

  // 面包屑 —— 和 ProjectDetailPage 同款。空数组 = 项目根。
  const [breadcrumb, setBreadcrumb] = useState<
    { id: number | null; name: string }[]
  >([]);

  const currentParent = breadcrumb[breadcrumb.length - 1];
  const currentParentId = currentParent?.id ?? null;

  const projectQuery = useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => projectsApi.get(projectId),
    enabled: Number.isFinite(projectId),
  });

  // 模块树 —— 复用 contentApi.list("functional") 拿当前层 + 过滤模块部分
  const contentQuery = useQuery({
    queryKey: queryKeys.content(projectId, currentParentId, "functional"),
    queryFn: () => contentApi.list(projectId, currentParentId, "functional"),
    enabled: Number.isFinite(projectId),
  });

  // 用例列表 —— 只在"进了模块"以后才查（functional 用例必须挂模块下）
  // status 过滤是 URL 状态；翻页也走 URL，刷新 / 后退都能恢复
  const casesQuery = useQuery({
    queryKey: queryKeys.functionalCases({
      moduleId: currentParentId,
      status: statusFilter ?? null,
      page,
      pageSize: PAGE_SIZE,
    }),
    queryFn: () =>
      functionalCasesApi.list({
        moduleId: currentParentId ?? undefined,
        status: statusFilter,
        page,
        pageSize: PAGE_SIZE,
      }),
    enabled: Number.isFinite(projectId) && currentParentId !== null,
  });

  /** 用例列表 / 模块树都失效，badge 也跟着刷。 */
  const invalidateAll = () => {
    queryClient.invalidateQueries({
      queryKey: ["content", projectId, currentParentId],
    });
    queryClient.invalidateQueries({ queryKey: ["functional_cases"] });
    queryClient.invalidateQueries({
      queryKey: queryKeys.projectStackCounts(projectId),
    });
  };

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "操作失败";
    toast.error(msg);
  };

  // ------ Dialog 状态 ------
  const [moduleDialog, setModuleDialog] = useState<
    | { mode: "create"; parentId: number | null }
    | { mode: "rename"; moduleId: number; name: string }
    | null
  >(null);

  const [caseDialog, setCaseDialog] = useState<
    | { mode: "create"; moduleId: number }
    | { mode: "edit"; caseId: number }
    | null
  >(null);

  const [markingCase, setMarkingCase] = useState<FunctionalCase | null>(null);

  const [pendingDelete, setPendingDelete] = useState<
    | { kind: "module"; id: number; name: string }
    | { kind: "case"; id: number; name: string }
    | null
  >(null);

  const [historyCase, setHistoryCase] = useState<FunctionalCase | null>(null);

  const [batchesOpen, setBatchesOpen] = useState(false);

  // ------ 测试模式状态 ------
  // 进入测试模式后：每行出现选择框，底部固定操作栏；离开则清空选择 + batchId
  const [testMode, setTestMode] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // 测试模式开关：进入时拿一个新 batch_id；退出时清空
  const enterTestMode = async () => {
    try {
      const { batch_id } = await functionalCasesApi.newBatchId();
      setBatchId(batch_id);
      setSelected(new Set());
      setTestMode(true);
      toast.info(`已进入测试模式 · 批次 ${batch_id.slice(0, 8)}…`);
    } catch (e) {
      handleError(e);
    }
  };
  const exitTestMode = () => {
    setTestMode(false);
    setBatchId(null);
    setSelected(new Set());
  };

  // ------ Mutations ------
  const createModule = useMutation({
    mutationFn: (body: { project_id: number; parent_id: number | null; name: string }) =>
      modulesApi.create(body),
    onSuccess: () => {
      toast.success("模块已创建");
      invalidateAll();
      setModuleDialog(null);
    },
    onError: handleError,
  });

  const renameModule = useMutation({
    mutationFn: ({ id: mid, name }: { id: number; name: string }) =>
      modulesApi.rename(mid, name),
    onSuccess: () => {
      toast.success("模块已重命名");
      invalidateAll();
      setModuleDialog(null);
    },
    onError: handleError,
  });

  const deleteModule = useMutation({
    mutationFn: (mid: number) => modulesApi.remove(mid),
    onSuccess: () => {
      toast.success("模块已删除");
      invalidateAll();
      setPendingDelete(null);
    },
    onError: handleError,
  });

  const deleteCase = useMutation({
    mutationFn: (cid: number) => functionalCasesApi.remove(cid),
    onSuccess: () => {
      toast.success("用例已删除");
      invalidateAll();
      setPendingDelete(null);
    },
    onError: handleError,
  });

  // ------ 业务动作 ------
  const project = projectQuery.data;

  const handleEnterModule = (node: ContentNode) => {
    setBreadcrumb((prev) => [...prev, { id: node.id, name: node.name }]);
    // 切层级时清掉选择，避免跨模块选中混乱
    setSelected(new Set());
  };

  const handleJumpTo = (index: number) => {
    if (index < 0) setBreadcrumb([]);
    else setBreadcrumb((prev) => prev.slice(0, index + 1));
    setSelected(new Set());
  };

  const setQS = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "" || v === "all") next.delete(k);
      else next.set(k, v);
    }
    setSearchParams(next, { replace: true });
  };

  const handleStatusChange = (raw: string) => {
    setQS({ status: raw === "all" ? undefined : raw, page: undefined });
  };

  // ------ 渲染 ------
  if (!Number.isFinite(projectId)) {
    return <div className="p-8 text-sm text-destructive">非法的项目 ID。</div>;
  }

  // 模块/用例分别从两个查询拿 —— contentQuery 给模块，casesQuery 给用例
  const modules = (contentQuery.data ?? []).filter((n) => n.type === "module");
  const cases = casesQuery.data?.items ?? [];
  const totalCases = casesQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalCases / PAGE_SIZE));

  // 页内全选状态：选中数 = 当前页用例数 时勾上"全选"
  const allSelectedOnPage =
    cases.length > 0 && cases.every((c) => selected.has(c.id));
  const toggleSelectAll = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelectedOnPage) {
        cases.forEach((c) => next.delete(c.id));
      } else {
        cases.forEach((c) => next.add(c.id));
      }
      return next;
    });
  };
  const toggleSelect = (cid: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(cid)) next.delete(cid);
      else next.add(cid);
      return next;
    });
  };

  return (
    <div className="space-y-4 p-6 pb-24">
      {/* 顶栏：返回 + 面包屑 */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            onClick={() => navigate(`/projects/${projectId}?stack=functional`)}
            title="返回项目详情"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <Breadcrumb
            project={project?.name ?? "…"}
            trail={breadcrumb}
            onJump={handleJumpTo}
          />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setBatchesOpen(true)}
          >
            <ClipboardList className="h-4 w-4" />
            批次概览
          </Button>
          {testMode ? (
            <Button variant="default" size="sm" onClick={exitTestMode}>
              退出测试模式
            </Button>
          ) : (
            <Button variant="outline" size="sm" onClick={enterTestMode}>
              进入测试模式
            </Button>
          )}
        </div>
      </div>

      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            setModuleDialog({ mode: "create", parentId: currentParentId })
          }
        >
          <FolderPlus className="h-4 w-4" />
          新建{currentParentId === null ? "顶层模块" : "子模块"}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={currentParentId === null}
          onClick={() =>
            currentParentId !== null &&
            setCaseDialog({ mode: "create", moduleId: currentParentId })
          }
          title={
            currentParentId === null
              ? "用例必须挂在模块下 —— 先进入一个模块"
              : undefined
          }
        >
          <Plus className="h-4 w-4" />
          新建功能用例
        </Button>
        <ImportButton
          moduleId={currentParentId}
          disabled={currentParentId === null}
          onDone={invalidateAll}
        />
        <ExportButton projectId={projectId} moduleId={currentParentId} />

        {/* 状态过滤：仅在模块层级显示（项目根没有用例列表） */}
        {currentParentId !== null ? (
          <div className="ml-auto flex items-center gap-2">
            <Label className="text-xs text-muted-foreground">状态</Label>
            <Select
              value={(statusFilter && statusFilter.join(",")) || "all"}
              onValueChange={handleStatusChange}
            >
              <SelectTrigger className="h-8 w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                <SelectItem value="pending">待执行</SelectItem>
                <SelectItem value="passed">通过</SelectItem>
                <SelectItem value="failed">失败</SelectItem>
                <SelectItem value="blocked">阻塞</SelectItem>
                <SelectItem value="na">N.A.</SelectItem>
                <SelectItem value="failed,blocked">失败 + 阻塞</SelectItem>
              </SelectContent>
            </Select>
          </div>
        ) : null}
      </div>

      {/* 主区域：模块（永远展示）+ 用例（仅当 parentId !== null 时展示） */}
      {contentQuery.isLoading ? (
        <ListSkeleton />
      ) : contentQuery.error ? (
        <ErrorBox onRetry={() => contentQuery.refetch()} />
      ) : (
        <div className="space-y-6">
          {/* 模块行 */}
          <Section title={`子模块（${modules.length}）`}>
            {modules.length === 0 ? (
              <EmptyHint text="当前层级没有子模块" />
            ) : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {modules.map((m) => (
                  <ModuleRow
                    key={m.id}
                    node={m}
                    onEnter={() => handleEnterModule(m)}
                    onRename={() =>
                      setModuleDialog({
                        mode: "rename",
                        moduleId: m.id,
                        name: m.name,
                      })
                    }
                    onDelete={() =>
                      setPendingDelete({
                        kind: "module",
                        id: m.id,
                        name: m.name,
                      })
                    }
                  />
                ))}
              </div>
            )}
          </Section>

          {/* 用例行：仅在模块层显示 */}
          {currentParentId !== null ? (
            <Section title={`功能用例（${totalCases}）`}>
              {casesQuery.isLoading ? (
                <ListSkeleton />
              ) : casesQuery.error ? (
                <ErrorBox onRetry={() => casesQuery.refetch()} />
              ) : cases.length === 0 ? (
                <EmptyHint
                  text={
                    statusFilter
                      ? "当前过滤条件下没有用例"
                      : "本模块还没有功能用例 —— 点上面新建或导入"
                  }
                />
              ) : (
                <CaseList
                  cases={cases}
                  testMode={testMode}
                  selected={selected}
                  allSelectedOnPage={allSelectedOnPage}
                  onToggleSelectAll={toggleSelectAll}
                  onToggleSelect={toggleSelect}
                  onMark={(c) => setMarkingCase(c)}
                  onEdit={(c) => setCaseDialog({ mode: "edit", caseId: c.id })}
                  onDelete={(c) =>
                    setPendingDelete({ kind: "case", id: c.id, name: c.name })
                  }
                  onShowHistory={(c) => setHistoryCase(c)}
                />
              )}
              {totalCases > PAGE_SIZE ? (
                <div className="mt-4 flex items-center justify-between text-sm">
                  <div className="text-muted-foreground">
                    共 {totalCases} 条 · 第 {page} / {totalPages} 页
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={page <= 1}
                      onClick={() => setQS({ page: String(page - 1) })}
                    >
                      上一页
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={page >= totalPages}
                      onClick={() => setQS({ page: String(page + 1) })}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              ) : null}
            </Section>
          ) : null}
        </div>
      )}

      {/* 测试模式底栏 */}
      {testMode ? (
        <TestModeFooter
          batchId={batchId}
          selected={selected}
          cases={cases}
          onClear={() => setSelected(new Set())}
          onDone={() => {
            invalidateAll();
            // 完成一批后保留 batchId 不变，方便用户继续在同一批次内勾下一组
          }}
          onError={handleError}
        />
      ) : null}

      {/* Dialogs */}
      <ModuleDialog
        state={moduleDialog}
        onClose={() => setModuleDialog(null)}
        onSubmit={(values) => {
          if (!moduleDialog) return;
          if (moduleDialog.mode === "create") {
            createModule.mutate({
              project_id: projectId,
              parent_id: moduleDialog.parentId,
              name: values.name,
            });
          } else {
            renameModule.mutate({ id: moduleDialog.moduleId, name: values.name });
          }
        }}
        submitting={createModule.isPending || renameModule.isPending}
      />

      <FunctionalCaseDialog
        state={caseDialog}
        onClose={() => setCaseDialog(null)}
        onDone={() => {
          invalidateAll();
          setCaseDialog(null);
        }}
        onError={handleError}
      />

      <MarkDialog
        target={markingCase}
        batchId={null /* 单点勾不带 batch_id */}
        onClose={() => setMarkingCase(null)}
        onDone={() => {
          invalidateAll();
          setMarkingCase(null);
        }}
        onError={handleError}
      />

      <DeleteDialog
        target={pendingDelete}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => {
          if (!pendingDelete) return;
          if (pendingDelete.kind === "module") {
            deleteModule.mutate(pendingDelete.id);
          } else {
            deleteCase.mutate(pendingDelete.id);
          }
        }}
        submitting={deleteModule.isPending || deleteCase.isPending}
      />

      <HistoryDialog
        target={historyCase}
        onClose={() => setHistoryCase(null)}
      />

      <BatchesDialog
        projectId={projectId}
        open={batchesOpen}
        onClose={() => setBatchesOpen(false)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 面包屑
// ---------------------------------------------------------------------------
function Breadcrumb({
  project,
  trail,
  onJump,
}: {
  project: string;
  trail: { id: number | null; name: string }[];
  onJump: (index: number) => void;
}) {
  return (
    <nav className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
      <span className="text-muted-foreground">功能用例 ·</span>
      <button
        className="max-w-[12rem] truncate rounded px-1.5 py-0.5 font-medium hover:bg-accent"
        onClick={() => onJump(-1)}
      >
        {project}
      </button>
      {trail.map((seg, i) => (
        <span key={`${seg.id}-${i}`} className="flex min-w-0 items-center gap-1">
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <button
            className="max-w-[12rem] truncate rounded px-1.5 py-0.5 hover:bg-accent"
            onClick={() => onJump(i)}
          >
            {seg.name}
          </button>
        </span>
      ))}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Section / 模块行 / 用例列表
// ---------------------------------------------------------------------------
function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      {children}
    </section>
  );
}

function ModuleRow({
  node,
  onEnter,
  onRename,
  onDelete,
}: {
  node: ContentNode;
  onEnter: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card p-3 transition-colors hover:border-primary/40">
      <button
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
        onClick={onEnter}
      >
        <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="truncate text-sm font-medium">{node.name}</span>
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0">
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={onRename}>
            <Pencil className="mr-2 h-4 w-4" />
            重命名
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={onDelete}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="mr-2 h-4 w-4" />
            删除
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function CaseList({
  cases,
  testMode,
  selected,
  allSelectedOnPage,
  onToggleSelectAll,
  onToggleSelect,
  onMark,
  onEdit,
  onDelete,
  onShowHistory,
}: {
  cases: FunctionalCase[];
  testMode: boolean;
  selected: Set<number>;
  allSelectedOnPage: boolean;
  onToggleSelectAll: () => void;
  onToggleSelect: (id: number) => void;
  onMark: (c: FunctionalCase) => void;
  onEdit: (c: FunctionalCase) => void;
  onDelete: (c: FunctionalCase) => void;
  onShowHistory: (c: FunctionalCase) => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      {/* 头：测试模式时多一列全选；统一表头视觉 */}
      <div className="flex items-center gap-3 border-b bg-muted/40 px-4 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {testMode ? (
          <SelectToggle
            checked={allSelectedOnPage}
            onChange={onToggleSelectAll}
            ariaLabel="全选当前页"
          />
        ) : null}
        <div className="flex-1">用例</div>
        <div className="w-32 shrink-0">最近状态</div>
        <div className="w-44 shrink-0">最近执行</div>
        <div className="w-32 shrink-0 text-right">操作</div>
      </div>
      <ul className="divide-y">
        {cases.map((c) => (
          <CaseRow
            key={c.id}
            row={c}
            testMode={testMode}
            selected={selected.has(c.id)}
            onToggleSelect={() => onToggleSelect(c.id)}
            onMark={() => onMark(c)}
            onEdit={() => onEdit(c)}
            onDelete={() => onDelete(c)}
            onShowHistory={() => onShowHistory(c)}
          />
        ))}
      </ul>
    </div>
  );
}

function CaseRow({
  row,
  testMode,
  selected,
  onToggleSelect,
  onMark,
  onEdit,
  onDelete,
  onShowHistory,
}: {
  row: FunctionalCase;
  testMode: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onMark: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onShowHistory: () => void;
}) {
  const status: FunctionalRunStatus = row.latest_run?.status ?? "pending";
  const meta = STATUS_META[status];
  const Icon = meta.icon;

  return (
    <li
      className={cn(
        "flex items-start gap-3 px-4 py-3 transition-colors",
        selected ? "bg-accent/40" : "hover:bg-accent/20",
        row.skip && "opacity-60",
      )}
    >
      {testMode ? (
        <div className="pt-0.5">
          <SelectToggle
            checked={selected}
            onChange={onToggleSelect}
            ariaLabel={`选中用例 ${row.name}`}
          />
        </div>
      ) : null}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{row.name}</span>
          {row.priority != null ? (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
              P{row.priority}
            </span>
          ) : null}
          {row.skip ? (
            <span className="rounded border border-dashed px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">
              skip
            </span>
          ) : null}
        </div>
        {row.description ? (
          <div className="line-clamp-1 text-xs text-muted-foreground">
            {row.description}
          </div>
        ) : null}
        {row.tags?.length ? (
          <div className="flex flex-wrap gap-1">
            {row.tags.map((t) => (
              <span
                key={t}
                className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-secondary-foreground"
              >
                {t}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <div className="w-32 shrink-0">
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset",
            meta.tone,
            meta.ring,
          )}
        >
          <Icon className="h-3.5 w-3.5" />
          {meta.label}
        </span>
      </div>
      <div className="w-44 shrink-0 text-xs text-muted-foreground">
        {row.latest_run ? (
          <>
            <div>{formatTime(row.latest_run.executed_at)}</div>
            {row.latest_run.operator ? (
              <div className="truncate text-[11px]">
                by {row.latest_run.operator}
              </div>
            ) : null}
          </>
        ) : (
          "—"
        )}
      </div>
      <div className="flex w-32 shrink-0 items-center justify-end gap-1">
        <Button
          variant="outline"
          size="sm"
          onClick={onMark}
          title="勾结果"
        >
          标记
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-7 w-7">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onEdit}>
              <Pencil className="mr-2 h-4 w-4" />
              编辑
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onShowHistory}>
              <History className="mr-2 h-4 w-4" />
              历史记录
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={onDelete}
              className="text-destructive focus:text-destructive"
            >
              <Trash2 className="mr-2 h-4 w-4" />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </li>
  );
}

/**
 * 简易"勾选框"：项目里没有 @radix-ui/react-checkbox，
 * 用 button + aria-pressed 实现，视觉上是 4×4 方块带可选打勾。
 */
function SelectToggle({
  checked,
  onChange,
  ariaLabel,
}: {
  checked: boolean;
  onChange: () => void;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={checked}
      aria-label={ariaLabel}
      onClick={(e) => {
        e.stopPropagation();
        onChange();
      }}
      className={cn(
        "flex h-4 w-4 items-center justify-center rounded border transition-colors",
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-input bg-background hover:border-primary/60",
      )}
    >
      {checked ? <CheckCircle2 className="h-3 w-3" /> : null}
    </button>
  );
}

// ---------------------------------------------------------------------------
// 测试模式底栏
// ---------------------------------------------------------------------------
function TestModeFooter({
  batchId,
  selected,
  cases,
  onClear,
  onDone,
  onError,
}: {
  batchId: string | null;
  selected: Set<number>;
  cases: FunctionalCase[];
  onClear: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [pendingStatus, setPendingStatus] = useState<
    Exclude<FunctionalRunStatus, "pending"> | null
  >(null);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const visibleSelected = cases.filter((c) => selected.has(c.id));

  const submit = async (status: Exclude<FunctionalRunStatus, "pending">) => {
    if (!batchId) {
      onError(new Error("批次 ID 未生成"));
      return;
    }
    if (visibleSelected.length === 0) {
      toast.info("没有选中用例");
      return;
    }
    setSubmitting(true);
    setPendingStatus(status);
    try {
      const items: FunctionalBatchItem[] = visibleSelected.map((c) => ({
        case_id: c.id,
        status,
        note: note || undefined,
      }));
      const res = await functionalCasesApi.batchMark({
        batch_id: batchId,
        items,
      });
      const ok = res.created;
      const failed = res.errors.length;
      toast.success(
        failed > 0
          ? `已标记 ${ok} 条，失败 ${failed} 条`
          : `已标记 ${ok} 条`,
      );
      setNote("");
      onClear();
      onDone();
    } catch (e) {
      onError(e);
    } finally {
      setSubmitting(false);
      setPendingStatus(null);
    }
  };

  return (
    <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 px-6 py-3 shadow-lg backdrop-blur">
      <div className="flex flex-wrap items-center gap-3">
        <div className="text-sm">
          已选 <span className="font-semibold">{selected.size}</span> 条
          {batchId ? (
            <span className="ml-2 text-xs text-muted-foreground">
              批次 {batchId.slice(0, 8)}…
            </span>
          ) : null}
        </div>
        <Input
          className="h-8 w-72"
          placeholder="批量备注（可选）"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          disabled={submitting}
        />
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {MARKABLE_STATUSES.map((s) => {
            const meta = STATUS_META[s];
            const Icon = meta.icon;
            return (
              <Button
                key={s}
                size="sm"
                variant={s === "passed" ? "default" : "outline"}
                disabled={submitting || selected.size === 0}
                onClick={() => submit(s)}
              >
                {pendingStatus === s ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
                标记为{meta.label}
              </Button>
            );
          })}
          <Button
            size="sm"
            variant="ghost"
            onClick={onClear}
            disabled={submitting}
          >
            清空选择
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 模块对话框（创建 / 重命名）
// ---------------------------------------------------------------------------
function ModuleDialog({
  state,
  onClose,
  onSubmit,
  submitting,
}: {
  state:
    | { mode: "create"; parentId: number | null }
    | { mode: "rename"; moduleId: number; name: string }
    | null;
  onClose: () => void;
  onSubmit: (values: ModuleFormValues) => void;
  submitting: boolean;
}) {
  const form = useForm<ModuleFormValues>({
    resolver: zodResolver(moduleSchema),
    defaultValues: { name: "" },
  });

  useEffect(() => {
    if (!state) return;
    form.reset({ name: state.mode === "rename" ? state.name : "" });
  }, [state, form]);

  if (!state) return null;
  const isRename = state.mode === "rename";

  return (
    <Dialog open={!!state} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isRename ? "重命名模块" : "新建模块"}</DialogTitle>
          <DialogDescription>
            {isRename ? "改一下名字" : "在当前层级下新建一个模块"}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={form.handleSubmit(onSubmit)}
        >
          <div className="space-y-1">
            <Label htmlFor="module-name">模块名</Label>
            <Input id="module-name" {...form.register("name")} autoFocus />
            {form.formState.errors.name ? (
              <div className="text-xs text-destructive">
                {form.formState.errors.name.message}
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {isRename ? "保存" : "创建"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 功能用例编辑对话框
// ---------------------------------------------------------------------------
function FunctionalCaseDialog({
  state,
  onClose,
  onDone,
  onError,
}: {
  state:
    | { mode: "create"; moduleId: number }
    | { mode: "edit"; caseId: number }
    | null;
  onClose: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const isEdit = state?.mode === "edit";
  // 编辑态拉详情；创建态用空表单
  const detailQuery = useQuery({
    queryKey: state?.mode === "edit" ? queryKeys.functionalCase(state.caseId) : ["fc-noop"],
    queryFn: () =>
      functionalCasesApi.get(state?.mode === "edit" ? state.caseId : -1),
    enabled: !!state && state.mode === "edit",
    staleTime: 0,
  });

  const detail = detailQuery.data;

  const form = useForm<CaseFormValues>({
    resolver: zodResolver(caseSchema),
    defaultValues: {
      name: "",
      description: "",
      preconditions: "",
      steps: "",
      expected: "",
      priority: 3,
      tags: "",
      skip: false,
    },
  });

  useEffect(() => {
    if (!state) return;
    if (state.mode === "create") {
      form.reset({
        name: "",
        description: "",
        preconditions: "",
        steps: "",
        expected: "",
        priority: 3,
        tags: "",
        skip: false,
      });
    } else if (detail) {
      const spec = detail.functional_spec ?? {
        preconditions: [],
        steps: [],
        expected: null,
      };
      form.reset({
        name: detail.name,
        description: detail.description ?? "",
        preconditions: spec.preconditions.join("\n"),
        steps: spec.steps.join("\n"),
        expected: spec.expected ?? "",
        priority: detail.priority ?? 3,
        tags: (detail.tags ?? []).join(","),
        skip: detail.skip,
      });
    }
  }, [state, detail, form]);

  const createMutation = useMutation({
    mutationFn: (body: CaseFormValues) => {
      if (state?.mode !== "create") return Promise.reject(new Error("invalid"));
      const spec: FunctionalSpec = {
        preconditions: splitLines(body.preconditions),
        steps: splitLines(body.steps),
        expected: body.expected?.trim() || null,
      };
      return functionalCasesApi.create({
        module_id: state.moduleId,
        name: body.name.trim(),
        description: body.description?.trim() || null,
        skip: body.skip ?? false,
        priority: body.priority ?? null,
        tags: splitTags(body.tags),
        functional_spec: spec,
      });
    },
    onSuccess: () => {
      toast.success("用例已创建");
      onDone();
    },
    onError,
  });

  const updateMutation = useMutation({
    mutationFn: (body: CaseFormValues) => {
      if (state?.mode !== "edit") return Promise.reject(new Error("invalid"));
      const spec: FunctionalSpec = {
        preconditions: splitLines(body.preconditions),
        steps: splitLines(body.steps),
        expected: body.expected?.trim() || null,
      };
      return functionalCasesApi.update(state.caseId, {
        name: body.name.trim(),
        description: body.description?.trim() || null,
        skip: body.skip,
        priority: body.priority ?? null,
        tags: splitTags(body.tags),
        functional_spec: spec,
      });
    },
    onSuccess: () => {
      toast.success("用例已更新");
      onDone();
    },
    onError,
  });

  const submit = (values: CaseFormValues) => {
    if (state?.mode === "create") createMutation.mutate(values);
    else updateMutation.mutate(values);
  };

  const submitting = createMutation.isPending || updateMutation.isPending;
  const loading = isEdit && detailQuery.isLoading;

  return (
    <Dialog open={!!state} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "编辑功能用例" : "新建功能用例"}
          </DialogTitle>
          <DialogDescription>
            人工执行的功能用例：写明前置条件 / 步骤 / 预期，执行时勾选结果
          </DialogDescription>
        </DialogHeader>
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : (
          <form
            className="space-y-3 max-h-[65vh] overflow-y-auto pr-1"
            onSubmit={form.handleSubmit(submit)}
          >
            <Field
              label="用例名"
              error={form.formState.errors.name?.message}
            >
              <Input {...form.register("name")} autoFocus />
            </Field>
            <Field
              label="描述"
              error={form.formState.errors.description?.message}
            >
              <Textarea
                {...form.register("description")}
                rows={2}
                placeholder="一句话简介"
              />
            </Field>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="优先级（0-5）">
                <Input
                  type="number"
                  min={0}
                  max={5}
                  {...form.register("priority")}
                />
              </Field>
              <Field label="标签（逗号分隔）">
                <Input
                  {...form.register("tags")}
                  placeholder="冒烟,登录"
                />
              </Field>
            </div>
            <Field label="前置条件（每行一条）">
              <Textarea
                {...form.register("preconditions")}
                rows={3}
                placeholder="已注册账号&#10;已登录"
              />
            </Field>
            <Field label="操作步骤（每行一步）">
              <Textarea
                {...form.register("steps")}
                rows={5}
                placeholder="打开登录页&#10;输入账号密码&#10;点击登录"
              />
            </Field>
            <Field label="预期结果">
              <Textarea
                {...form.register("expected")}
                rows={2}
                placeholder="跳转到首页，欢迎语包含用户名"
              />
            </Field>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                取消
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : null}
                {isEdit ? "保存" : "创建"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
      {error ? <div className="text-xs text-destructive">{error}</div> : null}
    </div>
  );
}

function splitLines(text?: string): string[] {
  if (!text) return [];
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function splitTags(text?: string): string[] {
  if (!text) return [];
  return text
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// ---------------------------------------------------------------------------
// 单点 Mark 对话框
// ---------------------------------------------------------------------------
function MarkDialog({
  target,
  batchId,
  onClose,
  onDone,
  onError,
}: {
  target: FunctionalCase | null;
  batchId: string | null;
  onClose: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const [status, setStatus] = useState<Exclude<FunctionalRunStatus, "pending">>(
    "passed",
  );
  const [actualResult, setActualResult] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (target) {
      setStatus("passed");
      setActualResult("");
      setNote("");
    }
  }, [target]);

  const mutation = useMutation({
    mutationFn: () => {
      if (!target) return Promise.reject(new Error("invalid"));
      return functionalCasesApi.mark(target.id, {
        status,
        actual_result: actualResult || null,
        note: note || null,
        batch_id: batchId,
      });
    },
    onSuccess: () => {
      toast.success("已记录结果");
      onDone();
    },
    onError,
  });

  if (!target) return null;
  const spec = target.functional_spec ?? { preconditions: [], steps: [], expected: null };

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>勾结果 · {target.name}</DialogTitle>
          <DialogDescription>记录这次人工执行的实际结果</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          {/* 用例摘要：用户勾的时候把步骤 / 预期摆在眼前，避免来回切 */}
          <SpecPreview spec={spec} />
          <div className="space-y-1">
            <Label className="text-xs">结果</Label>
            <div className="flex flex-wrap gap-2">
              {MARKABLE_STATUSES.map((s) => {
                const meta = STATUS_META[s];
                const Icon = meta.icon;
                const active = status === s;
                return (
                  <Button
                    key={s}
                    type="button"
                    size="sm"
                    variant={active ? "default" : "outline"}
                    onClick={() => setStatus(s)}
                  >
                    <Icon className="h-4 w-4" />
                    {meta.label}
                  </Button>
                );
              })}
            </div>
          </div>
          <Field label="实际结果">
            <Textarea
              rows={2}
              value={actualResult}
              onChange={(e) => setActualResult(e.target.value)}
              placeholder="实际看到的现象（可空）"
            />
          </Field>
          <Field label="备注">
            <Textarea
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="额外信息（可空）"
            />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            保存结果
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SpecPreview({ spec }: { spec: FunctionalSpec }) {
  return (
    <div className="space-y-2 rounded-lg border bg-muted/40 p-3 text-sm">
      {spec.preconditions.length ? (
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            前置
          </div>
          <ul className="ml-4 list-disc text-sm">
            {spec.preconditions.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {spec.steps.length ? (
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            步骤
          </div>
          <ol className="ml-4 list-decimal text-sm">
            {spec.steps.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ol>
        </div>
      ) : null}
      {spec.expected ? (
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            预期
          </div>
          <p className="text-sm">{spec.expected}</p>
        </div>
      ) : null}
      {spec.preconditions.length === 0 &&
      spec.steps.length === 0 &&
      !spec.expected ? (
        <p className="text-xs text-muted-foreground">用例尚未填写步骤</p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 删除确认
// ---------------------------------------------------------------------------
function DeleteDialog({
  target,
  onClose,
  onConfirm,
  submitting,
}: {
  target:
    | { kind: "module"; id: number; name: string }
    | { kind: "case"; id: number; name: string }
    | null;
  onClose: () => void;
  onConfirm: () => void;
  submitting: boolean;
}) {
  if (!target) return null;
  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            删除{target.kind === "module" ? "模块" : "用例"}？
          </DialogTitle>
          <DialogDescription>
            {target.kind === "module"
              ? "模块下所有子模块和用例都会被一并删除，且不可恢复。"
              : "用例的所有历史 run 记录会一并删除，且不可恢复。"}
          </DialogDescription>
        </DialogHeader>
        <p className="text-sm">
          目标：<span className="font-medium">{target.name}</span>
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="destructive"
            onClick={onConfirm}
            disabled={submitting}
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            确认删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 历史记录对话框
// ---------------------------------------------------------------------------
function HistoryDialog({
  target,
  onClose,
}: {
  target: FunctionalCase | null;
  onClose: () => void;
}) {
  const runsQuery = useQuery({
    queryKey: target ? queryKeys.functionalRuns(target.id) : ["fc-runs-noop"],
    queryFn: () => functionalCasesApi.runs(target!.id, 50),
    enabled: !!target,
    staleTime: 0,
  });

  if (!target) return null;
  const runs = runsQuery.data ?? [];

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>执行历史 · {target.name}</DialogTitle>
          <DialogDescription>最近 50 次"勾结果"，倒序</DialogDescription>
        </DialogHeader>
        <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-1">
          {runsQuery.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : runs.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              还没有人执行过这条用例
            </p>
          ) : (
            <ul className="divide-y rounded-lg border bg-card text-sm">
              {runs.map((r) => {
                const meta = STATUS_META[r.status];
                const Icon = meta.icon;
                return (
                  <li key={r.id} className="flex items-start gap-3 px-3 py-2">
                    <span
                      className={cn(
                        "mt-0.5 inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset",
                        meta.tone,
                        meta.ring,
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                      {meta.label}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs text-muted-foreground">
                        {formatTime(r.executed_at)}
                        {r.operator ? ` · by ${r.operator}` : ""}
                        {r.batch_id ? (
                          <span className="ml-2 rounded bg-muted px-1.5 py-0.5 font-mono">
                            {r.batch_id.slice(0, 8)}…
                          </span>
                        ) : null}
                      </div>
                      {r.actual_result ? (
                        <div className="text-sm">{r.actual_result}</div>
                      ) : null}
                      {r.note ? (
                        <div className="text-xs italic text-muted-foreground">
                          {r.note}
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 批次概览对话框
// ---------------------------------------------------------------------------
function BatchesDialog({
  projectId,
  open,
  onClose,
}: {
  projectId: number;
  open: boolean;
  onClose: () => void;
}) {
  const batchesQuery = useQuery({
    queryKey: queryKeys.functionalBatches(projectId),
    queryFn: () => functionalCasesApi.batches(projectId, 30),
    enabled: open,
    staleTime: 0,
  });

  const batches: FunctionalBatchSummary[] = batchesQuery.data ?? [];

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>批次概览</DialogTitle>
          <DialogDescription>
            "测试模式"下批量勾的批次聚合，最近 30 个；单点勾不在内
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto">
          {batchesQuery.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : batches.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              本项目还没有任何"测试模式"批次
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-2 py-2">批次</th>
                  <th className="px-2 py-2">开始</th>
                  <th className="px-2 py-2">结束</th>
                  <th className="px-2 py-2 text-right">总数</th>
                  <th className="px-2 py-2 text-right">通过</th>
                  <th className="px-2 py-2 text-right">失败</th>
                  <th className="px-2 py-2 text-right">阻塞</th>
                  <th className="px-2 py-2 text-right">N.A.</th>
                  <th className="px-2 py-2 text-right">通过率</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {batches.map((b) => (
                  <tr key={b.batch_id}>
                    <td className="px-2 py-2 font-mono text-xs">
                      {b.batch_id.slice(0, 12)}…
                    </td>
                    <td className="px-2 py-2 text-xs text-muted-foreground">
                      {formatTime(b.started_at)}
                    </td>
                    <td className="px-2 py-2 text-xs text-muted-foreground">
                      {formatTime(b.finished_at)}
                    </td>
                    <td className="px-2 py-2 text-right">{b.total}</td>
                    <td className="px-2 py-2 text-right text-emerald-700">
                      {b.passed}
                    </td>
                    <td className="px-2 py-2 text-right text-red-700">
                      {b.failed}
                    </td>
                    <td className="px-2 py-2 text-right text-amber-700">
                      {b.blocked}
                    </td>
                    <td className="px-2 py-2 text-right text-slate-600">
                      {b.na}
                    </td>
                    <td className="px-2 py-2 text-right font-medium">
                      {Math.round(b.pass_rate * 100)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 导入 / 导出按钮
// ---------------------------------------------------------------------------
function ImportButton({
  moduleId,
  disabled,
  onDone,
}: {
  moduleId: number | null;
  disabled?: boolean;
  onDone: () => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);

  const handleFile = async (file: File | null) => {
    if (!file || moduleId == null) return;
    setBusy(true);
    try {
      const res = await functionalCasesApi.importExcel(moduleId, file);
      const failed = res.errors.length;
      toast.success(
        failed > 0
          ? `已导入 ${res.imported} 条，失败 ${failed} 条`
          : `已导入 ${res.imported} 条`,
      );
      if (failed > 0) {
        // 失败行简单 toast 列前 3 条；详细可去后端日志
        const sample = res.errors.slice(0, 3).map((e) => `行 ${e.row}: ${e.error}`);
        toast.message("部分行失败", { description: sample.join("；") });
      }
      onDone();
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : "导入失败";
      toast.error(msg);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls"
        className="hidden"
        onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
      />
      <Button
        variant="outline"
        size="sm"
        disabled={disabled || busy}
        onClick={() => inputRef.current?.click()}
        title={
          disabled ? "请先进入一个模块再导入" : "Excel 模板：name/description/preconditions/steps/expected/priority/tags"
        }
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
        导入
      </Button>
    </>
  );
}

function ExportButton({
  projectId,
  moduleId,
}: {
  projectId: number;
  moduleId: number | null;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await functionalCasesApi.exportExcel({ projectId, moduleId });
        } catch (e) {
          const msg =
            e instanceof ApiError
              ? e.message
              : e instanceof Error
                ? e.message
                : "导出失败";
          toast.error(msg);
        } finally {
          setBusy(false);
        }
      }}
      title={
        moduleId == null
          ? "导出整个项目的功能用例"
          : "导出当前模块及其子模块"
      }
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
      导出
    </Button>
  );
}

// ---------------------------------------------------------------------------
// 兜底视图
// ---------------------------------------------------------------------------
function ListSkeleton() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-12 w-full" />
      <Skeleton className="h-12 w-full" />
      <Skeleton className="h-12 w-full" />
    </div>
  );
}

function ErrorBox({ onRetry }: { onRetry: () => void }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 py-6">
        <Info className="h-4 w-4 text-destructive" />
        <span className="text-sm">加载失败</span>
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </CardContent>
    </Card>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <Card>
      <CardContent className="py-8 text-center text-sm text-muted-foreground">
        {text}
      </CardContent>
    </Card>
  );
}
