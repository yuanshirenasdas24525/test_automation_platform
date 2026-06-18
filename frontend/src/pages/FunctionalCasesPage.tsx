import { Fragment, useEffect, useRef, useState, useCallback } from "react";
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
  FunctionalCase,
  FunctionalCaseRun,
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

/** 优先级颜色表。P1=红 P2=橙 P3=绿 P4=蓝 P5=灰 */
const PRIORITY_META: Record<number, { label: string; tone: string; ring: string }> = {
  1: { label: "P1", tone: "bg-red-100 text-red-700", ring: "ring-red-300" },
  2: { label: "P2", tone: "bg-orange-100 text-orange-700", ring: "ring-orange-300" },
  3: { label: "P3", tone: "bg-emerald-100 text-emerald-700", ring: "ring-emerald-300" },
  4: { label: "P4", tone: "bg-blue-100 text-blue-700", ring: "ring-blue-300" },
  5: { label: "P5", tone: "bg-gray-100 text-gray-600", ring: "ring-gray-300" },
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
export function FunctionalCasesPage({ embedded = false }: { embedded?: boolean } = {}) {
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

  const [historyOpen, setHistoryOpen] = useState(false);

  // ------ 详情弹窗 ------
  const [detailCase, setDetailCase] = useState<FunctionalCase | null>(null);

  // ------ 测试模式状态 ------
  // 进入测试模式后：每行出现选择框，底部固定操作栏；离开则清空选择 + batchId
  // 默认进入测试模式（页面一加载或进入模块后自动拿 batch_id）
  const [testMode, setTestMode] = useState(true);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // ------ 快速编辑模式状态 ------
  const [quickEditMode, setQuickEditMode] = useState(false);
  const [newRows, setNewRows] = useState<
    Array<{ tempId: string; belowCaseId?: number }>
  >([]);

  // 测试模式开关：进入时拿一个新 batch_id；退出时清空
  const enterTestMode = async () => {
    try {
      const { batch_id } = await functionalCasesApi.newBatchId();
      setBatchId(batch_id);
      setSelected(new Set());
      setTestMode(true);
      setQuickEditMode(false);
      setNewRows([]);
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

  const enterQuickEditMode = () => {
    setQuickEditMode(true);
    setTestMode(false);
    setBatchId(null);
    setSelected(new Set());
    // 进入快速编辑时，底部先放一行空的快速输入
    setNewRows([{ tempId: genTempId() }]);
  };
  const exitQuickEditMode = () => {
    setQuickEditMode(false);
    setNewRows([]);
  };

  const addNewRow = useCallback((belowCaseId?: number) => {
    const tempId = genTempId();
    setNewRows((prev) => {
      if (belowCaseId === undefined) return [...prev, { tempId }];
      const idx = prev.findIndex((r) => r.belowCaseId === belowCaseId);
      if (idx >= 0) {
        const next = [...prev];
        next.splice(idx + 1, 0, { tempId, belowCaseId });
        return next;
      }
      return [...prev, { tempId, belowCaseId }];
    });
  }, []);

  const removeNewRow = useCallback((tempId: string) => {
    setNewRows((prev) => prev.filter((r) => r.tempId !== tempId));
  }, []);

  // 底部快速输入：在“最后一行”里输入第一个字符时，自动追加一行新的空快速输入
  const handleFirstInput = useCallback((tempId: string) => {
    setNewRows((prev) => {
      const bottoms = prev.filter((r) => r.belowCaseId === undefined);
      const last = bottoms[bottoms.length - 1];
      if (last && last.tempId === tempId) {
        return [...prev, { tempId: genTempId() }];
      }
      return prev;
    });
  }, []);

  // 进入快速编辑后，若底部没有任何空的快速输入行，补一行（保证永远有一个录入入口）
  useEffect(() => {
    if (!quickEditMode) return;
    setNewRows((prev) =>
      prev.some((r) => r.belowCaseId === undefined)
        ? prev
        : [...prev, { tempId: genTempId() }],
    );
  }, [quickEditMode]);

  // 默认测试模式：页面加载 / 进入模块后自动拿 batch_id
  useEffect(() => {
    if (testMode && !batchId) {
      functionalCasesApi.newBatchId()
        .then(({ batch_id }) => {
          setBatchId(batch_id);
          toast.info(`已进入测试模式 · 批次 ${batch_id.slice(0, 8)}…`);
        })
        .catch(handleError);
    }
  }, [testMode, batchId]);

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
    // 乐观更新：先把这条从所有用例列表缓存里抹掉，UI 立即生效，不用等 refetch
    onMutate: async (cid: number) => {
      await queryClient.cancelQueries({ queryKey: ["functional_cases"] });
      const snapshots = queryClient.getQueriesData<{ items: FunctionalCase[]; total: number }>({
        queryKey: ["functional_cases"],
      });
      for (const [key, data] of snapshots) {
        if (data?.items) {
          queryClient.setQueryData(key, {
            ...data,
            items: data.items.filter((c) => c.id !== cid),
            total: Math.max(0, (data.total ?? 1) - 1),
          });
        }
      }
      setPendingDelete(null);
      return { snapshots };
    },
    onError: (err, _cid, ctx) => {
      ctx?.snapshots?.forEach(([key, data]) => queryClient.setQueryData(key, data));
      handleError(err);
    },
    onSuccess: () => {
      toast.success("用例已删除");
    },
    onSettled: () => invalidateAll(),
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
    <div className={cn("space-y-4 pb-24", !embedded && "p-6")}>
      {/* 顶栏：返回 + 面包屑 */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2">
          {!embedded ? (
            <Button
              variant="ghost"
              size="icon"
              className="shrink-0"
              onClick={() => navigate(`/projects/${projectId}?stack=functional`)}
              title="返回项目详情"
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          ) : null}
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
            onClick={() => setHistoryOpen(true)}
          >
            <History className="h-4 w-4" />
            历史记录
          </Button>
          <div className="inline-flex rounded-md border overflow-hidden">
            <button
              className={cn(
                "px-3 py-1.5 text-xs font-medium transition-colors",
                testMode ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-accent",
              )}
              onClick={testMode ? exitTestMode : enterTestMode}
            >
              测试模式
            </button>
            <button
              className={cn(
                "px-3 py-1.5 text-xs font-medium transition-colors border-l",
                quickEditMode ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-accent",
              )}
              onClick={quickEditMode ? exitQuickEditMode : enterQuickEditMode}
            >
              快速编辑
            </button>
          </div>
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
          {/* 模块行：进入模块后若当前层级没有子模块，则不渲染这一块（避免用例列表上方出现空区块） */}
          {currentParentId === null || modules.length > 0 ? (
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
          ) : null}

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
                  quickEditMode={quickEditMode}
                  selected={selected}
                  newRows={newRows}
                  allSelectedOnPage={allSelectedOnPage}
                  moduleId={currentParentId}
                  onToggleSelectAll={toggleSelectAll}
                  onToggleSelect={toggleSelect}
                  onMark={(c) => setMarkingCase(c)}
                  onEdit={(c) => setCaseDialog({ mode: "edit", caseId: c.id })}
                  onDelete={(c) =>
                    setPendingDelete({ kind: "case", id: c.id, name: c.name })
                  }
                  onShowHistory={(c) => setHistoryCase(c)}
                  onOpenDetail={(c) => setDetailCase(c)}
                  onAddNewRow={addNewRow}
                  onRemoveNewRow={removeNewRow}
                  onFirstInput={handleFirstInput}
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

      <CaseDetailDialog
        target={detailCase}
        onClose={() => setDetailCase(null)}
        onMark={(c) => {
          setDetailCase(null);
          setMarkingCase(c);
        }}
        onEdit={(c) => {
          setDetailCase(null);
          setCaseDialog({ mode: "edit", caseId: c.id });
        }}
        onDelete={(c) => {
          setDetailCase(null);
          setPendingDelete({ kind: "case", id: c.id, name: c.name });
        }}
        onShowHistory={(c) => {
          setDetailCase(null);
          setHistoryCase(c);
        }}
      />

      <HistoryDialog
        target={historyCase}
        onClose={() => setHistoryCase(null)}
      />

      <ModuleHistoryDialog
        moduleId={currentParentId}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
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
  quickEditMode,
  selected,
  newRows,
  allSelectedOnPage,
  moduleId,
  onToggleSelectAll,
  onToggleSelect,
  onMark,
  onEdit,
  onDelete,
  onShowHistory,
  onOpenDetail,
  onAddNewRow,
  onRemoveNewRow,
  onFirstInput,
}: {
  cases: FunctionalCase[];
  testMode: boolean;
  quickEditMode: boolean;
  selected: Set<number>;
  newRows: Array<{ tempId: string; belowCaseId?: number }>;
  allSelectedOnPage: boolean;
  moduleId: number | null;
  onToggleSelectAll: () => void;
  onToggleSelect: (id: number) => void;
  onMark: (c: FunctionalCase) => void;
  onEdit: (c: FunctionalCase) => void;
  onDelete: (c: FunctionalCase) => void;
  onShowHistory: (c: FunctionalCase) => void;
  onOpenDetail: (c: FunctionalCase) => void;
  onAddNewRow: (belowCaseId?: number) => void;
  onRemoveNewRow: (tempId: string) => void;
  onFirstInput: (tempId: string) => void;
}) {
  const cols = testMode ? 8 : 7;
  // 底部快速输入行（belowCaseId 为空的都算底部行；最后一行是“trailing”录入入口）
  const bottomRows = newRows.filter((nr) => nr.belowCaseId === undefined);
  return (
    <div className="overflow-hidden rounded-md border">
      <table className="w-full text-sm border-collapse">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            {testMode ? (
              <th className="w-10 border border-border px-1 py-2 text-center align-middle">
                <SelectToggle checked={allSelectedOnPage} onChange={onToggleSelectAll} ariaLabel="全选当前页" />
              </th>
            ) : null}
            <th className="border border-border px-3 py-2 text-left font-medium">用例名称</th>
            <th className="border border-border px-3 py-2 text-left font-medium w-[12%]">前置条件</th>
            <th className="border border-border px-3 py-2 text-left font-medium w-[40%]">操作步骤</th>
            <th className="border border-border px-3 py-2 text-left font-medium w-[12%]">预期结果</th>
            <th className="border border-border px-3 py-2 text-center font-medium w-20">优先级</th>
            <th className="border border-border px-3 py-2 text-center font-medium w-28">最近状态</th>
            <th className="border border-border px-3 py-2 text-right font-medium w-36">操作</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => {
            const pendingNew = newRows.find((nr) => nr.belowCaseId === c.id);
            return (
              <Fragment key={c.id}>
                <CaseRow
                  row={c}
                  testMode={testMode}
                  quickEditMode={quickEditMode}
                  selected={selected.has(c.id)}
                  onToggleSelect={() => onToggleSelect(c.id)}
                  onMark={() => onMark(c)}
                  onEdit={() => onEdit(c)}
                  onDelete={() => onDelete(c)}
                  onShowHistory={() => onShowHistory(c)}
                  onOpenDetail={() => onOpenDetail(c)}
                />
                {pendingNew ? (
                  <NewCaseRow
                    key={pendingNew.tempId}
                    moduleId={moduleId!}
                    onCreated={() => onRemoveNewRow(pendingNew.tempId)}
                    onRemove={() => onRemoveNewRow(pendingNew.tempId)}
                  />
                ) : null}
                {quickEditMode && (
                  <InsertRowBelow key={`ins-${c.id}`} onInsert={() => onAddNewRow(c.id)} colSpan={cols} />
                )}
              </Fragment>
            );
          })}
          {quickEditMode && moduleId != null
            ? bottomRows.map((nr, idx) => (
                <NewCaseRow
                  key={nr.tempId}
                  moduleId={moduleId}
                  isTrailing={idx === bottomRows.length - 1}
                  onFirstInput={() => onFirstInput(nr.tempId)}
                  onCreated={() => onRemoveNewRow(nr.tempId)}
                  onRemove={() => onRemoveNewRow(nr.tempId)}
                />
              ))
            : null}
        </tbody>
      </table>
    </div>
  );
}

function CaseRow({
  row,
  testMode,
  quickEditMode,
  selected,
  onToggleSelect,
  onMark,
  onEdit,
  onDelete,
  onShowHistory,
  onOpenDetail,
}: {
  row: FunctionalCase;
  testMode: boolean;
  quickEditMode: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onMark: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onShowHistory: () => void;
  onOpenDetail: () => void;
}) {
  const queryClient = useQueryClient();
  const status: FunctionalRunStatus = row.latest_run?.status ?? "pending";
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  const spec = row.functional_spec ?? { preconditions: [], steps: [], expected: null };

  const saveField = async (field: string, rawValue: string) => {
    let data: Record<string, unknown>;
    switch (field) {
      case "name":
        data = { name: rawValue };
        break;
      case "priority":
        data = { priority: rawValue ? Number(rawValue) : null };
        break;
      case "preconditions":
        data = { functional_spec: { ...spec, preconditions: splitLines(rawValue) } };
        break;
      case "steps":
        data = { functional_spec: { ...spec, steps: splitLines(rawValue) } };
        break;
      case "expected":
        data = { functional_spec: { ...spec, expected: rawValue || null } };
        break;
      default:
        return;
    }
    await functionalCasesApi.update(row.id, data);
    queryClient.invalidateQueries({ queryKey: ["functional_cases"] });
  };

  const stepsDisplay = spec.steps.length > 0
    ? spec.steps.map((s, i) => `${i + 1}. ${s}`).join("\n")
    : "";
  const preconditionsDisplay = spec.preconditions.length > 0
    ? spec.preconditions.map((s, i) => `${i + 1}. ${s}`).join("\n")
    : "";
  const expectedLines = splitLines(spec.expected ?? "");
  const expectedDisplay = expectedLines.length > 0
    ? expectedLines.map((s, i) => `${i + 1}. ${s}`).join("\n")
    : "";
  const pmeta = row.priority != null ? PRIORITY_META[row.priority] : null;

  return (
    <tr className={cn("hover:bg-accent/30", selected && "bg-accent/40", row.skip && "opacity-60")}>
      {testMode ? (
        <td className="border border-border px-1 py-2 text-center align-middle">
          <SelectToggle checked={selected} onChange={onToggleSelect} ariaLabel={`选中用例 ${row.name}`} />
        </td>
      ) : null}
      {/* 用例名称 */}
      <td className="border border-border px-3 py-2">
        <NameCell name={row.name} onOpenDetail={onOpenDetail} onSave={(v) => saveField("name", v)} quickEditMode={quickEditMode} />
      </td>
      {/* 前置条件 - 有序列表 */}
      <td className="border border-border px-3 py-2 align-top">
        {quickEditMode ? (
          <OrderedInlineInput value={spec.preconditions.join("\n")} onSave={(v) => saveField("preconditions", v)} />
        ) : (
          <div className="whitespace-pre-wrap text-xs text-muted-foreground break-words" title={preconditionsDisplay}>
            {preconditionsDisplay || <span className="text-muted-foreground/50">—</span>}
          </div>
        )}
      </td>
      {/* 操作步骤 - 有序列表 */}
      <td className="border border-border px-3 py-2 align-top">
        {quickEditMode ? (
          <OrderedInlineInput value={spec.steps.join("\n")} onSave={(v) => saveField("steps", v)} />
        ) : (
          <div className="whitespace-pre-wrap text-xs text-muted-foreground break-words" title={stepsDisplay}>
            {stepsDisplay || <span className="text-muted-foreground/50">—</span>}
          </div>
        )}
      </td>
      {/* 预期结果 - 有序列表 */}
      <td className="border border-border px-3 py-2 align-top">
        {quickEditMode ? (
          <OrderedInlineInput value={spec.expected ?? ""} onSave={(v) => saveField("expected", v)} />
        ) : (
          <div className="whitespace-pre-wrap text-xs text-muted-foreground break-words" title={expectedDisplay}>
            {expectedDisplay || <span className="text-muted-foreground/50">—</span>}
          </div>
        )}
      </td>
      {/* 优先级 */}
      <td className="border border-border px-3 py-2 text-center align-middle">
        {quickEditMode ? (
          <PrioritySelect value={row.priority} onSave={(v) => saveField("priority", v)} />
        ) : (
          pmeta ? (
            <span className={cn("inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset", pmeta.tone, pmeta.ring)}>
              {pmeta.label}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          )
        )}
      </td>
      {/* 最近状态 */}
      <td className="border border-border px-3 py-2 text-center align-middle">
        <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", meta.tone, meta.ring)}>
          <Icon className="h-3.5 w-3.5" />
          {meta.label}
        </span>
      </td>
      {/* 操作 */}
      <td className="border border-border px-3 py-2 text-right align-middle">
        <div className="inline-flex items-center gap-1">
          {testMode ? (
            <Button variant="outline" size="sm" onClick={onMark}>标记</Button>
          ) : quickEditMode ? (
            <>
              <Button variant="outline" size="sm" onClick={onMark}>标记</Button>
              <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive hover:text-destructive" onClick={onDelete} title="删除用例">
                <Trash2 className="h-4 w-4" />
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" size="sm" onClick={onMark}>标记</Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-7 w-7">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={onEdit}><Pencil className="mr-2 h-4 w-4" />编辑</DropdownMenuItem>
                  <DropdownMenuItem onClick={onShowHistory}><History className="mr-2 h-4 w-4" />历史记录</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={onDelete} className="text-destructive focus:text-destructive">
                    <Trash2 className="mr-2 h-4 w-4" />删除
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

// ===== NameCell: single click → detail dialog, double-click → inline edit =====
function NameCell({
  name,
  onOpenDetail,
  onSave,
  quickEditMode,
}: {
  name: string;
  onOpenDetail: () => void;
  onSave: (v: string) => Promise<void>;
  quickEditMode: boolean;
}) {
  const [editing, setEditing] = useState(false);
  // 进入行内编辑态：用受控 InlineInput，初始即处于编辑模式
  const [editValue, setEditValue] = useState(name);

  if (editing) {
    return (
      <input
        autoFocus
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onBlur={async () => {
          setEditing(false);
          if (editValue !== name) await onSave(editValue);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLElement).blur();
          if (e.key === "Escape") { setEditValue(name); setEditing(false); }
        }}
        className="w-full rounded border border-input bg-background px-1.5 py-0.5 text-xs outline-none ring-1 ring-ring"
      />
    );
  }

  return (
    <div
      className="truncate text-sm font-medium hover:text-primary cursor-pointer"
      onClick={() => {
        // 快速编辑模式：单击用例名直接进入行内编辑，不弹详情框
        if (quickEditMode) {
          setEditValue(name);
          setEditing(true);
        } else {
          onOpenDetail();
        }
      }}
      title={quickEditMode ? "单击编辑" : `查看"${name}"详情`}
    >
      {name}
    </div>
  );
}

// ===== OrderedInlineInput: 有序列表行内编辑（双击编辑、回车自动加序号、失焦保存） =====
// value 为纯文本（按行分隔、不含序号）；保存时同样回吐纯文本，序号只在显示/编辑时生成。
function OrderedInlineInput({
  value,
  onSave,
}: {
  value: string;
  onSave: (v: string) => Promise<void> | void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(""); // 编辑态文本（带序号）
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  const stripNumber = (l: string) => l.replace(/^\s*\d+[.、]\s*/, "").trim();

  // 纯文本 → 带序号文本
  const toNumbered = (plain: string) =>
    plain
      .split(/\r?\n/)
      .map(stripNumber)
      .filter(Boolean)
      .map((l, i) => `${i + 1}. ${l}`)
      .join("\n");

  // 带序号文本 → 纯文本（去掉序号）
  const toPlain = (numbered: string) =>
    numbered.split(/\r?\n/).map(stripNumber).filter(Boolean).join("\n");

  const plainLines = value.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const numberedDisplay = plainLines.map((l, i) => `${i + 1}. ${l}`).join("\n");

  useEffect(() => {
    if (editing && ref.current) {
      ref.current.focus();
      const len = ref.current.value.length;
      ref.current.setSelectionRange(len, len);
    }
  }, [editing]);

  const finish = async () => {
    const plain = toPlain(draft);
    setEditing(false);
    if (plain !== value) {
      setSaving(true);
      try {
        await onSave(plain);
      } finally {
        setSaving(false);
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape") {
      setEditing(false);
      return;
    }
    if (e.key === "Enter") {
      // 回车：在末尾另起一行并自动带上下一个序号（Excel 体验）
      e.preventDefault();
      const lines = draft.split(/\r?\n/).filter((l) => l.trim().length > 0);
      const next = lines.length + 1;
      setDraft((lines.length ? lines.join("\n") + "\n" : "") + `${next}. `);
    }
  };

  if (editing) {
    return (
      <textarea
        ref={ref}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={finish}
        onKeyDown={handleKeyDown}
        rows={Math.max(2, draft.split(/\r?\n/).length)}
        className={cn(
          "w-full resize-none rounded border border-input bg-background px-1.5 py-0.5 text-xs outline-none ring-1 ring-ring",
          saving && "opacity-50",
        )}
        disabled={saving}
      />
    );
  }

  return (
    <div
      // 负边距把可点击区域撑满整个单元格（含 td 的 px-3 py-2 内边距），
      // 这样双击单元格任意位置都能进入编辑，而不只是文字那一行
      className="-mx-3 -my-2 min-h-[2.5rem] cursor-pointer whitespace-pre-wrap px-3 py-2 text-xs hover:bg-accent/40"
      onDoubleClick={() => {
        setDraft(toNumbered(value) || "1. ");
        setEditing(true);
      }}
      title="双击编辑"
    >
      {numberedDisplay || <span className="text-muted-foreground/50">—</span>}
    </div>
  );
}

// ===== PrioritySelect =====
function PrioritySelect({
  value,
  onSave,
}: {
  value: number | null | undefined;
  onSave: (v: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value ?? ""));
  const [saving, setSaving] = useState(false);

  const handleBlur = async () => {
    setEditing(false);
    if (draft !== String(value ?? "")) {
      setSaving(true);
      try {
        await onSave(draft);
      } catch {
        setDraft(String(value ?? ""));
      } finally {
        setSaving(false);
      }
    }
  };

  if (editing) {
    return (
      <select
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={handleBlur}
        autoFocus
        className={cn(
          "w-16 rounded border border-input bg-background px-1 py-0.5 text-xs outline-none ring-1 ring-ring",
          saving && "opacity-50",
        )}
        disabled={saving}
      >
        <option value="">—</option>
        {[1, 2, 3, 4, 5].map((p) => {
          const pm = PRIORITY_META[p];
          return <option key={p} value={p} className={cn(pm.tone)}>P{p}</option>;
        })}
      </select>
    );
  }

  const pm = value != null ? PRIORITY_META[value] : null;

  return (
    <div
      className="inline-block cursor-pointer rounded px-0.5 hover:bg-accent/40"
      onDoubleClick={() => { setDraft(String(value ?? "")); setEditing(true); }}
    >
      {pm ? (
        <span className={cn("inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset", pm.tone, pm.ring)}>
          {pm.label}
        </span>
      ) : (
        <span className="text-xs text-muted-foreground">—</span>
      )}
    </div>
  );
}

// ===== InsertRowBelow: "+" button between rows =====
function InsertRowBelow({ onInsert, colSpan }: { onInsert: () => void; colSpan: number }) {
  return (
    <tr className="group/ins">
      <td colSpan={colSpan} className="border-x border-border p-0">
        <button
          type="button"
          onClick={onInsert}
          className="flex w-full items-center justify-center gap-1 py-px text-[11px] text-transparent transition-colors hover:bg-primary/5 group-hover/ins:py-0.5 group-hover/ins:text-primary"
          title="在此处插入一行"
        >
          <Plus className="h-3 w-3" />
          插入
        </button>
      </td>
    </tr>
  );
}

// ===== NewCaseRow: empty row for creating new case (quick edit mode) =====
function NewCaseRow({
  moduleId,
  onCreated,
  onRemove,
  onFirstInput,
  isTrailing = false,
}: {
  moduleId: number;
  onCreated: () => void;
  onRemove: () => void;
  onFirstInput?: () => void;
  isTrailing?: boolean;
}) {
  // 三列预填一个 "1."，用户直接接着写即可（保存时会去掉序号前缀，避免显示叠加）
  const [name, setName] = useState("");
  const [preconditions, setPreconditions] = useState("1. ");
  const [steps, setSteps] = useState("1. ");
  const [expected, setExpected] = useState("1. ");
  const [priority, setPriority] = useState("");
  const [saving, setSaving] = useState(false);
  const queryClient = useQueryClient();
  const rowRef = useRef<HTMLTableRowElement>(null);
  const spawnedRef = useRef(false);

  // 是否已有“真内容”：用例名非空，或三列去掉默认 "1." 后还剩东西
  const hasContent =
    name.trim() !== "" ||
    stripNumbering(preconditions).length > 0 ||
    stripNumbering(steps).length > 0 ||
    stripNumbering(expected).length > 0;

  // 输入第一个字符 → 让父级在底部追加一行新的空快速输入（只触发一次）
  const markInput = () => {
    if (!spawnedRef.current) {
      spawnedRef.current = true;
      onFirstInput?.();
    }
  };

  const handleSave = async () => {
    if (!name.trim() || saving) return;
    setSaving(true);
    try {
      await functionalCasesApi.create({
        module_id: moduleId,
        name: name.trim(),
        functional_spec: {
          preconditions: stripNumbering(preconditions),
          steps: stripNumbering(steps),
          expected: stripNumbering(expected).join("\n") || null,
        },
        priority: priority ? Number(priority) : null,
      });
      onCreated();
      queryClient.invalidateQueries({ queryKey: ["functional_cases"] });
      toast.success("用例已创建");
    } catch (e) {
      handleApiError(e);
      setSaving(false);
    }
  };

  // 焦点离开整行：有用例名 → 保存；空行且不是末行 → 关闭（移除）；末行留着继续录入
  const handleRowBlur = () => {
    setTimeout(() => {
      if (!rowRef.current || rowRef.current.contains(document.activeElement) || saving) return;
      if (name.trim()) {
        void handleSave();
      } else if (!isTrailing) {
        onRemove();
      }
    }, 120);
  };

  const inputCls =
    "w-full rounded border border-input bg-background px-1.5 py-0.5 text-xs outline-none ring-1 ring-ring";

  return (
    <tr ref={rowRef} onBlur={handleRowBlur} className="bg-muted/10">
      <td className="border border-border px-3 py-2">
        <input value={name} onChange={(e) => { setName(e.target.value); markInput(); }} placeholder="输入用例名" className={inputCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2">
        <input value={preconditions} onChange={(e) => { setPreconditions(e.target.value); markInput(); }} placeholder="前置条件" className={inputCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2">
        <input value={steps} onChange={(e) => { setSteps(e.target.value); markInput(); }} placeholder="操作步骤" className={inputCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2">
        <input value={expected} onChange={(e) => { setExpected(e.target.value); markInput(); }} placeholder="预期结果" className={inputCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2 text-center align-middle">
        <select value={priority} onChange={(e) => { setPriority(e.target.value); markInput(); }} className="w-14 rounded border border-input bg-background px-1 py-0.5 text-xs" disabled={saving}>
          <option value="">—</option>
          {[1, 2, 3, 4, 5].map((p) => (<option key={p} value={p}>P{p}</option>))}
        </select>
      </td>
      <td className="border border-border px-3 py-2 text-center text-xs text-muted-foreground">—</td>
      <td className="border border-border px-3 py-2 text-right align-middle">
        {saving ? (
          <Loader2 className="ml-auto h-4 w-4 animate-spin text-muted-foreground" />
        ) : hasContent ? (
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive hover:text-destructive"
            onClick={onRemove}
            title="删除这一行"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        ) : null}
      </td>
    </tr>
  );
}

// ===== CaseDetailDialog =====
function CaseDetailDialog({
  target,
  onClose,
  onMark,
  onEdit,
  onDelete,
  onShowHistory,
}: {
  target: FunctionalCase | null;
  onClose: () => void;
  onMark: (c: FunctionalCase) => void;
  onEdit: (c: FunctionalCase) => void;
  onDelete: (c: FunctionalCase) => void;
  onShowHistory: (c: FunctionalCase) => void;
}) {
  if (!target) return null;
  const spec = target.functional_spec ?? { preconditions: [], steps: [], expected: null };
  const status: FunctionalRunStatus = target.latest_run?.status ?? "pending";
  const meta = STATUS_META[status];
  const Icon = meta.icon;

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {target.name}
            {(() => {
              const pm = target.priority != null ? PRIORITY_META[target.priority] : null;
              return pm ? (
                <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset", pm.tone, pm.ring)}>
                  {pm.label}
                </span>
              ) : null;
            })()}
          </DialogTitle>
          <DialogDescription>
            最近状态：
            <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", meta.tone, meta.ring)}>
              <Icon className="h-3 w-3" />{meta.label}
            </span>
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 max-h-[55vh] overflow-y-auto pr-1">
          {spec.preconditions.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">前置条件</div>
              <ul className="ml-4 list-disc text-sm">{spec.preconditions.map((p, i) => <li key={i}>{p}</li>)}</ul>
            </div>
          )}
          {spec.steps.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">操作步骤</div>
              <ol className="ml-4 list-decimal text-sm">{spec.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </div>
          )}
          {spec.expected && (
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">预期结果</div>
              <p className="text-sm">{spec.expected}</p>
            </div>
          )}
          {spec.preconditions.length === 0 && spec.steps.length === 0 && !spec.expected && (
            <p className="py-4 text-center text-sm text-muted-foreground">该用例尚未填写具体步骤</p>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button variant="outline" size="sm" onClick={() => onMark(target)}>标记</Button>
          <Button variant="outline" size="sm" onClick={() => onEdit(target)}>编辑</Button>
          <Button variant="outline" size="sm" onClick={() => onShowHistory(target)}>历史记录</Button>
          <Button variant="destructive" size="sm" onClick={() => onDelete(target)}>删除</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function handleApiError(err: unknown) {
  const msg = err instanceof ApiError ? err.message : err instanceof Error ? err.message : "操作失败";
  toast.error(msg);
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

/** 去掉每行开头的序号前缀（"1. " / "1、"），把带序号的输入还原成纯文本入库，避免显示时序号叠加 */
function stripNumbering(text?: string): string[] {
  if (!text) return [];
  return text
    .split(/\r?\n/)
    .map((s) => s.replace(/^\s*\d+[.、]\s*/, "").trim())
    .filter(Boolean);
}

function genTempId(): string {
  return `new-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
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
// 模块历史记录对话框 - 展示用例执行历史（按时间倒序）
// ---------------------------------------------------------------------------
function ModuleHistoryDialog({
  moduleId,
  open,
  onClose,
}: {
  moduleId: number | null;
  open: boolean;
  onClose: () => void;
}) {
  const historyQuery = useQuery({
    queryKey: ["module-history", moduleId],
    queryFn: async () => {
      if (moduleId == null) return [];
      const res = await functionalCasesApi.list({ moduleId: moduleId, pageSize: 200 });
      const cases = res.items;
      const runResults = await Promise.allSettled(
        cases.map((c) => functionalCasesApi.runs(c.id, 5))
      );
      type Entry = FunctionalCaseRun & { caseName: string };
      const all: Entry[] = [];
      for (let i = 0; i < cases.length; i++) {
        const r = runResults[i];
        if (r.status === "fulfilled") {
          for (const run of r.value) {
            all.push({ ...run, caseName: cases[i].name });
          }
        }
      }
      all.sort((a, b) => new Date(b.executed_at).getTime() - new Date(a.executed_at).getTime());
      return all;
    },
    enabled: open && moduleId != null,
  });

  const entries: Array<FunctionalCaseRun & { caseName: string }> = historyQuery.data ?? [];

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>执行历史记录</DialogTitle>
          <DialogDescription>
            模块内所有用例的执行记录，按时间倒序
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[65vh] overflow-y-auto">
          {historyQuery.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : entries.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              该模块下还没有任何执行记录
            </p>
          ) : (
            <table className="w-full text-sm border-collapse">
              <thead className="bg-muted/40 text-xs text-muted-foreground">
                <tr>
                  <th className="border border-border px-2 py-2 text-left">时间</th>
                  <th className="border border-border px-2 py-2 text-left">用例名</th>
                  <th className="border border-border px-2 py-2 text-center">状态</th>
                  <th className="border border-border px-2 py-2 text-left">操作人</th>
                  <th className="border border-border px-2 py-2 text-left">批次</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => {
                  const m = STATUS_META[e.status as FunctionalRunStatus];
                  const Icon = m.icon;
                  return (
                    <tr key={e.id} className="hover:bg-accent/30">
                      <td className="border border-border px-2 py-2 text-xs text-muted-foreground whitespace-nowrap">
                        {formatTime(e.executed_at)}
                      </td>
                      <td className="border border-border px-2 py-2 text-sm font-medium">
                        {e.caseName}
                      </td>
                      <td className="border border-border px-2 py-2 text-center">
                        <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", m.tone, m.ring)}>
                          <Icon className="h-3 w-3" />
                          {m.label}
                        </span>
                      </td>
                      <td className="border border-border px-2 py-2 text-sm text-muted-foreground">
                        {e.operator || "—"}
                      </td>
                      <td className="border border-border px-2 py-2 text-xs font-mono text-muted-foreground">
                        {e.batch_id ? `${e.batch_id.slice(0, 12)}…` : "—"}
                      </td>
                    </tr>
                  );
                })}
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
    <div className="py-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}
