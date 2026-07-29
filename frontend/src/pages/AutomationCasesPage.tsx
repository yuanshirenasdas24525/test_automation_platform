import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Bug,
  Check,
  ChevronRight,
  CloudOff,
  Download,
  FileText,
  Folder,
  Gauge,
  GripVertical,
  History,
  ListChecks,
  ListPlus,
  ListOrdered,
  Loader2,
  Pencil,
  Play,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MarkdownView } from "@/components/editor/MarkdownView";
import {
  aiModelsApi,
  automationCasesApi,
  ApiError,
  aiApi,
  casesApi,
  contentApi,
  functionalCasesApi,
  modulesApi,
  projectsApi,
  reportsApi,
  runsApi,
  type ReportAnalysisOutput,
  type ReportAnalysisSuggestion,
  type ModulePickerNode,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  AiCaseFlag,
  AiFlagClearReason,
  AiFlagCounts,
  AiFlagType,
  ApiCase,
  ApiCaseEditRecord,
  ApiRunStatus,
  ApiTestHistoryReport,
  CaseType,
  ContentNode,
  TestCaseCreate,
  TestStepDraft,
} from "@/types/domain";
import { Textarea } from "@/components/ui/textarea";
import { AiGenerateDialog } from "./FunctionalCasesPage";
import { CaseDialog, type CaseFormValues } from "@/components/case/CaseDialog";

type DiagnoseResult = {
  classification: string;
  reason: string;
  suggestion: string;
  fix: {
    extract: Record<string, unknown>;
    assertion: Record<string, unknown>;
    params?: Record<string, unknown>;
  };
};

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 500] as const;
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
const CASE_LABELS: Record<CaseType, string> = {
  api: "API",
  web: "Web",
  android: "Android",
  ios: "iOS",
  mixed: "Mixed",
  functional: "功能",
};
type AutomationTabCaseType = Exclude<CaseType, "functional" | "mixed">;
type ApiQuickNewRow = {
  tempId: string;
  aboveCaseId?: number;
  insertSortOrder?: number;
};

type PerformancePoolItem = {
  caseId: number;
  moduleId: number;
  moduleName: string;
  name: string;
  method: string;
  path: string;
};

function performancePoolKey(projectId: number): string {
  return `performance-interface-pool:${projectId}`;
}

function readPerformancePool(projectId: number): PerformancePoolItem[] {
  if (!Number.isFinite(projectId)) return [];
  try {
    const raw = window.localStorage.getItem(performancePoolKey(projectId));
    if (!raw) return [];
    const items = JSON.parse(raw) as PerformancePoolItem[];
    if (!Array.isArray(items)) return [];
    return items.filter(
      (item) =>
        Number.isInteger(item.caseId) &&
        item.caseId > 0 &&
        Number.isInteger(item.moduleId) &&
        item.moduleId > 0,
    );
  } catch {
    return [];
  }
}

function mergePerformancePool(
  current: PerformancePoolItem[],
  incoming: PerformancePoolItem[],
): PerformancePoolItem[] {
  const byCaseId = new Map(current.map((item) => [item.caseId, item]));
  for (const item of incoming) byCaseId.set(item.caseId, item);
  return [...byCaseId.values()];
}

const STATUS_META: Record<ApiRunStatus, { label: string; className: string }> = {
  pending: { label: "待执行", className: "bg-slate-100 text-slate-600" },
  passed: { label: "通过", className: "bg-emerald-100 text-emerald-700" },
  failed: { label: "失败", className: "bg-red-100 text-red-700" },
  error: { label: "错误", className: "bg-orange-100 text-orange-700" },
  skipped: { label: "跳过", className: "bg-zinc-100 text-zinc-600" },
};

// AI 诊断标记的展示元数据（docs/ai_case_flags_design.md §1）
const FLAG_META: Record<AiFlagType, { label: string; className: string; Icon: typeof Wrench; hint: string }> = {
  manual_fix: {
    label: "需人工",
    className: "bg-amber-100 text-amber-700 hover:bg-amber-200",
    Icon: Wrench,
    hint: "AI 判定为用例问题但无法自动修复，需人工修改",
  },
  interface_defect: {
    label: "疑似接口缺陷",
    className: "bg-red-100 text-red-700 hover:bg-red-200",
    Icon: Bug,
    hint: "AI 判定接口返回异常，建议重点检查接口本身",
  },
  environment: {
    label: "环境",
    className: "bg-slate-200 text-slate-600 hover:bg-slate-300",
    Icon: CloudOff,
    hint: "AI 判定为环境/依赖问题（超时/5xx/连不上等）",
  },
  ai_fixed: {
    label: "AI已修复",
    className: "bg-emerald-100 text-emerald-700 hover:bg-emerald-200",
    Icon: Sparkles,
    hint: "AI 已修复且重跑验证通过，建议复核；下次通过后自动消失",
  },
};

const CLEAR_REASON_OPTIONS: { value: AiFlagClearReason; label: string; desc: string }[] = [
  { value: "manually_fixed", label: "已人工修复", desc: "备注里写下改了什么，会作为经验喂给 AI" },
  { value: "misjudged", label: "AI 判断有误", desc: "需选择正确分类；下次诊断会遵循你的更正" },
  { value: "external_fixed", label: "接口已修复 / 环境已恢复", desc: "问题在平台外部解决了" },
  { value: "wont_fix", label: "无需处理（预期行为）", desc: "如负向用例 4xx 本来就对；AI 以后不再自动修它" },
];

function messageOf(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "操作失败";
}

function sessionId(): string {
  return `api-edit-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function buildQuickHttpStep(input: {
  name: string;
  method: string;
  path: string;
  headers?: string;
  params?: string;
}): TestStepDraft {
  return {
    step_order: 0,
    step_name: input.name.trim() || "API 请求",
    step_type: "http_request",
    skip: false,
    config: {
      method: (input.method || "GET").toUpperCase(),
      path: input.path || "",
      headers: input.headers || "",
      data_type: "application/json",
      params: input.params || "",
    },
    wait_before: 0,
    timeout: 60,
    retry: 0,
    on_failure: "stop",
  };
}

function appendAssertionRule(
  steps: TestStepDraft[],
  stepId: number | null,
  target: string,
  expected: unknown,
) {
  const nextSteps = steps.map((step) => ({ ...step, assertion: [...(step.assertion ?? [])] }));
  const indexById = stepId == null ? -1 : nextSteps.findIndex((step) => step.id === stepId);
  const index = indexById >= 0
    ? indexById
    : nextSteps.findIndex((step) => step.step_type === "http_request");
  if (index < 0) {
    throw new Error("该用例没有可更新的 HTTP 步骤，请进入用例编辑器手动处理");
  }
  const type = expected === "not_empty"
    ? "is_not_null"
    : target.startsWith("$")
      ? "jsonpath"
      : "equal";
  const rule = {
    type,
    target,
    expected: expected === "not_empty" ? null : expected,
    description: `AI 全面分析补充：${target}`,
  };
  const old = nextSteps[index].assertion ?? [];
  const exists = old.some((item) => {
    const raw = item as Record<string, unknown>;
    return String(raw.target ?? "") === target;
  });
  nextSteps[index].assertion = exists
    ? old.map((item) => String((item as Record<string, unknown>).target ?? "") === target ? rule : item)
    : [...old, rule];
  return { steps: nextSteps };
}

function appendExtractRule(
  steps: TestStepDraft[],
  stepId: number | null,
  variable: string,
  jsonpath: string,
) {
  const nextSteps = steps.map((step) => ({ ...step, extract: [...(step.extract ?? [])] }));
  const indexById = stepId == null ? -1 : nextSteps.findIndex((step) => step.id === stepId);
  const index = indexById >= 0
    ? indexById
    : nextSteps.findIndex((step) => step.step_type === "http_request");
  if (index < 0) {
    throw new Error("该用例没有可更新的 HTTP 步骤，请进入用例编辑器手动处理");
  }
  const rule = { name: variable, from: "response.body", jsonpath };
  const old = nextSteps[index].extract ?? [];
  const exists = old.some((item) => String((item as Record<string, unknown>).name ?? "") === variable);
  nextSteps[index].extract = exists
    ? old.map((item) => String((item as Record<string, unknown>).name ?? "") === variable ? rule : item)
    : [...old, rule];
  return { steps: nextSteps };
}

const PLATFORM_TIME_ZONE = "Asia/Shanghai";
const EDIT_HISTORY_MERGE_WINDOW_MS = 30 * 60 * 1000;

function normalizeApiTime(value: string): string {
  const normalized = value.trim().replace(" ", "T");
  return /[zZ]|[+-]\d{2}:?\d{2}$/.test(normalized) ? normalized : `${normalized}Z`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "--";
  const date = new Date(normalizeApiTime(value));
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: PLATFORM_TIME_ZONE,
  }).format(date);
}

function getTimeValue(value: string | null | undefined): number {
  if (!value) return 0;
  const time = new Date(normalizeApiTime(value)).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function Checkbox({ checked, onCheckedChange }: { checked: boolean; onCheckedChange: () => void }) {
  return <input type="checkbox" checked={checked} onChange={onCheckedChange} className="h-4 w-4 rounded border-input accent-primary" />;
}

function Badge({ children, className }: { children: ReactNode; variant?: "outline"; className?: string }) {
  return <span className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs", className)}>{children}</span>;
}

export function AutomationCasesPage({
  embedded = false,
  caseType = "api",
  resetKey = 0,
}: {
  embedded?: boolean;
  caseType?: AutomationTabCaseType;
  resetKey?: number;
} = {}) {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const caseLabel = CASE_LABELS[caseType];
  const isApiWorkbench = caseType === "api";
  const [trail, setTrail] = useState<Array<{ id: number; name: string }>>([]);
  const moduleId = trail.at(-1)?.id ?? null;
  const [quickEdit, setQuickEdit] = useState(false);
  const [editSession, setEditSession] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState<ApiRunStatus | "all">("all");
  const [flagFilter, setFlagFilter] = useState<AiFlagType | "all">("all");
  const [flagDialogCase, setFlagDialogCase] = useState<ApiCase | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [editor, setEditor] = useState<ApiCase | "new" | null>(null);
  const [editorSaving, setEditorSaving] = useState(false);
  const [recordsOpen, setRecordsOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [newRows, setNewRows] = useState<ApiQuickNewRow[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [diagnose, setDiagnose] = useState<{ row: ApiCase; loading: boolean; result: DiagnoseResult | null } | null>(null);
  const [runDetailCase, setRunDetailCase] = useState<ApiCase | null>(null);
  const [renumbering, setRenumbering] = useState(false);
  const [performancePool, setPerformancePool] = useState<PerformancePoolItem[]>(
    () => readPerformancePool(projectId),
  );
  const [performancePickerOpen, setPerformancePickerOpen] = useState(false);

  const aiModelsQuery = useQuery({
    queryKey: ["ai-models", projectId],
    queryFn: () => aiModelsApi.list(projectId),
    enabled: Number.isFinite(projectId),
  });
  const firstModel = (aiModelsQuery.data ?? []).find((m) => m.enabled)?.name ?? "";
  const enabledModels = (aiModelsQuery.data ?? []).filter((m) => m.enabled).map((m) => m.name);

  useEffect(() => {
    if (!Number.isFinite(projectId)) return;
    if (performancePool.length === 0) {
      window.localStorage.removeItem(performancePoolKey(projectId));
      return;
    }
    window.localStorage.setItem(
      performancePoolKey(projectId),
      JSON.stringify(performancePool),
    );
  }, [performancePool, projectId]);

  const handleDiagnose = async (row: ApiCase) => {
    if (!firstModel) {
      toast.error("请先在项目配置 → AI 添加可用 AI 模型");
      return;
    }
    setDiagnose({ row, loading: true, result: null });
    try {
      const res = await functionalCasesApi.aiDiagnoseRun({ case_id: row.id, model_name: firstModel });
      setDiagnose({ row, loading: false, result: res });
    } catch (e) {
      toast.error(messageOf(e));
      setDiagnose(null);
    }
  };

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => projectsApi.get(projectId),
    enabled: Number.isFinite(projectId),
  });
  const contentQuery = useQuery({
    queryKey: ["content", projectId, moduleId, "automation-workbench", caseType],
    queryFn: () => contentApi.list(projectId, moduleId, caseType),
    enabled: Number.isFinite(projectId),
  });
  const casesQuery = useQuery({
    queryKey: ["automation-cases", caseType, moduleId, status, flagFilter, keyword, page, pageSize],
    queryFn: () => automationCasesApi.list({
      moduleId: moduleId!,
      caseType,
      status: status === "all" ? undefined : status,
      flagType: isApiWorkbench && flagFilter !== "all" ? flagFilter : undefined,
      keyword,
      page,
      pageSize,
    }),
    enabled: moduleId != null,
  });
  // 模块卡片角标：项目内各模块 active AI 标记计数（含子树聚合）
  const flagCountsQuery = useQuery({
    queryKey: ["api-flag-counts", projectId],
    queryFn: () => automationCasesApi.aiFlagCounts(projectId),
    enabled: Number.isFinite(projectId) && isApiWorkbench,
  });
  const flagCounts: AiFlagCounts = isApiWorkbench ? flagCountsQuery.data ?? {} : {};

  const modules = (contentQuery.data ?? []).filter((node) => node.type === "module");
  const cases = casesQuery.data?.items ?? [];
  const total = casesQuery.data?.total ?? 0;
  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(total / pageSize));

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["automation-cases", caseType] });
    queryClient.invalidateQueries({ queryKey: ["automation-test-history", caseType] });
    queryClient.invalidateQueries({ queryKey: ["automation-edit-history", caseType] });
    queryClient.invalidateQueries({ queryKey: ["project-stack-counts", projectId] });
    queryClient.invalidateQueries({ queryKey: ["api-flag-counts", projectId] });
  };

  const renumberCases = async (enable: boolean) => {
    if (moduleId == null) return;
    setRenumbering(true);
    try {
      const res = await automationCasesApi.renumber(moduleId, { enable, caseType });
      invalidate();
      toast.success(enable ? `已按执行顺序编号 ${res.total} 条用例` : `已去掉 ${res.updated} 条用例的序号`);
    } catch (e) {
      toast.error(messageOf(e));
    } finally {
      setRenumbering(false);
    }
  };

  useEffect(() => {
    setSelected(new Set());
    setPage(1);
  }, [moduleId, quickEdit, caseType]);

  useEffect(() => {
    setTrail([]);
    setSelected(new Set());
    setPage(1);
    setEditor(null);
    setRecordsOpen(false);
    setRunDetailCase(null);
    setFlagDialogCase(null);
  }, [resetKey]);

  useEffect(() => {
    if (!isApiWorkbench) exitQuickEdit();
  }, [isApiWorkbench]);

  const enterQuickEdit = () => {
    setQuickEdit(true);
    setEditSession(sessionId());
    setSelected(new Set());
    setNewRows([{ tempId: sessionId() }]);
  };
  const exitQuickEdit = () => {
    setQuickEdit(false);
    setEditSession(null);
    setSelected(new Set());
    setNewRows([]);
  };

  const insertRowAbove = useCallback((caseId: number, sortOrder?: number | null) => {
    setNewRows((current) => [
      ...current,
      { tempId: sessionId(), aboveCaseId: caseId, insertSortOrder: sortOrder ?? undefined },
    ]);
  }, []);

  // AI 自愈用哪个模型；"__rules__" = 只做零成本的规则自愈，不调 LLM
  const [healModel, setHealModel] = useState<string>("__rules__");
  const { data: healModels } = useQuery({
    queryKey: ["ai-models", projectId],
    queryFn: () => aiModelsApi.list(projectId),
    enabled: Number.isFinite(projectId),
    staleTime: 5 * 60_000,
  });

  const runMutation = useMutation({
    mutationFn: ({ ids, heal, model }: { ids: number[]; heal?: boolean; model?: string | null }) =>
      runsApi.trigger({
        project: projectId,
        category: caseType,
        case_ids: ids,
        ai_heal: heal,
        ai_model: model ?? null,
      }),
    onSuccess: (result) => {
      toast.success(
        `已提交 ${result.case_number ?? 0} 条 ${caseLabel} 用例，报告 #${result.report_id}` +
        (result.ai_heal ? "；跑完会自动分诊并修复可确定的问题" : ""),
      );
      setSelected(new Set());
      invalidate();
      window.setTimeout(invalidate, 2500);
      window.setTimeout(invalidate, 7000);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const removeMutation = useMutation({
    mutationFn: (caseId: number) => automationCasesApi.remove(caseId, editSession ?? undefined),
    onSuccess: (data) => {
      if (data.batch_id) {
        toast.success("用例已删除", {
          action: {
            label: "撤销",
            onClick: async () => {
              try {
                await casesApi.rollbackHistory(data.batch_id!, { mode: "full" });
                toast.success("已恢复");
                invalidate();
              } catch (error) {
                toast.error(messageOf(error));
              }
            },
          },
        });
      } else {
        toast.success("用例已删除");
      }
      invalidate();
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const batchDelete = async () => {
    if (!selected.size || !window.confirm(`确定删除选中的 ${selected.size} 条用例吗？`)) return;
    try {
      const results = await Promise.all([...selected].map((caseId) => automationCasesApi.remove(caseId, editSession ?? undefined)));
      const batchIds = results.map((item) => item.batch_id).filter((id): id is number => id != null);
      toast.success(`已删除 ${selected.size} 条用例`, batchIds.length > 0 ? {
        action: {
          label: "撤销",
          onClick: async () => {
            try {
              await Promise.all(batchIds.map((batchId) => casesApi.rollbackHistory(batchId, { mode: "full" })));
              toast.success("已恢复");
              invalidate();
            } catch (error) {
              toast.error(messageOf(error));
            }
          },
        },
      } : undefined);
      setSelected(new Set());
      invalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const reorder = async (row: ApiCase, direction: "up" | "down") => {
    const index = cases.findIndex((item) => item.id === row.id);
    const target = direction === "up" ? index - 1 : index + 1;
    if (target < 0 || target >= cases.length) return;
    const next = cases.slice();
    [next[index], next[target]] = [next[target], next[index]];
    try {
      await casesApi.reorder(next.map((item, order) => ({ id: item.id, type: "case", new_order: order })));
      invalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const importFile = async (file: File) => {
    if (moduleId == null) return;
    try {
      await casesApi.importExcel(projectId, moduleId, file);
      toast.success("API 用例导入完成");
      invalidate();
    } catch (error) {
      toast.error(messageOf(error));
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const exportCases = async () => {
    if (moduleId == null) return;
    try {
      await casesApi.exportCases({ projectId, moduleId, caseTypes: [caseType], format: "xlsx" });
      toast.success(`${caseLabel} 用例已导出`);
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const submitCase = async (values: CaseFormValues) => {
    if (moduleId == null || editor == null) return;
    const payload: TestCaseCreate = {
      module_id: editor === "new" ? moduleId : editor.module_id,
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
      repeat_count: values.repeat_count ?? 1,
      skip: values.skip,
      case_type: caseType,
      pre_hook: values.pre_hook as TestCaseCreate["pre_hook"],
      // API 单请求和多步骤都由 CaseDialog 归一化为 steps；后端不再兜底生成步骤。
    };
    // API 用例统一以 steps 为唯一执行来源：CaseDialog 已把「单请求合成的 1 步」或
    // 「多步骤编辑器的 N 步」放进 values.steps，这里整体下发，避免两种模式互相覆盖。
    const finalSteps = values.steps as TestStepDraft[] | null | undefined;
    if (finalSteps) {
      payload.steps = finalSteps;
    }
    setEditorSaving(true);
    try {
      if (editor === "new") {
        await automationCasesApi.create(payload, quickEdit ? editSession ?? undefined : undefined);
        toast.success("用例已创建");
      } else {
        await automationCasesApi.update(editor.id, payload, quickEdit ? editSession ?? undefined : undefined);
        toast.success("用例已更新");
      }
      setEditor(null);
      invalidate();
    } catch (error) {
      toast.error(messageOf(error));
    } finally {
      setEditorSaving(false);
    }
  };

  const originalDialogState = editor === null
    ? null
    : editor === "new"
      ? { mode: "create" as const, moduleId: moduleId ?? 0 }
      : {
          mode: "edit" as const,
          node: {
            ...editor,
            sort_order: editor.sort_order ?? undefined,
            type: "case" as const,
          } satisfies ContentNode,
        };

  return (
    <div className={cn("space-y-4 pb-24", !embedded && "p-6")}>
      <div className="flex items-center justify-between gap-4">
        <Breadcrumb
          typeLabel={`${caseLabel} 用例`}
          project={projectQuery.data?.name ?? "…"}
          trail={trail}
          onJump={(index) =>
            setTrail(index < 0 ? [] : trail.slice(0, index + 1))
          }
        />
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setRecordsOpen(true)} disabled={moduleId == null}>
            <History className="h-4 w-4" />{quickEdit ? "编辑记录" : "测试记录"}
          </Button>
          {isApiWorkbench ? (
            <div className="inline-flex overflow-hidden rounded-md border">
              <button type="button" className={cn("px-3 py-1.5 text-xs font-medium", !quickEdit ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")} onClick={exitQuickEdit}>运行模式</button>
              <button type="button" className={cn("border-l px-3 py-1.5 text-xs font-medium", quickEdit ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")} onClick={enterQuickEdit}>快速编辑</button>
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={moduleId == null} onClick={() => setEditor("new")}><Plus className="h-4 w-4" />新建 {caseLabel} 用例</Button>
        {isApiWorkbench ? (
          <Button variant="outline" size="sm" disabled={moduleId == null} className="border-primary/40 text-primary" onClick={() => setAiOpen(true)}><Sparkles className="h-4 w-4" />AI 生成用例</Button>
        ) : null}
        {quickEdit ? (
          <>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" className="hidden" onChange={(event) => event.target.files?.[0] && importFile(event.target.files[0])} />
            <Button variant="outline" size="sm" disabled={moduleId == null} onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" />导入</Button>
            {selected.size > 0 ? <Button variant="destructive" size="sm" onClick={batchDelete}><Trash2 className="h-4 w-4" />删除选中（{selected.size}）</Button> : null}
          </>
        ) : (
          <>
            <Button variant="outline" size="sm" disabled={moduleId == null || renumbering} onClick={() => renumberCases(true)} title="按执行顺序给用例名加 0001/0002… 前缀">
              {renumbering ? <Loader2 className="h-4 w-4 animate-spin" /> : <ListOrdered className="h-4 w-4" />}按顺序编号
            </Button>
            <Button variant="ghost" size="sm" disabled={moduleId == null || renumbering} onClick={() => renumberCases(false)} title="去掉用例名上的序号前缀">去掉编号</Button>
            <Button variant="outline" size="sm" disabled={moduleId == null} onClick={exportCases}><Download className="h-4 w-4" />导出</Button>
            {selected.size > 0 ? (
              <>
                <Button size="sm" disabled={runMutation.isPending} onClick={() => runMutation.mutate({ ids: [...selected] })}>
                  <Play className="h-4 w-4" />运行选中（{selected.size}）
                </Button>
                {/* AI 自愈运行：执行本身与左边完全一样，只是跑完后自动分诊 → 修复 → 重跑验证。
                    模型下拉留空＝只做零成本的规则自愈（变量悬空、断言状态码等确定性问题）。 */}
                <Select value={healModel} onValueChange={setHealModel}>
                  <SelectTrigger className="h-8 w-[132px]" title="自愈时用哪个模型做深度诊断">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__rules__">仅规则（免费）</SelectItem>
                    {(healModels ?? []).map((m) => (
                      <SelectItem key={m.name} value={m.name}>{m.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={runMutation.isPending}
                  title="执行与普通运行完全一致；跑完自动分诊、应用可确定的修复，并重跑验证（绿变红自动回滚）"
                  onClick={() => runMutation.mutate({
                    ids: [...selected],
                    heal: true,
                    model: healModel === "__rules__" ? null : healModel,
                  })}
                >
                  <Sparkles className="h-4 w-4" />AI 自愈运行
                </Button>
              </>
            ) : null}
          </>
        )}
        {moduleId != null && !quickEdit ? (
          <div className="ml-auto flex items-center gap-2">
            <Input className="h-8 w-48" placeholder="按名称或路径筛选" value={keyword} onChange={(event) => { setKeyword(event.target.value); setPage(1); }} />
            <Select value={status} onValueChange={(value) => { setStatus(value as ApiRunStatus | "all"); setPage(1); }}>
              <SelectTrigger className="h-8 w-28"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="pending">待执行</SelectItem>
                <SelectItem value="passed">通过</SelectItem>
                <SelectItem value="failed">失败</SelectItem>
                <SelectItem value="error">错误</SelectItem>
                <SelectItem value="skipped">跳过</SelectItem>
              </SelectContent>
            </Select>
            {isApiWorkbench ? (
              <Select value={flagFilter} onValueChange={(value) => { setFlagFilter(value as AiFlagType | "all"); setPage(1); }}>
                <SelectTrigger className="h-8 w-36"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部标记</SelectItem>
                  <SelectItem value="manual_fix">需人工</SelectItem>
                  <SelectItem value="interface_defect">疑似接口缺陷</SelectItem>
                  <SelectItem value="environment">环境问题</SelectItem>
                  <SelectItem value="ai_fixed">AI已修复</SelectItem>
                </SelectContent>
              </Select>
            ) : null}
          </div>
        ) : null}
      </div>

      {isApiWorkbench && moduleId == null ? (
        <PerformancePoolPanel
          items={performancePool}
          onManualSelect={() => setPerformancePickerOpen(true)}
          onMove={(fromIndex, toIndex) =>
            setPerformancePool((current) => {
              if (
                fromIndex < 0 ||
                fromIndex >= current.length ||
                toIndex < 0 ||
                toIndex >= current.length ||
                fromIndex === toIndex
              ) {
                return current;
              }
              const next = [...current];
              const [moving] = next.splice(fromIndex, 1);
              next.splice(toIndex, 0, moving);
              return next;
            })
          }
          onRemove={(caseId) =>
            setPerformancePool((current) =>
              current.filter((item) => item.caseId !== caseId),
            )
          }
          onClear={() => {
            setPerformancePool([]);
            toast.success("压测接口池已清空");
          }}
          onDesign={() =>
            navigate(
              `/projects/${projectId}/performance?case_ids=${performancePool
                .map((item) => item.caseId)
                .join(",")}`,
            )
          }
        />
      ) : null}

      {contentQuery.isLoading ? <Loading /> : (
        <div className="space-y-6">
          {moduleId == null || modules.length > 0 ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">{moduleId == null ? "模块" : "子模块"}（{modules.length}）</h3>
              {modules.length ? <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{modules.map((node) => <ModuleCard key={node.id} node={node} flagCount={isApiWorkbench ? flagCounts[String(node.id)] : undefined} onOpen={() => setTrail([...trail, { id: node.id, name: node.name }])} />)}</div> : <Empty text="当前层级没有模块" />}
            </section>
          ) : null}
          {moduleId != null ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">{caseLabel} 用例（{total}）</h3>
              {casesQuery.isLoading ? <Loading /> : cases.length === 0 ? <Empty text={`当前模块还没有 ${caseLabel} 用例`} /> : (
                <ApiCaseTable
                  cases={cases}
                  moduleId={moduleId}
                  quickEdit={quickEdit}
                  caseType={caseType}
                  enableAiActions={isApiWorkbench}
                  sessionId={editSession}
                  selected={selected}
                  onSelected={setSelected}
                  onEdit={setEditor}
                  onRun={(row) => runMutation.mutate({ ids: [row.id] })}
                  onDiagnose={handleDiagnose}
                  onShowRunDetail={setRunDetailCase}
                  onShowFlag={setFlagDialogCase}
                  onDelete={(row) => window.confirm(`确定删除“${row.name}”吗？`) && removeMutation.mutate(row.id)}
                  onReorder={reorder}
                  onInsertAbove={insertRowAbove}
                  onSaved={invalidate}
                  newRows={newRows}
                  onFirstInput={(rowId) => {
                    setNewRows((current) => {
                      const bottomRows = current.filter((item) => item.aboveCaseId == null);
                      return bottomRows.at(-1)?.tempId === rowId
                        ? [...current, { tempId: sessionId() }]
                        : current;
                    });
                  }}
                  onRemoveNewRow={(rowId) => setNewRows((current) => {
                    const next = current.filter((item) => item.tempId !== rowId);
                    return next.some((item) => item.aboveCaseId == null) ? next : [...next, { tempId: sessionId() }];
                  })}
                />
              )}
              <div className="flex items-center justify-end gap-2">
                <span className="text-xs text-muted-foreground">每页</span>
                <Select value={String(pageSize)} onValueChange={(value) => { setPageSize(Number(value)); setPage(1); }}>
                  <SelectTrigger className="h-8 w-24"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZE_OPTIONS.map((size) => <SelectItem key={size} value={String(size)}>{size}</SelectItem>)}
                    <SelectItem value="0">不分页</SelectItem>
                  </SelectContent>
                </Select>
                {pageSize !== 0 && totalPages > 1 ? (
                  <>
                    <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
                    <span className="text-xs text-muted-foreground">{page} / {totalPages}</span>
                    <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</Button>
                  </>
                ) : null}
              </div>
            </section>
          ) : null}
        </div>
      )}

      <CaseDialog projectId={projectId} state={originalDialogState} category={caseType} onClose={() => setEditor(null)} onSubmit={submitCase} submitting={editorSaving} />
      <RecordsDialog
        open={recordsOpen}
        quickEdit={quickEdit}
        moduleId={moduleId}
        caseType={caseType}
        firstModel={firstModel}
        models={enabledModels}
        onInvalidate={invalidate}
        onEditCase={(row) => {
          setRecordsOpen(false);
          setEditor(row);
        }}
        onClose={() => setRecordsOpen(false)}
      />
      {isApiWorkbench ? (
        <>
          <AiGenerateDialog open={aiOpen} moduleId={moduleId} projectId={projectId} initialMode="interface" onClose={() => setAiOpen(false)} onInserted={invalidate} />
          <PerformanceCasePickerDialog
            open={performancePickerOpen}
            projectId={projectId}
            poolItems={performancePool}
            onAdd={(items) =>
              setPerformancePool((current) =>
                mergePerformancePool(current, items),
              )
            }
            onClose={() => setPerformancePickerOpen(false)}
          />
        </>
      ) : null}
      <DiagnoseDialog state={diagnose} onClose={() => setDiagnose(null)} onFixed={() => { setDiagnose(null); invalidate(); }} />
      <RunDetailDialog row={runDetailCase} onClose={() => setRunDetailCase(null)} />
      <AiFlagClearDialog row={flagDialogCase} onClose={() => setFlagDialogCase(null)} onCleared={() => { setFlagDialogCase(null); invalidate(); }} />
    </div>
  );
}

function PerformancePoolPanel({
  items,
  onManualSelect,
  onMove,
  onRemove,
  onClear,
  onDesign,
}: {
  items: PerformancePoolItem[];
  onManualSelect: () => void;
  onMove: (fromIndex: number, toIndex: number) => void;
  onRemove: (caseId: number) => void;
  onClear: () => void;
  onDesign: () => void;
}) {
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);

  return (
    <section className="overflow-hidden rounded-xl border border-sky-200 bg-gradient-to-br from-sky-50/80 via-background to-indigo-50/50 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-sky-100 px-5 py-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="rounded-lg bg-sky-100 p-2 text-sky-700">
            <Gauge className="h-5 w-5" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-semibold">压测接口池</h3>
              <Badge className="border-sky-200 bg-white/80 text-sky-700">
                {items.length} 个接口
              </Badge>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              按模块选择接口，并拖动调整执行顺序；确认后再设计压测目标与并发模型。
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onManualSelect}
            className="bg-white"
          >
            <ListPlus className="h-4 w-4" />
            手动选择接口
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={items.length === 0}
            onClick={onClear}
            className="text-muted-foreground hover:text-destructive"
          >
            <X className="h-4 w-4" />
            一键清除池子
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={items.length === 0}
            onClick={onDesign}
          >
            <Gauge className="h-4 w-4" />
            设计压测（{items.length}）
          </Button>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="px-5 py-8 text-center">
          <p className="text-sm font-medium text-muted-foreground">
            接口池还是空的
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            点击“手动选择接口”，按模块勾选需要参与压测的 API 用例。
          </p>
        </div>
      ) : (
        <div className="max-h-80 overflow-auto p-4">
          <div className="min-w-[760px] overflow-hidden rounded-lg border bg-background/90">
            <div className="grid grid-cols-[36px_28px_90px_minmax(220px,1fr)_minmax(150px,0.6fr)_116px] items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              <span>顺序</span>
              <span />
              <span>方法</span>
              <span>用例 / 路径</span>
              <span>模块</span>
              <span className="text-right">调整</span>
            </div>
          {items.map((item, index) => (
            <div
              key={item.caseId}
              draggable
              onDragStart={(event) => {
                setDraggedIndex(index);
                event.dataTransfer.effectAllowed = "move";
              }}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
              }}
              onDrop={(event) => {
                event.preventDefault();
                if (draggedIndex != null) onMove(draggedIndex, index);
                setDraggedIndex(null);
              }}
              onDragEnd={() => setDraggedIndex(null)}
              className={cn(
                "grid grid-cols-[36px_28px_90px_minmax(220px,1fr)_minmax(150px,0.6fr)_116px] items-center gap-2 border-b px-3 py-2.5 text-sm last:border-b-0",
                "transition-colors hover:bg-muted/20",
                draggedIndex === index && "opacity-50",
              )}
            >
              <span className="font-mono text-xs text-muted-foreground">{index + 1}</span>
              <span className="cursor-grab text-muted-foreground active:cursor-grabbing" title="拖动排序">
                <GripVertical className="h-4 w-4" />
              </span>
              <Badge className="w-fit font-mono">{item.method}</Badge>
              <div className="min-w-0">
                <div className="truncate font-medium" title={item.name}>
                  {item.name}
                </div>
                <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground" title={item.path}>
                  {item.path}
                </div>
              </div>
              <span className="truncate text-xs text-muted-foreground" title={item.moduleName}>
                {item.moduleName}
              </span>
              <div className="flex justify-end gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  disabled={index === 0}
                  title="上移"
                  onClick={() => onMove(index, index - 1)}
                >
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8"
                  disabled={index === items.length - 1}
                  title="下移"
                  onClick={() => onMove(index, index + 1)}
                >
                  <ArrowDown className="h-4 w-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`从压测接口池移除 ${item.name}`}
                  title="从接口池移除"
                  className="h-8 w-8 text-muted-foreground hover:text-destructive"
                  onClick={() => onRemove(item.caseId)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ))}
          </div>
        </div>
      )}
    </section>
  );
}

function modulePath(
  module: ModulePickerNode,
  modulesById: Map<number, ModulePickerNode>,
): string {
  const names = [module.name];
  const visited = new Set<number>([module.id]);
  let parentId = module.parent_id;
  while (parentId != null && !visited.has(parentId)) {
    visited.add(parentId);
    const parent = modulesById.get(parentId);
    if (!parent) break;
    names.unshift(parent.name);
    parentId = parent.parent_id;
  }
  return names.join(" / ");
}

function PerformanceCasePickerDialog({
  open,
  projectId,
  poolItems,
  onAdd,
  onClose,
}: {
  open: boolean;
  projectId: number;
  poolItems: PerformancePoolItem[];
  onAdd: (items: PerformancePoolItem[]) => void;
  onClose: () => void;
}) {
  const [selectedModuleId, setSelectedModuleId] = useState<number | null>(null);
  const [checkedCaseIds, setCheckedCaseIds] = useState<Set<number>>(new Set());

  const modulesQuery = useQuery({
    queryKey: ["performance-module-picker", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: open && Number.isFinite(projectId),
  });
  const modules = useMemo(() => modulesQuery.data ?? [], [modulesQuery.data]);
  const modulesById = useMemo(
    () => new Map(modules.map((module) => [module.id, module])),
    [modules],
  );
  const moduleOptions = useMemo(
    () =>
      modules.map((module) => ({
        ...module,
        displayName: modulePath(module, modulesById),
      })),
    [modules, modulesById],
  );

  useEffect(() => {
    if (!open || selectedModuleId != null || moduleOptions.length === 0) return;
    setSelectedModuleId(moduleOptions[0].id);
  }, [moduleOptions, open, selectedModuleId]);

  const casesQuery = useQuery({
    queryKey: ["performance-module-cases", selectedModuleId],
    queryFn: () =>
      automationCasesApi.list({
        moduleId: selectedModuleId!,
        caseType: "api",
        pageSize: 0,
      }),
    enabled: open && selectedModuleId != null,
  });
  const moduleCases = casesQuery.data?.items ?? [];
  const poolCaseIds = useMemo(
    () => new Set(poolItems.map((item) => item.caseId)),
    [poolItems],
  );
  const selectableCases = moduleCases.filter((item) => !poolCaseIds.has(item.id));
  const allSelectableChecked =
    selectableCases.length > 0 &&
    selectableCases.every((item) => checkedCaseIds.has(item.id));
  const selectedModule = selectedModuleId == null
    ? null
    : moduleOptions.find((module) => module.id === selectedModuleId) ?? null;

  const changeModule = (value: string) => {
    setSelectedModuleId(Number(value));
    setCheckedCaseIds(new Set());
  };

  const addCheckedCases = () => {
    if (!selectedModule) return;
    const items = moduleCases
      .filter((item) => checkedCaseIds.has(item.id))
      .map((item) => ({
        caseId: item.id,
        moduleId: item.module_id,
        moduleName: selectedModule.displayName,
        name: item.name,
        method: (item.method || "GET").toUpperCase(),
        path: item.path || "--",
      } satisfies PerformancePoolItem));
    onAdd(items);
    setCheckedCaseIds(new Set());
    toast.success(`已加入 ${items.length} 个接口`);
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>手动选择压测接口</DialogTitle>
          <DialogDescription>
            先选择模块，再勾选该模块中的 API 用例加入压测接口池。可连续切换模块添加。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-2">
            <span className="text-sm font-medium">选择模块</span>
            <Select
              value={selectedModuleId == null ? "" : String(selectedModuleId)}
              onValueChange={changeModule}
            >
              <SelectTrigger>
                <SelectValue placeholder={modulesQuery.isLoading ? "加载模块…" : "请选择模块"} />
              </SelectTrigger>
              <SelectContent>
                {moduleOptions.map((module) => (
                  <SelectItem key={module.id} value={String(module.id)}>
                    {module.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="overflow-hidden rounded-lg border">
            <div className="grid grid-cols-[36px_90px_minmax(180px,1fr)_minmax(180px,1.2fr)_90px] items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                aria-label="选择当前模块全部接口"
                className="h-4 w-4 rounded border-input accent-primary"
                checked={allSelectableChecked}
                disabled={selectableCases.length === 0}
                onChange={() =>
                  setCheckedCaseIds(
                    allSelectableChecked
                      ? new Set()
                      : new Set(selectableCases.map((item) => item.id)),
                  )
                }
              />
              <span>方法</span>
              <span>用例名称</span>
              <span>请求路径</span>
              <span>状态</span>
            </div>
            <div className="max-h-80 overflow-y-auto">
              {selectedModuleId == null ? (
                <div className="py-10 text-center text-sm text-muted-foreground">
                  请先选择模块
                </div>
              ) : casesQuery.isLoading ? (
                <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  加载接口…
                </div>
              ) : moduleCases.length === 0 ? (
                <div className="py-10 text-center text-sm text-muted-foreground">
                  当前模块没有 API 用例
                </div>
              ) : (
                moduleCases.map((item) => {
                  const alreadyInPool = poolCaseIds.has(item.id);
                  const checked = alreadyInPool || checkedCaseIds.has(item.id);
                  return (
                    <label
                      key={item.id}
                      className={cn(
                        "grid grid-cols-[36px_90px_minmax(180px,1fr)_minmax(180px,1.2fr)_90px] items-center gap-2 border-b px-3 py-2.5 text-sm last:border-b-0",
                        alreadyInPool
                          ? "cursor-default bg-muted/30 text-muted-foreground"
                          : "cursor-pointer hover:bg-muted/20",
                      )}
                    >
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border-input accent-primary"
                        checked={checked}
                        disabled={alreadyInPool}
                        onChange={() => {
                          const next = new Set(checkedCaseIds);
                          if (next.has(item.id)) next.delete(item.id);
                          else next.add(item.id);
                          setCheckedCaseIds(next);
                        }}
                      />
                      <Badge className="w-fit font-mono">
                        {(item.method || "GET").toUpperCase()}
                      </Badge>
                      <span className="truncate" title={item.name}>{item.name}</span>
                      <span className="truncate font-mono text-xs" title={item.path || ""}>
                        {item.path || "--"}
                      </span>
                      <span className={alreadyInPool ? "text-sky-600" : "text-muted-foreground"}>
                        {alreadyInPool ? "已在池中" : "可选择"}
                      </span>
                    </label>
                  );
                })
              )}
            </div>
          </div>
        </div>

        <DialogFooter className="items-center sm:justify-between">
          <span className="text-xs text-muted-foreground">
            池中已有 {poolItems.length} 个接口，本次勾选 {checkedCaseIds.size} 个
          </span>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={onClose}>完成</Button>
            <Button
              disabled={checkedCaseIds.size === 0}
              onClick={addCheckedCases}
            >
              <Check className="h-4 w-4" />
              加入池子（{checkedCaseIds.size}）
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DiagnoseDialog({
  state,
  onClose,
  onFixed,
}: {
  state: { row: ApiCase; loading: boolean; result: DiagnoseResult | null } | null;
  onClose: () => void;
  onFixed: () => void;
}) {
  const [fixing, setFixing] = useState(false);
  if (!state) return null;
  const { row, loading, result } = state;
  const cls = result?.classification ?? "";
  const clsColor =
    cls === "用例问题"
      ? "bg-amber-100 text-amber-700"
      : cls === "接口问题"
        ? "bg-red-100 text-red-700"
        : "bg-slate-100 text-slate-600";
  const canFix =
    cls === "用例问题" &&
    result != null &&
    (Object.keys(result.fix.extract || {}).length > 0 ||
      Object.keys(result.fix.assertion || {}).length > 0 ||
      Object.keys(result.fix.params || {}).length > 0);

  const applyFix = async () => {
    if (!result) return;
    setFixing(true);
    try {
      const detail = await casesApi.get(row.id);
      let steps = detail.steps ?? [];

      const fixExtract = result.fix.extract || {};
      for (const [name, jp] of Object.entries(fixExtract)) {
        if (name && jp) steps = appendExtractRule(steps, null, name, String(jp)).steps;
      }
      const fixAssertion = result.fix.assertion || {};
      for (const [target, expected] of Object.entries(fixAssertion)) {
        if (target) steps = appendAssertionRule(steps, null, target, expected).steps;
      }
      const fixParams = result.fix.params || {};
      if (Object.keys(fixParams).length > 0) {
        const idx = steps.findIndex((s) => s.step_type === "http_request");
        if (idx >= 0) {
          steps = steps.map((s, i) =>
            i === idx
              ? { ...s, config: { ...((s.config as Record<string, unknown>) ?? {}), params: fixParams } }
              : s,
          );
        }
      }

      const body: TestCaseCreate = {
        module_id: detail.module_id,
        name: detail.name,
        case_type: "api",
        steps,
      };
      await automationCasesApi.update(row.id, body);
      toast.success("已按修正更新执行步骤，可重新运行验证");
      onFixed();
    } catch (e) {
      toast.error(messageOf(e));
    } finally {
      setFixing(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>AI 分析执行结果</DialogTitle>
          <DialogDescription className="truncate">{row.name}</DialogDescription>
        </DialogHeader>
        {loading ? (
          <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 正在分析最近一次执行结果…
          </div>
        ) : result ? (
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">结论：</span>
              <span className={cn("rounded px-2 py-0.5 text-xs", clsColor)}>{cls || "未判定"}</span>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">原因</div>
              <div className="whitespace-pre-wrap">{result.reason || "—"}</div>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">建议</div>
              <div className="whitespace-pre-wrap">{result.suggestion || "—"}</div>
            </div>
            {canFix ? (
              <div className="rounded-md border bg-amber-50 p-2 text-xs">
                <div className="mb-1 font-medium text-amber-700">修正方案（用例问题，可一键修复）</div>
                {Object.keys(result.fix.extract || {}).length > 0 ? (
                  <div>提取：{JSON.stringify(result.fix.extract)}</div>
                ) : null}
                {Object.keys(result.fix.assertion || {}).length > 0 ? (
                  <div>断言：{JSON.stringify(result.fix.assertion)}</div>
                ) : null}
                {Object.keys(result.fix.params || {}).length > 0 ? (
                  <div>请求参数：{JSON.stringify(result.fix.params)}</div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
          {canFix ? (
            <Button onClick={applyFix} disabled={fixing}>
              {fixing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
              一键修复并保存
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RunDetailDialog({ row, onClose }: { row: ApiCase | null; onClose: () => void }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const query = useQuery({
    queryKey: ["api-case-latest-run-detail", row?.id],
    queryFn: () => automationCasesApi.latestRunDetail(row!.id),
    enabled: row != null,
  });

  useEffect(() => {
    setExpanded(new Set());
  }, [row?.id]);

  const detail = query.data;
  useEffect(() => {
    if (!detail?.steps?.length) return;
    setExpanded(new Set(detail.steps.map((step) => step.step_report_id)));
  }, [detail?.report_id, detail?.steps]);
  return (
    <Dialog open={row != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[88vh] max-w-5xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>最近一次执行详情</DialogTitle>
          <DialogDescription className="truncate">{row?.name ?? ""}</DialogDescription>
        </DialogHeader>
        {query.isLoading ? (
          <Loading />
        ) : query.isError ? (
          <div className="rounded border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            加载失败：{query.error instanceof Error ? query.error.message : "未知错误"}
          </div>
        ) : detail ? (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-center gap-3 rounded border bg-muted/30 p-3 text-xs">
              <span>报告 #{detail.report_id}</span>
              <StatusBadge status={detail.status} />
              <span>执行时间：{formatTime(detail.executed_at)}</span>
              <span>耗时：{detail.duration.toFixed(2)}s</span>
            </div>
            <JsonBlock title="变量池" value={detail.variable_pool} />
            <div className="space-y-2">
              {detail.steps.map((step, index) => {
                const key = step.step_report_id;
                const open = expanded.has(key);
                return (
                  <div key={key} className="rounded border">
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 px-3 py-2 text-left"
                      onClick={() => {
                        const next = new Set(expanded);
                        if (next.has(key)) next.delete(key);
                        else next.add(key);
                        setExpanded(next);
                      }}
                    >
                      <ChevronRight className={cn("h-4 w-4 text-muted-foreground transition-transform", open && "rotate-90")} />
                      <StatusBadge status={normalizeRunStatus(step.status)} />
                      <span className="min-w-0 flex-1 truncate">{index + 1}. {step.step_name || step.step_type || `步骤 ${key}`}</span>
                      <span className="font-mono text-xs text-muted-foreground">{step.request.method || step.step_type || "--"}</span>
                      <span className="text-xs text-muted-foreground">{step.status_code ?? "--"}</span>
                    </button>
                    {open ? (
                      <div className="space-y-2 border-t bg-muted/10 p-3">
                        <div className="grid gap-2 md:grid-cols-2">
                          <JsonBlock title="请求地址" value={step.request.url} />
                          <JsonBlock title="请求头" value={step.request.headers} />
                        </div>
                        <RequestBodySection
                          written={step.request.body_template ?? step.request.params}
                          sent={step.request.body ?? step.request.params}
                        />
                        <DetailSection title="响应参数" summary={summarizeValue(step.response)} defaultOpen={false}>
                          <JsonPre value={step.response} />
                        </DetailSection>
                        <AssertionSection assertion={step.assertion} />
                        <ExtractSection extract={step.extract} />
                        {step.error_message ? (
                          <DetailSection title="错误信息" summary={String(step.error_message).slice(0, 80)} defaultOpen>
                            <JsonPre value={step.error_message} />
                          </DetailSection>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="min-w-0 rounded border bg-background p-2">
      <div className="mb-1 text-xs font-medium text-muted-foreground">{title}</div>
      <JsonPre value={value} />
    </div>
  );
}

function JsonPre({ value }: { value: unknown }) {
  return (
    <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono text-xs">
      {formatJsonValue(value)}
    </pre>
  );
}

function DetailSection({
  title,
  summary,
  defaultOpen = false,
  children,
}: {
  title: string;
  summary?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details open={defaultOpen} className="group rounded border bg-background">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
        <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-90" />
        <span className="text-xs font-medium text-muted-foreground">{title}</span>
        {summary ? <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{summary}</span> : null}
      </summary>
      <div className="border-t p-3">{children}</div>
    </details>
  );
}

function RequestBodySection({ written, sent }: { written: unknown; sent: unknown }) {
  return (
    <DetailSection title="请求参数" summary={summarizeValue(sent)} defaultOpen>
      <div className="grid gap-2 md:grid-cols-2">
        <JsonBlock title="实际填写的" value={written} />
        <JsonBlock title="请求填写的" value={sent} />
      </div>
    </DetailSection>
  );
}

function AssertionSection({ assertion }: { assertion: { configured: unknown; results: unknown } }) {
  const configured = Array.isArray(assertion.configured) ? assertion.configured : [];
  const results = Array.isArray(assertion.results) ? assertion.results : [];
  const failed = results.filter((item) => isRecord(item) && item.status === "failed").length;
  const passed = results.filter((item) => isRecord(item) && item.status === "passed").length;
  const summary = results.length
    ? `通过 ${passed} · 失败 ${failed}`
    : configured.length
      ? `已配置 ${configured.length} 条，暂无执行明细`
      : "未配置";

  return (
    <DetailSection title="断言" summary={summary} defaultOpen={failed > 0}>
      <div className="space-y-3">
        <JsonBlock title={`配置断言（${configured.length}）`} value={configured} />
        {results.length ? (
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground">执行明细（{results.length}）</div>
          {results.map((item, index) => {
            const row = isRecord(item) ? item : {};
            const status = String(row.status ?? "");
            return (
              <div key={index} className="rounded border bg-muted/10 p-2">
                <div className="mb-1 flex items-center gap-2 text-xs">
                  <StatusBadge status={normalizeRunStatus(status)} />
                  <span className="font-mono">{String(row.type ?? "--")}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">{String(row.target ?? "--")}</span>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  <JsonBlock title="预期" value={row.expected} />
                  <JsonBlock title="实际" value={row.actual} />
                </div>
                {row.error ? <div className="mt-2 text-xs text-destructive">{String(row.error)}</div> : null}
              </div>
            );
          })}
          </div>
        ) : null}
      </div>
    </DetailSection>
  );
}

function ExtractSection({ extract }: { extract: { configured: unknown; values: unknown } }) {
  const configured = Array.isArray(extract.configured) ? extract.configured : [];
  const values = isRecord(extract.values) ? extract.values : {};
  return (
    <DetailSection title="提取参数" summary={summarizeExtract(extract)} defaultOpen={configured.length > 0 || Object.keys(values).length > 0}>
      <div className="grid gap-2 md:grid-cols-2">
        <JsonBlock title={`配置提取规则（${configured.length}）`} value={configured} />
        <JsonBlock title={`提取结果（${Object.keys(values).length}）`} value={values} />
      </div>
    </DetailSection>
  );
}

function formatJsonValue(value: unknown): string {
  if (value == null || value === "") return "--";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function summarizeValue(value: unknown): string {
  if (value == null || value === "") return "--";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (isRecord(value)) return `${Object.keys(value).length} 个字段`;
  const text = typeof value === "string" ? value : formatJsonValue(value);
  return text.replace(/\s+/g, " ").slice(0, 80);
}

function summarizeExtract(value: { configured: unknown; values: unknown }): string {
  const values = isRecord(value.values) ? Object.keys(value.values).length : 0;
  const configured = Array.isArray(value.configured) ? value.configured.length : 0;
  return `已提取 ${values} · 已配置 ${configured}`;
}

function normalizeRunStatus(status: string | null | undefined): ApiRunStatus {
  const value = (status ?? "").toLowerCase();
  if (value === "passed" || value === "pass" || value === "success") return "passed";
  if (value === "failed" || value === "fail") return "failed";
  if (value === "error" || value === "broken") return "error";
  if (value === "skipped") return "skipped";
  return "pending";
}

function Breadcrumb({
  typeLabel,
  project,
  trail,
  onJump,
}: {
  typeLabel: string;
  project: string;
  trail: Array<{ id: number; name: string }>;
  onJump: (index: number) => void;
}) {
  return (
    <nav className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
      <span className="text-muted-foreground">{typeLabel} ·</span>
      <button
        className="max-w-[12rem] truncate rounded px-1.5 py-0.5 font-medium hover:bg-accent"
        onClick={() => onJump(-1)}
      >
        {project}
      </button>
      {trail.map((item, index) => (
        <span key={item.id} className="flex min-w-0 items-center gap-1">
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <button
            className="max-w-48 truncate rounded px-1.5 py-0.5 hover:bg-accent"
            onClick={() => onJump(index)}
          >
            {item.name}
          </button>
        </span>
      ))}
    </nav>
  );
}

function ModuleCard({ node, flagCount, onOpen }: {
  node: ContentNode;
  flagCount?: { total: number } & Partial<Record<AiFlagType, number>>;
  onOpen: () => void;
}) {
  const problemCount = (flagCount?.manual_fix ?? 0) + (flagCount?.interface_defect ?? 0) + (flagCount?.environment ?? 0);
  const fixedCount = flagCount?.ai_fixed ?? 0;
  const flagTitle = flagCount
    ? [
        flagCount.manual_fix ? `需人工 ${flagCount.manual_fix}` : "",
        flagCount.interface_defect ? `疑似接口缺陷 ${flagCount.interface_defect}` : "",
        flagCount.environment ? `环境 ${flagCount.environment}` : "",
        flagCount.ai_fixed ? `AI已修复 ${flagCount.ai_fixed}` : "",
      ].filter(Boolean).join(" · ")
    : "";
  return (
    <button type="button" onClick={onOpen} className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3 text-left hover:border-primary/40 hover:bg-accent/30">
      <Folder className="h-5 w-5 shrink-0 text-amber-500" />
      <span className="truncate text-sm font-medium">{node.name}</span>
      {problemCount > 0 ? (
        <span className="shrink-0 rounded-full bg-red-100 px-1.5 py-0.5 text-[11px] font-medium leading-none text-red-700" title={`AI 标记（含子模块）：${flagTitle}`}>
          {problemCount}
        </span>
      ) : null}
      {problemCount === 0 && fixedCount > 0 ? (
        <span className="shrink-0 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[11px] font-medium leading-none text-emerald-700" title={`AI 标记（含子模块）：${flagTitle}`}>
          {fixedCount}
        </span>
      ) : null}
      <ChevronRight className="ml-auto h-4 w-4 shrink-0 text-muted-foreground" />
    </button>
  );
}

/** 用例行上的 AI 诊断标记徽标；点击打开详情/清除对话框。 */
function AiFlagBadge({ flag, onClick }: { flag?: AiCaseFlag | null; onClick: () => void }) {
  if (!flag) return null;
  const meta = FLAG_META[flag.flag_type];
  if (!meta) return null;
  const Icon = meta.Icon;
  return (
    <button
      type="button"
      onClick={onClick}
      title={`${meta.hint}${flag.findings.length ? `\n${flag.findings.slice(0, 2).join("\n")}` : ""}\n点击查看详情 / 清除标记`}
      className={cn("flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium leading-none", meta.className)}
    >
      <Icon className="h-3 w-3" />
      {meta.label}
    </button>
  );
}

/** 标记详情 + 清除（清除原因会作为反馈回流给下次 AI 诊断）。 */
function AiFlagClearDialog({ row, onClose, onCleared }: {
  row: ApiCase | null;
  onClose: () => void;
  onCleared: () => void;
}) {
  const [reason, setReason] = useState<AiFlagClearReason>("manually_fixed");
  const [corrected, setCorrected] = useState<string>("正常");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    if (row) { setReason("manually_fixed"); setCorrected("正常"); setNote(""); }
  }, [row?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const flag = row?.ai_flag;
  if (!row || !flag) return null;
  const meta = FLAG_META[flag.flag_type];
  const Icon = meta?.Icon ?? Sparkles;

  const submit = async () => {
    setSubmitting(true);
    try {
      await automationCasesApi.clearAiFlag(row.id, {
        reason,
        corrected_classification: reason === "misjudged" ? corrected : undefined,
        note: note.trim() || undefined,
      });
      toast.success(
        reason === "misjudged"
          ? "已清除标记并记录更正——下次 AI 诊断该用例时会遵循你的分类"
          : reason === "wont_fix"
            ? "已清除标记——AI 以后不会再自动修复该用例"
            : "已清除标记，反馈已记录",
      );
      onCleared();
    } catch (e) {
      toast.error(messageOf(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="w-[calc(100vw-2rem)] max-w-lg overflow-hidden">
        <DialogHeader className="min-w-0 pr-8">
          <DialogTitle className="flex min-w-0 items-center gap-2">
            <span className={cn("flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs", meta?.className)}>
              <Icon className="h-3.5 w-3.5" />{meta?.label}
            </span>
            <span className="min-w-0 truncate">{row.name}</span>
          </DialogTitle>
          <DialogDescription>
            清除原因会作为反馈记录，并在下次 AI 诊断该用例时生效（更正分类 / 不再自动修复 / 经验参考）。
          </DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-3 text-sm">
          {flag.findings.length ? (
            <div className="min-w-0 space-y-1 overflow-hidden rounded border bg-muted/30 p-2.5">
              <div className="text-xs font-medium text-muted-foreground">AI 诊断发现{flag.fix_rounds ? `（尝试修复 ${flag.fix_rounds} 轮）` : ""}</div>
              <ul className="list-disc space-y-0.5 pl-4">
                {flag.findings.slice(0, 5).map((f, i) => <li key={i} className="break-words">{f}</li>)}
              </ul>
            </div>
          ) : null}
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground">清除原因（必选，将回流给 AI）</div>
            {CLEAR_REASON_OPTIONS.map((opt) => (
              <label key={opt.value} className={cn("flex min-w-0 cursor-pointer items-start gap-2 rounded border p-2", reason === opt.value && "border-primary bg-primary/5")}>
                <input type="radio" className="mt-0.5" checked={reason === opt.value} onChange={() => setReason(opt.value)} />
                <span className="min-w-0">
                  <span className="font-medium">{opt.label}</span>
                  <span className="block text-xs text-muted-foreground">{opt.desc}</span>
                </span>
              </label>
            ))}
          </div>
          {reason === "misjudged" ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">正确分类：</span>
              <Select value={corrected} onValueChange={setCorrected}>
                <SelectTrigger className="h-8 w-36"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="正常">正常</SelectItem>
                  <SelectItem value="用例问题">用例问题</SelectItem>
                  <SelectItem value="接口问题">接口问题</SelectItem>
                  <SelectItem value="环境/其他">环境/其他</SelectItem>
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={reason === "manually_fixed" ? "改了什么？（会作为经验喂给 AI，建议填写）" : "备注（可选）"}
            className="min-h-16 text-sm"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            清除标记并提交反馈
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Loading() { return <div className="flex items-center justify-center rounded-lg border py-10 text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />加载中…</div>; }
function Empty({ text }: { text: string }) { return <div className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">{text}</div>; }

function ApiCaseTable({ cases, moduleId, quickEdit, caseType, enableAiActions, sessionId, selected, onSelected, onEdit, onRun, onDelete, onDiagnose, onShowRunDetail, onShowFlag, onReorder, onInsertAbove, onSaved, newRows, onFirstInput, onRemoveNewRow }: {
  cases: ApiCase[];
  moduleId: number;
  quickEdit: boolean;
  caseType: AutomationTabCaseType;
  enableAiActions: boolean;
  sessionId: string | null;
  selected: Set<number>;
  onSelected: (next: Set<number>) => void;
  onEdit: (row: ApiCase) => void;
  onRun: (row: ApiCase) => void;
  onDelete: (row: ApiCase) => void;
  onDiagnose: (row: ApiCase) => void;
  onShowRunDetail: (row: ApiCase) => void;
  onShowFlag: (row: ApiCase) => void;
  onReorder: (row: ApiCase, direction: "up" | "down") => void;
  onInsertAbove: (caseId: number, sortOrder?: number | null) => void;
  onSaved: () => void;
  newRows: ApiQuickNewRow[];
  onFirstInput: (rowId: string) => void;
  onRemoveNewRow: (rowId: string) => void;
}) {
  const allSelected = cases.length > 0 && cases.every((row) => selected.has(row.id));
  const caseLabel = CASE_LABELS[caseType];
  const gridClass = quickEdit
    ? "grid-cols-[36px_minmax(180px,1.1fr)_90px_minmax(180px,1.2fr)_minmax(220px,1.4fr)_minmax(220px,1.4fr)_90px_120px]"
    : "grid-cols-[36px_1.1fr_100px_1.5fr_120px_150px]";
  return <div className="overflow-x-auto rounded-lg border bg-card">
    <div className={cn("grid min-w-max items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs text-muted-foreground", gridClass)}>
      <Checkbox checked={allSelected} onCheckedChange={() => onSelected(allSelected ? new Set() : new Set(cases.map((row) => row.id)))} />
      <span>用例名称</span><span>{caseType === "api" ? "方法" : "类型"}</span><span>{caseType === "api" ? "路径" : "步骤"}</span>
      {quickEdit ? <><span>请求头</span><span>请求参数</span><span>排序</span></> : <span>最近结果</span>}
      <span className="text-right">操作</span>
    </div>
    {cases.map((row, index) => quickEdit ? (
      <Fragment key={row.id}>
        {newRows.filter((item) => item.aboveCaseId === row.id).map((item, itemIndex) => (
          <QuickCreateRow
            key={item.tempId}
            moduleId={moduleId}
            sessionId={sessionId}
            isTrailing={false}
            autoFocusName={itemIndex === 0}
            insertSortOrder={item.insertSortOrder}
            onFirstInput={() => undefined}
            onCreated={() => onRemoveNewRow(item.tempId)}
            onRemove={() => onRemoveNewRow(item.tempId)}
            onSaved={onSaved}
          />
        ))}
        <QuickEditRow key={`case-${row.id}`} row={row} sessionId={sessionId} checked={selected.has(row.id)} onChecked={() => { const next = new Set(selected); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); onSelected(next); }} onEdit={() => onEdit(row)} onDelete={() => onDelete(row)} onInsertAbove={() => onInsertAbove(row.id, row.sort_order)} onUp={() => index > 0 && onReorder(row, "up")} onDown={() => index < cases.length - 1 && onReorder(row, "down")} onSaved={onSaved} />
      </Fragment>
    ) : (
      <div key={row.id} className={cn("grid items-center gap-2 border-b px-3 py-2.5 text-sm last:border-b-0 hover:bg-muted/30", gridClass)}>
        <Checkbox checked={selected.has(row.id)} onCheckedChange={() => { const next = new Set(selected); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); onSelected(next); }} />
        <div className="flex min-w-0 items-center gap-1.5">
          <button className="flex min-w-0 items-center gap-2 text-left hover:underline" onClick={() => onEdit(row)} title={(row.step_count ?? 0) > 1 ? "多步骤用例 · 点击编辑" : "点击编辑"}>{(row.step_count ?? 0) > 1 ? <ListChecks className="h-4 w-4 shrink-0 text-violet-500" /> : <FileText className="h-4 w-4 shrink-0 text-sky-500" />}<span className="truncate">{row.name}</span></button>
          {enableAiActions ? <AiFlagBadge flag={row.ai_flag} onClick={() => onShowFlag(row)} /> : null}
        </div>
        <Badge variant="outline" className="w-fit font-mono">{caseType === "api" ? row.method ?? "GET" : caseLabel}</Badge>
        <span className="truncate font-mono text-xs" title={caseType === "api" ? row.path ?? "" : `${row.step_count ?? 0} 个步骤`}>
          {caseType === "api" ? row.path || "--" : `${row.step_count ?? 0} 个步骤`}
        </span>
        <StatusBadge
          status={row.latest_run?.status ?? "pending"}
          clickable={row.latest_run != null}
          onClick={() => row.latest_run && onShowRunDetail(row)}
        />
        <div className="flex justify-end gap-1"><Button variant="ghost" size="icon" className="h-8 w-8" title="运行" onClick={() => onRun(row)}><Play className="h-4 w-4" /></Button>{enableAiActions ? <Button variant="ghost" size="icon" className="h-8 w-8 text-primary" title="AI 分析执行结果" onClick={() => onDiagnose(row)}><Sparkles className="h-4 w-4" /></Button> : null}<Button variant="ghost" size="icon" className="h-8 w-8" title="编辑" onClick={() => onEdit(row)}><Pencil className="h-4 w-4" /></Button><Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" title="删除" onClick={() => onDelete(row)}><Trash2 className="h-4 w-4" /></Button></div>
      </div>
    ))}
    {quickEdit ? newRows.filter((item) => item.aboveCaseId == null).map((item, index, bottomRows) => (
      <QuickCreateRow
        key={item.tempId}
        moduleId={moduleId}
        sessionId={sessionId}
        isTrailing={index === bottomRows.length - 1}
        onFirstInput={() => onFirstInput(item.tempId)}
        onCreated={() => onRemoveNewRow(item.tempId)}
        onRemove={() => onRemoveNewRow(item.tempId)}
        onSaved={onSaved}
      />
    )) : null}
  </div>;
}

function StatusBadge({ status, clickable = false, onClick }: { status: ApiRunStatus; clickable?: boolean; onClick?: () => void }) {
  const meta = STATUS_META[status];
  const className = cn("w-fit rounded px-2 py-0.5 text-xs", meta.className, clickable && "cursor-pointer hover:ring-1 hover:ring-primary/40");
  if (!clickable) return <span className={className}>{meta.label}</span>;
  return (
    <button type="button" className={className} onClick={onClick} title="查看最近一次请求/响应详情">
      {meta.label}
    </button>
  );
}

function QuickEditRow({ row, sessionId, checked, onChecked, onEdit, onDelete, onInsertAbove, onUp, onDown, onSaved }: {
  row: ApiCase; sessionId: string | null; checked: boolean; onChecked: () => void; onEdit: () => void; onDelete: () => void; onInsertAbove: () => void; onUp: () => void; onDown: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(row.name);
  const [method, setMethod] = useState(row.method ?? "GET");
  const [path, setPath] = useState(row.path ?? "");
  const [headers, setHeaders] = useState(row.headers ?? "");
  const [params, setParams] = useState(row.params ?? "");
  const [saving, setSaving] = useState(false);
  const savedDraftRef = useRef(JSON.stringify({ name: row.name, method: row.method ?? "GET", path: row.path ?? "", headers: row.headers ?? "", params: row.params ?? "" }));
  const draft = JSON.stringify({ name, method, path, headers, params });
  const dirty = draft !== savedDraftRef.current;
  const save = useCallback(async () => {
    if (!name.trim()) return toast.error("用例名称不能为空");
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await automationCasesApi.update(
        row.id,
        {
          module_id: row.module_id,
          name: name.trim(),
          method,
          path,
          headers,
          params,
          steps: [buildQuickHttpStep({ name, method, path, headers, params })],
        },
        sessionId ?? undefined,
      );
      savedDraftRef.current = JSON.stringify({ name: name.trim(), method, path, headers, params });
      onSaved();
    } catch (error) { toast.error(messageOf(error)); } finally { setSaving(false); }
  }, [dirty, headers, method, name, onSaved, params, path, row.id, row.module_id, saving, sessionId]);
  useEffect(() => {
    if (!dirty || !name.trim() || saving) return;
    const timer = window.setTimeout(() => { void save(); }, 600);
    return () => window.clearTimeout(timer);
  }, [draft, dirty, name, save, saving]);
  return <div className="grid min-w-max grid-cols-[36px_minmax(180px,1.1fr)_90px_minmax(180px,1.2fr)_minmax(220px,1.4fr)_minmax(220px,1.4fr)_90px_120px] items-center gap-2 border-b px-3 py-2 last:border-b-0">
    <Checkbox checked={checked} onCheckedChange={onChecked} />
    <Input className="h-8" value={name} onChange={(event) => setName(event.target.value)} />
    <Select value={method} onValueChange={setMethod}><SelectTrigger className="h-8"><SelectValue /></SelectTrigger><SelectContent>{METHODS.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select>
    <Input className="h-8 font-mono text-xs" value={path} onChange={(event) => setPath(event.target.value)} placeholder="/api/example" />
    <Input className="h-8 font-mono text-xs" value={headers} onChange={(event) => setHeaders(event.target.value)} placeholder='{"Authorization":"Bearer ${token}"}' />
    <Input className="h-8 font-mono text-xs" value={params} onChange={(event) => setParams(event.target.value)} placeholder='{"key":"value"}' />
    <div className="flex"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={onUp}><ArrowUp className="h-3.5 w-3.5" /></Button><Button variant="ghost" size="icon" className="h-7 w-7" onClick={onDown}><ArrowDown className="h-3.5 w-3.5" /></Button></div>
    <div className="flex justify-end gap-1"><Button variant="ghost" size="icon" className="h-8 w-8" disabled={!dirty || saving} onClick={save}>{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}</Button><Button variant="ghost" size="icon" className="h-8 w-8" title="在本行上方插入" onClick={onInsertAbove}><Plus className="h-4 w-4" /></Button><Button variant="ghost" size="icon" className="h-8 w-8" onClick={onEdit}><Pencil className="h-4 w-4" /></Button><Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={onDelete}><Trash2 className="h-4 w-4" /></Button></div>
  </div>;
}

function QuickCreateRow({ moduleId, sessionId, isTrailing, autoFocusName = false, insertSortOrder, onFirstInput, onCreated, onRemove, onSaved }: { moduleId: number; sessionId: string | null; isTrailing: boolean; autoFocusName?: boolean; insertSortOrder?: number; onFirstInput: () => void; onCreated: () => void; onRemove: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [method, setMethod] = useState("GET");
  const [path, setPath] = useState("");
  const [headers, setHeaders] = useState("");
  const [params, setParams] = useState("");
  const [saving, setSaving] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const spawnedRef = useRef(false);
  const hasContent = Boolean(name.trim() || path.trim() || headers.trim() || params.trim());
  useEffect(() => {
    if (autoFocusName) nameRef.current?.focus();
  }, [autoFocusName]);
  const markInput = () => {
    if (!spawnedRef.current) {
      spawnedRef.current = true;
      onFirstInput();
    }
  };
  const save = async () => {
    if (!hasContent || saving) return;
    setSaving(true);
    try {
      const caseName = name.trim() || "未命名 API 用例";
      await automationCasesApi.create(
        {
          module_id: moduleId,
          name: caseName,
          method,
          path,
          headers,
          params,
          case_type: "api",
          sort_order: insertSortOrder ?? null,
          steps: [buildQuickHttpStep({ name: caseName, method, path, headers, params })],
        },
        sessionId ?? undefined,
      );
      onSaved();
      onCreated();
    } catch (error) { toast.error(messageOf(error)); } finally { setSaving(false); }
  };
  const handleBlur = () => {
    window.setTimeout(() => {
      if (rowRef.current?.contains(document.activeElement) || saving) return;
      if (hasContent) void save();
      else if (!isTrailing) onRemove();
    }, 120);
  };
  return <div ref={rowRef} onBlur={handleBlur} className="grid min-w-max grid-cols-[36px_minmax(180px,1.1fr)_90px_minmax(180px,1.2fr)_minmax(220px,1.4fr)_minmax(220px,1.4fr)_90px_120px] items-center gap-2 bg-primary/5 px-3 py-2">
    <Plus className="h-4 w-4 text-primary" />
    <Input ref={nameRef} className="h-8" value={name} onChange={(event) => { setName(event.target.value); markInput(); }} placeholder={insertSortOrder != null ? "在本行上方插入新用例" : "输入任意内容后自动追加下一行"} />
    <Select value={method} onValueChange={(value) => { setMethod(value); markInput(); }}><SelectTrigger className="h-8"><SelectValue /></SelectTrigger><SelectContent>{METHODS.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select>
    <Input className="h-8 font-mono text-xs" value={path} onChange={(event) => { setPath(event.target.value); markInput(); }} placeholder="/api/example" />
    <Input className="h-8 font-mono text-xs" value={headers} onChange={(event) => { setHeaders(event.target.value); markInput(); }} placeholder='{"Authorization":"Bearer ${token}"}' />
    <Input className="h-8 font-mono text-xs" value={params} onChange={(event) => { setParams(event.target.value); markInput(); }} placeholder='{"key":"value"}' />
    <span className="text-xs text-muted-foreground">{insertSortOrder != null ? "向上插入" : "新增到末尾"}</span>
    <div className="flex justify-end">{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : hasContent ? <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={onRemove}><Trash2 className="h-4 w-4" /></Button> : null}</div>
  </div>;
}

type ReportAnalysisStage = "collecting" | "rules" | "ai" | "done" | "failed";

type ReportAnalysisState = {
  reportId: number;
  stage: ReportAnalysisStage;
  preview: ReportAnalysisOutput | null;
  runId?: number;
  error?: string;
};

function RecordsDialog({
  open,
  quickEdit,
  moduleId,
  caseType,
  firstModel,
  models,
  onInvalidate,
  onEditCase,
  onClose,
}: {
  open: boolean;
  quickEdit: boolean;
  moduleId: number | null;
  caseType: Exclude<CaseType, "functional">;
  firstModel: string;
  models: string[];
  onInvalidate: () => void;
  onEditCase: (row: ApiCase) => void;
  onClose: () => void;
}) {
  const analysisAbortRef = useRef<AbortController | null>(null);
  const editQuery = useQuery({
    queryKey: ["automation-edit-history", caseType, moduleId],
    queryFn: () => automationCasesApi.editHistory(moduleId!, 200, caseType),
    enabled: open && quickEdit && moduleId != null,
    staleTime: 0,
  });
  const testQuery = useQuery({
    queryKey: ["automation-test-history", caseType, moduleId],
    queryFn: () => automationCasesApi.testHistory(moduleId!, 100, caseType),
    enabled: open && !quickEdit && moduleId != null,
    staleTime: 0,
  });
  const loading = quickEdit ? editQuery.isLoading : testQuery.isLoading;
  const [analysis, setAnalysis] = useState<ReportAnalysisState | null>(null);
  // 修复用的模型：诊断+修参数是推理要求最高的任务，允许用户选强模型，默认第一个启用的
  const [fixModel, setFixModel] = useState<string>("");
  const effectiveFixModel = fixModel || firstModel;
  const aiRunQuery = useQuery({
    queryKey: ["api-report-analysis-run", analysis?.runId],
    queryFn: () => aiApi.getRun(analysis!.runId!),
    enabled: analysis?.runId != null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "success" || status === "failed" || status === "cancelled" ? false : 2_000;
    },
  });

  const rollbackRecord = async (record: ApiCaseEditRecord, fullBatch = false) => {
    if (!record.batch_id) return;
    try {
      await casesApi.rollbackHistory(record.batch_id, {
        mode: fullBatch ? "full" : "partial",
        event_ids: fullBatch ? undefined : [record.id],
      });
      toast.success("已回滚");
      editQuery.refetch();
      onInvalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const analyzeReport = async (reportId: number, opts?: { force?: boolean }) => {
    analysisAbortRef.current?.abort();
    const controller = new AbortController();
    analysisAbortRef.current = controller;
    setAnalysis({ reportId, stage: "collecting", preview: null });
    try {
      const preview = await reportsApi.analysisPreview(reportId, controller.signal);
      // 非强制刷新：优先复用上次已保存的分析结果，避免重新跑（尤其 AI 汇总）
      if (!opts?.force) {
        try {
          const saved = await reportsApi.analysisLatest(reportId);
          if (saved?.ai_run_id) {
            setAnalysis({ reportId, stage: "done", preview, runId: saved.ai_run_id });
            toast.success("已加载上次分析结果（点「重新分析」可刷新）");
            return;
          }
        } catch {
          /* 没有历史就照常新跑 */
        }
      }
      setAnalysis({ reportId, stage: firstModel ? "rules" : "done", preview });
      if (!firstModel) {
        toast.success("规则诊断已完成");
        return;
      }
      const res = await reportsApi.analyze(reportId, { model_name: firstModel });
      setAnalysis({ reportId, stage: "ai", preview, runId: res.ai_run_id });
      toast.success("规则诊断已展示，正在生成 AI 汇总");
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setAnalysis((current) => current?.reportId === reportId ? { ...current, stage: "failed", error: "已中断分析" } : current);
        return;
      }
      toast.error(messageOf(e));
      setAnalysis({ reportId, stage: "failed", preview: null, error: messageOf(e) });
    } finally {
      if (analysisAbortRef.current === controller) analysisAbortRef.current = null;
    }
  };

  const cancelAnalysis = async () => {
    analysisAbortRef.current?.abort();
    const runId = analysis?.runId;
    setAnalysis((current) => current ? { ...current, stage: "failed", error: "已中断分析" } : current);
    if (runId) {
      try {
        await aiApi.cancelRun(runId);
        toast.success("已中断 AI 全面分析");
      } catch (error) {
        toast.error(messageOf(error));
      }
    }
  };

  const openSuggestedCase = async (caseId: number | null) => {
    if (caseId == null) {
      toast.error("建议里缺少用例 ID，无法打开编辑器");
      return;
    }
    try {
      const detail = await casesApi.get(caseId);
      onEditCase({
        ...detail,
        case_type: "api",
        tags: detail.tags ?? [],
        skip: Boolean(detail.skip),
        latest_run: null,
      });
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const applyAssertionSuggestion = async (
    suggestion: ReportAnalysisSuggestion,
    history?: { sessionId: string; batchId?: number },
  ) => {
    if (suggestion.case_id == null) {
      toast.error("建议里缺少用例 ID，无法应用");
      return;
    }
    const action = suggestion.action ?? {};
    if (action.type !== "add_assertion") {
      toast.error("这条建议需要人工确认，请进入用例编辑");
      return;
    }
    const target = String(action.target ?? "").trim();
    if (!target) {
      toast.error("建议里缺少断言目标，无法应用");
      return;
    }
    try {
      const detail = await casesApi.get(suggestion.case_id);
      const nextAssertion = appendAssertionRule(
        detail.steps ?? [],
        Number(suggestion.step_id ?? 0) || null,
        target,
        action.expected ?? "",
      );
      const updateResult = await automationCasesApi.update(detail.id, {
        module_id: detail.module_id,
        name: detail.name,
        steps: nextAssertion.steps,
        case_type: "api",
      }, history?.sessionId, history?.batchId);
      if (history && updateResult.batch_id != null) history.batchId = updateResult.batch_id;
      toast.success("已应用断言，重新运行后可验证效果");
      onInvalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const applyExtractSuggestion = async (
    suggestion: ReportAnalysisSuggestion,
    history?: { sessionId: string; batchId?: number },
  ) => {
    if (suggestion.case_id == null) {
      toast.error("建议里缺少用例 ID，无法应用");
      return;
    }
    const action = suggestion.action ?? {};
    if (action.type !== "update_extract") {
      toast.error("这条建议不是提取参数修复");
      return;
    }
    const variable = String(action.variable ?? "").trim();
    const jsonpath = String(action.suggested_jsonpath ?? "").trim();
    if (!variable || !jsonpath) {
      toast.error("建议里缺少变量名或 JSONPath");
      return;
    }
    try {
      const detail = await casesApi.get(suggestion.case_id);
      const nextExtract = appendExtractRule(
        detail.steps ?? [],
        Number(suggestion.step_id ?? 0) || null,
        variable,
        jsonpath,
      );
      const updateResult = await automationCasesApi.update(detail.id, {
        module_id: detail.module_id,
        name: detail.name,
        steps: nextExtract.steps,
        case_type: "api",
      }, history?.sessionId, history?.batchId);
      if (history && updateResult.batch_id != null) history.batchId = updateResult.batch_id;
      toast.success(`已更新提取参数 ${variable} → ${jsonpath}`);
      onInvalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const applyAssertionSuggestions = async (items: ReportAnalysisSuggestion[]) => {
    const applicable = items.filter((item) => item.action?.type === "add_assertion" && item.case_id != null);
    if (applicable.length === 0) {
      toast.info("没有可一键应用的高置信断言");
      return;
    }
    let ok = 0;
    const history = { sessionId: `ai-apply-assertions-${Date.now()}` };
    for (const item of applicable) {
      await applyAssertionSuggestion(item, history);
      ok += 1;
    }
    toast.success(`已应用 ${ok} 条断言建议`);
  };

  const applyExtractSuggestions = async (items: ReportAnalysisSuggestion[]) => {
    const applicable = items.filter((item) => item.action?.type === "update_extract" && item.case_id != null);
    if (applicable.length === 0) {
      toast.info("没有可一键应用的提取修复");
      return;
    }
    let ok = 0;
    const history = { sessionId: `ai-apply-extracts-${Date.now()}` };
    for (const item of applicable) {
      await applyExtractSuggestion(item, history);
      ok += 1;
    }
    toast.success(`已应用 ${ok} 条提取修复`);
  };

  const applyOrderSuggestion = async (suggestion: ReportAnalysisSuggestion) => {
    const action = suggestion.action ?? {};
    if (action.type !== "reorder_case_before") {
      toast.info("这条建议需要新增准备用例，请先用 AI 生成或手工新建后再运行");
      return;
    }
    const movingId = Number(action.case_id);
    const beforeId = Number(action.before_case_id);
    const moduleIdForAction = Number(action.module_id);
    if (!movingId || !beforeId || !moduleIdForAction) {
      toast.error("顺序调整建议缺少用例或模块信息");
      return;
    }
    try {
      const list = await automationCasesApi.list({ moduleId: moduleIdForAction, pageSize: 0 });
      const ordered = list.items.slice().sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
      const moving = ordered.find((item) => item.id === movingId);
      const before = ordered.find((item) => item.id === beforeId);
      if (!moving || !before) {
        toast.error("没有找到要调整顺序的用例");
        return;
      }
      const withoutMoving = ordered.filter((item) => item.id !== movingId);
      const beforeIndex = withoutMoving.findIndex((item) => item.id === beforeId);
      if (beforeIndex < 0) {
        toast.error("没有找到目标位置");
        return;
      }
      withoutMoving.splice(beforeIndex, 0, moving);
      await casesApi.reorder(withoutMoving.map((item, order) => ({ id: item.id, type: "case", new_order: order })));
      toast.success(`已将「${moving.name}」移动到「${before.name}」之前`);
      onInvalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const deleteDuplicateSuggestions = async (items: ReportAnalysisSuggestion[]) => {
    const ids = [
      ...new Set(
        items
          .filter((item) => item.action?.type === "delete_duplicate_case")
          .map((item) => Number(item.action?.duplicate_case_id ?? item.action?.case_id ?? item.case_id))
          .filter((id) => Number.isFinite(id) && id > 0),
      ),
    ];
    if (ids.length === 0) {
      toast.info("没有可删除的重复用例");
      return;
    }
    if (!window.confirm(`确认删除 ${ids.length} 条重复用例吗？建议先确认保留用例无误。`)) return;
    try {
      const sessionId = `ai-delete-duplicates-${Date.now()}`;
      let historyBatchId: number | undefined;
      const results = [];
      for (const caseId of ids) {
        const result = await automationCasesApi.remove(caseId, sessionId, historyBatchId);
        if (result.batch_id != null) historyBatchId = result.batch_id;
        results.push(result);
      }
      const batchIds = results.map((item) => item.batch_id).filter((id): id is number => id != null);
      toast.success(`已删除 ${ids.length} 条重复用例，建议重新分析刷新结果`, batchIds.length > 0 ? {
        action: {
          label: "撤销",
          onClick: async () => {
            try {
              await Promise.all(batchIds.map((batchId) => casesApi.rollbackHistory(batchId, { mode: "full" })));
              toast.success("已恢复重复用例");
              onInvalidate();
            } catch (error) {
              toast.error(messageOf(error));
            }
          },
        },
      } : undefined);
      onInvalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const deleteDuplicateSuggestion = async (suggestion: ReportAnalysisSuggestion) => {
    await deleteDuplicateSuggestions([suggestion]);
  };

  // AI 读取真实响应 → 生成 fix → 服务端预检 + 应用 + 重跑验证闭环。
  // 模型输出只是候选：分类过滤 / JSONPath 对真实响应预检 / 变量产出校验 / params 合并
  // 都在服务端做；应用后自动重跑，绿变红自动按快照回滚，修复率不会为负。
  const applyAiReportFixes = async () => {
    const reportId = analysis?.reportId;
    if (!reportId) return;
    if (!effectiveFixModel) {
      toast.error("没有可用的 AI 模型，无法生成参数修复");
      return;
    }
    try {
      toast.info(`正在用 ${effectiveFixModel} 读取响应并生成修复，可在任务看板查看进度 / 终止…`);
      // 异步提交：建 AiRun + 派 Celery，任务进全局看板、可终止。再轮询拿结果。
      const submitted = await functionalCasesApi.aiDiagnoseReport({ report_id: reportId, model_name: effectiveFixModel });
      const runId = submitted.ai_run_id;
      let run = await aiApi.getRun(runId);
      while (run.status === "pending" || run.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        run = await aiApi.getRun(runId);
      }
      if (run.status === "cancelled") {
        toast.info("已终止 AI 修复任务");
        return;
      }
      if (run.status === "failed") {
        toast.error(run.error || "AI 修复任务失败");
        return;
      }
      // 服务端应用：预检（分类过滤/真实响应 JSONPath 预检/变量校验/params 合并）
      // + 每用例快照 + 自动重跑验证；绿变红的用例后端自动回滚。
      const applyRes = await functionalCasesApi.aiApplyReportFixes({ ai_run_id: runId });
      const appliedCount = applyRes.applied.length;
      const skippedCount = applyRes.skipped.length;
      if (appliedCount === 0) {
        toast.info(
          skippedCount > 0
            ? `预检未放行任何修复（拦截 ${skippedCount} 条：非用例问题 / 响应预检不通过 / 变量缺产出方等）`
            : "AI 没有给出可应用的修复",
        );
        onInvalidate();
        return;
      }
      onInvalidate();
      if (applyRes.verify_report_id == null) {
        toast.success(`已应用 ${appliedCount} 条修复（拦截 ${skippedCount} 条），未触发自动验证，请手动重跑核对`);
        return;
      }
      toast.info(`已应用 ${appliedCount} 条修复（预检拦截 ${skippedCount} 条），正在重跑验证；仍失败的会带新证据自动再修（最多 3 轮），绿变红自动回滚…`);

      type VerifyResult = {
        status: string;
        fixed_count?: number;
        regressed_count?: number;
        still_red_count?: number;
        rolled_back_count?: number;
        collateral_regressed_count?: number;
        untouched_red_count?: number;
        rounds_used?: number;
        note?: string;
        message?: string;
      };
      const deadline = Date.now() + 15 * 60_000;
      let verify: VerifyResult | undefined;
      let lastRound = 1;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        const latest = await aiApi.getRun(runId);
        const payload = latest.output_payload as { verify?: VerifyResult; rounds?: { round?: number }[] } | null;
        verify = payload?.verify;
        if (verify) break;
        const round = payload?.rounds?.length ?? 1;
        if (round > lastRound) {
          lastRound = round;
          toast.info(`第 ${round} 轮修复已应用，正在重跑验证…`);
        }
      }
      onInvalidate();
      if (!verify) {
        toast.info("多轮验证仍在后台执行，结果稍后可在测试记录里查看（绿变红会自动回滚）");
        return;
      }
      if (verify.status !== "done") {
        toast.warning(verify.message || "验证未正常完成，已应用的修复保留，请手动重跑核对");
        return;
      }
      const collateral = verify.collateral_regressed_count ?? 0;
      const summary =
        `${verify.rounds_used ?? 1} 轮修复完成：生效 ${verify.fixed_count ?? 0} 条（红→绿）；` +
        `绿变红 ${verify.regressed_count ?? 0} 条（已自动回滚 ${verify.rolled_back_count ?? 0}）；` +
        `仍失败 ${verify.still_red_count ?? 0} 条` +
        (collateral > 0 ? `；另有 ${collateral} 条未修改用例被连带打挂，请人工检查` : "") +
        (verify.note ? `（${verify.note}）` : "");
      if ((verify.fixed_count ?? 0) > 0 && (verify.regressed_count ?? 0) === 0 && collateral === 0) {
        toast.success(summary);
      } else {
        toast.info(summary);
      }
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(value) => !value && onClose()}><DialogContent className="max-w-4xl"><DialogHeader><DialogTitle>{quickEdit ? "编辑记录" : "测试记录"}</DialogTitle><DialogDescription>{quickEdit ? "相邻两次编辑间隔不超过 30 分钟时，自动合并为一条记录。" : "API 自动执行结果按报告聚合。点报告右侧「AI 全面分析」可批量诊断所有用例。"}</DialogDescription></DialogHeader><div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">{loading ? <Loading /> : quickEdit ? <EditRecords records={editQuery.data ?? []} onRollback={rollbackRecord} /> : <TestRecords reports={testQuery.data ?? []} onAnalyze={analyzeReport} />}</div><DialogFooter><Button variant="outline" onClick={onClose}>关闭</Button></DialogFooter></DialogContent></Dialog>
      <ReportAnalysisDialog
        state={analysis}
        run={aiRunQuery.data ?? null}
        onApplyAssertion={applyAssertionSuggestion}
        onApplyAssertions={applyAssertionSuggestions}
        onApplyExtract={applyExtractSuggestion}
        onApplyExtracts={applyExtractSuggestions}
        onApplyOrder={applyOrderSuggestion}
        onDeleteDuplicate={deleteDuplicateSuggestion}
        onDeleteDuplicates={deleteDuplicateSuggestions}
        onAiFixAll={applyAiReportFixes}
        models={models}
        fixModel={effectiveFixModel}
        onFixModelChange={setFixModel}
        onEditCase={openSuggestedCase}
        onReanalyze={() => analysis && analyzeReport(analysis.reportId, { force: true })}
        onCancel={cancelAnalysis}
        onClose={() => setAnalysis(null)}
      />
    </>
  );
}

function TestRecords({ reports, onAnalyze }: { reports: ApiTestHistoryReport[]; onAnalyze: (reportId: number) => void }) {
  if (!reports.length) return <Empty text="该模块还没有测试记录" />;
  return <>{reports.map((report) => (
    <div key={report.report_id} className="flex items-center gap-3 rounded-lg border bg-card px-3 py-3 text-sm">
      <span className="font-medium">API-{formatTime(report.started_at)}-测试报告</span>
      <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
        <span>用例 {report.cases.length}</span>
        <span className="text-emerald-600">通过 {report.counts.passed ?? 0}</span>
        <span className="text-red-600">失败 {report.counts.failed ?? 0}</span>
        <span className="text-orange-600">错误 {report.counts.error ?? 0}</span>
        <button type="button" className="flex items-center gap-1 text-primary hover:underline" onClick={() => onAnalyze(report.report_id)}>
          <Sparkles className="h-3.5 w-3.5" /> AI 全面分析
        </button>
        {report.allure_url ? (
          <button type="button" className="text-primary hover:underline" onClick={() => window.open(report.allure_url!, "_blank", "noopener,noreferrer")}>
            Allure 报告
          </button>
        ) : null}
      </span>
    </div>
  ))}</>;
}

const CLS_COLOR: Record<string, string> = {
  用例问题: "bg-amber-100 text-amber-700",
  接口问题: "bg-red-100 text-red-700",
  "环境/其他": "bg-slate-100 text-slate-600",
  正常: "bg-emerald-100 text-emerald-700",
};

function ReportAnalysisDialog({
  state,
  run,
  onApplyAssertion,
  onApplyAssertions,
  onApplyExtract,
  onApplyExtracts,
  onApplyOrder,
  onDeleteDuplicate,
  onDeleteDuplicates,
  onAiFixAll,
  models,
  fixModel,
  onFixModelChange,
  onEditCase,
  onReanalyze,
  onCancel,
  onClose,
}: {
  state: ReportAnalysisState | null;
  run: { status: string; output_payload?: Record<string, unknown> | null; error?: string | null } | null;
  onApplyAssertion: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onApplyAssertions: (suggestions: ReportAnalysisSuggestion[]) => Promise<void>;
  onApplyExtract: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onApplyExtracts: (suggestions: ReportAnalysisSuggestion[]) => Promise<void>;
  onApplyOrder: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onDeleteDuplicate: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onDeleteDuplicates: (suggestions: ReportAnalysisSuggestion[]) => Promise<void>;
  onAiFixAll: () => Promise<void>;
  models: string[];
  fixModel: string;
  onFixModelChange: (name: string) => void;
  onEditCase: (caseId: number | null) => Promise<void>;
  onReanalyze: () => void;
  onCancel: () => void;
  onClose: () => void;
}) {
  const [applyingAll, setApplyingAll] = useState(false);
  const [aiFixing, setAiFixing] = useState(false);
  const [deletingDuplicates, setDeletingDuplicates] = useState(false);
  if (!state) return null;
  const aiOutput = run?.status === "success" ? (run.output_payload as ReportAnalysisOutput | null) : null;
  const output = aiOutput ?? state.preview;
  const stage: ReportAnalysisStage =
    run?.status === "success" ? "done"
      : run?.status === "failed" || run?.status === "cancelled" || state.stage === "failed" ? "failed"
        : state.stage;
  const suggestions = (output?.cases ?? []).flatMap((item) =>
    (item.suggestions ?? []).map((suggestion) => ({
      ...suggestion,
      caseName: item.name,
      classification: item.classification,
    })),
  );
  const high = suggestions.filter((item) => item.apply_mode === "high_confidence").length;
  const review = suggestions.filter((item) => item.apply_mode === "need_review").length;
  const manual = suggestions.filter((item) => item.apply_mode === "manual_required").length;
  const autoApplicable = suggestions.filter((item) => item.apply_mode === "high_confidence" && item.action?.type === "add_assertion");
  const autoExtract = suggestions.filter((item) => item.apply_mode === "high_confidence" && item.action?.type === "update_extract");
  const autoOrder = suggestions.filter((item) => item.apply_mode === "high_confidence" && item.action?.type === "reorder_case_before");
  const duplicateSuggestions = suggestions.filter((item) => item.action?.type === "delete_duplicate_case");
  const hasApplyAll = autoOrder.length + autoExtract.length + autoApplicable.length > 0;
  const groupedSuggestions = groupAnalysisSuggestions(suggestions);
  const cancellable =
    state.stage !== "failed" &&
    state.stage !== "done" &&
    (stage === "collecting" ||
      stage === "rules" ||
      stage === "ai" ||
      run?.status === "pending" ||
      run?.status === "running");

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[88vh] w-[min(92vw,1120px)] max-w-6xl grid-rows-[auto_minmax(0,1fr)_auto]">
        <DialogHeader>
          <DialogTitle>AI 全面分析</DialogTitle>
          <DialogDescription>先展示规则诊断，AI 汇总在后台继续生成。</DialogDescription>
        </DialogHeader>
        {!output && stage !== "failed" ? (
          <div className="space-y-3 p-4 text-sm text-muted-foreground">
            <AnalysisStageBar stage={stage} />
            <div><Loader2 className="mr-2 inline h-4 w-4 animate-spin" />正在收集报告并运行规则诊断…</div>
          </div>
        ) : stage === "failed" && !output ? (
          <div className="rounded border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            分析失败：{state.error || run?.error || "未知错误"}
          </div>
        ) : (
          <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
            <AnalysisStageBar stage={stage} />
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-medium">规则诊断已输出</span>
              <span className="text-xs text-muted-foreground">用例 {output?.summary.total_cases ?? 0}</span>
              <span className="text-xs text-muted-foreground">建议 {output?.summary.total_suggestions ?? 0}</span>
              <span className="text-xs text-emerald-700">高置信 {high}</span>
              <span className="text-xs text-amber-700">需审核 {review}</span>
              <span className="text-xs text-red-700">需人工 {manual}</span>
              {hasApplyAll ? (
                <Button
                  size="sm"
                  className="ml-auto h-7 px-2 text-xs"
                  disabled={applyingAll}
                  onClick={async () => {
                    setApplyingAll(true);
                    try {
                      // 顺序优先（会重排 sort_order），再取参、再断言
                      for (const item of autoOrder) await onApplyOrder(item);
                      if (autoExtract.length) await onApplyExtracts(autoExtract);
                      if (autoApplicable.length) await onApplyAssertions(autoApplicable);
                      toast.success(
                        `已一键应用 ${autoOrder.length + autoExtract.length + autoApplicable.length} 项调整（顺序/取参/断言）`,
                      );
                    } finally {
                      setApplyingAll(false);
                    }
                  }}
                >
                  {applyingAll ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  一键应用全部调整（{autoOrder.length + autoExtract.length + autoApplicable.length}）
                </Button>
              ) : null}
              {duplicateSuggestions.length > 0 ? (
                <Button
                  size="sm"
                  variant="destructive"
                  className={hasApplyAll ? "h-7 px-2 text-xs" : "ml-auto h-7 px-2 text-xs"}
                  disabled={deletingDuplicates}
                  onClick={async () => {
                    setDeletingDuplicates(true);
                    try {
                      await onDeleteDuplicates(duplicateSuggestions);
                    } finally {
                      setDeletingDuplicates(false);
                    }
                  }}
                >
                  {deletingDuplicates ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                  一键删除重复用例（{duplicateSuggestions.length}）
                </Button>
              ) : null}
              {models.length > 1 ? (
                <select
                  className={
                    (hasApplyAll || duplicateSuggestions.length > 0 ? "" : "ml-auto ") +
                    "h-7 rounded-md border bg-background px-1.5 text-xs text-muted-foreground"
                  }
                  value={fixModel}
                  disabled={aiFixing}
                  title="修复用的 AI 模型（诊断+修参数是推理要求最高的任务，建议选推理模型）"
                  onChange={(e) => onFixModelChange(e.target.value)}
                >
                  {models.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              ) : null}
              <Button
                size="sm"
                variant="outline"
                className={
                  hasApplyAll || duplicateSuggestions.length > 0 || models.length > 1
                    ? "h-7 px-2 text-xs"
                    : "ml-auto h-7 px-2 text-xs"
                }
                disabled={aiFixing}
                title="用 AI 读取真实响应，批量修复请求参数/提取/断言；应用前程序化预检，应用后自动重跑验证（最多 3 轮），绿变红自动回滚"
                onClick={async () => {
                  setAiFixing(true);
                  try {
                    await onAiFixAll();
                  } finally {
                    setAiFixing(false);
                  }
                }}
              >
                {aiFixing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                AI 修复参数并应用
              </Button>
            </div>
            {aiOutput?.ai_summary ? (
              <div className="h-64 min-h-[160px] max-h-[56vh] resize-y overflow-auto rounded border bg-muted/20 p-3 text-sm">
                <MarkdownView
                  source={aiOutput.ai_summary}
                  className="prose prose-sm max-w-none break-words [&_ol]:list-decimal [&_ul]:list-disc [&_li]:ml-4 [&_pre]:whitespace-pre-wrap"
                />
              </div>
            ) : state.runId && run?.status !== "failed" ? (
              <div className="rounded border bg-muted/30 p-2 text-xs text-muted-foreground">
                <Loader2 className="mr-1.5 inline h-3.5 w-3.5 animate-spin" />
                AI 正在生成中文汇总，下面的规则诊断结果已经可以先看。
              </div>
            ) : null}
            {groupedSuggestions.length === 0 ? <Empty text="没有可分析的执行结果" /> : groupedSuggestions.slice(0, 100).map((group) => (
              <AnalysisSuggestionGroupCard
                key={`${group.caseId ?? "case"}-${group.caseName}`}
                group={group}
                onApplyAssertion={onApplyAssertion}
                onApplyExtract={onApplyExtract}
                onApplyOrder={onApplyOrder}
                onDeleteDuplicate={onDeleteDuplicate}
                onEditCase={onEditCase}
              />
            ))}
          </div>
        )}
        <DialogFooter>
          {cancellable ? <Button variant="destructive" onClick={onCancel}>中断分析</Button> : null}
          {!cancellable && output ? (
            <Button variant="outline" onClick={onReanalyze}>
              <Sparkles className="h-4 w-4" /> 重新分析
            </Button>
          ) : null}
          <Button variant="outline" onClick={onClose}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type AnalysisSuggestionWithMeta = ReportAnalysisSuggestion & { caseName: string; classification: string };

function groupAnalysisSuggestions(items: AnalysisSuggestionWithMeta[]) {
  const map = new Map<string, {
    caseId: number | null;
    caseName: string;
    classification: string;
    confidence: number;
    suggestions: AnalysisSuggestionWithMeta[];
  }>();
  for (const item of items) {
    const key = `${item.case_id ?? "unknown"}:${item.caseName}`;
    const group = map.get(key) ?? {
      caseId: item.case_id,
      caseName: item.caseName,
      classification: item.classification,
      confidence: item.confidence ?? 0,
      suggestions: [],
    };
    group.confidence = Math.max(group.confidence, item.confidence ?? 0);
    group.suggestions.push(item);
    map.set(key, group);
  }
  return [...map.values()];
}

function AnalysisSuggestionGroupCard({
  group,
  onApplyAssertion,
  onApplyExtract,
  onApplyOrder,
  onDeleteDuplicate,
  onEditCase,
}: {
  group: ReturnType<typeof groupAnalysisSuggestions>[number];
  onApplyAssertion: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onApplyExtract: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onApplyOrder: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onDeleteDuplicate: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onEditCase: (caseId: number | null) => Promise<void>;
}) {
  const [busy, setBusy] = useState<"apply" | "extract" | "order" | "delete" | "edit" | null>(null);
  const runAction = async (kind: "apply" | "extract" | "order" | "delete" | "edit", item?: ReportAnalysisSuggestion) => {
    setBusy(kind);
    try {
      if (kind === "apply" && item) await onApplyAssertion(item);
      else if (kind === "extract" && item) await onApplyExtract(item);
      else if (kind === "order" && item) await onApplyOrder(item);
      else if (kind === "delete" && item) await onDeleteDuplicate(item);
      else await onEditCase(group.caseId);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-lg border p-2 text-sm">
      <div className="flex items-center gap-2">
        <span className={cn("shrink-0 rounded px-2 py-0.5 text-xs", CLS_COLOR[group.classification] ?? "bg-slate-100 text-slate-600")}>{group.classification || "未判定"}</span>
        <span className="min-w-0 flex-1 truncate font-medium">{group.caseName}</span>
        <span className="text-xs text-muted-foreground">{group.suggestions.length} 条建议</span>
        <span className="text-xs text-muted-foreground">{Math.round((group.confidence ?? 0) * 100)}%</span>
      </div>
      <div className="mt-2 space-y-2">
        {group.suggestions.map((item, index) => {
          const canApplyAssertion = item.action?.type === "add_assertion";
          const canApplyExtract = item.action?.type === "update_extract" && item.action?.suggested_jsonpath;
          const canApplyOrder = item.action?.type === "reorder_case_before";
          const canDeleteDuplicate = item.action?.type === "delete_duplicate_case";
          const needsSetupCase = item.action?.type === "create_setup_case";
          return (
            <div key={`${item.step_report_id ?? index}-${index}`} className="rounded-md bg-muted/20 p-2">
              <div className="flex items-center gap-2">
                <span className="shrink-0 rounded bg-background px-1.5 py-0.5 text-xs">{index + 1}</span>
                <span className="shrink-0 rounded bg-background px-1.5 py-0.5 text-xs">{analysisCategoryLabel(item.category)}</span>
                <span className="shrink-0 rounded bg-background px-1.5 py-0.5 text-xs">{analysisModeLabel(item.apply_mode)}</span>
                <span className="min-w-0 flex-1 text-xs font-medium">{item.title}</span>
              </div>
              {item.evidence ? <div className="mt-1 break-all text-xs text-muted-foreground">{item.evidence}</div> : null}
              <pre className="mt-1 max-h-24 overflow-auto rounded bg-background/80 p-1.5 font-mono text-xs">{JSON.stringify(item.action, null, 2)}</pre>
              <div className="mt-2 flex justify-end gap-2">
                {needsSetupCase ? (
                  <span className="mr-auto rounded bg-amber-100 px-2 py-1 text-xs text-amber-700">
                    需新增准备用例
                  </span>
                ) : null}
                {canApplyOrder ? (
                  <Button
                    size="sm"
                    className="h-7 px-2 text-xs"
                    disabled={busy !== null}
                    onClick={() => void runAction("order", item)}
                  >
                    {busy === "order" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-3.5 w-3.5" />}
                    调整顺序
                  </Button>
                ) : null}
                {canDeleteDuplicate ? (
                  <Button
                    size="sm"
                    variant="destructive"
                    className="h-7 px-2 text-xs"
                    disabled={busy !== null}
                    onClick={() => void runAction("delete", item)}
                  >
                    {busy === "delete" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    删除重复用例
                  </Button>
                ) : null}
                {canApplyExtract ? (
                  <Button
                    size="sm"
                    className="h-7 px-2 text-xs"
                    disabled={busy !== null}
                    onClick={() => void runAction("extract", item)}
                  >
                    {busy === "extract" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                    应用提取
                  </Button>
                ) : null}
                {canApplyAssertion ? (
                  <Button
                    size="sm"
                    className="h-7 px-2 text-xs"
                    disabled={busy !== null}
                    onClick={() => void runAction("apply", item)}
                  >
                    {busy === "apply" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                    应用断言
                  </Button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-end gap-2">
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2 text-xs"
          disabled={busy !== null}
          onClick={() => void runAction("edit")}
        >
          {busy === "edit" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pencil className="h-3.5 w-3.5" />}
          编辑用例
        </Button>
      </div>
    </div>
  );
}

function AnalysisStageBar({ stage }: { stage: ReportAnalysisStage }) {
  const steps = [
    { key: "collecting", label: "收集报告" },
    { key: "rules", label: "规则诊断" },
    { key: "ai", label: "AI 汇总" },
    { key: "done", label: "完成" },
  ] as const;
  const current = stage === "collecting" ? 0 : stage === "rules" ? 1 : stage === "ai" ? 2 : stage === "done" ? 3 : 1;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {steps.map((item, index) => {
        const active = index <= current;
        const running = index === current && stage !== "done" && stage !== "failed";
        return (
          <span key={item.key} className={cn("inline-flex items-center gap-1 rounded border px-2 py-0.5", active ? "border-primary/30 bg-primary/10 text-primary" : "bg-muted/30 text-muted-foreground")}>
            {running ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            {item.label}
          </span>
        );
      })}
    </div>
  );
}

function analysisCategoryLabel(category: string): string {
  const map: Record<string, string> = {
    missing_extraction: "提取",
    missing_assertion: "断言",
    parameter_error: "参数",
    sql_assertion_needed: "SQL",
    function_needed: "Function",
    data_safety: "数据安全",
    execution_order: "顺序",
    environment_issue: "环境",
    api_defect: "接口",
  };
  return map[category] ?? category;
}

function analysisModeLabel(mode: string): string {
  const map: Record<string, string> = {
    high_confidence: "高置信",
    need_review: "需审核",
    manual_required: "需人工",
  };
  return map[mode] ?? mode;
}

function EditRecords({ records, onRollback }: { records: ApiCaseEditRecord[]; onRollback: (record: ApiCaseEditRecord, fullBatch?: boolean) => void }) {
  const groups = useMemo(() => {
    const sortedRecords = records
      .filter((record) => !(record.session_id?.startsWith("ai-") && record.batch_id == null))
      .sort((a, b) => getTimeValue(b.created_at) - getTimeValue(a.created_at));
    const merged: Array<{
      key: string;
      items: ApiCaseEditRecord[];
      time: number;
      previousTime: number;
    }> = [];

    sortedRecords.forEach((record) => {
      const time = getTimeValue(record.created_at);
      const current = merged.at(-1);
      const interval = current && time > 0 ? current.previousTime - time : Number.POSITIVE_INFINITY;
      if (current && interval >= 0 && interval <= EDIT_HISTORY_MERGE_WINDOW_MS) {
        current.items.push(record);
        current.previousTime = time;
        return;
      }
      merged.push({
        key: `history-${record.batch_id ?? "legacy"}-${record.id}`,
        items: [record],
        time,
        previousTime: time,
      });
    });

    return merged.map((group) => ({
      key: group.key,
      items: group.items,
      time: group.time,
    }));
  }, [records]);
  if (!groups.length) return <Empty text="该模块还没有编辑记录" />;
  return (
    <>
      {groups.map((group) => {
        const counts = { create: 0, update: 0, delete: 0 };
        group.items.forEach((item) => { counts[item.action] += 1; });
        const rollbackBatchIds = [
          ...new Set(group.items.filter((item) => item.rollback_available && item.batch_id).map((item) => item.batch_id)),
        ];
        const canRollbackBatch = rollbackBatchIds.length === 1 && group.items.filter((item) => item.rollback_available).length > 1;
        const first = group.items[0];
        const title = first.session_id?.startsWith("ai-") || group.items.length > 1 ? "批量编辑记录" : "编辑记录";
        return (
          <details key={group.key} className="rounded-lg border bg-card px-3 py-2">
            <summary className="cursor-pointer list-none">
              <div className="flex items-center gap-3">
                <span className="font-medium">{formatTime(first.created_at)} {title}</span>
                <span className="ml-auto text-xs text-muted-foreground">
                  新增 {counts.create} · 修改 {counts.update} · 删除 {counts.delete}
                </span>
              </div>
            </summary>
            <div className="mt-2 divide-y border-t">
              {group.items.map((item) => (
                <div key={`${item.batch_id ?? "legacy"}-${item.id}`} className="py-2 text-sm">
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{item.action}</Badge>
                    <span>{item.case_name}</span>
                    {item.rollback_status && item.rollback_status !== "none" ? (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">已回滚</span>
                    ) : item.rollback_available ? (
                      <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">可回滚</span>
                    ) : null}
                    <span className="ml-auto text-xs text-muted-foreground">{item.operator ?? "System"}</span>
                    {item.rollback_available && item.batch_id ? (
                      <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => onRollback(item)}>回滚</Button>
                    ) : null}
                    {canRollbackBatch && item.rollback_available && item.batch_id ? (
                      <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => onRollback(item, true)}>整次回滚</Button>
                    ) : null}
                  </div>
                  {item.changes.length ? (
                    <ul className="mt-1 space-y-0.5 pl-16 text-xs text-muted-foreground">
                      {item.changes.map((change, index) => (
                        <li key={`${change.field}-${index}`}>
                          {change.field}：
                          <span className="line-through">{String(change.old || "空")}</span>
                          {" → "}
                          <span className="text-foreground">{String(change.new || "空")}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))}
            </div>
          </details>
        );
      })}
    </>
  );
}
