import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FunctionalCasesPage } from "./FunctionalCasesPage";
import { ProjectAiOverviewView } from "@/components/ProjectAiOverviewView";
import {
  Apple,
  ArrowLeft,
  Braces,
  Check,
  ChevronRight,
  ClipboardList,
  Download,
  FileText,
  Folder,
  FolderKanban,
  Globe,
  Info,
  Loader2,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  Smartphone,
  Trash2,
  Upload,
  X,
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
import { HighlightedTextarea } from "@/components/ui/highlighted-textarea";
import { StepEditor } from "@/components/case/step-editor";
import { DevicePickerDialog } from "@/components/device-picker-dialog";
import { ProjectManagementPage } from "@/pages/ProjectManagementPage";
import { cn } from "@/lib/utils";
import {
  ApiError,
  casesApi,
  contentApi,
  modulesApi,
  projectsApi,
  runsApi,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import {
  ALL_PROJECT_STACKS,
  type CaseType,
  type ContentNode,
  type ProjectStack,
  type TestCaseCreate,
  type TestStepDraft,
} from "@/types/domain";

/**
 * 项目详情页：文件管理器风格。
 *
 * 交互：
 *  - 面包屑顶部：项目名 > 模块 A > 模块 B … 点击任意一段返回对应层级
 *  - 主区域：列出当前层级的子模块 + 用例（模块在前），点模块下钻
 *  - 工具栏：新建子模块 / 新建用例 / 导入用例 / 运行整个项目
 *  - 每行尾部三点菜单：编辑、删除、运行、在此之前插入用例
 *
 * 为什么不用递归树？shadcn 里没有现成的 Tree 组件，递归展开得自己写节点 state；
 * 文件管理器风格在后端接口形状（按 parent_id 拉一层）下最贴合，也方便用户理解。
 */

const HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"] as const;

// ---------------------------------------------------------------------------
// 用例字段校验工具
// ---------------------------------------------------------------------------
// 运行期会 `rep_expr` 把 ${var} 替换成参数池里的值，为了让 JSON.parse 不挂，
// 这里先把 ${...} 暂时替换成一个合法的 JSON 字面量再校验。
//
// 历史坑：原来用 '"__placeholder__"'（带引号）替换 —— 假定用户写的是
//   { "x": ${var} }     ← 裸用作 value
// 但实际上常见且推荐的写法是字符串内嵌：
//   { "x": "Bearer ${token}" }
// 替换之后变成 `{ "x": "Bearer "__placeholder__"" }`，JSON.parse 直接挂。
// 用户因此被迫把 ${var} 改成 $.{var} / $.var 才能保存，丢失了真正的变量替换语义。
//
// 改用 `0`（合法 JSON number 字面量），它在两种位置都成立：
//   - `"Bearer ${token}"` → `"Bearer 0"`     字符串里嵌字符 0，仍合法
//   - `${var}` 裸用作值   → `0`              成为数字 0，仍合法
// JSON.parse 都能过，前端校验（仅判断对象/键合法性）就不会再误判 ${var}。
function substitutePlaceholdersForParse(text: string): string {
  return text.replace(/\$\{[^}\n]*\}/g, "0");
}

type JsonCheck =
  | { state: "empty" }
  | { state: "ok"; pretty: string; parsed: unknown }
  | { state: "error"; message: string };

function checkJson(text: string | undefined | null): JsonCheck {
  const s = (text ?? "").trim();
  if (!s) return { state: "empty" };
  const candidate = substitutePlaceholdersForParse(s);
  try {
    const parsed = JSON.parse(candidate);
    // pretty：默认用 JSON.stringify 重排，但如果原文里有 ${var}，重排后会被
    // 替换成 0（substitutePlaceholdersForParse 的副作用），用户的变量名就丢了。
    // 保险起见，原文带 ${var} 时直接返回原文，不再格式化。
    let pretty = s;
    if (!/\$\{[^}\n]*\}/.test(s)) {
      try {
        pretty = JSON.stringify(JSON.parse(candidate), null, 2);
      } catch {
        pretty = s;
      }
    }
    return { state: "ok", pretty, parsed };
  } catch (e) {
    return { state: "error", message: (e as Error).message || "JSON 解析失败" };
  }
}

/** 提取参数的左值 key 合法；右值必须是 `$.path` / `$[..]` 起始的 JSONPath，或者 `function:xxx`。 */
function checkExtract(text: string | undefined | null): JsonCheck {
  const base = checkJson(text);
  if (base.state !== "ok") return base;
  if (typeof base.parsed !== "object" || base.parsed === null || Array.isArray(base.parsed)) {
    return { state: "error", message: "提取参数必须是一个 JSON 对象" };
  }
  for (const [k, v] of Object.entries(base.parsed as Record<string, unknown>)) {
    if (typeof v !== "string") {
      return { state: "error", message: `"${k}" 的值必须是字符串（$.xxx 或 function:xxx）` };
    }
    if (!v.startsWith("$") && !v.startsWith("function:") && !v.startsWith("${")) {
      return {
        state: "error",
        message: `"${k}" 的值应以 $. / $[ / function: 开头，实际：${v.slice(0, 20)}`,
      };
    }
  }
  return base;
}

/** 断言的 key 允许 `$.xxx` 或 `sql:select ...`，value 可以是任意标量或 `function:xxx`。 */
function checkAssertion(text: string | undefined | null): JsonCheck {
  const base = checkJson(text);
  if (base.state !== "ok") return base;
  if (typeof base.parsed !== "object" || base.parsed === null || Array.isArray(base.parsed)) {
    return { state: "error", message: "断言必须是一个 JSON 对象" };
  }
  for (const k of Object.keys(base.parsed as Record<string, unknown>)) {
    if (!k.startsWith("$") && !k.startsWith("sql:") && !k.startsWith("${")) {
      return {
        state: "error",
        message: `"${k}" 应以 $. / sql: 开头`,
      };
    }
  }
  return base;
}

/** Headers：必须是对象，key/value 都是字符串。 */
function checkHeaders(text: string | undefined | null): JsonCheck {
  const base = checkJson(text);
  if (base.state !== "ok") return base;
  if (typeof base.parsed !== "object" || base.parsed === null || Array.isArray(base.parsed)) {
    return { state: "error", message: "请求头必须是一个 JSON 对象" };
  }
  return base;
}

// =============================================================================
// 栈相关工具：v2 起项目可同时启用多个栈，详情页通过顶部 Tab 切栈视图。
// =============================================================================

/** Tab 上的中文展示名。Tab 顺序按 ALL_PROJECT_STACKS（功能 → API → Web → Android → iOS）。 */
const STACK_LABELS: Record<ProjectStack, string> = {
  functional: "功能",
  api: "API",
  web: "Web",
  android: "Android",
  ios: "iOS",
};

/**
 * 当前栈 → /api/content?case_type=... 的过滤值。
 *
 *  - 自动化栈：当前栈 + "mixed"。mixed 用例（跨栈）会同时出现在它涉及的每个栈的 Tab，
 *    用户能在任一相关 Tab 看到它。表里会带 mixed 徽章避免误解。后续若要改成
 *    "按第一步骤栈唯一归属"，把 mixed 从这里去掉、由后端在 list 时改写归属即可。
 *  - functional：只展示 functional 用例，不串扰自动化栈。
 *
 *  这里返回的字符串会按逗号 join 后塞进 ?case_type=，对应后端 _parse_case_types。
 */
function caseTypesFor(stack: ProjectStack): CaseType[] {
  if (stack === "functional") return ["functional"];
  return [stack as CaseType, "mixed"];
}

/** URL 上的 ?stack=xxx 解析；非法值兜回 "api"。 */
function parseStackParam(raw: string | null): ProjectStack {
  if (!raw) return "api";
  const v = raw.trim().toLowerCase();
  if ((ALL_PROJECT_STACKS as readonly string[]).includes(v)) {
    return v as ProjectStack;
  }
  return "api";
}

// ---------------------------------------------------------------------------
// zod schemas
// ---------------------------------------------------------------------------
const moduleSchema = z.object({
  name: z.string().trim().min(1, "请输入模块名").max(50, "最多 50 字"),
});
type ModuleFormValues = z.infer<typeof moduleSchema>;

const caseSchema = z.object({
  name: z.string().trim().min(1, "请输入用例名").max(100, "最多 100 字"),
  description: z.string().max(200).optional(),
  method: z.string().optional(),
  path: z.string().optional(),
  headers: z.string().optional(),
  data_type: z.string().optional(),
  params: z.string().optional(),
  extract_data: z.string().optional(),
  assertion: z.string().optional(),
  sql_query: z.string().optional(),
  wait_time: z.coerce.number().int().min(0).max(3600).optional(),
  skip: z.boolean().optional(),
  /** web / app 用例的步骤。zod 只做通过校验，细粒度校验交给 StepEditor 自己。 */
  steps: z.array(z.any()).optional(),
  case_type: z.string().optional(),
});
type CaseFormValues = z.infer<typeof caseSchema>;

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------
export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // 栈 Tab 状态走 URL（?stack=api/web/app/functional），便于刷新 + 分享 + 浏览器后退。
  // 默认 "api"；下方 effect 会在 stackCounts 拿到后自动校正到"项目实际启用的第一个栈"。
  const [searchParams, setSearchParams] = useSearchParams();
  const activeStack = parseStackParam(searchParams.get("stack"));

  // 面包屑：从根到当前。每段保存 { id, name }。id 为 null 表示项目根。
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

  /**
   * 项目详情页 Tab 角标用：每种 case_type 的用例数 + 启用栈集合。
   * 拿到后用来：
   *   1) 控制哪些 Tab 显示（只显示 enabled_stacks 内的栈）
   *   2) 校正 URL：如果 URL 带的栈不在 enabled_stacks 内，跳到第一个启用的栈
   *   3) 在每个 Tab 上显示数量 badge
   */
  const stackCountsQuery = useQuery({
    queryKey: queryKeys.projectStackCounts(projectId),
    queryFn: () => projectsApi.stackCounts(projectId),
    enabled: Number.isFinite(projectId),
  });

  // URL 校正：activeStack 不在项目启用栈内时，跳到第一个启用的栈
  // （按 ALL_PROJECT_STACKS 固定顺序：功能 → API → Web → App）
  const enabledStacks = useMemo<ProjectStack[]>(() => {
    const counts = stackCountsQuery.data;
    if (!counts) return [];
    const set = new Set(counts.enabled_stacks);
    return ALL_PROJECT_STACKS.filter((s) => set.has(s));
  }, [stackCountsQuery.data]);

  useEffect(() => {
    if (enabledStacks.length === 0) return;
    if ((activeStack as string) === "management") return; // management is always valid
    if (!enabledStacks.includes(activeStack as ProjectStack)) {
      const next = new URLSearchParams(searchParams);
      next.set("stack", enabledStacks[0]);
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabledStacks, activeStack]);

  // 当前 Tab 的 case_type 过滤值（自动化 Tab 含 mixed；functional Tab 仅 functional）
  const caseTypeFilter = useMemo(() => caseTypesFor(activeStack), [activeStack]);

  const contentQuery = useQuery({
    queryKey: queryKeys.content(projectId, currentParentId, caseTypeFilter),
    queryFn: () => contentApi.list(projectId, currentParentId, caseTypeFilter),
    enabled: Number.isFinite(projectId),
  });

  const invalidateContent = () => {
    // 切栈 / 不同 case_type 都是不同的 cache key，统一失效到 prefix
    queryClient.invalidateQueries({
      queryKey: ["content", projectId, currentParentId],
    });
    // 数量也可能变（建/删用例后 badge 要更新）
    queryClient.invalidateQueries({
      queryKey: queryKeys.projectStackCounts(projectId),
    });
  };

  /** 切 Tab：写回 URL，触发 contentQuery / 各种 useMemo 重算。
   * 同时把 breadcrumb 清空回到项目根 —— 不同栈视图下用例 / 模块概念差异较大
   * （比如 functional 栈不允许新建用例只允许在模块下勾结果），保留旧 breadcrumb
   * 容易让用户误以为切栈后看到的列表跟之前是一致的。直接回根更不容易迷路。 */
  const handleStackChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("stack", next);
    setSearchParams(params, { replace: true });
    setBreadcrumb([]);
  };

  const isFunctionalTab = activeStack === "functional";

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
    | { mode: "create"; moduleId: number; insertAt?: number }
    | { mode: "edit"; node: ContentNode }
    | null
  >(null);

  const [pendingDelete, setPendingDelete] = useState<ContentNode | null>(null);
  const [runningId, setRunningId] = useState<{ type: string; id: number } | null>(
    null,
  );

  // 「移动模块到其他父节点」对话框：选中要移动的模块后弹出，里面拉一棵树让用户挑目标。
  const [movingModule, setMovingModule] = useState<{
    id: number;
    name: string;
    currentParentId: number | null;
  } | null>(null);

  // ------ Mutations ------
  const createModule = useMutation({
    mutationFn: (body: { project_id: number; parent_id: number | null; name: string }) =>
      modulesApi.create(body),
    onSuccess: () => {
      toast.success("模块已创建");
      invalidateContent();
      setModuleDialog(null);
    },
    onError: handleError,
  });

  const renameModule = useMutation({
    mutationFn: ({ id: mid, name }: { id: number; name: string }) =>
      modulesApi.rename(mid, name),
    onSuccess: () => {
      toast.success("模块已重命名");
      invalidateContent();
      setModuleDialog(null);
    },
    onError: handleError,
  });

  const deleteModule = useMutation({
    mutationFn: (mid: number) => modulesApi.remove(mid),
    onSuccess: () => {
      toast.success("模块已删除");
      invalidateContent();
      setPendingDelete(null);
    },
    onError: handleError,
  });

  const moveModule = useMutation({
    mutationFn: ({ id: mid, targetParentId }: { id: number; targetParentId: number | null }) =>
      modulesApi.move(mid, targetParentId),
    onSuccess: () => {
      toast.success("模块已移动");
      invalidateContent();
      setMovingModule(null);
    },
    onError: handleError,
  });

  const createCase = useMutation({
    mutationFn: (body: TestCaseCreate) => casesApi.create(body),
    onSuccess: () => {
      toast.success("用例已创建");
      invalidateContent();
      setCaseDialog(null);
    },
    onError: handleError,
  });

  const updateCase = useMutation({
    mutationFn: ({ id: cid, body }: { id: number; body: TestCaseCreate }) =>
      casesApi.update(cid, body),
    onSuccess: (_data, vars) => {
      toast.success("用例已更新");
      invalidateContent();
      // 步骤可能已经变了，下一次打开编辑要拉新数据
      queryClient.invalidateQueries({ queryKey: queryKeys.case(vars.id) });
      setCaseDialog(null);
    },
    onError: handleError,
  });

  const deleteCase = useMutation({
    mutationFn: (cid: number) => casesApi.remove(cid),
    onSuccess: () => {
      toast.success("用例已删除");
      invalidateContent();
      setPendingDelete(null);
    },
    onError: handleError,
  });

  const runTest = useMutation({
    mutationFn: (body: {
      project: number;
      module?: number | null;
      case?: number | null;
      category: string;
      /** 仅 app 场景生效：指定某台 idle 设备；null/undefined 走自动池分配。 */
      device_id?: number | null;
      _key?: { type: string; id: number };
    }) =>
      runsApi.trigger(body),
    onMutate: (vars) => setRunningId(vars._key ?? null),
    onSettled: () => setRunningId(null),
    onSuccess: (res) => {
      toast.success(
        `已后台启动 · ${res.case_number ?? 0} 条用例 · task ${
          res.task_id ?? "-"
        }`,
      );
      setDevicePicker(null);
    },
    onError: handleError,
  });

  // ------ App 设备选择器：仅 category === "app" 时在运行前弹窗 ------
  // 保存被"挂起等设备选择"的运行参数；确认后再真正 trigger。
  type PendingRun = {
    project: number;
    module?: number | null;
    case?: number | null;
    category: string;
    _key?: { type: string; id: number };
    /** 给对话框标题展示用的目标名称（"用例 login"/"模块 登录"/"项目 某某"）。 */
    target?: string;
  };
  const [devicePicker, setDevicePicker] = useState<PendingRun | null>(null);

  const reorder = useMutation({
    mutationFn: casesApi.reorder,
    onSuccess: () => invalidateContent(),
    onError: handleError,
  });

  // ------ 业务动作 ------
  const project = projectQuery.data;
  /**
   * 用例编辑 / 运行链路里仍叫 `category`（历史名 + StepEditor 等下游 API 兼容），
   * v2 起它直接 = 当前 Tab 的栈，不再从 project.type 推导（projects.type 列已经删掉）。
   *
   * 注意：`category` 可能是 "functional" —— 对于自动化路径（运行 / CaseDialog HTTP 表单）
   * 我们会用 `automationCategory` 做兜底，把 functional 映射到 "api"，避免下游崩溃。
   * 真正的功能用例编辑 / 执行链路走独立的 FunctionalCases* 组件（B5）。
   */
  const category: ProjectStack = activeStack;
  const automationCategory: Exclude<ProjectStack, "functional"> =
    activeStack === "functional" ? "api" : (activeStack as Exclude<ProjectStack, "functional">);

  const handleEnterModule = (node: ContentNode) => {
    setBreadcrumb((prev) => [...prev, { id: node.id, name: node.name }]);
  };

  const handleJumpTo = (index: number) => {
    // -1 表示跳回根（面包屑第 0 段，也就是项目本身）
    if (index < 0) setBreadcrumb([]);
    else setBreadcrumb((prev) => prev.slice(0, index + 1));
  };

  /**
   * App 场景下："运行"按钮先挂起运行参数、弹出设备选择框；
   * api / web 场景下则直接触发，避免给用户加干扰步骤。
   *
   * functional Tab 不应该走到这里 —— 调用方都在按钮 disabled 时挡掉；
   * 防御性兜底：直接 toast 提示"功能用例不支持自动化执行"。
   */
  const triggerRun = (pending: PendingRun) => {
    if (pending.category === "functional") {
      toast.info("功能用例由人工勾结果，不走自动化执行链路");
      return;
    }
    // android / ios 都走 Appium 设备链路，运行前都需要选设备
    if (pending.category === "android" || pending.category === "ios") {
      setDevicePicker(pending);
      return;
    }
    runTest.mutate(pending);
  };

  const handleRunProject = () => {
    if (!project) return;
    triggerRun({
      project: project.id,
      category: automationCategory,
      _key: { type: "project", id: project.id },
      target: `项目 ${project.name} · ${STACK_LABELS[automationCategory]}`,
    });
  };

  const handleRunModule = (node: ContentNode) => {
    if (!project) return;
    triggerRun({
      project: project.id,
      module: node.id,
      category: automationCategory,
      _key: { type: "module", id: node.id },
      target: `模块 ${node.name}`,
    });
  };

  const handleRunCase = (node: ContentNode) => {
    if (!project) return;
    triggerRun({
      project: project.id,
      module: node.module_id ?? currentParentId ?? undefined,
      case: node.id,
      category: automationCategory,
      _key: { type: "case", id: node.id },
      target: `用例 ${node.name}`,
    });
  };

  /** 在某条用例「之前」插入新用例：目标 sort_order 就是那条用例当前的 sort_order。 */
  const handleInsertBefore = (node: ContentNode) => {
    if (!currentParentId) {
      toast.error("请先进入一个模块再插入用例");
      return;
    }
    setCaseDialog({
      mode: "create",
      moduleId: currentParentId,
      insertAt: node.sort_order ?? 0,
    });
  };

  const isManagementTab = searchParams.get("stack") === "management";

  // ------ 渲染：项目管理 Tab ------
  if (isManagementTab) {
    return (
      <div className="space-y-4 p-6">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Button variant="ghost" size="icon" className="shrink-0" onClick={() => navigate("/projects")}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="font-medium truncate">{projectQuery.data?.name ?? "…"}</span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
          </div>
        </div>
        <StackTabs projectId={projectId} enabledStacks={enabledStacks} counts={stackCountsQuery.data?.counts} active={activeStack} onChange={handleStackChange} loading={stackCountsQuery.isLoading} />
        <ProjectManagementPage />
      </div>
    );
  }

  // ------ 渲染：测试 Tab ------
  if (!Number.isFinite(projectId)) {
    return (
      <div className="p-8 text-sm text-destructive">非法的项目 ID。</div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      {/* 顶栏：返回 + 项目名 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            onClick={() => navigate("/projects")}
            title="返回项目列表"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <span className="font-medium truncate">
            {projectQuery.data?.name ?? "…"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2"></div>
      </div>

      {/* Tab 栏 */}
      <StackTabs
        projectId={projectId}
        enabledStacks={enabledStacks}
        counts={stackCountsQuery.data?.counts}
        active={activeStack}
        onChange={handleStackChange}
        loading={stackCountsQuery.isLoading}
      />

      {/* 面包屑 + 操作按钮 */}
      <div className="flex items-center justify-between gap-2">
        <Breadcrumb project={project?.name ?? "…"} trail={breadcrumb} onJump={handleJumpTo} />
        <div className="flex shrink-0 items-center gap-2">
        {!isFunctionalTab && (
        <Button
              variant="outline"
              onClick={handleRunProject}
              disabled={
                runningId?.type === "project" && runningId.id === projectId
              }
            >
              {runningId?.type === "project" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              运行 {STACK_LABELS[automationCategory]} 全部用例
            </Button>
        )}
        </div>
      </div>

      {/* functional Tab：项目概览面板 + 整页内嵌功能用例管理 */}
      {isFunctionalTab ? (
        <>
          <Card>
            <CardContent className="p-4">
              <details>
                <summary className="cursor-pointer text-sm font-medium">
                  项目概览 · 模块关联（AI 生成用例时据此设计跨模块联动用例）
                </summary>
                <div className="mt-3">
                  <ProjectAiOverviewView projectId={projectId} />
                </div>
              </details>
            </CardContent>
          </Card>
          <FunctionalCasesPage embedded />
        </>
      ) : (
        <>
        <div className="flex flex-wrap items-center gap-2">
          {/* 模块 CRUD 已移至项目管理页 */}
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
            新建用例
          </Button>
          <ImportButton
            projectId={projectId}
            moduleId={currentParentId}
            onDone={invalidateContent}
          />
          {/* 导出按钮：根目录不显示。在模块下导出"当前栈 + 当前模块（含子树）"；
              caseTypeFilter 跟当前 Tab 一致（API/Web/App 含 mixed；functional 单独）。 */}
          {currentParentId !== null ? (
            <ExportButton
              projectId={projectId}
              moduleId={currentParentId}
              caseTypes={caseTypeFilter}
            />
          ) : null}
        </div>
      </>)}

      {/* 主列表：functional Tab 已用内嵌的 FunctionalCasesPage 展示，这里不再渲染，
          否则功能页下面会多出一块「模块/用例」列表卡片（名称/信息/操作）。 */}
      {!isFunctionalTab && (contentQuery.isLoading ? (
        <ListSkeleton />
      ) : contentQuery.isError ? (
        <ErrorBox
          message={
            contentQuery.error instanceof Error
              ? contentQuery.error.message
              : "加载失败"
          }
          onRetry={() => contentQuery.refetch()}
        />
      ) : (contentQuery.data ?? []).length === 0 ? (
        <EmptyHint
          isRoot={currentParentId === null}
          onCreateModule={() =>
            setModuleDialog({ mode: "create", parentId: currentParentId })
          }
          onCreateCase={() =>
            currentParentId !== null &&
            setCaseDialog({ mode: "create", moduleId: currentParentId })
          }
        />
      ) : (
        <NodeTable
          nodes={contentQuery.data ?? []}
          onEnterModule={handleEnterModule}
          onEditModule={(n) =>
            setModuleDialog({ mode: "rename", moduleId: n.id, name: n.name })
          }
          onDeleteModule={(n) => setPendingDelete(n)}
          onRunModule={handleRunModule}
          onEditCase={(n) => setCaseDialog({ mode: "edit", node: n })}
          onDeleteCase={(n) => setPendingDelete(n)}
          onRunCase={handleRunCase}
          onInsertBefore={handleInsertBefore}
          onMoveModule={(n) =>
            setMovingModule({
              id: n.id,
              name: n.name,
              currentParentId: n.parent_id ?? null,
            })
          }
          onMove={(node, direction) => {
            // 历史 bug：之前在混合 nodes 里跨类型 swap，但 modules 和 test_cases
            // 各自有独立的 sort_order 命名空间（两边都从 1 开始），跨类型换号
            // 没意义；老数据里很多 case.sort_order 是 NULL/0/重复值，swap 也表现不出
            // 视觉差异 → 用户看到"用例上下移动无效"。
            //
            // 修复策略：
            //   1) 只在"同类型"相邻项之间 swap（模块跟模块换、用例跟用例换）；
            //   2) 不再单点改两个 sort_order，改成对该类型的整组重新 enumerate
            //      赋值 0..N-1，这样不管旧数据状态如何，重号一次就对齐了。
            const nodes = contentQuery.data ?? [];
            const sameType = nodes.filter((x) => x.type === node.type);
            const idx = sameType.findIndex((x) => x.id === node.id);
            const swapWith = direction === "up" ? idx - 1 : idx + 1;
            if (swapWith < 0 || swapWith >= sameType.length) return;
            const next = sameType.slice();
            [next[idx], next[swapWith]] = [next[swapWith], next[idx]];
            reorder.mutate(
              next.map((n, i) => ({
                id: n.id,
                type: n.type,
                new_order: i,
              })),
            );
          }}
          runningKey={runningId}
        />
      ))}

      {/* Dialogs */}
      <ModuleDialog
        state={moduleDialog}
        onClose={() => setModuleDialog(null)}
        submitting={createModule.isPending || renameModule.isPending}
        onSubmit={(values) => {
          if (!moduleDialog) return;
          if (moduleDialog.mode === "create") {
            createModule.mutate({
              project_id: projectId,
              parent_id: moduleDialog.parentId,
              name: values.name,
            });
          } else {
            renameModule.mutate({
              id: moduleDialog.moduleId,
              name: values.name,
            });
          }
        }}
      />

      <CaseDialog
        state={caseDialog}
        category={category}
        onClose={() => setCaseDialog(null)}
        submitting={createCase.isPending || updateCase.isPending}
        onSubmit={(values) => {
          if (!caseDialog) return;
          // values.steps 被 zod 放成了 any[]，这里转回 TestStepDraft[] 再往后端发
          const steps = (values.steps as TestStepDraft[] | undefined) ?? undefined;
          // API 项目不用 steps（保留后端推断），直接丢掉
          const isApi = category === "api";
          const payloadCommon: Omit<TestCaseCreate, "module_id"> = {
            name: values.name,
            description: values.description,
            method: values.method,
            path: values.path,
            headers: values.headers,
            data_type: values.data_type,
            params: values.params,
            extract_data: values.extract_data,
            assertion: values.assertion,
            sql_query: values.sql_query,
            wait_time: values.wait_time,
            skip: values.skip,
            case_type: (values.case_type as CaseType | undefined) ?? category,
            steps: isApi ? null : steps ?? [],
          };
          if (caseDialog.mode === "create") {
            createCase.mutate({
              ...payloadCommon,
              module_id: caseDialog.moduleId,
              sort_order:
                caseDialog.insertAt !== undefined ? caseDialog.insertAt : null,
            });
          } else {
            const n = caseDialog.node;
            updateCase.mutate({
              id: n.id,
              body: {
                ...payloadCommon,
                module_id: n.module_id ?? currentParentId ?? 0,
              },
            });
          }
        }}
      />

      <DeleteDialog
        node={pendingDelete}
        submitting={deleteModule.isPending || deleteCase.isPending}
        onCancel={() => setPendingDelete(null)}
        onConfirm={(node) =>
          node.type === "module"
            ? deleteModule.mutate(node.id)
            : deleteCase.mutate(node.id)
        }
      />

      {/* App 场景下运行前的设备选择器 —— 其它场景不会设 devicePicker */}
      <DevicePickerDialog
        open={devicePicker !== null}
        submitting={runTest.isPending}
        target={devicePicker?.target}
        onCancel={() => setDevicePicker(null)}
        onConfirm={(deviceId) => {
          if (!devicePicker) return;
          // device_id 可能为 null（= 自动），后端 runs.py 只对非 null 的值强校验 idle
          const { target: _t, ...runArgs } = devicePicker;
          runTest.mutate({
            ...runArgs,
            device_id: deviceId,
          });
        }}
      />

      {/* 「移动模块到…」对话框 */}
      <MoveModuleDialog
        projectId={projectId}
        projectName={project?.name ?? "项目"}
        moving={movingModule}
        submitting={moveModule.isPending}
        onCancel={() => setMovingModule(null)}
        onConfirm={(targetParentId) => {
          if (!movingModule) return;
          moveModule.mutate({
            id: movingModule.id,
            targetParentId,
          });
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 栈 Tab：v2 起项目可同时启用多个栈，详情页用顶部 Tab 切栈视图。
//
// 角标计数规则（保持和 caseTypesFor() 同步）：
//   - functional Tab：counts.functional
//   - 自动化 Tab（api/web/app）：counts[stack] + counts.mixed
//     mixed 用例会同时出现在它涉及的每个 Tab，所以这里也要把 mixed 计上。
//
// loading 期间渲染 skeleton 占位，不让 Tab 闪现一下又收起。
// 项目只启用 1 个栈时也照样渲染 Tab —— 视觉一致 + 防御后续启用更多栈不需要改 UI。
// ---------------------------------------------------------------------------
/** 每种栈的 lucide 图标 + 主色调（active 态用，主要是边框 / 文字色提亮）。
 * 颜色直接走 Tailwind 调色板而不是 token，因为这里就是要"每种栈一眼能区分"。
 * Android / iOS 当前 ProjectStack 还没拆出来；图标先备好，等 Issue 1 落地直接接。 */
const STACK_VISUAL: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; accent: string }
> = {
  functional: { icon: ClipboardList, accent: "border-amber-500 text-amber-700" },
  api: { icon: Braces, accent: "border-sky-500 text-sky-700" },
  web: { icon: Globe, accent: "border-emerald-500 text-emerald-700" },
  android: { icon: Smartphone, accent: "border-emerald-600 text-emerald-700" },
  ios: { icon: Apple, accent: "border-slate-700 text-slate-800" },
};

function StackTabs({
  projectId: _projectId,
  enabledStacks,
  counts,
  active,
  onChange,
  loading,
}: {
  projectId: number;
  enabledStacks: ProjectStack[];
  counts?: Record<CaseType, number>;
  active: ProjectStack;
  onChange: (next: string) => void;
  loading: boolean;
}) {
  const [searchParams] = useSearchParams();
  const isManagementActive = searchParams.get("stack") === "management";
  if (loading && enabledStacks.length === 0) {
    return (
      <div className="flex items-center gap-2">
        <Skeleton className="h-12 w-32" />
        <Skeleton className="h-12 w-32" />
        <Skeleton className="h-12 w-32" />
      </div>
    );
  }
  if (enabledStacks.length === 0) return null;

  const badgeOf = (stack: ProjectStack): number | null => {
    if (!counts) return null;
    if (stack === "functional") return counts.functional ?? 0;
    return (counts[stack as CaseType] ?? 0) + (counts.mixed ?? 0);
  };

  return (
    <div
      role="tablist"
      className="flex flex-wrap items-stretch gap-1 rounded-lg border bg-muted/40 p-1"
    >
      {/* 项目管理 — 通过 URL ?stack=management 内嵌在 ProjectDetailPage 中 */}
      <button
        type="button"
        role="tab"
        aria-selected={isManagementActive}
        onClick={() => onChange("management")}
        className={cn(
          "group relative flex items-center gap-2 rounded-md px-4 py-2 text-sm transition-all",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          isManagementActive
            ? "border-t-[3px] bg-background font-semibold shadow-sm border-red-500 text-red-800"
            : "border-t-[3px] border-transparent text-muted-foreground hover:bg-background/60 hover:text-foreground",
        )}
      >
        <FolderKanban className="h-4 w-4" />
        <span>项目管理</span>
      </button>
      {enabledStacks.map((s) => {
        const isActive = !isManagementActive && active === s;
        const badge = badgeOf(s);
        const visual = STACK_VISUAL[s] ?? STACK_VISUAL.api;
        const Icon = visual.icon;
        return (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(s)}
            className={cn(
              "group relative flex items-center gap-2 rounded-md px-4 py-2 text-sm transition-all",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              isActive
                ? cn(
                    "border-t-[3px] bg-background font-semibold shadow-sm",
                    visual.accent,
                  )
                : "border-t-[3px] border-transparent text-muted-foreground hover:bg-background/60 hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" />
            <span>{STACK_LABELS[s]}</span>
            {badge !== null ? (
              <span
                className={cn(
                  "ml-1 rounded-full px-1.5 py-0.5 text-xs font-normal tabular-nums",
                  isActive
                    ? "bg-muted text-foreground"
                    : "bg-background text-muted-foreground",
                )}
              >
                {badge}
              </span>
            ) : null}
          </button>
        );
      })}
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
// 列表（模块 / 用例）
// ---------------------------------------------------------------------------
function NodeTable({
  nodes,
  onEnterModule,
  onRunModule,
  onEditCase,
  onDeleteCase,
  onRunCase,
  onInsertBefore,
  onMove,
  runningKey,
}: {
  nodes: ContentNode[];
  onEnterModule: (n: ContentNode) => void;
  onEditModule: (n: ContentNode) => void;
  onDeleteModule: (n: ContentNode) => void;
  onRunModule: (n: ContentNode) => void;
  onEditCase: (n: ContentNode) => void;
  onDeleteCase: (n: ContentNode) => void;
  onRunCase: (n: ContentNode) => void;
  onInsertBefore: (n: ContentNode) => void;
  onMove: (n: ContentNode, direction: "up" | "down") => void;
  /** 模块专用：移动到不同父节点（弹树形选择器）。 */
  onMoveModule: (n: ContentNode) => void;
  runningKey: { type: string; id: number } | null;
}) {
  return (
    <Card>
      <div className="divide-y">
        <div className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-4 py-2 text-xs text-muted-foreground">
          <span>名称</span>
          <span>信息</span>
          <span className="w-[120px] text-right">操作</span>
        </div>
        {nodes.map((node) => {
          const running =
            runningKey?.type === node.type && runningKey.id === node.id;
          return (
            <div
              key={`${node.type}-${node.id}`}
              className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-4 py-3 text-sm hover:bg-muted/40"
            >
              <div className="flex min-w-0 items-center gap-2">
                {node.type === "module" ? (
                  <button
                    className="flex min-w-0 items-center gap-2 rounded px-1 py-0.5 hover:underline"
                    onClick={() => onEnterModule(node)}
                  >
                    <Folder className="h-4 w-4 shrink-0 text-amber-500" />
                    <span className="truncate font-medium">{node.name}</span>
                  </button>
                ) : (
                  <button
                    className="flex min-w-0 items-center gap-2 rounded px-1 py-0.5 text-left hover:underline"
                    onClick={() => onEditCase(node)}
                    title="点击编辑"
                  >
                    <FileText className="h-4 w-4 shrink-0 text-sky-500" />
                    <span className="truncate">{node.name}</span>
                  </button>
                )}
              </div>
              <div className="text-right text-xs text-muted-foreground">
                {node.type === "module" ? (
                  <span>模块</span>
                ) : (
                  <span className="font-mono">
                    {node.method ?? "--"}{" "}
                    <span className="text-muted-foreground">
                      {node.path ?? ""}
                    </span>
                  </span>
                )}
              </div>
              <div className="flex w-[120px] items-center justify-end gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  disabled={running}
                  title="运行"
                  onClick={() =>
                    node.type === "module"
                      ? onRunModule(node)
                      : onRunCase(node)
                  }
                >
                  {running ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-8 w-8">
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {node.type === "module" ? (
                      <>
                        <DropdownMenuItem
                          onSelect={(e) => {
                            e.preventDefault();
                            onEnterModule(node);
                          }}
                        >
                          <Folder className="h-4 w-4" />
                          进入
                        </DropdownMenuItem>
                        {/* 模块 CRUD 已统一收归"项目管理"页 */}
                      </>
                    ) : (
                      <>
                        <DropdownMenuItem
                          onSelect={(e) => {
                            e.preventDefault();
                            onEditCase(node);
                          }}
                        >
                          <Pencil className="h-4 w-4" />
                          编辑
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={(e) => {
                            e.preventDefault();
                            onInsertBefore(node);
                          }}
                        >
                          <Plus className="h-4 w-4" />
                          在此之前插入
                        </DropdownMenuItem>
                      </>
                    )}
                    {/* 同类型相邻判断：模块/用例各算各的，不被对方挤掉
                        disable 阈值。例如同层有 2 模块 + 5 用例，第 1 条用例
                        在 sameTypeIdx=0 才该 disable 上移，而不是它在混合 list
                        里的 i=2。 */}
                    {(() => {
                      const sameTypeNodes = nodes.filter(
                        (x) => x.type === node.type,
                      );
                      const sameIdx = sameTypeNodes.findIndex(
                        (x) => x.id === node.id,
                      );
                      const upDisabled = sameIdx <= 0;
                      const downDisabled =
                        sameIdx < 0 || sameIdx >= sameTypeNodes.length - 1;
                      return (
                        <>
                          <DropdownMenuItem
                            disabled={upDisabled}
                            onSelect={(e) => {
                              e.preventDefault();
                              onMove(node, "up");
                            }}
                          >
                            上移
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            disabled={downDisabled}
                            onSelect={(e) => {
                              e.preventDefault();
                              onMove(node, "down");
                            }}
                          >
                            下移
                          </DropdownMenuItem>
                        </>
                      );
                    })()}
                    {/* 模块删除已统一收归"项目管理"页；仅用例可在此删除 */}
                    {node.type === "case" ? (
                      <><DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onSelect={(e) => {
                        e.preventDefault();
                        onDeleteCase(node);
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                      删除
                    </DropdownMenuItem>
                    </>) : null}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 模块对话框（新建 / 重命名）
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
  const initialName = state?.mode === "rename" ? state.name : "";
  const form = useForm<ModuleFormValues>({
    resolver: zodResolver(moduleSchema),
    defaultValues: { name: initialName },
    values: { name: initialName },
  });

  return (
    <Dialog open={state !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {state?.mode === "rename" ? "重命名模块" : "新建模块"}
          </DialogTitle>
          <DialogDescription>
            模块用来组织用例，可以嵌套多层。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => onSubmit(values))}
        >
          <div className="space-y-1.5">
            <Label htmlFor="module-name">名称</Label>
            <Input
              id="module-name"
              autoFocus
              maxLength={50}
              placeholder="例如：登录流程"
              {...form.register("name")}
            />
            {form.formState.errors.name ? (
              <p className="text-xs text-destructive">
                {form.formState.errors.name.message}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              确定
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 用例对话框（新建 / 编辑 / 插入）
// ---------------------------------------------------------------------------
function CaseDialog({
  state,
  category,
  onClose,
  onSubmit,
  submitting,
}: {
  state:
    | { mode: "create"; moduleId: number; insertAt?: number }
    | { mode: "edit"; node: ContentNode }
    | null;
  category: ProjectStack;
  onClose: () => void;
  onSubmit: (values: CaseFormValues) => void;
  submitting: boolean;
}) {
  const existing = state?.mode === "edit" ? state.node : null;

  // 编辑态：拉用例详情（含 steps），创建态不需要
  const caseDetailQuery = useQuery({
    queryKey: existing ? queryKeys.case(existing.id) : ["case", -1],
    queryFn: () => casesApi.get(existing!.id),
    enabled: !!existing && state?.mode === "edit",
    // 对话框每次打开都重新拿，避免改过步骤后缓存不新鲜
    staleTime: 0,
  });

  const detail = caseDetailQuery.data;

  const defaults = useMemo<CaseFormValues>(() => {
    if (existing) {
      // 优先使用详情接口返回的字段；详情没回来之前先兜用树节点数据（少了 steps）
      const src = detail ?? (existing as unknown as Record<string, unknown>);
      const steps = (detail?.steps as TestStepDraft[] | undefined) ?? [];
      return {
        name: (src.name as string) ?? "",
        description: (src.description as string) ?? "",
        method: (src.method as string) ?? (category === "api" ? "GET" : ""),
        path: (src.path as string) ?? "",
        headers: (src.headers as string) ?? "",
        data_type: (src.data_type as string) ?? "application/json",
        params: (src.params as string) ?? "",
        extract_data: (src.extract_data as string) ?? "",
        assertion: (src.assertion as string) ?? "",
        sql_query: (src.sql_query as string) ?? "",
        wait_time: (src.wait_time as number) ?? 0,
        skip: (src.skip as boolean) ?? false,
        steps,
        case_type:
          (src.case_type as string | undefined) ??
          (category === "api" ? "api" : category),
      };
    }
    return {
      name: "",
      description: "",
      method: category === "api" ? "GET" : "",
      path: "",
      headers: "",
      data_type: "application/json",
      params: "",
      extract_data: "",
      assertion: "",
      sql_query: "",
      wait_time: 0,
      skip: false,
      steps: [],
      case_type: category,
    };
  }, [existing, detail, category]);

  const form = useForm<CaseFormValues>({
    resolver: zodResolver(caseSchema),
    defaultValues: defaults,
    values: defaults,
  });

  const isApi = category === "api";
  // 任何"非 API"用例都走 StepEditor（含 web / android / ios / mixed / functional）。
  // 名字保留 isWebOrApp 是历史遗留；语义其实是"step-editor 类用例"。
  const isWebOrApp =
    category === "web" ||
    category === "android" ||
    category === "ios";
  // 用 form.watch 订阅 steps，StepEditor 作为受控组件消费
  const currentSteps = (form.watch("steps") as TestStepDraft[] | undefined) ?? [];
  // 编辑态 + 正在加载详情：禁用表单，避免用户在空白 steps 上乱改
  const loadingDetail = !!existing && caseDetailQuery.isLoading;
  const title =
    state?.mode === "edit"
      ? "编辑用例"
      : state?.mode === "create" && state.insertAt !== undefined
        ? "在此处插入用例"
        : "新建用例";

  return (
    <Dialog open={state !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            用例的必填字段是名称。HTTP 相关字段只有 API 项目需要填。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => onSubmit(values))}
        >
          <div className="space-y-1.5">
            <Label htmlFor="case-name">名称</Label>
            <Input
              id="case-name"
              autoFocus
              maxLength={100}
              {...form.register("name")}
            />
            {form.formState.errors.name ? (
              <p className="text-xs text-destructive">
                {form.formState.errors.name.message}
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="case-desc">描述</Label>
            <Input
              id="case-desc"
              maxLength={200}
              {...form.register("description")}
            />
          </div>

          {isApi ? (
            <>
              <div className="grid grid-cols-[140px_1fr] gap-3">
                <div className="space-y-1.5">
                  <Label>方法</Label>
                  <Select
                    value={form.watch("method") ?? "GET"}
                    onValueChange={(v) =>
                      form.setValue("method", v, { shouldDirty: true })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {HTTP_METHODS.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="case-path">路径</Label>
                  <Input
                    id="case-path"
                    placeholder="/api/login"
                    {...form.register("path")}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="case-dtype">Content-Type</Label>
                  <Input
                    id="case-dtype"
                    placeholder="application/json"
                    {...form.register("data_type")}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="case-wait">等待秒数</Label>
                  <Input
                    id="case-wait"
                    type="number"
                    min={0}
                    {...form.register("wait_time")}
                  />
                </div>
              </div>

              <HighlightedField
                id="case-headers"
                label="请求头"
                hint={
                  <>
                    JSON 对象。支持变量 <Code>{"${token}"}</Code>；如
                    <Code>{'{"Authorization": "Bearer ${token}"}'}</Code>
                  </>
                }
                rows={2}
                placeholder='{"Authorization": "Bearer ${token}"}'
                value={form.watch("headers") ?? ""}
                onChange={(v) =>
                  form.setValue("headers", v, { shouldDirty: true })
                }
                check={checkHeaders}
                withJsonButton
              />

              <HighlightedField
                id="case-params"
                label="请求体 / 参数"
                hint={
                  <>
                    JSON 对象。支持 <Code>{"${var}"}</Code> 变量占位和
                    <Code>function:gen_xxx()</Code> 动态值
                  </>
                }
                rows={3}
                placeholder='{"username": "${user}", "ts": "function:now()"}'
                value={form.watch("params") ?? ""}
                onChange={(v) =>
                  form.setValue("params", v, { shouldDirty: true })
                }
                check={checkJson}
                withJsonButton
              />

              <div className="grid grid-cols-2 gap-3">
                <HighlightedField
                  id="case-extract"
                  label="提取参数"
                  hint={
                    <>
                      JSON 对象：<Code>{"{ 参数名: $.json.path }"}</Code>。 也可用
                      <Code>function:xxx</Code>
                    </>
                  }
                  rows={2}
                  placeholder='{"token": "$.data.token"}'
                  value={form.watch("extract_data") ?? ""}
                  onChange={(v) =>
                    form.setValue("extract_data", v, { shouldDirty: true })
                  }
                  check={checkExtract}
                />
                <HighlightedField
                  id="case-assert"
                  label="断言"
                  hint={
                    <>
                      JSON 对象：<Code>{"{ $.code: 0 }"}</Code>。key 可用
                      <Code>sql:select ...</Code>
                    </>
                  }
                  rows={2}
                  placeholder='{"$.code": 0, "$.data.msg": "ok"}'
                  value={form.watch("assertion") ?? ""}
                  onChange={(v) =>
                    form.setValue("assertion", v, { shouldDirty: true })
                  }
                  check={checkAssertion}
                />
              </div>

              <HighlightedField
                id="case-sql"
                label="SQL 校验"
                hint={
                  <>
                    用于在请求前 / 后查库拿数据。支持多条 SQL 用 <Code>;</Code> 分隔，支持
                    <Code>{"${var}"}</Code>
                  </>
                }
                rows={2}
                placeholder="select status from orders where id = ${order_id}"
                value={form.watch("sql_query") ?? ""}
                onChange={(v) =>
                  form.setValue("sql_query", v, { shouldDirty: true })
                }
              />
            </>
          ) : isWebOrApp ? (
            loadingDetail ? (
              <div className="flex items-center gap-2 rounded border border-dashed p-3 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在加载步骤…
              </div>
            ) : (
              <StepEditor
                category={category as CaseType}
                value={currentSteps}
                onChange={(next) =>
                  form.setValue(
                    "steps",
                    next as unknown as CaseFormValues["steps"],
                    { shouldDirty: true },
                  )
                }
              />
            )
          ) : (
            <p className="rounded border border-dashed p-3 text-xs text-muted-foreground">
              当前项目类型未识别，用例只会保留名称 / 描述这些基础字段。
            </p>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 删除确认
// ---------------------------------------------------------------------------
function DeleteDialog({
  node,
  submitting,
  onCancel,
  onConfirm,
}: {
  node: ContentNode | null;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (node: ContentNode) => void;
}) {
  return (
    <Dialog open={node !== null} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            删除{node?.type === "module" ? "模块" : "用例"}
          </DialogTitle>
          <DialogDescription>
            {node?.type === "module"
              ? `确认删除模块「${node.name}」？模块下所有子模块和用例会一起被删除，不可恢复。`
              : `确认删除用例「${node?.name}」？此操作不可恢复。`}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="destructive"
            disabled={submitting}
            onClick={() => node && onConfirm(node)}
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 移动模块对话框
//
// 把模块挪到另一个父节点下：树形选择器，根 = 项目本身，子节点 = 项目下其他模块。
// 后端 /api/modules?project_id=X&exclude_subtree=Y 已经把"自己 + 后代"过滤掉了，
// 这里只负责渲染 + 让用户挑一个 target_parent_id（null = 项目根）。
//
// 设计取舍：
//   - 拉的是"扁平 list + parent_id"，这里现做 group-by-parent 拼成树。模块数量
//     一般不大（几十到几百），递归渲染 O(N) 完全可接受，没必要后端就给嵌套结构。
//   - 默认选中"当前 parent"，让用户明确看到"现在在哪"，需要主动改才会触发移动。
//   - 当前 parent 项不禁用，但提交时若与原值相同后端会直接当成功（避免双向耦合）。
// ---------------------------------------------------------------------------
function MoveModuleDialog({
  projectId,
  projectName,
  moving,
  submitting,
  onCancel,
  onConfirm,
}: {
  projectId: number;
  projectName: string;
  moving: { id: number; name: string; currentParentId: number | null } | null;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (targetParentId: number | null) => void;
}) {
  const open = moving !== null;
  const [selected, setSelected] = useState<number | null>(null);

  const pickerQuery = useQuery({
    enabled: open && moving !== null,
    queryKey: ["modules-picker", projectId, moving?.id ?? null],
    queryFn: () =>
      modulesApi.listForPicker(projectId, moving?.id ?? null),
  });

  // 每次打开把 selected 重置成"当前 parent"，让用户看见现状
  // （key 变了重新挂载也行，但 useEffect 更显式）。
  useEffect(() => {
    if (open && moving) setSelected(moving.currentParentId ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, moving?.id]);

  const nodes = useMemo(() => pickerQuery.data ?? [], [pickerQuery.data]);
  // group by parent_id（null = 顶层）
  const childrenOf = useMemo(() => {
    const map = new Map<number | "root", typeof nodes>();
    for (const n of nodes) {
      const key: number | "root" = n.parent_id == null ? "root" : n.parent_id;
      const arr = map.get(key) ?? [];
      arr.push(n);
      map.set(key, arr);
    }
    // 每层按 sort_order / id 稳定排序
    for (const arr of map.values()) {
      arr.sort(
        (a, b) =>
          (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id,
      );
    }
    return map;
  }, [nodes]);

  const renderNode = (n: (typeof nodes)[number], depth: number) => {
    const subs = childrenOf.get(n.id) ?? [];
    const isSelected = selected === n.id;
    return (
      <div key={n.id}>
        <button
          type="button"
          onClick={() => setSelected(n.id)}
          className={cn(
            "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent",
            isSelected && "bg-accent font-medium",
          )}
          style={{ paddingLeft: 8 + depth * 16 }}
        >
          <Folder className="h-4 w-4 shrink-0 text-amber-500" />
          <span className="truncate">{n.name}</span>
        </button>
        {subs.length > 0 ? (
          <div>{subs.map((c) => renderNode(c, depth + 1))}</div>
        ) : null}
      </div>
    );
  };

  const tops = childrenOf.get("root") ?? [];

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>移动模块</DialogTitle>
          <DialogDescription>
            选择「{moving?.name ?? "—"}」要移动到的位置。模块自身和它的子孙不会出现在列表里。
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[55vh] overflow-y-auto rounded-md border">
          {/* 项目根节点（target_parent_id = null） */}
          <button
            type="button"
            onClick={() => setSelected(null)}
            className={cn(
              "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent",
              selected === null && "bg-accent font-medium",
            )}
          >
            <Folder className="h-4 w-4 shrink-0 text-amber-500" />
            <span className="truncate">{projectName}（项目根）</span>
          </button>

          {pickerQuery.isLoading ? (
            <div className="px-3 py-4 text-xs text-muted-foreground">
              加载中…
            </div>
          ) : pickerQuery.isError ? (
            <div className="px-3 py-4 text-xs text-destructive">
              加载失败：
              {pickerQuery.error instanceof Error
                ? pickerQuery.error.message
                : "未知错误"}
            </div>
          ) : tops.length === 0 ? (
            <div className="px-3 py-4 text-xs text-muted-foreground">
              项目下暂无其他可选模块，只能移动到项目根。
            </div>
          ) : (
            <div className="py-1">{tops.map((n) => renderNode(n, 0))}</div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
          <Button
            disabled={submitting}
            onClick={() => onConfirm(selected)}
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            确认移动
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 导入按钮（隐藏的 file input + 上传）
// ---------------------------------------------------------------------------
function ImportButton({
  projectId,
  moduleId,
  onDone,
}: {
  projectId: number;
  moduleId: number | null;
  onDone: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  const importMutation = useMutation({
    mutationFn: ({ file }: { file: File }) => {
      if (moduleId == null) {
        throw new Error("用例必须导入到具体模块下，请先进入一个模块");
      }
      return casesApi.importExcel(projectId, moduleId, file);
    },
    onSuccess: () => {
      toast.success("导入成功");
      onDone();
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "导入失败";
      toast.error(msg);
    },
  });

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = ""; // 同一个文件可以重复选
          if (f) importMutation.mutate({ file: f });
        }}
      />
      <Button
        variant="outline"
        size="sm"
        disabled={moduleId == null || importMutation.isPending}
        title={
          moduleId == null
            ? "导入需要在某个模块下进行 —— 先进入一个模块"
            : undefined
        }
        onClick={() => inputRef.current?.click()}
      >
        {importMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Upload className="h-4 w-4" />
        )}
        导入用例
      </Button>
    </>
  );
}

// ---------------------------------------------------------------------------
// 导出按钮（下拉选 xlsx / csv）
//
// 范围语义跟后端 export_cases 对齐（v2）：
//   - moduleId 必传 + caseTypes 必传（前端在根目录直接不渲染本组件）
//   - 导出"该模块为根的子树"，按"同层 modules + cases 按 sort_order 交错"
//     做前序遍历——视觉上跟用户在文件管理器看到的顺序一致
//   - caseTypes 跟当前栈 Tab 一致（API/Web/App 通常 [stack, "mixed"]，
//     functional 是 ["functional"]），mixed 用例会跟着对应自动化栈一起被导出
//
// 走原生 fetch 拉 Blob，不复用 request()，因为后端返回的是文件流而不是 JSON。
// 失败统一用 toast 报错；触发瞬间禁用按钮，避免双击重复下载。
// ---------------------------------------------------------------------------
function ExportButton({
  projectId,
  moduleId,
  caseTypes,
}: {
  projectId: number;
  moduleId: number;
  caseTypes: CaseType[];
}) {
  const [pending, setPending] = useState<null | "xlsx" | "csv">(null);

  const handleExport = async (format: "xlsx" | "csv") => {
    if (pending) return;
    setPending(format);
    try {
      await casesApi.exportCases({
        projectId,
        moduleId,
        caseTypes,
        format,
      });
      toast.success(format === "xlsx" ? "Excel 导出完成" : "CSV 导出完成");
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "导出失败";
      toast.error(msg);
    } finally {
      setPending(null);
    }
  };

  const scopeHint = `导出当前模块（含子模块）下 ${caseTypes.join("/")} 用例`;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={pending !== null}
          title={scopeHint}
        >
          {pending !== null ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
          导出用例
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          disabled={pending !== null}
          onClick={() => handleExport("xlsx")}
        >
          导出 Excel (.xlsx)
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={pending !== null}
          onClick={() => handleExport("csv")}
        >
          导出 CSV (.csv)
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------------------
// 辅助：空态 / 骨架 / 错误
// ---------------------------------------------------------------------------
function EmptyHint({
  isRoot,
  onCreateModule,
  onCreateCase,
}: {
  isRoot: boolean;
  onCreateModule: () => void;
  onCreateCase: () => void;
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center text-sm text-muted-foreground">
        <p>
          {isRoot
            ? "项目还是空的，先建一个顶层模块吧。"
            : "这个模块里还没有内容 —— 可以新建子模块，或者直接建一条用例。"}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onCreateModule}>
            <Folder className="h-4 w-4" />
            新建模块
          </Button>
          {!isRoot ? (
            <Button variant="outline" size="sm" onClick={onCreateCase}>
              <Plus className="h-4 w-4" />
              新建用例
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function ListSkeleton() {
  return (
    <Card>
      <div className="divide-y">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-4 py-3">
            <Skeleton className="h-4 w-4" />
            <Skeleton className="h-4 flex-1" />
            <Skeleton className="h-4 w-20" />
          </div>
        ))}
      </div>
    </Card>
  );
}

function ErrorBox({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="border-destructive/50">
      <CardContent className="flex flex-col items-start gap-3 py-6">
        <div className="text-sm text-destructive">加载失败：{message}</div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          重试
        </Button>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 用例表单里的"带高亮 + 校验状态"字段
// ---------------------------------------------------------------------------
/**
 * HighlightedField：Label + HighlightedTextarea + 可选的 "校验 JSON" 按钮 + 实时格式提示。
 * 把校验逻辑抽成 `check` 函数传进来，组件不关心每个字段的具体语法。
 */
function HighlightedField({
  id,
  label,
  hint,
  rows,
  placeholder,
  value,
  onChange,
  check,
  withJsonButton,
}: {
  id: string;
  label: string;
  hint?: React.ReactNode;
  rows?: number;
  placeholder?: string;
  value: string;
  onChange: (next: string) => void;
  /** 每次渲染会跑一遍，返回 state=empty 时不显示错误。 */
  check?: (text: string) => JsonCheck;
  /** 是否在右上角显示 "校验 JSON" 小按钮（失败 toast 错误、成功自动 format） */
  withJsonButton?: boolean;
}) {
  const result: JsonCheck = check ? check(value) : { state: "empty" };
  const showError = result.state === "error" && value.trim().length > 0;
  const showOk = result.state === "ok";

  const handleValidate = () => {
    const r = check ? check(value) : checkJson(value);
    if (r.state === "empty") {
      toast.info("内容为空");
      return;
    }
    if (r.state === "error") {
      toast.error("JSON 格式错误：" + r.message);
      return;
    }
    // ok —— 如果格式化之后和原值不一样，自动落回
    if (r.pretty && r.pretty.trim() !== value.trim()) {
      onChange(r.pretty);
      toast.success("JSON 格式正确，已自动格式化");
    } else {
      toast.success("JSON 格式正确");
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id} className="flex items-center gap-1.5">
          {label}
          {value.trim().length > 0 && check ? (
            showOk ? (
              <Check className="h-3.5 w-3.5 text-emerald-600" />
            ) : showError ? (
              <X className="h-3.5 w-3.5 text-destructive" />
            ) : null
          ) : null}
        </Label>
        {withJsonButton ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={handleValidate}
          >
            <Braces className="h-3 w-3" />
            校验 JSON
          </Button>
        ) : null}
      </div>
      <HighlightedTextarea
        id={id}
        rows={rows}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        invalid={showError}
      />
      {showError ? (
        <p className="flex items-start gap-1 text-[11px] text-destructive">
          <Info className="mt-[1px] h-3 w-3 shrink-0" />
          <span>{(result as Extract<JsonCheck, { state: "error" }>).message}</span>
        </p>
      ) : hint ? (
        <p
          className={cn(
            "flex items-start gap-1 text-[11px] text-muted-foreground",
          )}
        >
          <Info className="mt-[1px] h-3 w-3 shrink-0 opacity-60" />
          <span>{hint}</span>
        </p>
      ) : null}
    </div>
  );
}

/** 行内的小标记代码，用来在 hint 里展示 `${var}` 这种占位符。 */
function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="mx-0.5 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-foreground/80">
      {children}
    </code>
  );
}

