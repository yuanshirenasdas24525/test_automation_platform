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
  ChevronLeft,
  ChevronRight,
  FileText,
  CircleHelp,
  Download,
  Folder,
  FolderPlus,
  GripVertical,
  History,
  Info,
  Loader2,
  MinusCircle,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  ShieldOff,
  Sparkles,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SideDrawer } from "@/components/ui/side-drawer";
import { ModuleOutlinePanel } from "@/components/case/module-outline-drawer";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { ProjectAiOverviewView } from "@/components/ProjectAiOverviewView";
import {
  aiModelsApi,
  analysisDocsApi,
  ApiError,
  automationCasesApi,
  casesApi,
  contentApi,
  functionalCasesApi,
  modulesApi,
  projectsApi,
  requirementsApi,
  runsApi,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  AiGeneratedCase,
  AiOutlinePoint,
  ContentNode,
  FunctionalBatchItem,
  FunctionalCase,
  FunctionalCaseEditRecord,
  FunctionalRunStatus,
  FunctionalSpec,
  FunctionalTestHistoryRun,
  Requirement,
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

/** 结果按钮配色：通过=绿 / 失败=红 / 阻塞=黄 / N.A.=不变(outline)。 */
const STATUS_BTN_CLS: Record<string, string> = {
  passed: "border-transparent bg-emerald-600 text-white hover:bg-emerald-700",
  failed: "border-transparent bg-red-600 text-white hover:bg-red-700",
  blocked: "border-transparent bg-amber-500 text-white hover:bg-amber-600",
  na: "",
};

const DEFAULT_PAGE_SIZE = 50;
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 500] as const;

/** 编辑记录跳转后，单条用例的差异：动作 + 改了哪些字段 + 字段改前的值（供高亮/悬停用）。 */
type CaseDiffEntry = { action: string; fields: Set<string>; old: Record<string, string> };

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
  const pageSizeParam = Number(searchParams.get("page_size") ?? String(DEFAULT_PAGE_SIZE));
  const pageSize = Number.isFinite(pageSizeParam) && pageSizeParam >= 0 ? pageSizeParam : DEFAULT_PAGE_SIZE;
  const page = pageSize === 0 ? 1 : Number(searchParams.get("page") ?? "1") || 1;

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
      pageSize,
    }),
    queryFn: () =>
      functionalCasesApi.list({
        moduleId: currentParentId ?? undefined,
        status: statusFilter,
        page,
        pageSize,
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
    // 测试记录 / 编辑记录也失效，标记或编辑后重新打开能看到最新数据
    queryClient.invalidateQueries({ queryKey: ["fc-test-history"] });
    queryClient.invalidateQueries({ queryKey: ["fc-edit-history"] });
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
  const [aiGenOpen, setAiGenOpen] = useState(false);

  // ------ 详情弹窗 ------
  const [detailCase, setDetailCase] = useState<FunctionalCase | null>(null);

  // ------ 测试模式状态 ------
  // 进入测试模式后：每行出现选择框，底部固定操作栏；离开则清空选择 + batchId
  // 默认进入测试模式（页面一加载或进入模块后自动拿 batch_id）
  const [testMode, setTestMode] = useState(true);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  // 测试模式下「状态列」按本轮/选中批次显示：caseId → 状态；不在表里的显示待执行
  const [statusOverride, setStatusOverride] = useState<Map<number, FunctionalRunStatus>>(new Map());

  // ------ 快速编辑模式状态 ------
  const [quickEditMode, setQuickEditMode] = useState(false);
  // 快速编辑会话 id：本次会话内所有增改删共享，编辑记录按它聚合成一条
  const [quickEditSessionId, setQuickEditSessionId] = useState<string | null>(null);
  // 从编辑记录跳转过来时，把用例列表筛选到这些 id
  const [caseIdFilter, setCaseIdFilter] = useState<Set<number> | null>(null);
  // 跳转带来的差异：caseId → {action, 改了哪些字段, 字段改前的值}；deletedNames=本次删除的用例名
  const [caseDiff, setCaseDiff] = useState<Map<number, CaseDiffEntry> | null>(null);
  const [deletedNames, setDeletedNames] = useState<string[]>([]);
  // 测试模式下按用例名筛选
  const [nameFilter, setNameFilter] = useState("");
  const [newRows, setNewRows] = useState<
    Array<{
      tempId: string;
      belowCaseId?: number;
      aboveCaseId?: number;
      initName?: string;   // 向上插入多行时预填的用例名（1/2/3…）
      insOrder?: number;   // 插入位置（目标用例的 sort_order）
    }>
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
      setStatusOverride(new Map()); // 本轮从零：状态列全部待执行
      setCaseIdFilter(null);
      setCaseDiff(null);
      setDeletedNames([]);
    } catch (e) {
      handleError(e);
    }
  };
  const exitTestMode = () => {
    setTestMode(false);
    setBatchId(null);
    setSelected(new Set());
    setStatusOverride(new Map());
    setCaseIdFilter(null);
    setCaseDiff(null);
    setDeletedNames([]);
  };

  // 标记后把这些用例的状态写进本轮覆盖表（状态列即时反映本轮结果）
  const applyStatusOverride = useCallback(
    (ids: number[], status: FunctionalRunStatus) => {
      setStatusOverride((prev) => {
        const m = new Map(prev);
        ids.forEach((id) => m.set(id, status));
        return m;
      });
    },
    [],
  );

  // 点测试记录里的某一批：把那一轮结果还原到状态列（不是展开），
  // 并把当前批次切到这一批 —— 之后继续标记就追加进这条测试记录，而不是新开一批
  const restoreBatch = useCallback((runs: FunctionalTestHistoryRun[]) => {
    const m = new Map<number, FunctionalRunStatus>();
    for (const r of runs) if (!m.has(r.case_id)) m.set(r.case_id, r.status); // runs 时间倒序，首个=最新
    setStatusOverride(m);
    setBatchId(runs.find((r) => r.batch_id)?.batch_id ?? null);
    setTestMode(true);
    setQuickEditMode(false);
  }, []);

  // 点编辑记录：跳到测试模式，筛选出涉及的用例，并算出差异（新增/修改/删除）供高亮
  const jumpToCases = useCallback((records: FunctionalCaseEditRecord[]) => {
    const byCase = new Map<number, CaseDiffEntry>();
    const deleted: string[] = [];
    const ids: number[] = [];
    for (const r of records) {
      if (r.case_id != null) {
        if (!byCase.has(r.case_id)) {
          byCase.set(r.case_id, { action: r.action, fields: new Set(), old: {} });
          ids.push(r.case_id);
        }
        const e = byCase.get(r.case_id)!;
        if (r.action === "create") e.action = "create";
        else if (r.action === "delete") e.action = "delete";
        for (const ch of r.changes) {
          e.fields.add(ch.field);
          if (!(ch.field in e.old)) e.old[ch.field] = ch.old;
        }
      } else if (r.action === "delete" && r.case_name) {
        deleted.push(r.case_name);
      }
    }
    setQuickEditMode(false);
    setQuickEditSessionId(null);
    setStatusOverride(new Map());
    setSelected(new Set());
    setCaseIdFilter(new Set(ids));
    setCaseDiff(byCase);
    setDeletedNames(deleted);
    setTestMode(true);
    setHistoryOpen(false);
  }, []);

  const enterQuickEditMode = () => {
    setQuickEditMode(true);
    setTestMode(false);
    setBatchId(null);
    setSelected(new Set());
    setQuickEditSessionId(genTempId()); // 开一个新会话
    setCaseIdFilter(null);
    setCaseDiff(null);
    setDeletedNames([]);
    // 进入快速编辑时，底部先放一行空的快速输入
    setNewRows([{ tempId: genTempId() }]);
  };
  const exitQuickEditMode = () => {
    setQuickEditMode(false);
    setQuickEditSessionId(null);
    setNewRows([]);
  };

  // 在某条用例上方一次插入 count 行：全部显示出来，用例名预填 1/2/3…，插到目标位置
  const insertRowsAbove = useCallback((caseId: number, count: number, sortOrder?: number) => {
    if (count <= 0) return;
    setNewRows((prev) => [
      ...prev,
      ...Array.from({ length: count }, (_, i) => ({
        tempId: genTempId(),
        aboveCaseId: caseId,
        initName: String(i + 1),
        // 每行用递增的 sort_order，按从上到下填写保存时顺序才正确（否则会 4321 反序）
        insOrder: sortOrder != null ? sortOrder + i : undefined,
      })),
    ]);
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
        .then(({ batch_id }) => setBatchId(batch_id))
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
    // 乐观更新：先从内容树缓存里抹掉这个模块，UI 立即生效，不用刷新页面
    onMutate: async (mid: number) => {
      await queryClient.cancelQueries({ queryKey: ["content"] });
      const snapshots = queryClient.getQueriesData<ContentNode[]>({ queryKey: ["content"] });
      for (const [key, data] of snapshots) {
        if (Array.isArray(data)) {
          queryClient.setQueryData(
            key,
            data.filter((n) => !(n.type === "module" && n.id === mid)),
          );
        }
      }
      setPendingDelete(null);
      return { snapshots };
    },
    onError: (err, _mid, ctx) => {
      ctx?.snapshots?.forEach(([key, data]) => queryClient.setQueryData(key, data));
      handleError(err);
    },
    onSuccess: () => toast.success("模块已删除"),
    onSettled: () => invalidateAll(),
  });

  const deleteCase = useMutation({
    mutationFn: ({ cid, sessionId }: { cid: number; sessionId?: string }) =>
      functionalCasesApi.remove(cid, sessionId),
    // 乐观更新：先把这条从所有用例列表缓存里抹掉，UI 立即生效，不用等 refetch
    onMutate: async ({ cid }: { cid: number; sessionId?: string }) => {
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
    onError: (err, _vars, ctx) => {
      ctx?.snapshots?.forEach(([key, data]) => queryClient.setQueryData(key, data));
      handleError(err);
    },
    onSuccess: (data) => {
      if (data.batch_id) {
        toast.success("用例已删除", {
          action: {
            label: "撤销",
            onClick: async () => {
              try {
                await casesApi.rollbackHistory(data.batch_id!, { mode: "full" });
                toast.success("已恢复");
                invalidateAll();
              } catch (e) {
                handleError(e);
              }
            },
          },
        });
      } else {
        toast.success("用例已删除");
      }
    },
    onSettled: () => invalidateAll(),
  });

  // 用例排序（快速编辑里上移/下移），走通用 /api/reorder
  const reorderCases = useMutation({
    mutationFn: (items: { type: string; id: number; new_order: number }[]) =>
      modulesApi.reorder(items),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["functional_cases"] }),
    onError: handleError,
  });

  // ------ 业务动作 ------
  const project = projectQuery.data;

  const setQS = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams);
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "" || v === "all") next.delete(k);
      else next.set(k, v);
    }
    setSearchParams(next, { replace: true });
  };

  const handleEnterModule = (node: ContentNode) => {
    setBreadcrumb((prev) => [...prev, { id: node.id, name: node.name }]);
    // 切层级时清掉选择，避免跨模块选中混乱
    setSelected(new Set());
    setQS({ page: undefined });
  };

  const handleJumpTo = (index: number) => {
    if (index < 0) setBreadcrumb([]);
    else setBreadcrumb((prev) => prev.slice(0, index + 1));
    setSelected(new Set());
    setQS({ page: undefined });
  };

  const handleStatusChange = (raw: string) => {
    setQS({ status: raw === "all" ? undefined : raw, page: undefined });
  };

  const handlePageSizeChange = (raw: string) => {
    const nextPageSize = Number(raw);
    setQS({
      page_size: nextPageSize === DEFAULT_PAGE_SIZE ? undefined : raw,
      page: undefined,
    });
  };

  // ------ 渲染 ------
  if (!Number.isFinite(projectId)) {
    return <div className="p-8 text-sm text-destructive">非法的项目 ID。</div>;
  }

  // 模块/用例分别从两个查询拿 —— contentQuery 给模块，casesQuery 给用例
  const modules = (contentQuery.data ?? []).filter((n) => n.type === "module");
  const allCases = casesQuery.data?.items ?? [];
  // 从编辑记录跳转过来时，把列表筛选到指定用例；测试模式下再按用例名筛选
  const baseCases = caseIdFilter ? allCases.filter((c) => caseIdFilter.has(c.id)) : allCases;
  const nf = nameFilter.trim().toLowerCase();
  const cases = testMode && nf ? baseCases.filter((c) => c.name.toLowerCase().includes(nf)) : baseCases;
  const totalCases = caseIdFilter || (testMode && nf) ? cases.length : casesQuery.data?.total ?? 0;

  // 拖拽排序：把 fromId 移到 toId 的位置，整组重排后发 /api/reorder
  const reorderTo = (fromId: number, toId: number) => {
    const from = cases.findIndex((c) => c.id === fromId);
    const to = cases.findIndex((c) => c.id === toId);
    if (from < 0 || to < 0 || from === to) return;
    const next = cases.slice();
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    reorderCases.mutate(next.map((c, k) => ({ type: "case", id: c.id, new_order: k })));
  };

  // 快速编辑：批量删除选中用例
  const batchDeleteCases = async () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    try {
      const results = await Promise.all(ids.map((id) => functionalCasesApi.remove(id, quickEditSessionId ?? undefined)));
      const batchIds = results.map((item) => item.batch_id).filter((id): id is number => id != null);
      toast.success(`已删除 ${ids.length} 条`, batchIds.length > 0 ? {
        action: {
          label: "撤销",
          onClick: async () => {
            try {
              await Promise.all(batchIds.map((batchId) => casesApi.rollbackHistory(batchId, { mode: "full" })));
              toast.success("已恢复");
              invalidateAll();
            } catch (e) {
              handleError(e);
            }
          },
        },
      } : undefined);
      setSelected(new Set());
      invalidateAll();
    } catch (e) {
      handleError(e);
    }
  };
  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(totalCases / pageSize));

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
            title={quickEditMode ? "本模块的编辑历史" : "本模块的测试记录"}
          >
            <History className="h-4 w-4" />
            {quickEditMode ? "编辑记录" : "测试记录"}
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
          新建{currentParentId === null ? "模块" : "子模块"}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={currentParentId === null}
          onClick={() => {
            if (currentParentId === null) return;
            // 直接进入快速编辑，在底部行内录入新用例
            if (!quickEditMode) enterQuickEditMode();
          }}
          title={
            currentParentId === null
              ? "用例必须挂在模块下 —— 先进入一个模块"
              : "进入快速编辑，在底部行内新建用例"
          }
        >
          <Plus className="h-4 w-4" />
          新建功能用例
        </Button>
        {/* AI 生成用例 */}
        <Button
          variant="outline"
          size="sm"
          disabled={currentParentId === null}
          onClick={() => setAiGenOpen(true)}
          title={currentParentId === null ? "先进入一个模块" : "用 AI 根据需求生成功能用例"}
          className="border-primary/40 text-primary hover:bg-primary/10"
        >
          <Sparkles className="h-4 w-4" />
          AI 生成用例
        </Button>
        {/* 快速编辑：批量删除选中 */}
        {quickEditMode && selected.size > 0 ? (
          <Button variant="destructive" size="sm" onClick={batchDeleteCases}>
            <Trash2 className="h-4 w-4" />
            删除选中（{selected.size}）
          </Button>
        ) : null}
        {/* 测试模式下不显示导入 */}
        {!testMode ? (
          <ImportButton
            moduleId={currentParentId}
            disabled={currentParentId === null}
            onDone={invalidateAll}
          />
        ) : null}
        {/* 快速编辑模式下不显示导出 */}
        {!quickEditMode ? (
          <ExportButton projectId={projectId} moduleId={currentParentId} />
        ) : null}
        {/* 从编辑记录跳转过来时的筛选提示 */}
        {caseIdFilter ? (
          <button
            type="button"
            onClick={() => { setCaseIdFilter(null); setCaseDiff(null); setDeletedNames([]); }}
            className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-xs text-primary hover:bg-primary/20"
          >
            已按编辑记录筛选 {caseIdFilter.size} 条 · 清除 ✕
          </button>
        ) : null}

        {/* 筛选：快速编辑→无；测试模式→用例名 + 状态；其它→状态 */}
        {currentParentId !== null && !quickEditMode ? (
          <div className="ml-auto flex items-center gap-2">
            {testMode ? (
              <Input
                className="h-8 w-44"
                placeholder="按用例名称筛选"
                value={nameFilter}
                onChange={(e) => setNameFilter(e.target.value)}
              />
            ) : null}
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
            <Section title={`${currentParentId === null ? "模块" : "子模块"}（${modules.length}）`}>
              {modules.length === 0 ? (
                <EmptyHint text={currentParentId === null ? "当前项目还没有模块" : "当前层级没有子模块"} />
              ) : (
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {modules.map((m) => (
                    <ModuleCard
                      key={m.id}
                      node={m}
                      onOpen={() => handleEnterModule(m)}
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
              {/* 编辑记录跳转后的差异图例 + 删除提示 */}
              {caseDiff ? (
                <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
                  <span className="font-medium text-primary">本次编辑差异：</span>
                  <span className="inline-flex items-center gap-1">
                    <span className="inline-block h-3 w-3 rounded-sm bg-emerald-100 ring-1 ring-emerald-300" />新增（整行绿底）
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span className="inline-block h-3 w-3 rounded-sm bg-yellow-100 ring-1 ring-yellow-300" />修改（黄格，悬停看改前）
                  </span>
                  {deletedNames.length > 0 ? (
                    <span className="text-red-600">已删除：{deletedNames.join("、")}</span>
                  ) : null}
                </div>
              ) : null}
              {casesQuery.isLoading ? (
                <ListSkeleton />
              ) : casesQuery.error ? (
                <ErrorBox onRetry={() => casesQuery.refetch()} />
              ) : cases.length === 0 && !quickEditMode ? (
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
                  onInsertAbove={insertRowsAbove}
                  onReorder={reorderTo}
                  onRemoveNewRow={removeNewRow}
                  onFirstInput={handleFirstInput}
                  statusOverride={statusOverride}
                  sessionId={quickEditMode ? quickEditSessionId ?? undefined : undefined}
                  caseDiff={caseDiff}
                />
              )}
              <div className="mt-4 flex items-center justify-end gap-2">
                <span className="text-xs text-muted-foreground">每页</span>
                <Select value={String(pageSize)} onValueChange={handlePageSizeChange}>
                  <SelectTrigger className="h-8 w-24">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <SelectItem key={size} value={String(size)}>{size}</SelectItem>
                    ))}
                    <SelectItem value="0">不分页</SelectItem>
                  </SelectContent>
                </Select>
                {pageSize !== 0 && totalPages > 1 ? (
                  <>
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
                  </>
                ) : null}
              </div>
            </Section>
          ) : null}
        </div>
      )}

      {/* 测试模式底栏 */}
      {testMode ? (
        <TestModeFooter
          batchId={batchId}
          moduleId={currentParentId}
          selected={selected}
          cases={cases}
          modulePath={breadcrumb.map((b) => b.name).join("_")}
          statusOverride={statusOverride}
          onClear={() => setSelected(new Set())}
          onDone={() => {
            invalidateAll();
            // 完成一批后保留 batchId 不变，方便用户继续在同一批次内勾下一组
          }}
          onError={handleError}
          onMarked={applyStatusOverride}
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
        modulePath={breadcrumb.map((b) => b.name).join(" - ")}
        onClose={() => setCaseDialog(null)}
        onDone={() => {
          invalidateAll();
          setCaseDialog(null);
        }}
        onError={handleError}
      />

      <MarkDialog
        target={markingCase}
        batchId={testMode ? batchId : null}
        onClose={() => setMarkingCase(null)}
        onDone={() => {
          invalidateAll();
          setMarkingCase(null);
        }}
        onError={handleError}
        onMarkedStatus={(id, status) => applyStatusOverride([id], status)}
      />

      <DeleteDialog
        target={pendingDelete}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => {
          if (!pendingDelete) return;
          if (pendingDelete.kind === "module") {
            deleteModule.mutate(pendingDelete.id);
          } else {
            deleteCase.mutate({
              cid: pendingDelete.id,
              sessionId: quickEditMode ? quickEditSessionId ?? undefined : undefined,
            });
          }
        }}
        submitting={deleteModule.isPending || deleteCase.isPending}
      />

      <CaseDetailDialog
        target={detailCase}
        cases={cases}
        batchId={batchId}
        onClose={() => setDetailCase(null)}
        onNavigate={(c) => setDetailCase(c)}
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
        onMarked={(id, status) => {
          applyStatusOverride([id], status);
          invalidateAll();
        }}
      />

      <HistoryDialog
        target={historyCase}
        onClose={() => setHistoryCase(null)}
      />

      {/* 历史记录按模式切换：编辑模式→编辑记录；否则→测试记录 */}
      {quickEditMode ? (
        <EditRecordsDialog
          moduleId={currentParentId}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onJump={jumpToCases}
          onChanged={invalidateAll}
        />
      ) : (
        <TestRecordsDialog
          moduleId={currentParentId}
          moduleName={breadcrumb[breadcrumb.length - 1]?.name ?? "功能用例"}
          totalCases={totalCases}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onRestore={restoreBatch}
        />
      )}

      <AiGenerateDialog
        open={aiGenOpen}
        moduleId={currentParentId}
        projectId={projectId}
        initialMode="functional"
        onClose={() => setAiGenOpen(false)}
        onInserted={() => { invalidateAll(); }}
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
  const [open, setOpen] = useState(true);
  return (
    <section className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-xs font-medium uppercase tracking-wide text-muted-foreground hover:text-foreground"
      >
        <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-90")} />
        {title}
      </button>
      {open ? children : null}
    </section>
  );
}

function ModuleCard({
  node,
  onOpen,
  onRename,
  onDelete,
}: {
  node: ContentNode;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      className="flex cursor-pointer items-center gap-3 rounded-lg border bg-card px-4 py-3 text-left transition-colors hover:border-primary/40 hover:bg-accent/30"
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <Folder className="h-5 w-5 shrink-0 text-amber-500" />
        <span className="truncate text-sm font-medium">{node.name}</span>
        <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-muted-foreground" />
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={(event) => event.stopPropagation()}
          >
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" onClick={(event) => event.stopPropagation()}>
          <DropdownMenuItem onClick={(event) => { event.stopPropagation(); onRename(); }}>
            <Pencil className="mr-2 h-4 w-4" />
            重命名
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={(event) => { event.stopPropagation(); onDelete(); }}
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
  onInsertAbove,
  onReorder,
  onRemoveNewRow,
  onFirstInput,
  statusOverride,
  sessionId,
  caseDiff,
}: {
  cases: FunctionalCase[];
  testMode: boolean;
  quickEditMode: boolean;
  selected: Set<number>;
  newRows: Array<{ tempId: string; belowCaseId?: number; aboveCaseId?: number; initName?: string; insOrder?: number }>;
  allSelectedOnPage: boolean;
  moduleId: number | null;
  onToggleSelectAll: () => void;
  onToggleSelect: (id: number) => void;
  onMark: (c: FunctionalCase) => void;
  onEdit: (c: FunctionalCase) => void;
  onDelete: (c: FunctionalCase) => void;
  onShowHistory: (c: FunctionalCase) => void;
  onOpenDetail: (c: FunctionalCase) => void;
  onInsertAbove: (caseId: number, count: number, sortOrder?: number) => void;
  onReorder: (fromId: number, toId: number) => void;
  onRemoveNewRow: (tempId: string) => void;
  onFirstInput: (tempId: string) => void;
  statusOverride: Map<number, FunctionalRunStatus>;
  sessionId?: string;
  caseDiff: Map<number, CaseDiffEntry> | null;
}) {
  // 底部快速输入行（belowCaseId / aboveCaseId 都为空的才算底部行；最后一行是 trailing 录入入口）
  const bottomRows = newRows.filter(
    (nr) => nr.belowCaseId === undefined && nr.aboveCaseId === undefined,
  );
  // 拖拽排序状态（快速编辑）
  const [dragId, setDragId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);
  const endDrag = () => { setDragId(null); setOverId(null); };
  return (
    <div className="overflow-hidden rounded-md border">
      <table className="w-full text-sm border-collapse">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            {testMode || quickEditMode ? (
              <th className="w-10 border border-border px-1 py-2 text-center align-middle">
                <SelectToggle checked={allSelectedOnPage} onChange={onToggleSelectAll} ariaLabel="全选当前页" />
              </th>
            ) : null}
            <th className="border border-border px-3 py-2 text-left font-medium">用例名称</th>
            <th className="border border-border px-3 py-2 text-left font-medium w-[12%]">前置条件</th>
            <th className="border border-border px-3 py-2 text-left font-medium w-[40%]">操作步骤</th>
            <th className="border border-border px-3 py-2 text-left font-medium w-[12%]">预期结果</th>
            <th className="border border-border px-3 py-2 text-center font-medium w-20">优先级</th>
            {!quickEditMode ? (
              <th className="border border-border px-3 py-2 text-center font-medium w-28">{testMode ? "状态" : "最近状态"}</th>
            ) : null}
            <th className="border border-border px-3 py-2 text-right font-medium w-36">操作</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => {
            const aboveRows = newRows.filter((nr) => nr.aboveCaseId === c.id);
            return (
              <Fragment key={c.id}>
                {aboveRows.map((nr, ai) => (
                  <NewCaseRow
                    key={nr.tempId}
                    moduleId={moduleId!}
                    autoFocusName={ai === 0}
                    initialName={nr.initName}
                    insertSortOrder={nr.insOrder}
                    sessionId={sessionId}
                    onCreated={() => onRemoveNewRow(nr.tempId)}
                    onRemove={() => onRemoveNewRow(nr.tempId)}
                  />
                ))}
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
                  onInsertAbove={(count) => onInsertAbove(c.id, count, c.sort_order ?? undefined)}
                  dragEnabled={quickEditMode}
                  isDragOver={overId === c.id && dragId !== null && dragId !== c.id}
                  isDragging={dragId === c.id}
                  onDragStartRow={() => setDragId(c.id)}
                  onDragOverRow={() => setOverId(c.id)}
                  onDropRow={() => { if (dragId !== null && dragId !== c.id) onReorder(dragId, c.id); endDrag(); }}
                  onDragEndRow={endDrag}
                  statusOverride={statusOverride}
                  sessionId={sessionId}
                  diff={caseDiff?.get(c.id) ?? null}
                />
              </Fragment>
            );
          })}
          {quickEditMode && moduleId != null
            ? bottomRows.map((nr, idx) => (
                <NewCaseRow
                  key={nr.tempId}
                  moduleId={moduleId}
                  isTrailing={idx === bottomRows.length - 1}
                  sessionId={sessionId}
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

// 有序列表只读展示：用原生 <ol> 渲染（同 Markdown 列表效果），换行自动对齐到序号后的文字，
// 序号是几位数都对齐。title 传了就用它（差异高亮时显示“改前”），否则默认显示全文。
function OrderedDisplay({ items, title }: { items: string[]; title?: string }) {
  if (items.length === 0) return <span className="text-muted-foreground/50" title={title}>—</span>;
  return (
    <ol
      className="list-decimal space-y-0.5 pl-5 text-xs text-muted-foreground marker:text-muted-foreground/70"
      title={title ?? items.map((s, i) => `${i + 1}. ${s}`).join("\n")}
    >
      {items.map((s, i) => (
        <li key={i} className="break-words pl-0.5">{s}</li>
      ))}
    </ol>
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
  onInsertAbove,
  dragEnabled,
  isDragOver,
  isDragging,
  onDragStartRow,
  onDragOverRow,
  onDropRow,
  onDragEndRow,
  statusOverride,
  sessionId,
  diff,
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
  onInsertAbove?: (count: number) => void;
  dragEnabled?: boolean;
  isDragOver?: boolean;
  isDragging?: boolean;
  onDragStartRow?: () => void;
  onDragOverRow?: () => void;
  onDropRow?: () => void;
  onDragEndRow?: () => void;
  statusOverride?: Map<number, FunctionalRunStatus>;
  sessionId?: string;
  diff?: CaseDiffEntry | null;
}) {
  const queryClient = useQueryClient();
  // 测试模式下状态列看本轮/选中批次（覆盖表，没有=待执行）；其它模式看最近一次
  const status: FunctionalRunStatus = testMode
    ? statusOverride?.get(row.id) ?? "pending"
    : row.latest_run?.status ?? "pending";
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
    await functionalCasesApi.update(row.id, data, sessionId);
    queryClient.invalidateQueries({ queryKey: ["functional_cases"] });
  };

  const expectedLines = splitLines(spec.expected ?? "");
  const pmeta = row.priority != null ? PRIORITY_META[row.priority] : null;

  // 编辑记录跳转后的高亮：新建=整行绿底；修改=对应单元格黄底（悬停看改前的值）
  const isCreated = diff?.action === "create";
  const hi = (field: string) =>
    diff?.fields.has(field) ? "bg-yellow-100" : undefined;
  const hiTitle = (field: string) =>
    diff?.fields.has(field) ? `改前：${diff.old[field] || "空"}` : undefined;

  return (
    <tr
      className={cn("hover:bg-accent/30", selected && "bg-accent/40", isCreated && "bg-emerald-50", isDragOver && "border-t-2 border-primary", isDragging && "opacity-40", row.skip && "opacity-60")}
      onDragOver={dragEnabled ? (e) => { e.preventDefault(); onDragOverRow?.(); } : undefined}
      onDrop={dragEnabled ? () => onDropRow?.() : undefined}
      onDragEnd={dragEnabled ? () => onDragEndRow?.() : undefined}
    >
      {testMode || quickEditMode ? (
        <td className="border border-border px-1 py-2 text-center align-middle">
          <SelectToggle checked={selected} onChange={onToggleSelect} ariaLabel={`选中用例 ${row.name}`} />
        </td>
      ) : null}
      {/* 用例名称 */}
      <td className={cn("border border-border px-3 py-2", hi("name"))} title={hiTitle("name")}>
        <NameCell name={row.name} onOpenDetail={onOpenDetail} onSave={(v) => saveField("name", v)} quickEditMode={quickEditMode} titleOverride={hiTitle("name")} />
      </td>
      {/* 前置条件 - 有序列表 */}
      <td className={cn("border border-border px-3 py-2 align-top", hi("preconditions"))} title={hiTitle("preconditions")}>
        {quickEditMode ? (
          <OrderedInlineInput value={spec.preconditions.join("\n")} onSave={(v) => saveField("preconditions", v)} />
        ) : (
          <OrderedDisplay items={spec.preconditions} title={hiTitle("preconditions")} />
        )}
      </td>
      {/* 操作步骤 - 有序列表 */}
      <td className={cn("border border-border px-3 py-2 align-top", hi("steps"))} title={hiTitle("steps")}>
        {quickEditMode ? (
          <OrderedInlineInput value={spec.steps.join("\n")} onSave={(v) => saveField("steps", v)} />
        ) : (
          <OrderedDisplay items={spec.steps} title={hiTitle("steps")} />
        )}
      </td>
      {/* 预期结果 - 有序列表 */}
      <td className={cn("border border-border px-3 py-2 align-top", hi("expected"))} title={hiTitle("expected")}>
        {quickEditMode ? (
          <OrderedInlineInput value={spec.expected ?? ""} onSave={(v) => saveField("expected", v)} />
        ) : (
          <OrderedDisplay items={expectedLines} title={hiTitle("expected")} />
        )}
      </td>
      {/* 优先级 */}
      <td className={cn("border border-border px-3 py-2 text-center align-middle", hi("priority"))} title={hiTitle("priority")}>
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
      {/* 最近状态（快速编辑下隐藏） */}
      {!quickEditMode ? (
        <td className="border border-border px-3 py-2 text-center align-middle">
          <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", meta.tone, meta.ring)}>
            <Icon className="h-3.5 w-3.5" />
            {meta.label}
          </span>
        </td>
      ) : null}
      {/* 操作 */}
      <td className="border border-border px-3 py-2 text-right align-middle">
        <div className="inline-flex items-center gap-1">
          {testMode ? (
            <Button variant="outline" size="sm" onClick={onMark}>标记</Button>
          ) : quickEditMode ? (
            <>
              <span
                draggable
                onDragStart={() => onDragStartRow?.()}
                className="flex h-7 w-7 cursor-grab items-center justify-center text-muted-foreground hover:text-foreground active:cursor-grabbing"
                title="拖动排序"
              >
                <GripVertical className="h-4 w-4" />
              </span>
              <InsertAboveControl onInsert={(n) => onInsertAbove?.(n)} />
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
  titleOverride,
}: {
  name: string;
  onOpenDetail: () => void;
  onSave: (v: string) => Promise<void>;
  quickEditMode: boolean;
  titleOverride?: string;
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
      title={titleOverride ?? (quickEditMode ? "单击编辑" : `查看"${name}"详情`)}
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
      className="-mx-3 -my-2 min-h-[2.5rem] cursor-pointer px-3 py-2 text-xs hover:bg-accent/40"
      onDoubleClick={() => {
        setDraft(toNumbered(value) || "1. ");
        setEditing(true);
      }}
      title="双击编辑"
    >
      {plainLines.length ? (
        plainLines.map((l, i) => (
          <div key={i} className="break-words pl-[1.6em] [text-indent:-1.6em]">
            {i + 1}. {l}
          </div>
        ))
      ) : (
        <span className="text-muted-foreground/50">—</span>
      )}
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
// ===== InsertAboveControl: 操作列里的「向上插入 N 行」（数字默认 1）=====
function InsertAboveControl({ onInsert }: { onInsert: (count: number) => void }) {
  const [count, setCount] = useState("1");
  return (
    <div className="inline-flex items-center gap-0.5">
      <input
        type="number"
        min={1}
        value={count}
        onChange={(e) => setCount(e.target.value)}
        className="h-7 w-11 rounded border border-input bg-background px-1 text-center text-xs outline-none focus:ring-1 focus:ring-ring"
        title="向上插入的行数"
      />
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        title="在本行上方插入"
        onClick={() => onInsert(Math.max(1, Math.floor(Number(count) || 1)))}
      >
        <Plus className="h-4 w-4" />
      </Button>
    </div>
  );
}

// ===== NewCaseRow: empty row for creating new case (quick edit mode) =====
function NewCaseRow({
  moduleId,
  onCreated,
  onRemove,
  onFirstInput,
  isTrailing = false,
  autoFocusName = false,
  sessionId,
  initialName,
  insertSortOrder,
}: {
  moduleId: number;
  onCreated: () => void;
  onRemove: () => void;
  onFirstInput?: () => void;
  isTrailing?: boolean;
  autoFocusName?: boolean;
  sessionId?: string;
  initialName?: string;     // 向上插入多行时预填的用例名（1/2/3…）
  insertSortOrder?: number; // 插入位置（目标用例的 sort_order）
}) {
  // 三列预填一个 "1."，用户直接接着写即可（保存时会去掉序号前缀，避免显示叠加）
  const [name, setName] = useState(initialName ?? "");
  const [preconditions, setPreconditions] = useState("1. ");
  const [steps, setSteps] = useState("1. ");
  const [expected, setExpected] = useState("1. ");
  const [priority, setPriority] = useState("3"); // 默认 P3
  const [saving, setSaving] = useState(false);
  const queryClient = useQueryClient();
  const rowRef = useRef<HTMLTableRowElement>(null);
  const spawnedRef = useRef(false);

  // 末行（录入入口）出现/变成末行时，自动滚动到可见，省得每次手动往上翻
  useEffect(() => {
    if (isTrailing) rowRef.current?.scrollIntoView({ block: "nearest" });
  }, [isTrailing]);

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
    if (saving || !hasContent) return; // 有任意内容就能存；没填用例名用缺省名
    setSaving(true);
    try {
      await functionalCasesApi.create({
        module_id: moduleId,
        name: name.trim() || "未命名用例",
        functional_spec: {
          preconditions: stripNumbering(preconditions),
          steps: stripNumbering(steps),
          expected: stripNumbering(expected).join("\n") || null,
        },
        priority: priority ? Number(priority) : null,
        sort_order: insertSortOrder,
      }, sessionId);
      onCreated();
      queryClient.invalidateQueries({ queryKey: ["functional_cases"] });
      toast.success("用例已创建");
    } catch (e) {
      handleApiError(e);
      setSaving(false);
    }
  };

  // 焦点离开整行：有内容 → 保存；空行且不是末行 → 关闭（移除）；末行留着继续录入
  const handleRowBlur = () => {
    setTimeout(() => {
      if (!rowRef.current || rowRef.current.contains(document.activeElement) || saving) return;
      if (hasContent) {
        void handleSave();
      } else if (!isTrailing) {
        onRemove();
      }
    }, 120);
  };

  const inputCls =
    "w-full rounded border border-input bg-background px-1.5 py-0.5 text-xs outline-none ring-1 ring-ring";
  const areaCls = cn(inputCls, "resize-none");
  // 三列文本框回车自动续号（可换行）
  const orderedKey =
    (val: string, setter: (v: string) => void) =>
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const lines = val.split(/\r?\n/).filter((l) => l.trim().length > 0);
        const n = lines.length + 1;
        setter((lines.length ? lines.join("\n") + "\n" : "") + `${n}. `);
      }
    };
  const areaRows = (v: string) => Math.max(1, v.split(/\r?\n/).length);

  return (
    <tr ref={rowRef} onBlur={handleRowBlur} className="bg-muted/10">
      {/* 占位：与选择列对齐（快速编辑下表头有选择列） */}
      <td className="border border-border px-1 py-2" />
      <td className="border border-border px-3 py-2 align-top">
        <input autoFocus={autoFocusName} onFocus={(e) => { if (initialName) e.currentTarget.select(); }} value={name} onChange={(e) => { setName(e.target.value); markInput(); }} placeholder="输入用例名" className={inputCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2 align-top">
        <textarea value={preconditions} onChange={(e) => { setPreconditions(e.target.value); markInput(); }} onKeyDown={orderedKey(preconditions, setPreconditions)} rows={areaRows(preconditions)} placeholder="前置条件" className={areaCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2 align-top">
        <textarea value={steps} onChange={(e) => { setSteps(e.target.value); markInput(); }} onKeyDown={orderedKey(steps, setSteps)} rows={areaRows(steps)} placeholder="操作步骤" className={areaCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2 align-top">
        <textarea value={expected} onChange={(e) => { setExpected(e.target.value); markInput(); }} onKeyDown={orderedKey(expected, setExpected)} rows={areaRows(expected)} placeholder="预期结果" className={areaCls} disabled={saving} />
      </td>
      <td className="border border-border px-3 py-2 text-center align-middle">
        <select value={priority} onChange={(e) => { setPriority(e.target.value); markInput(); }} className="w-14 rounded border border-input bg-background px-1 py-0.5 text-xs" disabled={saving}>
          <option value="">—</option>
          {[1, 2, 3, 4, 5].map((p) => (<option key={p} value={p}>P{p}</option>))}
        </select>
      </td>
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
  cases,
  batchId,
  onClose,
  onNavigate,
  onEdit,
  onDelete,
  onShowHistory,
  onMarked,
}: {
  target: FunctionalCase | null;
  cases: FunctionalCase[];
  batchId: string | null;
  onClose: () => void;
  onNavigate: (c: FunctionalCase) => void;
  onEdit: (c: FunctionalCase) => void;
  onDelete: (c: FunctionalCase) => void;
  onShowHistory: (c: FunctionalCase) => void;
  onMarked: (caseId: number, status: Exclude<FunctionalRunStatus, "pending">) => void;
}) {
  const [note, setNote] = useState("");
  const [marking, setMarking] = useState<Exclude<FunctionalRunStatus, "pending"> | null>(null);

  const idx = target ? cases.findIndex((c) => c.id === target.id) : -1;
  const prev = idx > 0 ? cases[idx - 1] : null;
  const next = idx >= 0 && idx < cases.length - 1 ? cases[idx + 1] : null;

  // 切换用例时清空备注
  useEffect(() => { setNote(""); }, [target?.id]);

  // 键盘左右键翻用例
  useEffect(() => {
    if (!target) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft" && prev) onNavigate(prev);
      else if (e.key === "ArrowRight" && next) onNavigate(next);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target, prev, next, onNavigate]);

  const doMark = async (status: Exclude<FunctionalRunStatus, "pending">) => {
    if (!target || marking) return;
    setMarking(status);
    try {
      await functionalCasesApi.mark(target.id, {
        status,
        actual_result: null,
        note: note || null,
        batch_id: batchId,
      });
      toast.success(`已标记：${STATUS_META[status].label}`);
      setNote("");
      onMarked(target.id, status);
    } catch (e) {
      handleApiError(e);
    } finally {
      setMarking(null);
    }
  };

  if (!target) return null;
  const spec = target.functional_spec ?? { preconditions: [], steps: [], expected: null };
  const status: FunctionalRunStatus = target.latest_run?.status ?? "pending";
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  const pm = target.priority != null ? PRIORITY_META[target.priority] : null;

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="max-w-4xl px-16"
        onWheel={(e) => {
          if (e.deltaY > 0 && next) onNavigate(next);
          else if (e.deltaY < 0 && prev) onNavigate(prev);
        }}
      >
        {/* 左右两侧大箭头翻用例 */}
        <button
          type="button"
          disabled={!prev}
          onClick={() => prev && onNavigate(prev)}
          title="上一个 (←)"
          className="absolute left-2 top-1/2 z-10 flex h-12 w-12 -translate-y-1/2 items-center justify-center rounded-full border bg-background text-primary shadow-sm transition hover:bg-primary/10 disabled:opacity-30"
        >
          <ChevronLeft className="h-7 w-7" />
        </button>
        <button
          type="button"
          disabled={!next}
          onClick={() => next && onNavigate(next)}
          title="下一个 (→)"
          className="absolute right-2 top-1/2 z-10 flex h-12 w-12 -translate-y-1/2 items-center justify-center rounded-full border bg-background text-primary shadow-sm transition hover:bg-primary/10 disabled:opacity-30"
        >
          <ChevronRight className="h-7 w-7" />
        </button>
        <DialogHeader>
          <DialogTitle className="flex items-center justify-center gap-2">
            <span className="min-w-0 truncate text-center">{target.name}</span>
            {pm ? (
              <span className={cn("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset", pm.tone, pm.ring)}>
                {pm.label}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            最近状态：
            <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", meta.tone, meta.ring)}>
              <Icon className="h-3 w-3" />{meta.label}
            </span>
            {idx >= 0 ? <span className="ml-2 text-xs text-muted-foreground">第 {idx + 1}/{cases.length} 条 · ← → / 滚轮切换</span> : null}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 max-h-[40vh] overflow-y-auto pr-1">
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

        {/* 备注：写入本次标记，历史可见 */}
        <div className="space-y-1">
          <Label className="text-xs">备注</Label>
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注信息（会写入下面的标记结果，历史记录可见）" />
        </div>

        <DialogFooter className="flex-wrap gap-2">
          {/* 直接标记结果 */}
          {MARKABLE_STATUSES.map((s) => {
            const m = STATUS_META[s];
            const I = m.icon;
            return (
              <Button
                key={s}
                size="sm"
                variant="outline"
                className={cn(STATUS_BTN_CLS[s])}
                disabled={marking !== null}
                onClick={() => doMark(s)}
              >
                {marking === s ? <Loader2 className="h-4 w-4 animate-spin" /> : <I className="h-4 w-4" />}
                {m.label}
              </Button>
            );
          })}
          <Button variant="outline" size="sm" onClick={() => onEdit(target)}>编辑</Button>
          <Button variant="outline" size="sm" onClick={() => onShowHistory(target)}>历史记录</Button>
          <Button variant="destructive" size="sm" onClick={() => onDelete(target)}>删除</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function handleApiError(err: unknown) {
  toast.error(errorMessage(err));
}

function errorMessage(err: unknown) {
  if (err instanceof DOMException && err.name === "AbortError") {
    return "已停止生成测试点";
  }
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : "操作失败";
}

function upsertFailedBatch(items: AiBatchFailure[], next: AiBatchFailure) {
  const exists = items.find((item) => item.id === next.id);
  if (!exists) return [...items, next];
  return items.map((item) =>
    item.id === next.id
      ? { ...next, attempts: item.attempts + next.attempts }
      : item,
  );
}

const ASSERT_NOT_EMPTY = new Set(["not_empty", "not_null", "notnull", "notempty", "非空"]);
const ASSERT_IS_EMPTY = new Set(["is_null", "为空", "空"]);

/** 断言映射 {target: expected} → 结构化断言规则；正确处理"非空/为空"哨兵，
 *  避免 not_empty 被当成字面量做相等比较（这会让非空值反而判失败）。 */
function assertionMapToRules(map: Record<string, unknown>) {
  return Object.entries(map ?? {})
    .filter(([t]) => t)
    .map(([target, expected]) => {
      const ev = typeof expected === "string" ? expected.trim().toLowerCase() : null;
      if (ev && ASSERT_NOT_EMPTY.has(ev)) return { type: "is_not_null", target, expected: null };
      if (ev && ASSERT_IS_EMPTY.has(ev)) return { type: "is_null", target, expected: null };
      return { type: target.startsWith("$") ? "jsonpath" : "equal", target, expected };
    });
}

/** 将 AI 返回的人类可读接口步骤整理成可直接执行的 API 用例字段。 */
function toInterfaceCase(moduleId: number, generated: AiGeneratedCase) {
  // 优先用 AI 给的结构化字段；缺失时再从 steps 文本里兜底解析
  let method = (generated.method || "").toUpperCase();
  let path = generated.path || "";
  const headers: Record<string, unknown> = { ...(generated.headers ?? {}) };
  let body: Record<string, unknown> | null = generated.body ?? null;
  const firstRequest = generated.requests?.[0];

  if (!method || !path) {
    for (const line of [generated.name, ...generated.steps]) {
      const m = line.match(/\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+([^\s，。；]+)/i);
      if (m) {
        method = method || m[1].toUpperCase();
        path = path || m[2];
        break;
      }
    }
  }
  if (firstRequest) {
    // 多步用例：v1 展示字段整体取第一步，避免"路径取第一步、body 取顶层"拼成不一致的假象
    method = (firstRequest.method || "GET").toUpperCase();
    path = firstRequest.path || path;
    for (const k of Object.keys(headers)) delete headers[k];
    Object.assign(headers, firstRequest.headers ?? {});
    body = firstRequest.body ?? null;
  }
  if (!method) method = "GET";
  if (Object.keys(headers).length === 0 || body === null) {
    for (const step of generated.steps) {
      const h = step.match(/^Header\s*:\s*([^:]+)\s*:\s*(.+)$/i);
      if (h && headers[h[1].trim()] === undefined) headers[h[1].trim()] = h[2].trim();
      const b = step.match(/^Body\s*:\s*([\s\S]+)$/i);
      if (b && body === null) {
        try {
          body = JSON.parse(b[1].trim());
        } catch {
          /* 非 JSON 文本忽略 */
        }
      }
    }
  }

  const ct =
    (headers["Content-Type"] as string) ?? (headers["content-type"] as string) ?? "application/json";
  const has = (o?: Record<string, unknown>) => o && Object.keys(o).length > 0;
  const primaryExtract = has(generated.extract) ? generated.extract : firstRequest?.extract;
  const primaryAssertion = has(generated.assertion) ? generated.assertion : firstRequest?.assertion;
  const primarySql = generated.sql || firstRequest?.sql || null;

  // 统一按操作流程执行：直接产出 http_request step（提取/断言进 step）。
  const extractRules = Object.entries(primaryExtract ?? {})
    .filter(([n, jp]) => n && jp)
    .map(([name, jp]) => ({ name, from: "response.body", jsonpath: String(jp) }));
  const assertionRules = assertionMapToRules(primaryAssertion ?? {});
  const safety = generated.data_safety;
  const safetyLines = [
    safety?.policy ? `数据安全：${safety.policy}` : "",
    safety?.rewritten_fields?.length ? `已自动加固字段：${safety.rewritten_fields.join("、")}` : "",
    safety?.readonly_seed_warnings?.length ? `只读种子提醒：${safety.readonly_seed_warnings.join("；")}` : "",
    safety?.function_hints?.length ? `动态函数建议：${safety.function_hints.join("；")}` : "",
    safety?.cleanup_required ? "清理建议：执行后清理 AUTO_TEST_ 命名空间下的临时测试数据" : "",
  ].filter(Boolean);
  // 场景用例：多步 requests → 多条 http_request step；单接口用例 → 一条 step。
  const steps =
    generated.requests && generated.requests.length > 0
      ? generated.requests.map((req, i) =>
          buildHttpStepFromReq(
            {
              method: req.method,
              path: req.path,
              headers: req.headers,
              body: req.body,
              extract: req.extract,
              assertion: req.assertion,
              sql: req.sql,
              name: req.name,
            },
            i,
            generated.name,
          ),
        )
      : [
          {
            step_order: 0,
            step_name: generated.name,
            step_type: "http_request",
            skip: false,
            config: { method, path, headers, data_type: ct, params: body ?? {}, sql_query: primarySql },
            extract: extractRules.length ? extractRules : null,
            assertion: assertionRules.length ? assertionRules : null,
            wait_before: 0,
            timeout: 60,
            retry: 0,
            on_failure: "stop" as const,
          },
        ];

  // 清理闭环（数据治理#2）：teardown_api / teardown_sql → post_hook（执行后无论成败都跑）
  const postHook = buildTeardownHooks(generated);

  // 会话隔离：pre_hook 登录前置（跑 steps 前先登录拿专属 token，免受前序用例登出/改密污染）
  const preHook = (generated.pre_hook ?? [])
    .filter((h) => h && (h.config as Record<string, unknown> | undefined)?.path)
    .map((h) => ({ type: h.type || "http_request", config: h.config }));

  // warnings：变量找不到来源 / 缺断言等提示，写进 description 引导用户/下一轮 AI 修正
  const warningLines = (generated.warnings ?? []).map((w) => `⚠️ ${w}`);

  return {
    module_id: moduleId,
    name: generated.name,
    description: [
      warningLines.length ? warningLines.join("\n") : "",
      safetyLines.length ? safetyLines.join("\n") : "",
      generated.preconditions.length ? `前置条件：${generated.preconditions.join("；")}` : "",
      generated.expected.length ? `预期结果：${generated.expected.join("；")}` : "",
    ]
      .filter(Boolean)
      .join("\n"),
    case_type: "api" as const,
    priority: 3,
    ...(preHook.length ? { pre_hook: preHook } : {}),
    ...(postHook.length ? { post_hook: postHook } : {}),
    steps,
  };
}

/** 把一个请求对象（场景多步里的一步，或单接口）转成 http_request step。 */
function buildHttpStepFromReq(
  req: { method?: string; path: string; headers?: Record<string, unknown>; body?: Record<string, unknown>; extract?: Record<string, unknown>; assertion?: Record<string, unknown>; sql?: string; name?: string },
  order: number,
  fallbackName: string,
) {
  const headers: Record<string, unknown> = { ...(req.headers ?? {}) };
  const ct = (headers["Content-Type"] as string) ?? (headers["content-type"] as string) ?? "application/json";
  const extractRules = Object.entries(req.extract ?? {})
    .filter(([n, jp]) => n && jp)
    .map(([name, jp]) => ({ name, from: "response.body", jsonpath: String(jp) }));
  const assertionRules = assertionMapToRules(req.assertion ?? {});
  return {
    step_order: order,
    step_name: req.name || `${fallbackName} #${order + 1}`,
    step_type: "http_request",
    skip: false,
    config: {
      method: (req.method || "GET").toUpperCase(),
      path: req.path,
      headers,
      data_type: ct,
      params: req.body ?? {},
      sql_query: req.sql || null,
    },
    extract: extractRules.length ? extractRules : null,
    assertion: assertionRules.length ? assertionRules : null,
    wait_before: 0,
    timeout: 60,
    retry: 0,
    on_failure: "stop" as const,
  };
}

/** teardown_api / teardown_sql → post_hook（CaseExecutor 在 finally 里跑，保证清理一定执行）。 */
function buildTeardownHooks(generated: AiGeneratedCase): Array<Record<string, unknown>> {
  const hooks: Array<Record<string, unknown>> = [];
  for (const t of generated.teardown_api ?? []) {
    if (!t?.path) continue;
    hooks.push({
      type: "http_request",
      config: {
        method: (t.method || "DELETE").toUpperCase(),
        path: t.path,
        headers: t.headers ?? {},
        params: t.body ?? {},
      },
    });
  }
  if (generated.teardown_sql && generated.teardown_sql.trim()) {
    hooks.push({ type: "sql", config: { sql: generated.teardown_sql.trim(), commit: true } });
  }
  return hooks;
}

function mergeCreatedCaseOrder(
  existing: Array<{ id: number; name: string; sort_order?: number | null }>,
  created: AiGeneratedCase[],
  createdIds: number[],
) {
  const merged: number[] = [];
  const used = new Set<number>();
  created.forEach((c, k) => {
    if (c.after === "__START__") {
      merged.push(createdIds[k]);
      used.add(k);
    }
  });
  for (const e of existing) {
    merged.push(e.id);
    created.forEach((c, k) => {
      if (!used.has(k) && c.after && c.after !== "__START__" && c.after === e.name) {
        merged.push(createdIds[k]);
        used.add(k);
      }
    });
  }
  created.forEach((_c, k) => {
    if (!used.has(k)) merged.push(createdIds[k]);
  });
  return merged;
}

/**
 * 接口用例按依赖做拓扑排序（问题9-12）：
 *  - 依赖 = 用例 A 引用了 ${var}，而 ${var} 由用例 B 的 extract 产出 ⇒ B 必须排在 A 前；
 *  - `externalVars`：模块已有用例（及变量池）能产出的变量名，视为已满足，
 *    避免新用例引用「已有登录用例的 token」时被误判为缺依赖（问题11）；
 *  - Kahn 拓扑排序：入度为 0 的按 interfaceCaseRank 启发式先后；
 *  - **环检测**：存在循环依赖时剩余用例按 rank 兜底排出，返回 hasCycle 供上层提示（问题12）。
 */
function orderInterfaceCasesForExecution(
  cases: AiGeneratedCase[],
  externalVars: Set<string> = new Set(),
): { ordered: AiGeneratedCase[]; hasCycle: boolean } {
  const n = cases.length;
  const produces = cases.map((c) => producedVariables(c));
  const refs = cases.map((c) => referencedVariables(c));

  // 变量 → 产出它的用例下标（同名多个取第一个产出者）
  const producerOf = new Map<string, number>();
  produces.forEach((set, i) => {
    set.forEach((name) => {
      if (!producerOf.has(name)) producerOf.set(name, i);
    });
  });

  // 建依赖边：j 产出、i 引用 ⇒ j 必须在 i 前（j → i）
  const indeg = new Array(n).fill(0);
  const edges: number[][] = Array.from({ length: n }, () => []);
  for (let i = 0; i < n; i++) {
    const dep = new Set<number>();
    refs[i].forEach((name) => {
      if (externalVars.has(name)) return; // 已有用例/变量池满足
      const j = producerOf.get(name);
      if (j != null && j !== i) dep.add(j);
    });
    dep.forEach((j) => {
      edges[j].push(i);
      indeg[i] += 1;
    });
  }

  const sortReady = (arr: number[]) =>
    arr.sort((a, b) => interfaceCaseRank(cases[a]) - interfaceCaseRank(cases[b]) || a - b);

  const orderedIdx: number[] = [];
  const placed = new Array(n).fill(false);
  let ready = sortReady([...Array(n).keys()].filter((i) => indeg[i] === 0));
  while (ready.length) {
    const i = ready.shift()!;
    if (placed[i]) continue;
    placed[i] = true;
    orderedIdx.push(i);
    for (const k of edges[i]) {
      indeg[k] -= 1;
      if (indeg[k] === 0 && !placed[k]) ready.push(k);
    }
    ready = sortReady(ready.filter((x) => !placed[x]));
  }

  // 环：剩余未放置的按 rank 兜底排出
  const hasCycle = orderedIdx.length < n;
  if (hasCycle) {
    const rest = [...Array(n).keys()]
      .filter((i) => !placed[i])
      .sort((a, b) => interfaceCaseRank(cases[a]) - interfaceCaseRank(cases[b]) || a - b);
    orderedIdx.push(...rest);
  }
  return { ordered: orderedIdx.map((i) => cases[i]), hasCycle };
}

function producedVariables(c: AiGeneratedCase) {
  const names = new Set(Object.keys(c.extract ?? {}).filter(Boolean));
  for (const req of c.requests ?? []) {
    for (const k of Object.keys(req.extract ?? {})) if (k) names.add(k);
  }
  return names;
}

function referencedVariables(c: AiGeneratedCase) {
  const text = JSON.stringify({
    path: c.path,
    headers: c.headers,
    body: c.body,
    sql: c.sql,
    requests: c.requests,
    steps: c.steps,
    expected: c.expected,
  });
  // 注意：${var.sub} 取基名 var，对齐后端校验（依赖判断只看变量来源是否存在）
  return new Set([...text.matchAll(/\$\{([A-Za-z_][\w.-]*)\}/g)].map((m) => m[1].split(".")[0]));
}

function interfaceCaseRank(c: AiGeneratedCase) {
  // 前置链最先执行（它准备账号并产出共享 token，后面都依赖它）
  if (/前置链/.test(c.name)) return 5;
  const text = `${c.name} ${c.method ?? ""} ${c.path ?? ""} ${c.steps.join(" ")} ${c.expected.join(" ")}`.toLowerCase();
  if (/login|登录|auth|token/.test(text)) return 10;
  if (/register|signup|create|add|新增|创建|注册|准备/.test(text)) return 20;
  if (/get|list|query|search|detail|获取|查询|列表|详情/.test(text)) return 30;
  if (/put|patch|update|modify|修改|更新/.test(text)) return 40;
  if (/delete|remove|删除|移除/.test(text)) return 50;
  if (/缺失|为空|null|非法|错误|边界|超长|鉴权|越权|未带|过期|异常/.test(text)) return 80;
  return 60;
}

type AiBatchFailure = {
  id: string;
  start: number;
  end: number;
  points: AiOutlinePoint[];
  message: string;
  attempts: number;
};

type AiGenerateDraft = {
  version: 1;
  savedAt: number;
  text: string;
  mode: "functional" | "interface";
  coverage: "standard" | "full" | "exhaustive";
  docUrls: string;
  setupDoc?: string;
  dimensions?: string[];
  smartInsert: boolean;
  modelName: string;
  gapModelName?: string;
  stage: "input" | "outline" | "cases";
  digest: string;
  points: AiOutlinePoint[];
  pickedPoints: number[];
  genQueue: AiOutlinePoint[];
  cursor: number;
  failedBatches: AiBatchFailure[];
  cases: AiGeneratedCase[];
  picked: number[];
  writtenNames: string[];
};

const LOCAL_TASKS_KEY = "local-in-progress-tasks:v1";
const LOCAL_TASKS_EVENT = "local-in-progress-tasks-change";
const LOCAL_TASKS_CANCEL_EVENT = "local-in-progress-task-cancel";

function aiGenerateDraftKey(projectId: number, moduleId: number, mode: "functional" | "interface") {
  return `ai-generate-draft:v1:${projectId}:${moduleId}:${mode}`;
}

function aiGenerateTaskId(projectId: number, moduleId: number, mode: "functional" | "interface") {
  const raw = `${projectId}${moduleId}${mode === "interface" ? 2 : 1}`;
  return -Math.abs(Number(raw.slice(0, 9)) || (projectId * 1000 + moduleId));
}

function updateLocalInProgressTask(task: {
  id: number;
  type_key: string;
  type_label: string;
  category: string;
  icon: string;
  name: string;
  status: string;
  project_id: number | null;
  project_name: string | null;
  detail_url: string;
} | null) {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(LOCAL_TASKS_KEY);
    const items = raw ? JSON.parse(raw) as Array<Record<string, unknown>> : [];
    const taskId = task?.id;
    const next = Array.isArray(items)
      ? items.filter((item) => item.id !== taskId && item.type_key !== task?.type_key)
      : [];
    if (task) {
      const existing = Array.isArray(items)
        ? items.find((item) => item.id === task.id || item.type_key === task.type_key)
        : null;
      next.unshift({
        ...task,
        started_at: existing?.started_at ?? new Date().toISOString(),
      });
    }
    window.localStorage.setItem(LOCAL_TASKS_KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(LOCAL_TASKS_EVENT));
  } catch {
    /* 本地任务仅用于展示，失败不阻断主流程 */
  }
}

function removeLocalInProgressTask(typeKey: string) {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(LOCAL_TASKS_KEY);
    const items = raw ? JSON.parse(raw) as Array<Record<string, unknown>> : [];
    const next = Array.isArray(items) ? items.filter((item) => item.type_key !== typeKey) : [];
    window.localStorage.setItem(LOCAL_TASKS_KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(LOCAL_TASKS_EVENT));
  } catch {
    /* 忽略浏览器存储异常 */
  }
}

function readAiGenerateDraft(projectId: number, moduleId: number, mode: "functional" | "interface") {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(aiGenerateDraftKey(projectId, moduleId, mode));
  if (!raw) return null;
  try {
    const draft = JSON.parse(raw) as AiGenerateDraft;
    return draft.version === 1 ? draft : null;
  } catch {
    return null;
  }
}

function writeAiGenerateDraft(projectId: number, moduleId: number, mode: "functional" | "interface", draft: AiGenerateDraft) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(aiGenerateDraftKey(projectId, moduleId, mode), JSON.stringify(draft));
  } catch {
    /* 本地存储空间不足时不阻断用户操作 */
  }
}

function removeAiGenerateDraft(projectId: number, moduleId: number, mode: "functional" | "interface") {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(aiGenerateDraftKey(projectId, moduleId, mode));
  } catch {
    /* 忽略浏览器存储异常 */
  }
}

function formatDraftTime(ts: number) {
  if (!ts) return "";
  return new Date(ts).toLocaleString();
}

/** 把富文本描述转成纯文本（需求 description 走 RichTextEditor，存的是 HTML）。 */
function htmlToText(html: string): string {
  if (!html) return "";
  if (!/[<>]/.test(html)) return html.trim();
  const doc = new DOMParser().parseFromString(html, "text/html");
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

/** AI 生成功能用例：需求文本 + 选模型 → 控件级详细用例草稿 → 审阅勾选 → 写入当前模块。 */
export function AiGenerateDialog({
  open,
  moduleId,
  projectId,
  initialMode = "functional",
  allowModeSwitch = false,
  onClose,
  onInserted,
}: {
  open: boolean;
  moduleId: number | null;
  projectId: number;
  initialMode?: "functional" | "interface";
  allowModeSwitch?: boolean;
  onClose: () => void;
  onInserted: () => void;
}) {
  const BATCH_SIZE = 6;
  const [text, setText] = useState("");
  const [mode, setMode] = useState<"functional" | "interface">(initialMode);
  const [coverage, setCoverage] = useState<"standard" | "full" | "exhaustive">("full");
  // 接口模式可选：勾选要生成的维度（空 = 按覆盖力度自动取舍全部）
  const [dimensions, setDimensions] = useState<Set<string>>(new Set());
  const [docUrls, setDocUrls] = useState("");
  // 接口模式可选：前置链账号准备接口信息（用户直接粘贴）
  const [setupDoc, setSetupDoc] = useState("");
  const [smartInsert, setSmartInsert] = useState(true);
  const [gapFilling, setGapFilling] = useState(false);
  const [modelName, setModelName] = useState("");
  const [gapModelName, setGapModelName] = useState("");
  const [enhanceAgentName, setEnhanceAgentName] = useState("");
  const [enhancing, setEnhancing] = useState(false);
  const [enhanceSummary, setEnhanceSummary] = useState<{
    summary: string;
    issues: string[];
    qualityScore?: number | null;
    runId?: number;
  } | null>(null);
  const [images, setImages] = useState<File[]>([]);
  const [docs, setDocs] = useState<File[]>([]);
  const [stage, setStage] = useState<"input" | "outline" | "cases">("input");
  // 抽屉顶部 Tab：模块大纲（长期保存）/ 生成用例（AI 向导）
  const [view, setView] = useState<"outline" | "generate">("generate");
  const [draftNotice, setDraftNotice] = useState("");
  const [savedDraft, setSavedDraft] = useState<AiGenerateDraft | null>(null);
  const [outlining, setOutlining] = useState(false);
  const [outlineError, setOutlineError] = useState("");
  const [digest, setDigest] = useState("");
  const [points, setPoints] = useState<AiOutlinePoint[]>([]);
  const [pickedPoints, setPickedPoints] = useState<Set<number>>(new Set());
  const [genQueue, setGenQueue] = useState<AiOutlinePoint[]>([]);
  const [cursor, setCursor] = useState(0);
  const [batchRunning, setBatchRunning] = useState(false);
  const [stoppingGeneration, setStoppingGeneration] = useState(false);
  const [failedBatches, setFailedBatches] = useState<AiBatchFailure[]>([]);
  const outlineAbortRef = useRef<AbortController | null>(null);
  const draftReadyRef = useRef(false);
  const initializedDraftKeyRef = useRef("");
  const wasOpenRef = useRef(false);
  const stopRef = useRef(false);
  const dragCaseRef = useRef<number | null>(null);
  const insertingRef = useRef(false);
  const [cases, setCases] = useState<AiGeneratedCase[]>([]);
  const casesRef = useRef<AiGeneratedCase[]>([]);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [inserting, setInserting] = useState(false);
  const [writtenNames, setWrittenNames] = useState<Set<string>>(new Set()); // 已写入的用例名（防重复、支持分次写）

  const modelsQuery = useQuery({
    queryKey: ["ai-models", projectId],
    queryFn: () => aiModelsApi.list(projectId),
    enabled: open,
  });
  const models = (modelsQuery.data ?? []).filter((m) => m.enabled);
  const isCliProvider = (provider: string) =>
    provider === "codex_cli" || provider === "claude_code";
  const apiModels = models.filter((m) => !isCliProvider(String(m.provider)));
  const cliAgents = models.filter((m) => isCliProvider(String(m.provider)));
  const gapModels = models;

  // 从需求池导入：选需求 → 选其分析文档 → 填入下方需求文本
  const [reqPickId, setReqPickId] = useState<number | null>(null);
  const [docPickId, setDocPickId] = useState<number | null>(null);
  const [docFilling, setDocFilling] = useState(false);
  const reqListQuery = useQuery({
    queryKey: ["aigen-requirements", projectId],
    queryFn: () => requirementsApi.list(projectId),
    enabled: open && mode === "functional",
  });
  const reqOptions = reqListQuery.data ?? [];
  const analysisDocsQuery = useQuery({
    queryKey: ["aigen-analysis-docs", reqPickId],
    queryFn: () => analysisDocsApi.listByRequirement(reqPickId as number),
    enabled: open && mode === "functional" && reqPickId != null,
  });
  const analysisDocs = analysisDocsQuery.data ?? [];

  const fillFromRequirement = (r: Requirement) => {
    const parts: string[] = [r.title];
    const desc = htmlToText(r.description ?? "");
    if (desc) parts.push(desc);
    if (r.acceptance_criteria?.length) {
      parts.push(
        "验收标准：\n" +
          r.acceptance_criteria.map((a, i) => `${i + 1}. ${a}`).join("\n"),
      );
    }
    setText(parts.join("\n\n"));
  };

  const fillFromDoc = async (docId: number) => {
    setDocFilling(true);
    try {
      const doc = await analysisDocsApi.get(docId);
      const md = (doc.current_markdown ?? "").trim();
      if (!md) {
        toast.error("该分析文档暂无内容");
        return;
      }
      setText(md);
      toast.success("已填入分析文档内容");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "读取分析文档失败");
    } finally {
      setDocFilling(false);
    }
  };

  const localTaskTypeKey =
    moduleId != null ? `local_ai_case_generate_${projectId}_${moduleId}_${mode}` : "";

  useEffect(() => {
    casesRef.current = cases;
  }, [cases]);

  useEffect(() => {
    if (!moduleId || !localTaskTypeKey) return;
    const running = outlining || gapFilling || batchRunning || enhancing;
    if (!running) {
      removeLocalInProgressTask(localTaskTypeKey);
      return;
    }
    const label = mode === "interface" ? "AI 生成接口用例" : "AI 生成功能用例";
    const phase = outlining
      ? "规划测试点"
      : gapFilling
        ? "查漏补缺"
        : enhancing
          ? "高级补全"
          : `生成详细用例 ${cursor}/${genQueue.length || points.length || 0}`;
    updateLocalInProgressTask({
      id: aiGenerateTaskId(projectId, moduleId, mode),
      type_key: localTaskTypeKey,
      type_label: label,
      category: "ai",
      icon: mode === "interface" ? "Globe" : "Sparkles",
      name: `${label} · ${phase}`,
      status: "running",
      project_id: projectId,
      project_name: null,
      detail_url: window.location.pathname + window.location.search,
    });
    return () => {
      removeLocalInProgressTask(localTaskTypeKey);
    };
  }, [
    moduleId,
    projectId,
    mode,
    localTaskTypeKey,
    outlining,
    gapFilling,
    batchRunning,
    enhancing,
    cursor,
    genQueue.length,
    points.length,
  ]);

  useEffect(() => {
    if (!moduleId || !localTaskTypeKey) return;
    const onCancel = (event: Event) => {
      const detail = (event as CustomEvent<{ type_key?: string; id?: number }>).detail;
      if (detail?.type_key !== localTaskTypeKey) return;
      stopRef.current = true;
      setStoppingGeneration(true);
      outlineAbortRef.current?.abort();
      outlineAbortRef.current = null;
      setOutlining(false);
      setGapFilling(false);
      setBatchRunning(false);
      setEnhancing(false);
      setStoppingGeneration(false);
      setOutlineError("已从任务列表终止生成");
      toast.info("已终止 AI 用例生成");
    };
    window.addEventListener(LOCAL_TASKS_CANCEL_EVENT, onCancel);
    return () => window.removeEventListener(LOCAL_TASKS_CANCEL_EVENT, onCancel);
  }, [moduleId, localTaskTypeKey]);

  useEffect(() => {
    if (!open) {
      wasOpenRef.current = false;
      return;
    }
    const identity = moduleId ? aiGenerateDraftKey(projectId, moduleId, initialMode) : "";
    if (identity && initializedDraftKeyRef.current === identity) {
      // 抽屉关闭只隐藏 UI，不重置或中止正在运行的生成任务。重新打开时回到最新产物。
      if (!wasOpenRef.current) {
        if (casesRef.current.length > 0 || genQueue.length > 0) setStage("cases");
        else if (points.length > 0 || digest) setStage("outline");
      }
      wasOpenRef.current = true;
      return;
    }

    const draft = moduleId ? readAiGenerateDraft(projectId, moduleId, initialMode) : null;
    const restoredCases = draft?.cases ?? [];
    setSavedDraft(null);
    setText(draft?.text ?? "");
    setMode(draft?.mode ?? initialMode);
    setCoverage(draft?.coverage ?? "full");
    setDimensions(new Set(draft?.dimensions ?? []));
    setDocUrls(draft?.docUrls ?? "");
    setSetupDoc(draft?.setupDoc ?? "");
    setSmartInsert(draft?.smartInsert ?? true);
    setModelName(draft?.modelName ?? "");
    setGapModelName(draft?.gapModelName ?? draft?.modelName ?? "");
    setStage(restoredCases.length || (draft?.genQueue.length ?? 0) > 0
      ? "cases"
      : (draft?.points.length ?? 0) > 0 || draft?.digest
        ? "outline"
        : "input");
    setOutlineError("");
    setDigest(draft?.digest ?? "");
    setPoints(draft?.points ?? []);
    setPickedPoints(new Set(draft?.pickedPoints ?? []));
    setGenQueue(draft?.genQueue ?? []);
    setCursor(draft?.cursor ?? 0);
    setFailedBatches(draft?.failedBatches ?? []);
    setCases(restoredCases);
    casesRef.current = restoredCases;
    setPicked(new Set(draft?.picked ?? []));
    setWrittenNames(new Set(draft?.writtenNames ?? []));
    setDraftNotice(draft ? `已自动恢复 ${formatDraftTime(draft.savedAt)} 保存的未写入草稿` : "");
    setView("generate");
    setReqPickId(null);
    setDocPickId(null);
    setImages([]);
    setDocs([]);
    stopRef.current = false;
    setStoppingGeneration(false);
    setEnhanceSummary(null);
    setEnhancing(false);
    initializedDraftKeyRef.current = identity;
    wasOpenRef.current = true;
    draftReadyRef.current = true;
  }, [open, initialMode, moduleId, projectId, digest, points.length, genQueue.length]);

  useEffect(() => {
    if (!moduleId || !draftReadyRef.current) return;
    const hasUsefulDraft =
      text.trim() ||
      digest ||
      points.length > 0 ||
      cases.length > 0 ||
      genQueue.length > 0 ||
      failedBatches.length > 0;
    if (!hasUsefulDraft) return;
    writeAiGenerateDraft(projectId, moduleId, initialMode, {
      version: 1,
      savedAt: Date.now(),
      text,
      mode,
      coverage,
      docUrls,
      setupDoc,
      dimensions: [...dimensions],
      smartInsert,
      modelName,
      gapModelName,
      // 导航回大纲/编辑需求不应覆盖可恢复节点；始终记录现有的最高完成阶段。
      stage: cases.length > 0 || genQueue.length > 0
        ? "cases"
        : points.length > 0 || digest
          ? "outline"
          : stage,
      digest,
      points,
      pickedPoints: [...pickedPoints],
      genQueue,
      cursor,
      failedBatches,
      cases,
      picked: [...picked],
      writtenNames: [...writtenNames],
    });
  }, [
    moduleId,
    projectId,
    initialMode,
    text,
    mode,
    coverage,
    docUrls,
    setupDoc,
    dimensions,
    smartInsert,
    modelName,
    gapModelName,
    stage,
    digest,
    points,
    pickedPoints,
    genQueue,
    cursor,
    failedBatches,
    cases,
    picked,
    writtenNames,
  ]);

  useEffect(() => {
    if (open && apiModels.length && !apiModels.some((m) => m.name === modelName)) {
      setModelName(apiModels[0].name);
    }
  }, [open, apiModels, modelName]);

  useEffect(() => {
    if (!open || !gapModels.length) return;
    if (gapModels.some((m) => m.name === gapModelName)) return;
    const fallback = gapModels.find((m) => m.name === modelName) ?? gapModels[0];
    setGapModelName(fallback.name);
  }, [open, gapModels, gapModelName, modelName]);

  useEffect(() => {
    if (open && cliAgents.length && !cliAgents.some((m) => m.name === enhanceAgentName)) {
      setEnhanceAgentName(cliAgents[0].name);
    }
  }, [open, cliAgents, enhanceAgentName]);

  const selectedModel = apiModels.find((m) => m.name === modelName);
  const visionWarn = images.length > 0 && selectedModel && !selectedModel.supports_vision;

  // 第一步：出测试点大纲 + 需求摘要
  const makeOutline = async () => {
    if (!moduleId) return;
    if (!modelName) {
      toast.error("请先选择 AI 模型");
      return;
    }
    outlineAbortRef.current?.abort();
    const controller = new AbortController();
    outlineAbortRef.current = controller;
    const timeout = window.setTimeout(() => {
      controller.abort();
    }, 180_000);
    setOutlineError("");
    setOutlining(true);
    setStoppingGeneration(false);
    try {
      const res = await functionalCasesApi.aiGenerateOutline({
        module_id: moduleId,
        text: text.trim(),
        model_name: modelName,
        mode,
        coverage,
        doc_urls: docUrls.trim(),
        dimensions: mode === "interface" ? [...dimensions].join(",") : "",
        setup_doc: mode === "interface" ? setupDoc.trim() : "",
        images,
        docs,
      }, controller.signal);
      setDigest(res.digest);
      setPoints(res.points);
      setPickedPoints(new Set(res.points.map((_, i) => i)));
      setStage("outline");
      if (res.points.length === 0) toast.info("没识别出测试点，补充需求或换模型再试");
    } catch (e) {
      const msg = controller.signal.aborted ? "生成测试点已停止或超时，请减少输入内容、更换模型后重试" : errorMessage(e);
      setOutlineError(msg);
      toast.error(msg);
    } finally {
      window.clearTimeout(timeout);
      if (outlineAbortRef.current === controller) outlineAbortRef.current = null;
      setOutlining(false);
      setStoppingGeneration(false);
    }
  };

  const stopOutline = () => {
    setStoppingGeneration(true);
    outlineAbortRef.current?.abort();
    setOutlineError("正在停止生成测试点…");
    toast.info("正在停止生成测试点");
  };

  // 第二步：按大纲分批生成详细用例（每批带已生成用例名 → 不重复、保持连贯）
  const runBatches = async (
    queue: AiOutlinePoint[],
    startCursor: number,
    existing: AiGeneratedCase[],
    options?: { retryFailureId?: string; displayOffset?: number },
  ) => {
    if (!moduleId) return;
    stopRef.current = false;
    setStoppingGeneration(false);
    setBatchRunning(true);
    let acc = existing.slice();
    let cur = startCursor;
    let failedCount = 0;
    while (cur < queue.length) {
      if (stopRef.current) break;
      const chunk = queue.slice(cur, cur + BATCH_SIZE);
      const displayStart = options?.displayOffset != null ? options.displayOffset + cur : cur;
      const displayEnd = displayStart + chunk.length;
      const failureId = `${displayStart}-${displayEnd}-${chunk.map((p) => p.title).join("|")}`;
      try {
        // 跨批次把前面已产出的变量名带过去，避免后批引用前批 extract 出的 ${id} 被误判缺来源
        const carriedVars = new Set<string>();
        for (const c of acc) producedVariables(c).forEach((v) => carriedVars.add(v));
        const res = await functionalCasesApi.aiGenerateBatch({
          module_id: moduleId,
          model_name: modelName,
          digest,
          points: chunk,
          done_names: acc.map((c) => c.name),
          mode,
          carried_vars: [...carriedVars],
          setup_doc: mode === "interface" ? setupDoc.trim() : "",
        });
        if (stopRef.current) break;
        acc = [...acc, ...res.cases];
        casesRef.current = acc;
        if (res.cases.length === 0) {
          failedCount += 1;
          setFailedBatches((prev) => upsertFailedBatch(prev, {
            id: options?.retryFailureId ?? failureId,
            start: displayStart,
            end: displayEnd,
            points: chunk,
            message: "本批返回 0 条有效用例",
            attempts: 1,
          }));
        } else if (options?.retryFailureId) {
          setFailedBatches((prev) => prev.filter((item) => item.id !== options.retryFailureId));
        }
        setCases(acc);
        // 默认全选，但与现有用例重名的（duplicate）不勾
        setPicked(new Set(acc.map((c, i) => (c.duplicate ? -1 : i)).filter((i) => i >= 0)));
      } catch (e) {
        if (stopRef.current) break;
        failedCount += 1;
        const message = errorMessage(e);
        setFailedBatches((prev) => upsertFailedBatch(prev, {
          id: options?.retryFailureId ?? failureId,
          start: displayStart,
          end: displayEnd,
          points: chunk,
          message,
          attempts: 1,
        }));
      } finally {
        cur += chunk.length;
        setCursor((prev) => Math.max(prev, cur));
      }
    }
    setBatchRunning(false);
    setStoppingGeneration(false);
    if (stopRef.current) {
      toast.info("已停止生成后续批次");
    } else if (failedCount > 0) {
      toast.warning(`有 ${failedCount} 个批次生成失败，已跳过并继续生成后续测试点`);
    }
  };

  const startGeneration = () => {
    const q = points.filter((_, i) => pickedPoints.has(i));
    if (!q.length) {
      toast.info("请至少选一个测试点");
      return;
    }
    setGenQueue(q);
    setCursor(0);
    setCases([]);
    casesRef.current = [];
    setPicked(new Set());
    setWrittenNames(new Set());
    setFailedBatches([]);
    setEnhanceSummary(null);
    setStage("cases");
    setStoppingGeneration(false);
    void runBatches(q, 0, []);
  };

  const retryFailedBatch = (failure: AiBatchFailure) => {
    void runBatches(failure.points, 0, casesRef.current, {
      retryFailureId: failure.id,
      displayOffset: failure.start,
    });
  };

  const retryAllFailedBatches = async () => {
    const failures = failedBatches.slice();
    for (const failure of failures) {
      if (stopRef.current) break;
      await runBatches(failure.points, 0, casesRef.current, {
        retryFailureId: failure.id,
        displayOffset: failure.start,
      });
    }
  };

  const restoreSavedDraft = () => {
    if (!savedDraft) return;
    const draft = savedDraft;
    draftReadyRef.current = false;
    setText(draft.text ?? "");
    setMode(draft.mode ?? initialMode);
    setCoverage(draft.coverage ?? "full");
    setDimensions(new Set(draft.dimensions ?? []));
    setDocUrls(draft.docUrls ?? "");
    setSetupDoc(draft.setupDoc ?? "");
    setSmartInsert(draft.smartInsert ?? true);
    setModelName(draft.modelName ?? "");
    setGapModelName(draft.gapModelName ?? draft.modelName ?? "");
    setStage(draft.stage ?? "input");
    setOutlineError("");
    setDigest(draft.digest ?? "");
    setPoints(draft.points ?? []);
    setPickedPoints(new Set(draft.pickedPoints ?? []));
    setGenQueue(draft.genQueue ?? []);
    setCursor(draft.cursor ?? 0);
    setFailedBatches(draft.failedBatches ?? []);
    setStoppingGeneration(false);
    setCases(draft.cases ?? []);
    casesRef.current = draft.cases ?? [];
    setPicked(new Set(draft.picked ?? []));
    setWrittenNames(new Set(draft.writtenNames ?? []));
    setDraftNotice(`已恢复 ${formatDraftTime(draft.savedAt)} 保存的未写入草稿`);
    setSavedDraft(null);
    window.setTimeout(() => {
      draftReadyRef.current = true;
    }, 0);
    toast.success("已恢复 AI 生成草稿");
  };

  const clearSavedDraft = () => {
    if (!moduleId) return;
    draftReadyRef.current = false;
    removeAiGenerateDraft(projectId, moduleId, initialMode);
    setSavedDraft(null);
    setText("");
    setMode(initialMode);
    setCoverage("full");
    setDimensions(new Set());
    setDocUrls("");
    setSetupDoc("");
    setSmartInsert(true);
    setStage("input");
    setOutlineError("");
    setDigest("");
    setPoints([]);
    setPickedPoints(new Set());
    setGenQueue([]);
    setCursor(0);
    setFailedBatches([]);
    setEnhanceSummary(null);
    setCases([]);
    casesRef.current = [];
    setPicked(new Set());
    setWrittenNames(new Set());
    setDraftNotice("");
    window.setTimeout(() => {
      draftReadyRef.current = true;
    }, 0);
    toast.success("已清除 AI 生成草稿");
  };

  const insert = async () => {
    if (!moduleId || cases.length === 0) return;
    if (insertingRef.current) return; // 防快速多点
    // 只写「已勾选且还没写过」的，支持分次写入、绝不重复
    const chosen = cases.filter((_, i) => picked.has(i) && !writtenNames.has(cases[i].name));
    if (chosen.length === 0) {
      toast.info("勾选的用例都已写入");
      return;
    }
    insertingRef.current = true;
    setInserting(true);
    try {
      if (mode === "interface") {
        const existing =
          smartInsert
            ? (await automationCasesApi.list({ moduleId, pageSize: 500 })).items
                .slice()
                .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
            : [];
        // 模块已有用例能产出的变量（如已有登录用例的 token）视为已满足，避免误判缺依赖
        const externalVars = new Set<string>();
        for (const e of existing) {
          try {
            const d = JSON.parse((e as { extract_data?: string }).extract_data || "{}");
            if (d && typeof d === "object") Object.keys(d).forEach((k) => k && externalVars.add(k));
          } catch {
            /* 忽略无法解析的 extract_data */
          }
        }
        const { ordered, hasCycle } = orderInterfaceCasesForExecution(chosen, externalVars);
        if (hasCycle) {
          toast.warning("检测到用例间存在循环依赖，已尽力排序但请人工核对执行顺序");
        }
        const createdIds: number[] = [];
        for (const c of ordered) {
          const res = await casesApi.create(toInterfaceCase(moduleId, c));
          createdIds.push(res.id);
        }
        if (smartInsert && existing.length) {
          const merged = mergeCreatedCaseOrder(existing, ordered, createdIds);
          await casesApi.reorder(
            merged.map((id, idx) => ({ type: "case" as const, id, new_order: idx })),
          );
        }
        // P0-2 试跑：写入成功后一键回归本批，跑绿=已验证，跑挂=真 bug 或用例要修
        if (createdIds.length) {
          toast.success(`已写入 ${createdIds.length} 条接口用例`, {
            duration: 15000,
            description: "建议立即试跑本批：跑通即验证可用，失败尽早暴露问题",
            action: {
              label: `试跑本批（${createdIds.length}）`,
              onClick: () => {
                void runsApi
                  .trigger({ project: projectId, category: "api", case_ids: createdIds })
                  .then((r) =>
                    toast.success(
                      `试跑已提交（${r.case_number ?? createdIds.length} 条），到「执行记录」查看报告`,
                    ),
                  )
                  .catch((e) => handleApiError(e));
              },
            },
          });
        }
      } else {
        // AI 智能插入：先取现有有序用例，创建后按每条 after 合并重排
        const existing =
          smartInsert
            ? (await functionalCasesApi.list({ moduleId, pageSize: 500 })).items
                .slice()
                .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
            : [];
        const createdIds: number[] = [];
        for (const c of chosen) {
          const res = await functionalCasesApi.create({
            module_id: moduleId,
            name: c.name,
            priority: 3,
            functional_spec: {
              preconditions: c.preconditions,
              steps: c.steps,
              expected: c.expected.join("\n") || null,
            },
          });
          createdIds.push(res.id);
        }
        if (smartInsert && existing.length) {
          // 合并：把每条新用例插到它 after 指定的现有用例之后（同锚点保持本次顺序）
          const merged = mergeCreatedCaseOrder(existing, chosen, createdIds);
          await casesApi.reorder(
            merged.map((id, idx) => ({ type: "case" as const, id, new_order: idx })),
          );
        }
      }
      setWrittenNames((prev) => new Set([...prev, ...chosen.map((c) => c.name)]));
      if (mode !== "interface") {
        // interface 分支已在上面弹带「试跑本批」动作的 toast，这里避免重复
        toast.success(`已写入 ${chosen.length} 条用例（可继续生成后再写新增的）`);
      }
      onInserted();
    } catch (e) {
      handleApiError(e);
    } finally {
      setInserting(false);
      insertingRef.current = false;
    }
  };

  const togglePick = (i: number) =>
    setPicked((p) => {
      const n = new Set(p);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  const togglePoint = (i: number) =>
    setPickedPoints((p) => {
      const n = new Set(p);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  // 查漏补缺：给已有大纲找遗漏的测试点，追加并默认勾选
  const fillGaps = async () => {
    if (!moduleId || points.length === 0) return;
    if (!gapModelName) {
      toast.error("请先选择 AI 模型");
      return;
    }
    const gapModel = gapModels.find((m) => m.name === gapModelName);
    const useCli = gapModel ? isCliProvider(String(gapModel.provider)) : false;
    stopRef.current = false;
    setGapFilling(true);
    try {
      const body = {
        module_id: moduleId,
        model_name: gapModelName,
        mode,
        digest,
        points,
        // 带上生成大纲时的原始材料：查漏只有 digest 会信息不对称，模型找不出字段级遗漏
        text,
        doc_urls: docUrls,
      };
      const res = useCli
        ? await functionalCasesApi.aiOutlineGapsCli(body)
        : await functionalCasesApi.aiOutlineGaps(body);
      if (stopRef.current) return;
      const have = new Set(points.map((p) => p.title.replace(/\s+/g, "")));
      const added = res.points.filter((p) => p.title && !have.has(p.title.replace(/\s+/g, "")));
      if (added.length === 0) {
        toast.info("没找到遗漏，大纲已比较全面");
      } else {
        setPoints((prev) => {
          const next = [...prev, ...added];
          setPickedPoints((sel) => {
            const n = new Set(sel);
            for (let i = prev.length; i < next.length; i++) n.add(i);
            return n;
          });
          return next;
        });
        toast.success(`补充了 ${added.length} 个测试点`);
      }
    } catch (e) {
      handleApiError(e);
    } finally {
      setGapFilling(false);
    }
  };

  const enhanceCases = async () => {
    if (!moduleId || cases.length === 0) return;
    if (!enhanceAgentName) {
      toast.error("请先在项目配置 → AI 添加并启用 Codex CLI 或 Claude Code");
      return;
    }
    setEnhancing(true);
    try {
      const res = await functionalCasesApi.aiEnhanceCases({
        module_id: moduleId,
        agent_model_name: enhanceAgentName,
        digest,
        requirement_text: text,
        cases,
        mode,
        target_extra_count: Math.max(3, Math.ceil(cases.length * 0.3)),
      });
      setCases(res.cases);
      casesRef.current = res.cases;
      setPicked(
        new Set(
          res.cases.map((c, i) => (c.duplicate ? -1 : i)).filter((i) => i >= 0),
        ),
      );
      setEnhanceSummary({
        summary: res.summary,
        issues: res.issues_found ?? [],
        qualityScore: res.quality_score,
        runId: res.run_id,
      });
      toast.success(`高级补全完成，返回 ${res.cases.length} 条候选用例`);
    } catch (e) {
      handleApiError(e);
    } finally {
      setEnhancing(false);
    }
  };

  // 审阅时拖拽调整用例顺序（= 写入后的执行顺序）。按对象身份保持勾选不变。
  const moveCase = (from: number, to: number) => {
    if (from === to) return;
    setCases((prev) => {
      const pickedItems = new Set([...picked].map((i) => prev[i]));
      const next = prev.slice();
      const [m] = next.splice(from, 1);
      next.splice(to, 0, m);
      setPicked(new Set(next.map((c, i) => (pickedItems.has(c) ? i : -1)).filter((i) => i >= 0)));
      return next;
    });
  };

  const pendingInsertCount = cases.filter(
    (c, i) => picked.has(i) && !writtenNames.has(c.name),
  ).length;

  const generateFooter = view === "generate" ? (
    stage === "input" ? (
      <DialogFooter>
        <Button variant="outline" onClick={outlining ? stopOutline : onClose} disabled={stoppingGeneration}>
          {outlining && stoppingGeneration ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> 停止中…
            </>
          ) : outlining ? (
            "停止"
          ) : (
            "取消"
          )}
        </Button>
        <Button onClick={makeOutline} disabled={outlining || !modelName}>
          {outlining ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> 规划测试点…
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" /> 生成大纲
            </>
          )}
        </Button>
      </DialogFooter>
    ) : stage === "outline" ? (
      <DialogFooter>
        <Button variant="outline" onClick={() => setStage("input")}>
          上一步
        </Button>
        <Button onClick={startGeneration} disabled={pickedPoints.size === 0}>
          <Sparkles className="h-4 w-4" /> 开始生成（{pickedPoints.size} 个点）
        </Button>
      </DialogFooter>
    ) : (
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          关闭
        </Button>
        <Button onClick={insert} disabled={inserting || batchRunning || enhancing || pendingInsertCount === 0}>
          {inserting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {pendingInsertCount === 0 && writtenNames.size > 0
            ? "已全部写入"
            : `写入当前模块（${pendingInsertCount}）`}
        </Button>
      </DialogFooter>
    )
  ) : null;

  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      storageKey="ai-gen-drawer-width"
      defaultWidth={720}
      minWidth={560}
      footer={generateFooter}
      title={
        <>
          <Sparkles className="h-[17px] w-[17px] text-primary" />
          AI 生成{mode === "interface" ? "接口" : "功能"}用例
        </>
      }
    >
      <div className="flex-1 space-y-3 overflow-auto p-4">
        <div className="flex w-fit items-center gap-1 rounded-md border bg-muted/30 p-0.5 text-xs">
          <button onClick={() => setView("generate")} className={cn("rounded px-3 py-1", view === "generate" ? "bg-background font-medium shadow-sm" : "text-muted-foreground")}>生成用例</button>
          <button onClick={() => setView("outline")} className={cn("rounded px-3 py-1", view === "outline" ? "bg-background font-medium shadow-sm" : "text-muted-foreground")}>模块大纲</button>
        </div>
        {view === "outline" ? (
          <ModuleOutlinePanel moduleId={moduleId} projectId={projectId} mode={mode} onApplied={onInserted} />
        ) : (
        <>
        <p className="text-xs text-muted-foreground">
          先出测试点大纲 → 你确认 → 逐批生成控件级详细用例（参考其它模块、保持连贯），审阅后写入当前模块
        </p>
        {draftNotice ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-primary">
            {savedDraft ? (
              <button className="text-left font-medium hover:underline" onClick={restoreSavedDraft}>
                {draftNotice}
              </button>
            ) : (
              <span>{draftNotice}</span>
            )}
            <button className="font-medium hover:underline" onClick={clearSavedDraft}>
              清除草稿
            </button>
          </div>
        ) : null}

        {stage === "input" ? (
          <div className="space-y-3">
            {allowModeSwitch ? (
              <div className="flex w-fit items-center gap-1 rounded-md border bg-muted/30 p-0.5 text-xs">
                {(["functional", "interface"] as const).map((mo) => (
                  <button
                    key={mo}
                    onClick={() => setMode(mo)}
                    className={cn(
                      "rounded px-3 py-1",
                      mode === mo ? "bg-background font-medium shadow-sm" : "text-muted-foreground",
                    )}
                  >
                    {mo === "functional" ? "功能用例" : "接口用例"}
                  </button>
                ))}
              </div>
            ) : null}
            {mode === "functional" ? (
            <div className="space-y-2 rounded-md border bg-muted/20 p-3">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <FileText className="h-3.5 w-3.5" /> 从需求池导入（可选）
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label className="text-xs">需求</Label>
                  <select
                    value={reqPickId ?? ""}
                    onChange={(e) => {
                      const id = e.target.value ? Number(e.target.value) : null;
                      setReqPickId(id);
                      setDocPickId(null);
                      const r = reqOptions.find((x) => x.id === id);
                      if (r) fillFromRequirement(r);
                    }}
                    disabled={reqListQuery.isLoading}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                  >
                    <option value="">
                      {reqListQuery.isLoading
                        ? "加载中…"
                        : reqOptions.length
                          ? "选择需求…"
                          : "（暂无需求）"}
                    </option>
                    {reqOptions.map((r) => (
                      <option key={r.id} value={r.id}>
                        #{r.id} {r.title}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">分析文档</Label>
                  <select
                    value={docPickId ?? ""}
                    onChange={(e) => {
                      const id = e.target.value ? Number(e.target.value) : null;
                      setDocPickId(id);
                      if (id) fillFromDoc(id);
                    }}
                    disabled={reqPickId == null || analysisDocsQuery.isLoading || docFilling}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-60"
                  >
                    <option value="">
                      {reqPickId == null
                        ? "先选需求"
                        : analysisDocsQuery.isLoading
                          ? "加载中…"
                          : analysisDocs.length
                            ? "选择分析文档…"
                            : "（该需求暂无分析文档）"}
                    </option>
                    {analysisDocs.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.title}
                        {d.model_label ? `（${d.model_label}）` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <p className="text-[11px] text-muted-foreground">
                选需求会填入其描述 + 验收标准；选分析文档会填入 AI 分析后的完整内容到下方需求框，可再手动编辑。
              </p>
            </div>
            ) : null}
            <div className="space-y-1">
              <Label className="text-xs">{mode === "interface" ? "接口说明 / 需求" : "需求 / 描述"}</Label>
              <Textarea
                rows={5}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={
                  mode === "interface"
                    ? "粘贴接口说明：URL、method、请求参数、响应字段等。或在下方上传/填链接。例如：POST /api/login，body{username,password}，成功返回 200{token,role}……"
                    : "粘贴需求描述、要测的功能点、页面流程等。例如：登录页，输入用户名密码点登录，成功后跳转到管理员工作台……"
                }
              />
            </div>
            {mode === "interface" ? (
              <div className="space-y-1">
                <Label className="text-xs">接口文档链接（Swagger UI / OpenAPI / 在线接口文档，多个换行或逗号分隔）</Label>
                <Textarea
                  rows={2}
                  value={docUrls}
                  onChange={(e) => setDocUrls(e.target.value)}
                  placeholder="https://example.com/v3/api-docs &#10;https://example.com/swagger-ui/index.html"
                />
              </div>
            ) : null}
            {mode === "interface" ? (
              <div className="space-y-1">
                <Label className="text-xs">前置链·账号准备接口（可选，给前置链跨模块建测试账号用）</Label>
                <Textarea
                  rows={2}
                  value={setupDoc}
                  onChange={(e) => setSetupDoc(e.target.value)}
                  placeholder={'粘贴"创建账号/注册"接口信息，如：\nPOST /api/users  body: {username, password, role_codes}  响应: {data:{id, username}}'}
                />
                <p className="text-xs text-muted-foreground">
                  当前模块没有注册接口时，填这个，前置链就能用它建一个一次性测试账号（改密/删除类用例不再卡 admin）。
                </p>
              </div>
            ) : null}
            {/* 上传：截图/原型图 + 文档 */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs">截图 / 原型图 / 设计稿</Label>
                <label className="flex h-9 cursor-pointer items-center gap-2 rounded-md border border-dashed px-3 text-xs text-muted-foreground hover:bg-muted/50">
                  <Upload className="h-3.5 w-3.5" /> 选择图片
                  <input
                    type="file"
                    accept="image/*"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      setImages((p) => [...p, ...Array.from(e.target.files ?? [])]);
                      e.target.value = "";
                    }}
                  />
                </label>
                {images.length ? (
                  <div className="flex flex-wrap gap-1">
                    {images.map((f, i) => (
                      <span key={i} className="flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px]">
                        {f.name}
                        <button
                          onClick={() => setImages((p) => p.filter((_, k) => k !== i))}
                          className="text-muted-foreground hover:text-destructive"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="space-y-1">
                <Label className="text-xs">
                  {mode === "interface"
                    ? "接口文档（Swagger/OpenAPI/Postman .json/.yaml，或 Word/PDF/MD）"
                    : "需求文档（PDF/Word/MD/TXT）"}
                </Label>
                <label className="flex h-9 cursor-pointer items-center gap-2 rounded-md border border-dashed px-3 text-xs text-muted-foreground hover:bg-muted/50">
                  <FileText className="h-3.5 w-3.5" /> 选择文档
                  <input
                    type="file"
                    accept=".pdf,.docx,.doc,.md,.markdown,.txt,.json,.yaml,.yml"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      setDocs((p) => [...p, ...Array.from(e.target.files ?? [])]);
                      e.target.value = "";
                    }}
                  />
                </label>
                {docs.length ? (
                  <div className="flex flex-wrap gap-1">
                    {docs.map((f, i) => (
                      <span key={i} className="flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px]">
                        {f.name}
                        <button
                          onClick={() => setDocs((p) => p.filter((_, k) => k !== i))}
                          className="text-muted-foreground hover:text-destructive"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
            {visionWarn ? (
              <div className="text-xs text-amber-600">
                所选模型不支持看图，图片将走 OCR 抽文字（效果较弱）。建议选带「· 视觉」的模型。
              </div>
            ) : null}
            <div className="space-y-1">
              <Label className="text-xs">AI 模型</Label>
              <select
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm"
              >
                {apiModels.length === 0 ? (
                  <option value="">（无可用模型，请先到项目配置 → AI 添加）</option>
                ) : null}
                {apiModels.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name}（{m.provider}/{m.model}）{m.supports_vision ? " · 视觉" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">覆盖力度</Label>
              <select
                value={coverage}
                onChange={(e) => setCoverage(e.target.value as "standard" | "full" | "exhaustive")}
                className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm"
              >
                <option value="standard">标准（少量：冒烟主流程 + 关键风险）</option>
                <option value="full">全面（中等：核心字段/场景系统覆盖，推荐）</option>
                <option value="exhaustive">穷尽（大量：每字段每维度都拆）</option>
              </select>
              <p className="text-xs text-muted-foreground">
                标准会明显减少数量；穷尽会显著增加测试点，适合最后补全覆盖。
              </p>
            </div>
            {mode === "interface" ? (
              <div className="space-y-1">
                <Label className="text-xs">测试维度（可选，不勾=全部）</Label>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {["正常", "参数校验", "边界", "鉴权", "越权", "响应校验", "安全", "场景", "关联"].map((d) => (
                    <label key={d} className="flex items-center gap-1 text-xs">
                      <input
                        type="checkbox"
                        checked={dimensions.has(d)}
                        onChange={(e) =>
                          setDimensions((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(d);
                            else next.delete(d);
                            return next;
                          })
                        }
                      />
                      {d}
                    </label>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  只想补某几类（如只要「安全」「场景」）时勾选；留空则按覆盖力度生成全部维度。
                </p>
              </div>
            ) : null}
            {outlineError ? (
              <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {outlineError}
              </div>
            ) : null}
            <details className="rounded-md border bg-muted/20 p-2">
              <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                项目概览 · 模块关联（生成时会据此设计跨模块联动用例）
              </summary>
              <div className="mt-2">
                <ProjectAiOverviewView projectId={projectId} />
              </div>
            </details>
          </div>
        ) : stage === "outline" ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                共 {points.length} 个测试点，已选 {pickedPoints.size} 个（可勾掉不想要的）
              </span>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <select
                  value={gapModelName}
                  onChange={(e) => setGapModelName(e.target.value)}
                  disabled={gapFilling || batchRunning}
                  className="h-8 max-w-[260px] rounded-md border border-input bg-background px-2 text-xs disabled:opacity-60"
                  title="查漏补缺使用的 AI 模型；Codex CLI / Claude Code 只用于查漏，不用于后续批量生成"
                >
                  {gapModels.length === 0 ? (
                    <option value="">（无可用模型）</option>
                  ) : null}
                  {gapModels.map((m) => (
                    <option key={m.name} value={m.name}>
                      {m.name}（{m.provider}/{m.model}）
                      {isCliProvider(String(m.provider)) ? " · CLI" : m.supports_vision ? " · 视觉" : ""}
                    </option>
                  ))}
                </select>
                <button
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline disabled:opacity-50"
                  disabled={gapFilling || points.length === 0 || !gapModelName}
                  onClick={fillGaps}
                  title="让 AI 再查一遍遗漏的测试点并补上，可反复点"
                >
                  {gapFilling ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> 查漏中…
                    </>
                  ) : (
                    <>
                      <Search className="h-3.5 w-3.5" /> 查漏补缺
                    </>
                  )}
                </button>
                <button className="text-xs text-primary hover:underline" onClick={() => setStage("input")}>
                  ← 改需求
                </button>
              </div>
            </div>
            {digest ? (
              <details className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
                <summary className="cursor-pointer font-medium">需求摘要（AI 提炼，用于保持各批连贯）</summary>
                <div className="mt-1 whitespace-pre-wrap">{digest}</div>
              </details>
            ) : null}
            <div className="max-h-[45vh] space-y-1 overflow-y-auto pr-1">
              {points.map((p, i) => (
                <label
                  key={i}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded border px-2 py-1 text-sm",
                    pickedPoints.has(i) ? "border-primary/40 bg-primary/5" : "bg-card",
                  )}
                >
                  <input type="checkbox" checked={pickedPoints.has(i)} onChange={() => togglePoint(i)} />
                  <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {p.category || "其它"}
                  </span>
                  <span className="min-w-0 flex-1">{p.title}</span>
                </label>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                已生成 {cases.length} 条 · 测试点 {cursor}/{genQueue.length}
                {failedBatches.length > 0 ? ` · 失败批次 ${failedBatches.length}` : ""}
                {batchRunning ? (stoppingGeneration ? "（停止中，等待当前批次结束…）" : "（生成中…）") : ""}
              </span>
              <div className="flex items-center gap-3">
                {batchRunning ? (
                  <button
                    className="inline-flex items-center gap-1 text-xs text-destructive hover:underline disabled:cursor-not-allowed disabled:opacity-70"
                    disabled={stoppingGeneration}
                    onClick={() => {
                      stopRef.current = true;
                      setStoppingGeneration(true);
                      toast.info("正在停止生成，当前批次返回后会结束");
                    }}
                  >
                    {stoppingGeneration ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> 停止中…
                      </>
                    ) : (
                      "停止"
                    )}
                  </button>
                ) : cursor < genQueue.length ? (
                  <button
                    className="text-xs text-primary hover:underline"
                    onClick={() => void runBatches(genQueue, cursor, cases)}
                  >
                    继续生成剩余（{genQueue.length - cursor}）
                  </button>
                ) : failedBatches.length > 0 ? (
                  <button
                    className="text-xs text-primary hover:underline"
                    onClick={() => void retryAllFailedBatches()}
                  >
                    重试失败批次（{failedBatches.length}）
                  </button>
                ) : null}
                {!batchRunning ? (
                  <button className="text-xs text-primary hover:underline" onClick={() => setStage("outline")}>
                    ← 回大纲
                  </button>
                ) : null}
              </div>
            </div>
            {genQueue.length ? (
              <div className="h-1 w-full overflow-hidden rounded bg-muted">
                <div
                  className="h-full bg-primary transition-all"
                  style={{ width: `${Math.round((cursor / genQueue.length) * 100)}%` }}
                />
              </div>
            ) : null}
            {cases.length > 0 && !batchRunning ? (
              <div className="rounded-md border bg-muted/20 p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    高级补全
                  </span>
                  <select
                    value={enhanceAgentName}
                    onChange={(e) => setEnhanceAgentName(e.target.value)}
                    disabled={enhancing}
                    className="h-8 max-w-[260px] rounded-md border border-input bg-background px-2 text-xs disabled:opacity-60"
                  >
                    {cliAgents.length === 0 ? (
                      <option value="">（先配置 Codex CLI / Claude Code）</option>
                    ) : null}
                    {cliAgents.map((m) => (
                      <option key={m.name} value={m.name}>
                        {m.name}（{m.provider}/{m.model}）
                      </option>
                    ))}
                  </select>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={enhanceCases}
                    disabled={enhancing || !enhanceAgentName || cases.length === 0}
                    className="h-8"
                  >
                    {enhancing ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Sparkles className="h-3.5 w-3.5" />
                    )}
                    一键补全
                  </Button>
                  <span className="text-[11px] text-muted-foreground">
                    用会员 CLI Agent 审稿、补边界/异常/安全场景，结果仍需勾选后写入。
                  </span>
                </div>
                {enhanceSummary ? (
                  <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                    <div>
                      {enhanceSummary.qualityScore != null ? (
                        <span className="mr-2 rounded bg-primary/10 px-1.5 py-0.5 text-primary">
                          评分 {String(enhanceSummary.qualityScore)}
                        </span>
                      ) : null}
                      {enhanceSummary.summary || "高级补全已完成"}
                      {enhanceSummary.runId ? (
                        <span className="ml-2">Run #{enhanceSummary.runId}</span>
                      ) : null}
                    </div>
                    {enhanceSummary.issues.length ? (
                      <div>发现：{enhanceSummary.issues.slice(0, 5).join("；")}</div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            {failedBatches.length > 0 && !batchRunning ? (
              <div className="space-y-2 rounded-md border border-amber-200 bg-amber-50/60 p-2 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-amber-800">
                    有 {failedBatches.length} 个批次生成失败，已跳过并继续生成其它测试点
                  </span>
                  <button className="text-primary hover:underline" onClick={() => void retryAllFailedBatches()}>
                    重试全部失败批次
                  </button>
                </div>
                <div className="max-h-36 space-y-1 overflow-y-auto pr-1">
                  {failedBatches.map((failure) => (
                    <div key={failure.id} className="rounded border border-amber-200 bg-background/80 p-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-amber-800">
                          测试点 {failure.start + 1}-{failure.end} · 已重试 {Math.max(0, failure.attempts - 1)} 次
                        </span>
                        <button className="shrink-0 text-primary hover:underline" onClick={() => retryFailedBatch(failure)}>
                          重试本批
                        </button>
                      </div>
                      <div className="mt-1 break-all text-red-700">{failure.message}</div>
                      <div className="mt-1 text-muted-foreground">
                        {failure.points.map((point) => point.title).join("；")}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {cases.length > 0 && !batchRunning ? (
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>已按执行依赖排序；可拖动左侧手柄调整顺序</span>
                {mode === "functional" ? (
                  <label className="flex cursor-pointer items-center gap-1.5 text-foreground/80">
                    <input
                      type="checkbox"
                      checked={smartInsert}
                      onChange={(e) => setSmartInsert(e.target.checked)}
                    />
                    AI 智能插入位置（插到现有用例之间，而非追加末尾）
                  </label>
                ) : null}
              </div>
            ) : null}
            <div className="space-y-2 pr-1">
              {cases.length === 0 && batchRunning ? (
                <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> 正在生成第一批…
                </div>
              ) : null}
              {cases.map((c, i) => (
                <div
                  key={i}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => {
                    if (dragCaseRef.current !== null) moveCase(dragCaseRef.current, i);
                    dragCaseRef.current = null;
                  }}
                  className={cn(
                    "rounded-lg border p-2 text-sm",
                    picked.has(i) ? "border-primary/40 bg-primary/5" : "bg-card",
                  )}
                >
                  <div className="flex items-start gap-2">
                    <span
                      draggable
                      onDragStart={() => {
                        dragCaseRef.current = i;
                      }}
                      className="mt-0.5 cursor-grab text-muted-foreground active:cursor-grabbing"
                      title="拖动调整执行顺序"
                    >
                      <GripVertical className="h-4 w-4" />
                    </span>
                    <input
                      type="checkbox"
                      checked={picked.has(i)}
                      onChange={() => togglePick(i)}
                      className="mt-1"
                    />
                    <div className="min-w-0 flex-1 cursor-pointer" onClick={() => togglePick(i)}>
                      <div className="font-medium">
                        <span className="mr-1 text-muted-foreground">{i + 1}.</span>
                        {c.name}
                        {c.duplicate ? (
                          <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-normal text-amber-700">
                            已存在
                          </span>
                        ) : null}
                        {c.auto_repaired ? (
                          <span
                            className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-normal text-emerald-700"
                            title="静态校验发现问题后已由 AI 自动修复"
                          >
                            已自动修复
                          </span>
                        ) : null}
                        {c.warnings?.length ? (
                          <span
                            className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-normal text-red-700"
                            title={c.warnings.join("\n")}
                          >
                            ⚠️ {c.warnings.length} 处提醒
                          </span>
                        ) : null}
                      </div>
                      {smartInsert && mode === "functional" && c.after ? (
                        <div className="mt-0.5 text-[11px] text-primary/70">
                          插入到{c.after === "__START__" ? "最前面" : `「${c.after}」之后`}
                        </div>
                      ) : null}
                      {c.preconditions.length ? (
                        <div className="mt-1 text-xs text-muted-foreground">
                          <span className="font-medium text-foreground/70">前置：</span>
                          {c.preconditions.join("；")}
                        </div>
                      ) : null}
                      {c.steps.length ? (
                        <ol className="mt-1 list-decimal pl-5 text-xs text-muted-foreground">
                          {c.steps.map((s, k) => (
                            <li key={k}>{s}</li>
                          ))}
                        </ol>
                      ) : null}
                      {c.expected.length ? (
                        <div className="mt-1 text-xs text-muted-foreground">
                          <span className="font-medium text-foreground/70">预期：</span>
                          {c.expected.join("；")}
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        </>
        )}
      </div>
    </SideDrawer>
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
  moduleId,
  selected,
  cases,
  modulePath,
  statusOverride,
  onClear,
  onDone,
  onError,
  onMarked,
}: {
  batchId: string | null;
  moduleId: number | null;
  selected: Set<number>;
  cases: FunctionalCase[];
  modulePath: string;
  statusOverride: Map<number, FunctionalRunStatus>;
  onClear: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
  onMarked: (ids: number[], status: Exclude<FunctionalRunStatus, "pending">) => void;
}) {
  const [pendingStatus, setPendingStatus] = useState<
    Exclude<FunctionalRunStatus, "pending"> | null
  >(null);
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const visibleSelected = cases.filter((c) => selected.has(c.id));

  // 备注「确定」：对已标记过的选中用例，用它们当前状态重打一次并带上备注（即注入备注）；
  // 还没标记的用例，备注会在下次点结果时一起写入。
  const applyNote = async () => {
    setNoteOpen(false);
    if (!batchId) return;
    // 用例当前状态（本轮覆盖优先，其次最近一次）；有状态就直接带备注重打一次
    const items: FunctionalBatchItem[] = visibleSelected.flatMap((c) => {
      const st = statusOverride.get(c.id) ?? c.latest_run?.status;
      if (!st || st === "pending") return [];
      return [{ case_id: c.id, status: st as Exclude<FunctionalRunStatus, "pending">, note: note || null }];
    });
    if (items.length === 0) {
      toast.info("选中用例还没有状态，备注会在下次点结果时写入");
      return;
    }
    try {
      await functionalCasesApi.batchMark({ batch_id: batchId, items });
      toast.success(`已为 ${items.length} 条用例写入备注`);
      setNote(""); // 注入后清空备注输入
      onDone();
    } catch (e) {
      onError(e);
    }
  };

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
      onMarked(visibleSelected.map((c) => c.id), status); // 写进本轮状态覆盖表
      // 标记后保留选中状态（不调用 onClear），方便对同一批用例连续打不同结果
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
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {/* 生成测试报告（放在结果按钮前） */}
          <Button
            size="sm"
            variant="outline"
            className="border-primary/40 text-primary hover:bg-primary/10"
            onClick={() => setReportOpen(true)}
          >
            <FileText className="h-4 w-4" />
            生成测试报告
          </Button>
          {MARKABLE_STATUSES.map((s) => {
            const meta = STATUS_META[s];
            const Icon = meta.icon;
            return (
              <Button
                key={s}
                size="sm"
                variant="outline"
                className={cn(STATUS_BTN_CLS[s])}
                disabled={submitting || selected.size === 0}
                onClick={() => submit(s)}
              >
                {pendingStatus === s ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
                {meta.label}
              </Button>
            );
          })}
          {/* 备注：放在 N.A. 后面，没有选中用例时不可点 */}
          <Button
            size="sm"
            variant="outline"
            onClick={() => setNoteOpen(true)}
            disabled={submitting || selected.size === 0}
          >
            备注{note ? " ✓" : ""}
          </Button>
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

      {/* 批量备注弹框 */}
      <Dialog open={noteOpen} onOpenChange={setNoteOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>批量备注</DialogTitle>
            <DialogDescription>这条备注会写入本次标记的所有选中用例</DialogDescription>
          </DialogHeader>
          <Textarea
            rows={4}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="备注信息（可空）"
            autoFocus
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => { setNote(""); setNoteOpen(false); }}>清空</Button>
            <Button onClick={applyNote}>确定</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <TestReportDialog open={reportOpen} onClose={() => setReportOpen(false)} cases={cases} modulePath={modulePath} moduleId={moduleId} batchId={batchId} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 测试报告对话框（#15）
// ---------------------------------------------------------------------------
function formatReportTime(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

const REPORT_COLORS: Record<FunctionalRunStatus, string> = {
  passed: "#10b981", failed: "#ef4444", blocked: "#f59e0b", na: "#94a3b8", pending: "#cbd5e1",
};

/** 甜甜圈饼图 SVG 字符串（屏幕与打印共用） */
function buildDonutSvg(segs: { value: number; color: string }[], size = 140): string {
  const total = segs.reduce((s, x) => s + x.value, 0) || 1;
  const C = 60, R = 44, sw = 20, circ = 2 * Math.PI * R;
  let acc = 0;
  const arcs = segs
    .filter((s) => s.value > 0)
    .map((s) => {
      const frac = s.value / total;
      const el = `<circle cx="${C}" cy="${C}" r="${R}" fill="none" stroke="${s.color}" stroke-width="${sw}" stroke-dasharray="${frac * circ} ${circ - frac * circ}" stroke-dashoffset="${-acc * circ}" transform="rotate(-90 ${C} ${C})" />`;
      acc += frac;
      return el;
    })
    .join("");
  return `<svg viewBox="0 0 120 120" width="${size}" height="${size}"><circle cx="${C}" cy="${C}" r="${R}" fill="none" stroke="#eee" stroke-width="${sw}"/>${arcs}</svg>`;
}

function TestReportDialog({
  open,
  onClose,
  cases,
  modulePath,
  moduleId,
  batchId,
}: {
  open: boolean;
  onClose: () => void;
  cases: FunctionalCase[];
  modulePath: string;
  moduleId: number | null;
  batchId: string | null;
}) {
  // 拉本模块的勾结果，只取当前测试记录（batchId）这一批
  const query = useQuery({
    queryKey: ["fc-test-history", moduleId],
    queryFn: () => functionalCasesApi.testHistory(moduleId!, 500),
    enabled: open && moduleId != null,
    staleTime: 0,
  });
  const allRuns = query.data ?? [];
  const batchRuns = batchId ? allRuns.filter((r) => r.batch_id === batchId) : [];

  // 每条用例在本批内的最近一次状态 + 时间
  const batchMap = new Map<number, FunctionalTestHistoryRun>();
  for (const r of batchRuns) {
    const prev = batchMap.get(r.case_id);
    if (!prev || new Date(r.executed_at).getTime() > new Date(prev.executed_at).getTime()) {
      batchMap.set(r.case_id, r);
    }
  }
  const statusOf = (c: FunctionalCase): FunctionalRunStatus =>
    batchMap.get(c.id)?.status ?? "pending";

  const summary: Record<FunctionalRunStatus, number> = {
    passed: 0, failed: 0, blocked: 0, na: 0, pending: 0,
  };
  for (const c of cases) summary[statusOf(c)] += 1;
  const total = cases.length;
  const passRate = total ? Math.round((summary.passed / total) * 1000) / 10 : 0;

  // 起止时间：本批最早/最晚一次勾结果
  const times = batchRuns.map((r) => new Date(r.executed_at).getTime());
  const startTs = times.length ? Math.min(...times) : null;
  const endTs = times.length ? Math.max(...times) : null;
  const startStr = startTs ? formatReportTime(new Date(startTs)) : "—";
  const endStr = endTs ? formatReportTime(new Date(endTs)) : "—";

  const reportName = `${modulePath || "功能用例"}模块-${startStr !== "—" ? startStr : formatReportTime(new Date())}-测试报告`;

  // 按用例序号（列表自然顺序）排列
  const rowsData = cases;

  const donutSegs = [
    { value: summary.passed, color: REPORT_COLORS.passed },
    { value: summary.failed, color: REPORT_COLORS.failed },
    { value: summary.blocked, color: REPORT_COLORS.blocked },
    { value: summary.na, color: REPORT_COLORS.na },
    { value: summary.pending, color: REPORT_COLORS.pending },
  ];
  const legend: { label: string; n: number; status: FunctionalRunStatus }[] = [
    { label: "通过", n: summary.passed, status: "passed" },
    { label: "失败", n: summary.failed, status: "failed" },
    { label: "阻塞", n: summary.blocked, status: "blocked" },
    { label: "N.A.", n: summary.na, status: "na" },
    { label: "未执行", n: summary.pending, status: "pending" },
  ];

  const printReport = () => {
    const rows = rowsData
      .map((c, i) => {
        const m = STATUS_META[statusOf(c)];
        const pr = c.priority != null ? `P${c.priority}` : "—";
        return `<tr><td>${i + 1}</td><td>${escapeHtml(c.name)}</td><td style="text-align:center">${pr}</td><td style="text-align:center">${m.label}</td><td>${escapeHtml(batchMap.get(c.id)?.note ?? "")}</td></tr>`;
      })
      .join("");
    const legendHtml = legend
      .map((l) => `<span style="margin-right:14px"><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${REPORT_COLORS[l.status]};margin-right:4px"></i>${l.label} <b>${l.n}</b></span>`)
      .join("");
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(reportName)}</title>
      <style>
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111;padding:24px}
        h1{font-size:24px;margin:0 0 6px;text-align:center}
        .sub{color:#666;font-size:13px;margin-bottom:16px;text-align:center}
        .top{display:flex;gap:24px;align-items:center;justify-content:center;margin-bottom:20px}
        .legend{font-size:13px}
        table{width:100%;border-collapse:collapse;font-size:12px}
        th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
        th{background:#f5f5f5}
      </style></head><body>
      <h1>${escapeHtml(reportName)}</h1>
      <div class="sub">开始时间：${startStr} &nbsp;·&nbsp; 结束时间：${endStr} &nbsp;·&nbsp; 共 ${total} 条 &nbsp;·&nbsp; 通过率 ${passRate}%</div>
      <div class="top">${buildDonutSvg(donutSegs, 160)}<div class="legend">${legendHtml}</div></div>
      <table><thead><tr><th>#</th><th>用例名称</th><th>优先级</th><th>状态</th><th>备注</th></tr></thead><tbody>${rows}</tbody></table>
      </body></html>`;
    const w = window.open("", "_blank");
    if (!w) {
      toast.error("浏览器拦截了新窗口，请允许弹窗后重试");
      return;
    }
    w.document.write(html);
    w.document.close();
    w.focus();
    setTimeout(() => w.print(), 300);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-center text-xl font-bold">{reportName}</DialogTitle>
          <DialogDescription className="text-center">
            开始时间：{startStr} · 结束时间：{endStr} · 共 {total} 条 · 通过率 {passRate}%
          </DialogDescription>
        </DialogHeader>

        {/* 饼图 + 图例 */}
        <div className="flex items-center justify-center gap-6">
          <div dangerouslySetInnerHTML={{ __html: buildDonutSvg(donutSegs) }} />
          <div className="space-y-1 text-sm">
            {legend.map((l) => (
              <div key={l.label} className="flex items-center gap-2">
                <span className="inline-block h-3 w-3 rounded-sm" style={{ background: REPORT_COLORS[l.status] }} />
                <span className="w-12">{l.label}</span>
                <span className="font-semibold tabular-nums">{l.n}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="max-h-[40vh] overflow-y-auto rounded-md border">
          <table className="w-full border-collapse text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="border border-border px-2 py-1.5 text-left w-10">#</th>
                <th className="border border-border px-2 py-1.5 text-left">用例名称</th>
                <th className="border border-border px-2 py-1.5 text-center w-16">优先级</th>
                <th className="border border-border px-2 py-1.5 text-center w-24 whitespace-nowrap">状态</th>
                <th className="border border-border px-2 py-1.5 text-left">备注</th>
              </tr>
            </thead>
            <tbody>
              {rowsData.map((c, i) => {
                const st = statusOf(c);
                const m = STATUS_META[st];
                const Icon = m.icon;
                const pm = c.priority != null ? PRIORITY_META[c.priority] : null;
                return (
                  <tr key={c.id}>
                    <td className="border border-border px-2 py-1.5 text-xs text-muted-foreground">{i + 1}</td>
                    <td className="border border-border px-2 py-1.5 font-medium">{c.name}</td>
                    <td className="border border-border px-2 py-1.5 text-center">
                      {pm ? <span className={cn("rounded px-1 py-0.5 text-[10px] font-medium ring-1 ring-inset", pm.tone, pm.ring)}>{pm.label}</span> : "—"}
                    </td>
                    <td className="border border-border px-2 py-1.5 text-center">
                      <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", m.tone, m.ring)}>
                        <Icon className="h-3 w-3" />{m.label}
                      </span>
                    </td>
                    <td className="border border-border px-2 py-1.5 text-xs text-muted-foreground">{batchMap.get(c.id)?.note ?? ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <DialogFooter>
          <Button onClick={printReport}>打印 / 导出 PDF</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
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
  modulePath,
  onClose,
  onDone,
  onError,
}: {
  state:
    | { mode: "create"; moduleId: number }
    | { mode: "edit"; caseId: number }
    | null;
  modulePath: string;
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
    const numbered = (arr: string[]) =>
      arr.length ? arr.map((s, i) => `${i + 1}. ${s}`).join("\n") : "1. ";
    if (state.mode === "create") {
      form.reset({
        name: "",
        description: "",
        preconditions: "1. ",
        steps: "1. ",
        expected: "1. ",
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
        preconditions: numbered(spec.preconditions),
        steps: numbered(spec.steps),
        expected: numbered(splitLines(spec.expected ?? "")),
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
        preconditions: stripNumbering(body.preconditions),
        steps: stripNumbering(body.steps),
        expected: stripNumbering(body.expected).join("\n") || null,
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
        preconditions: stripNumbering(body.preconditions),
        steps: stripNumbering(body.steps),
        expected: stripNumbering(body.expected).join("\n") || null,
      };
      // 不再编辑 描述 / 标签：不传这两个字段，后端保留原值
      return functionalCasesApi.update(state.caseId, {
        name: body.name.trim(),
        skip: body.skip,
        priority: body.priority ?? null,
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

  // 有序文本框：回车自动在末尾续上下一个序号（与快速编辑一致）
  const orderedKeyDown =
    (field: "preconditions" | "steps" | "expected") =>
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const lines = (form.getValues(field) || "").split(/\r?\n/).filter((l) => l.trim());
        const n = lines.length + 1;
        form.setValue(field, (lines.length ? lines.join("\n") + "\n" : "") + `${n}. `, {
          shouldDirty: true,
        });
      }
    };

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
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="优先级">
                <select
                  {...form.register("priority")}
                  className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-1 focus:ring-ring"
                >
                  {[1, 2, 3, 4, 5].map((p) => (
                    <option key={p} value={p}>P{p}</option>
                  ))}
                </select>
              </Field>
              <Field label="所属模块">
                <div className="flex h-9 items-center rounded-md border border-input bg-muted/40 px-3 text-sm text-muted-foreground">
                  {modulePath || "—"}
                </div>
              </Field>
            </div>
            <Field label="前置条件（回车自动续号）">
              <Textarea
                {...form.register("preconditions")}
                onKeyDown={orderedKeyDown("preconditions")}
                rows={3}
              />
            </Field>
            <Field label="操作步骤（回车自动续号）">
              <Textarea
                {...form.register("steps")}
                onKeyDown={orderedKeyDown("steps")}
                rows={5}
              />
            </Field>
            <Field label="预期结果（回车自动续号）">
              <Textarea
                {...form.register("expected")}
                onKeyDown={orderedKeyDown("expected")}
                rows={2}
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
  onMarkedStatus,
}: {
  target: FunctionalCase | null;
  batchId: string | null;
  onClose: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
  onMarkedStatus?: (caseId: number, status: Exclude<FunctionalRunStatus, "pending">) => void;
}) {
  const [status, setStatus] = useState<Exclude<FunctionalRunStatus, "pending">>(
    "passed",
  );
  const [note, setNote] = useState("");

  useEffect(() => {
    if (target) {
      setStatus("passed");
      setNote("");
    }
  }, [target]);

  const mutation = useMutation({
    mutationFn: () => {
      if (!target) return Promise.reject(new Error("invalid"));
      return functionalCasesApi.mark(target.id, {
        status,
        actual_result: null,
        note: note || null,
        batch_id: batchId,
      });
    },
    onSuccess: () => {
      toast.success("已记录结果");
      if (target) onMarkedStatus?.(target.id, status);
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
        {/* 用例摘要：步骤/预期单独滚动，结果和备注固定在下方常驻可见 */}
        <div className="max-h-[40vh] overflow-y-auto pr-1">
          <SpecPreview spec={spec} />
        </div>
        <div className="space-y-3">
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
                    variant="outline"
                    className={cn(active && (STATUS_BTN_CLS[s] || "ring-2 ring-slate-400"))}
                    onClick={() => setStatus(s)}
                  >
                    <Icon className="h-4 w-4" />
                    {meta.label}
                  </Button>
                );
              })}
            </div>
          </div>
          <Field label="备注">
            <Textarea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="备注信息（可空）"
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
const FIELD_LABELS: Record<string, string> = {
  name: "用例名", description: "描述", preconditions: "前置条件", steps: "操作步骤",
  expected: "预期结果", priority: "优先级", tags: "标签", module_id: "所属模块", skip: "跳过",
};

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
  const editQuery = useQuery({
    queryKey: target ? ["fc-edit-history", target.module_id] : ["fc-edit-noop"],
    queryFn: () => functionalCasesApi.editHistory(target!.module_id, 200),
    enabled: !!target,
    staleTime: 0,
  });

  if (!target) return null;
  const runs = runsQuery.data ?? [];
  const edits = (editQuery.data ?? []).filter((r) => r.case_id === target.id);

  return (
    <Dialog open={!!target} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>历史记录 · {target.name}</DialogTitle>
        </DialogHeader>
        <Tabs defaultValue="edit">
          <TabsList>
            <TabsTrigger value="edit">编辑历史</TabsTrigger>
            <TabsTrigger value="run">执行历史</TabsTrigger>
          </TabsList>

          {/* 编辑历史 */}
          <TabsContent value="edit" className="mt-3 max-h-[55vh] space-y-2 overflow-y-auto pr-1">
            {editQuery.isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : edits.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">这条用例还没有编辑记录</p>
            ) : (
              edits.map((rec) => (
                <div key={rec.id} className="rounded-lg border bg-card p-2 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">
                      {rec.action === "create" ? "新建" : rec.action === "delete" ? "删除" : "修改"}
                    </span>
                    <span className="ml-auto whitespace-nowrap text-xs text-muted-foreground">
                      {rec.operator || "—"} · {formatTime(rec.created_at)}
                    </span>
                  </div>
                  {rec.changes.length > 0 ? (
                    <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                      {rec.changes.map((c, i) => (
                        <li key={i} className="break-words">
                          <span className="text-foreground">{FIELD_LABELS[c.field] ?? c.field}</span>：
                          <span className="line-through opacity-60">{c.old || "空"}</span>
                          {" → "}
                          <span className="text-foreground">{c.new || "空"}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))
            )}
          </TabsContent>

          {/* 执行历史 */}
          <TabsContent value="run" className="mt-3 max-h-[55vh] space-y-2 overflow-y-auto pr-1">
            {runsQuery.isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : runs.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">还没有人执行过这条用例</p>
            ) : (
              <ul className="divide-y rounded-lg border bg-card text-sm">
                {runs.map((r) => {
                  const meta = STATUS_META[r.status];
                  const Icon = meta.icon;
                  return (
                    <li key={r.id} className="flex items-start gap-3 px-3 py-2">
                      <span className={cn("mt-0.5 inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", meta.tone, meta.ring)}>
                        <Icon className="h-3.5 w-3.5" />{meta.label}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="text-xs text-muted-foreground">
                          {formatTime(r.executed_at)}
                          {r.operator ? ` · by ${r.operator}` : ""}
                        </div>
                        {r.note ? <div className="text-xs italic text-muted-foreground">{r.note}</div> : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </TabsContent>
        </Tabs>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 测试记录对话框（#8）：顶部汇总条 + 按测试批次分组
// ---------------------------------------------------------------------------
function TestRecordsDialog({
  moduleId,
  moduleName,
  totalCases,
  open,
  onClose,
  onRestore,
}: {
  moduleId: number | null;
  moduleName: string;
  totalCases: number;
  open: boolean;
  onClose: () => void;
  onRestore: (runs: FunctionalTestHistoryRun[]) => void;
}) {
  const query = useQuery({
    queryKey: ["fc-test-history", moduleId],
    queryFn: () => functionalCasesApi.testHistory(moduleId!, 500),
    enabled: open && moduleId != null,
    staleTime: 0,
  });
  const runs = query.data ?? [];

  // 按批次分组（无 batch_id 的归到“单点”桶）
  const groupMap = new Map<string, FunctionalTestHistoryRun[]>();
  for (const r of runs) {
    const key = r.batch_id ?? "__single__";
    if (!groupMap.has(key)) groupMap.set(key, []);
    groupMap.get(key)!.push(r);
  }
  const groups = [...groupMap.entries()]
    .map(([key, rs]) => {
      const times = rs.map((x) => new Date(x.executed_at).getTime());
      return {
        key,
        batchId: key === "__single__" ? null : key,
        runs: rs,
        end: Math.max(...times),
      };
    })
    .sort((a, b) => b.end - a.end);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>测试记录</DialogTitle>
          <DialogDescription>本模块功能用例的勾结果，按测试批次分组（点一条还原到状态列）</DialogDescription>
        </DialogHeader>

        <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
          {query.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : groups.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">该模块下还没有任何测试记录</p>
          ) : (
            groups.map((g) => (
              <BatchCard
                key={g.key}
                moduleName={moduleName}
                totalCases={totalCases}
                runs={g.runs}
                onRestore={(rs) => { onRestore(rs); onClose(); }}
              />
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}


/** 统计数字：最多 4 位定宽显示，超出就 543.. 截断 */
function fmtCount(n: number): string {
  const s = String(n);
  return s.length > 4 ? s.slice(0, 3) + ".." : s;
}
function CountStat({ label, n, tone }: { label: string; n: number; tone?: string }) {
  return (
    <span className={cn("whitespace-nowrap", tone)}>
      {label}
      <span className="ml-0.5 inline-block w-[2.6em] text-right font-medium tabular-nums">{fmtCount(n)}</span>
    </span>
  );
}

function BatchCard({
  moduleName,
  totalCases,
  runs,
  onRestore,
}: {
  moduleName: string;
  totalCases: number;
  runs: FunctionalTestHistoryRun[];
  onRestore: (runs: FunctionalTestHistoryRun[]) => void;
}) {
  // 每条用例取本批内最近一次状态
  const latest = new Map<number, FunctionalTestHistoryRun>();
  for (const r of runs) {
    const prev = latest.get(r.case_id);
    if (!prev || new Date(r.executed_at).getTime() > new Date(prev.executed_at).getTime()) {
      latest.set(r.case_id, r);
    }
  }
  const counts: Record<"passed" | "failed" | "blocked" | "na", number> = {
    passed: 0, failed: 0, blocked: 0, na: 0,
  };
  for (const r of latest.values()) counts[r.status] += 1;
  // 用例总数 = 当前模块全部功能用例数；未执行 = 总数 − 本批已勾的
  const marked = counts.passed + counts.failed + counts.blocked + counts.na;
  const pending = Math.max(0, totalCases - marked);
  // 首次生成记录的时间（本批最早）
  const start = Math.min(...runs.map((r) => new Date(r.executed_at).getTime()));
  const name = `${moduleName}-${formatReportTime(new Date(start))}-测试记录`;

  return (
    <button
      onClick={() => onRestore(runs)}
      className="flex w-full items-center gap-4 rounded-lg border bg-card px-3 py-2 text-left hover:border-primary/40 hover:bg-accent/30"
      title="点此把这一轮结果还原到状态列"
    >
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{name}</span>
      <span className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
        <CountStat label="用例总数" n={totalCases} />
        <CountStat label="通过" n={counts.passed} tone="text-emerald-600" />
        <CountStat label="失败" n={counts.failed} tone="text-red-600" />
        <CountStat label="阻塞" n={counts.blocked} tone="text-amber-600" />
        <CountStat label="N.A." n={counts.na} tone="text-slate-500" />
        <CountStat label="未执行" n={pending} />
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// 编辑记录对话框：一次快速编辑的所有改动聚合成一条，点击跳转测试并筛选
// ---------------------------------------------------------------------------
function EditRecordsDialog({
  moduleId,
  open,
  onClose,
  onJump,
  onChanged,
}: {
  moduleId: number | null;
  open: boolean;
  onClose: () => void;
  onJump: (records: FunctionalCaseEditRecord[]) => void;
  onChanged: () => void;
}) {
  const query = useQuery({
    queryKey: ["fc-edit-history", moduleId],
    queryFn: () => functionalCasesApi.editHistory(moduleId!, 200),
    enabled: open && moduleId != null,
    staleTime: 0,
  });
  const records = query.data ?? [];

  const rollbackRecord = async (record: FunctionalCaseEditRecord, fullBatch = false) => {
    if (!record.batch_id) return;
    try {
      await casesApi.rollbackHistory(record.batch_id, {
        mode: fullBatch ? "full" : "partial",
        event_ids: fullBatch ? undefined : [record.id],
      });
      toast.success("已回滚");
      query.refetch();
      onChanged();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : err instanceof Error ? err.message : "回滚失败";
      toast.error(msg);
    }
  };

  // 按会话聚合：同 session_id 的多条改动合成一条记录；无 session 的各自一条
  const groupMap = new Map<string, FunctionalCaseEditRecord[]>();
  for (const r of records) {
    const key = r.session_id ?? `__single_${r.id}`;
    if (!groupMap.has(key)) groupMap.set(key, []);
    groupMap.get(key)!.push(r);
  }
  const groups = [...groupMap.entries()]
    .map(([key, recs]) => {
      const counts = { create: 0, update: 0, delete: 0 } as Record<string, number>;
      const caseIds = new Set<number>();
      const caseNames = new Set<string>();
      let operator: string | null = null;
      let end = 0;
      for (const r of recs) {
        counts[r.action] = (counts[r.action] ?? 0) + 1;
        if (r.case_id != null) caseIds.add(r.case_id);
        if (r.case_name) caseNames.add(r.case_name);
        if (!operator && r.operator) operator = r.operator;
        end = Math.max(end, new Date(r.created_at).getTime());
      }
      return {
        key,
        records: recs,
        isSession: !!recs[0].session_id,
        counts,
        caseIds: [...caseIds],
        caseNames: [...caseNames],
        operator,
        end,
      };
    })
    .sort((a, b) => b.end - a.end);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>编辑记录</DialogTitle>
          <DialogDescription>一次快速编辑的所有改动聚合成一条；点一条跳到测试模式并筛选出相关用例</DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
          {query.isLoading ? (
            <Skeleton className="h-32 w-full" />
          ) : groups.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">该模块下还没有任何编辑记录</p>
          ) : (
            groups.map((g) => {
              const summary = [
                g.counts.create ? `新增 ${g.counts.create}` : null,
                g.counts.update ? `修改 ${g.counts.update}` : null,
                g.counts.delete ? `删除 ${g.counts.delete}` : null,
              ].filter(Boolean).join(" · ");
              const clickable = g.records.length > 0;
              const canRollbackBatch = g.records.filter((r) => r.rollback_available && r.batch_id).length > 1;
              return (
                <div
                  key={g.key}
                  className={cn(
                    "w-full rounded-lg border bg-card p-2 text-left text-sm",
                    clickable ? "hover:border-primary/40 hover:bg-accent/30" : "opacity-70",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{g.isSession ? "快速编辑" : "编辑"}</span>
                    <span className="text-xs text-muted-foreground">{summary}</span>
                    <span className="ml-auto whitespace-nowrap text-xs text-muted-foreground">
                      {g.operator || "—"} · {formatTime(new Date(g.end).toISOString())}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    涉及用例：{g.caseNames.join("、") || "—"}
                    {clickable ? (
                      <button type="button" className="ml-2 text-primary hover:underline" onClick={() => onJump(g.records)}>
                        跳转测试并筛选 →
                      </button>
                    ) : null}
                  </div>
                  <div className="mt-2 space-y-1 border-t pt-2">
                    {g.records.map((record) => (
                      <div key={record.id} className="flex items-center gap-2 text-xs">
                        <span className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">{record.action}</span>
                        <span className="min-w-0 flex-1 truncate">{record.case_name || `#${record.case_id}`}</span>
                        {record.rollback_status && record.rollback_status !== "none" ? (
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">已回滚</span>
                        ) : record.rollback_available ? (
                          <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">可回滚</span>
                        ) : null}
                        {record.rollback_available && record.batch_id ? (
                          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => rollbackRecord(record)}>
                            回滚
                          </Button>
                        ) : null}
                        {canRollbackBatch && record.rollback_available && record.batch_id ? (
                          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => rollbackRecord(record, true)}>
                            整次回滚
                          </Button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>关闭</Button>
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
    <div className="flex items-center gap-3 rounded-md border border-destructive/30 px-3 py-4 text-sm">
      <Info className="h-4 w-4 text-destructive" />
      <span>加载失败</span>
      <Button variant="outline" size="sm" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}

function EmptyHint({ text }: { text: string }) {
  return (
    <div className="py-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  );
}
