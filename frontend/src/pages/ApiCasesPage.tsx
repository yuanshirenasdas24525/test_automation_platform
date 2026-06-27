import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  Download,
  FileText,
  Folder,
  History,
  Loader2,
  Pencil,
  Play,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Upload,
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
import {
  aiModelsApi,
  apiCasesApi,
  ApiError,
  aiApi,
  casesApi,
  contentApi,
  functionalCasesApi,
  projectsApi,
  reportsApi,
  runsApi,
  type ReportAnalysisOutput,
  type ReportAnalysisSuggestion,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  ApiCase,
  ApiCaseEditRecord,
  ApiRunStatus,
  ApiTestHistoryReport,
  ContentNode,
  TestCaseCreate,
  TestStepDraft,
} from "@/types/domain";
import { AiGenerateDialog } from "./FunctionalCasesPage";
import { CaseDialog, type CaseFormValues } from "./ProjectDetailPage";

type DiagnoseResult = {
  classification: string;
  reason: string;
  suggestion: string;
  fix: { extract: Record<string, unknown>; assertion: Record<string, unknown> };
};

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100, 500] as const;
const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

const STATUS_META: Record<ApiRunStatus, { label: string; className: string }> = {
  pending: { label: "待执行", className: "bg-slate-100 text-slate-600" },
  passed: { label: "通过", className: "bg-emerald-100 text-emerald-700" },
  failed: { label: "失败", className: "bg-red-100 text-red-700" },
  error: { label: "错误", className: "bg-orange-100 text-orange-700" },
  skipped: { label: "跳过", className: "bg-zinc-100 text-zinc-600" },
};

function messageOf(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message;
  return "操作失败";
}

function sessionId(): string {
  return `api-edit-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
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
    ? "not_null"
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

function assertionRulesToLegacyMap(steps: TestStepDraft[]) {
  const out: Record<string, unknown> = {};
  for (const step of steps) {
    for (const item of step.assertion ?? []) {
      const raw = item as Record<string, unknown>;
      const target = String(raw.target ?? "").trim();
      if (target) out[target] = raw.type === "not_null" ? "not_empty" : raw.expected;
    }
  }
  return out;
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

function extractRulesToLegacyMap(steps: TestStepDraft[]) {
  const out: Record<string, unknown> = {};
  for (const step of steps) {
    for (const item of step.extract ?? []) {
      const raw = item as Record<string, unknown>;
      const name = String(raw.name ?? "").trim();
      const jsonpath = raw.jsonpath ?? raw.path ?? raw.expr;
      if (name && jsonpath) out[name] = jsonpath;
    }
  }
  return out;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "--";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function Checkbox({ checked, onCheckedChange }: { checked: boolean; onCheckedChange: () => void }) {
  return <input type="checkbox" checked={checked} onChange={onCheckedChange} className="h-4 w-4 rounded border-input accent-primary" />;
}

function Badge({ children, className }: { children: ReactNode; variant?: "outline"; className?: string }) {
  return <span className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs", className)}>{children}</span>;
}

export function ApiCasesPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const queryClient = useQueryClient();
  const [trail, setTrail] = useState<Array<{ id: number; name: string }>>([]);
  const moduleId = trail.at(-1)?.id ?? null;
  const [quickEdit, setQuickEdit] = useState(false);
  const [editSession, setEditSession] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState<ApiRunStatus | "all">("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [editor, setEditor] = useState<ApiCase | "new" | null>(null);
  const [editorSaving, setEditorSaving] = useState(false);
  const [recordsOpen, setRecordsOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [newRows, setNewRows] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [diagnose, setDiagnose] = useState<{ row: ApiCase; loading: boolean; result: DiagnoseResult | null } | null>(null);
  const [runDetailCase, setRunDetailCase] = useState<ApiCase | null>(null);

  const aiModelsQuery = useQuery({ queryKey: ["ai-models"], queryFn: () => aiModelsApi.list() });
  const firstModel = (aiModelsQuery.data ?? []).find((m) => m.enabled)?.name ?? "";

  const handleDiagnose = async (row: ApiCase) => {
    if (!firstModel) {
      toast.error("请先在配置中心添加可用 AI 模型");
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
    queryKey: ["content", projectId, moduleId, "api-workbench"],
    queryFn: () => contentApi.list(projectId, moduleId, "api"),
    enabled: Number.isFinite(projectId),
  });
  const casesQuery = useQuery({
    queryKey: ["api-cases", moduleId, status, keyword, page, pageSize],
    queryFn: () => apiCasesApi.list({
      moduleId: moduleId!,
      status: status === "all" ? undefined : status,
      keyword,
      page,
      pageSize,
    }),
    enabled: moduleId != null,
  });

  const modules = (contentQuery.data ?? []).filter((node) => node.type === "module");
  const cases = casesQuery.data?.items ?? [];
  const total = casesQuery.data?.total ?? 0;
  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(total / pageSize));

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["api-cases"] });
    queryClient.invalidateQueries({ queryKey: ["api-test-history"] });
    queryClient.invalidateQueries({ queryKey: ["api-edit-history"] });
    queryClient.invalidateQueries({ queryKey: ["project-stack-counts", projectId] });
  };

  useEffect(() => {
    setSelected(new Set());
    setPage(1);
  }, [moduleId, quickEdit]);

  const enterQuickEdit = () => {
    setQuickEdit(true);
    setEditSession(sessionId());
    setSelected(new Set());
    setNewRows([sessionId()]);
  };
  const exitQuickEdit = () => {
    setQuickEdit(false);
    setEditSession(null);
    setSelected(new Set());
    setNewRows([]);
  };

  const runMutation = useMutation({
    mutationFn: (ids: number[]) => runsApi.trigger({
      project: projectId,
      category: "api",
      case_ids: ids,
    }),
    onSuccess: (result) => {
      toast.success(`已提交 ${result.case_number ?? 0} 条 API 用例，报告 #${result.report_id}`);
      setSelected(new Set());
      invalidate();
      window.setTimeout(invalidate, 2500);
      window.setTimeout(invalidate, 7000);
    },
    onError: (error) => toast.error(messageOf(error)),
  });

  const removeMutation = useMutation({
    mutationFn: (caseId: number) => apiCasesApi.remove(caseId, editSession ?? undefined),
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
      const results = await Promise.all([...selected].map((caseId) => apiCasesApi.remove(caseId, editSession ?? undefined)));
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
      await casesApi.exportCases({ projectId, moduleId, caseTypes: ["api"], format: "xlsx" });
      toast.success("API 用例已导出");
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
      skip: values.skip,
      case_type: "api",
      // 不送 steps：让后端按最新 v1 字段重新合成 http_request step，
      // 否则 steps=null 会走"整体替换"分支、跳过重建 → 改了执行旧的。
    };
    setEditorSaving(true);
    try {
      if (editor === "new") {
        await apiCasesApi.create(payload, quickEdit ? editSession ?? undefined : undefined);
        toast.success("用例已创建");
      } else {
        await apiCasesApi.update(editor.id, payload, quickEdit ? editSession ?? undefined : undefined);
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
        <Breadcrumb project={projectQuery.data?.name ?? "…"} trail={trail} onJump={(index) => setTrail(index < 0 ? [] : trail.slice(0, index + 1))} />
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setRecordsOpen(true)} disabled={moduleId == null}>
            <History className="h-4 w-4" />{quickEdit ? "编辑记录" : "测试记录"}
          </Button>
          <div className="inline-flex overflow-hidden rounded-md border">
            <button type="button" className={cn("px-3 py-1.5 text-xs font-medium", !quickEdit ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")} onClick={exitQuickEdit}>运行模式</button>
            <button type="button" className={cn("border-l px-3 py-1.5 text-xs font-medium", quickEdit ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")} onClick={enterQuickEdit}>快速编辑</button>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={moduleId == null} onClick={() => setEditor("new")}><Plus className="h-4 w-4" />新建 API 用例</Button>
        <Button variant="outline" size="sm" disabled={moduleId == null} className="border-primary/40 text-primary" onClick={() => setAiOpen(true)}><Sparkles className="h-4 w-4" />AI 生成用例</Button>
        {quickEdit ? (
          <>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" className="hidden" onChange={(event) => event.target.files?.[0] && importFile(event.target.files[0])} />
            <Button variant="outline" size="sm" disabled={moduleId == null} onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" />导入</Button>
            {selected.size > 0 ? <Button variant="destructive" size="sm" onClick={batchDelete}><Trash2 className="h-4 w-4" />删除选中（{selected.size}）</Button> : null}
          </>
        ) : (
          <>
            <Button variant="outline" size="sm" disabled={moduleId == null} onClick={exportCases}><Download className="h-4 w-4" />导出</Button>
            {selected.size > 0 ? <Button size="sm" disabled={runMutation.isPending} onClick={() => runMutation.mutate([...selected])}><Play className="h-4 w-4" />运行选中（{selected.size}）</Button> : null}
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
          </div>
        ) : null}
      </div>

      {contentQuery.isLoading ? <Loading /> : (
        <div className="space-y-6">
          {moduleId == null || modules.length > 0 ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">{moduleId == null ? "模块" : "子模块"}（{modules.length}）</h3>
              {modules.length ? <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{modules.map((node) => <ModuleCard key={node.id} node={node} onOpen={() => setTrail([...trail, { id: node.id, name: node.name }])} />)}</div> : <Empty text="当前层级没有模块" />}
            </section>
          ) : null}
          {moduleId != null ? (
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">API 用例（{total}）</h3>
              {casesQuery.isLoading ? <Loading /> : cases.length === 0 ? <Empty text="当前模块还没有 API 用例" /> : (
                <ApiCaseTable
                  cases={cases}
                  moduleId={moduleId}
                  quickEdit={quickEdit}
                  sessionId={editSession}
                  selected={selected}
                  onSelected={setSelected}
                  onEdit={setEditor}
                  onRun={(row) => runMutation.mutate([row.id])}
                  onDiagnose={handleDiagnose}
                  onShowRunDetail={setRunDetailCase}
                  onDelete={(row) => window.confirm(`确定删除“${row.name}”吗？`) && removeMutation.mutate(row.id)}
                  onReorder={reorder}
                  onSaved={invalidate}
                  newRows={newRows}
                  onFirstInput={(rowId) => {
                    setNewRows((current) => current.at(-1) === rowId
                      ? [...current, sessionId()]
                      : current);
                  }}
                  onRemoveNewRow={(rowId) => setNewRows((current) => {
                    const next = current.filter((id) => id !== rowId);
                    return next.length ? next : [sessionId()];
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

      <CaseDialog state={originalDialogState} category="api" onClose={() => setEditor(null)} onSubmit={submitCase} submitting={editorSaving} />
      <RecordsDialog
        open={recordsOpen}
        quickEdit={quickEdit}
        moduleId={moduleId}
        firstModel={firstModel}
        onInvalidate={invalidate}
        onEditCase={(row) => {
          setRecordsOpen(false);
          setEditor(row);
        }}
        onClose={() => setRecordsOpen(false)}
      />
      <AiGenerateDialog open={aiOpen} moduleId={moduleId} projectId={projectId} initialMode="interface" onClose={() => setAiOpen(false)} onInserted={invalidate} />
      <DiagnoseDialog state={diagnose} onClose={() => setDiagnose(null)} onFixed={() => { setDiagnose(null); invalidate(); }} />
      <RunDetailDialog row={runDetailCase} onClose={() => setRunDetailCase(null)} />
    </div>
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
      Object.keys(result.fix.assertion || {}).length > 0);

  const applyFix = async () => {
    if (!result) return;
    setFixing(true);
    try {
      const body: TestCaseCreate = { module_id: row.module_id, name: row.name, case_type: "api" };
      if (Object.keys(result.fix.extract || {}).length > 0) body.extract_data = JSON.stringify(result.fix.extract, null, 2);
      if (Object.keys(result.fix.assertion || {}).length > 0) body.assertion = JSON.stringify(result.fix.assertion, null, 2);
      await apiCasesApi.update(row.id, body);
      toast.success("已按修正更新用例，可重新运行验证");
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
    queryFn: () => apiCasesApi.latestRunDetail(row!.id),
    enabled: row != null,
  });

  useEffect(() => {
    setExpanded(new Set());
  }, [row?.id]);

  const detail = query.data;
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
                      <div className="grid gap-2 border-t bg-muted/10 p-3 md:grid-cols-2">
                        <JsonBlock title="请求地址" value={step.request.url} />
                        <JsonBlock title="请求头" value={step.request.headers} />
                        <JsonBlock title="请求参数" value={step.request.params} />
                        <JsonBlock title="响应参数" value={step.response} />
                        <JsonBlock title="断言" value={step.assertion} />
                        <JsonBlock title="提取参数" value={step.extract} />
                        {step.error_message ? <JsonBlock title="错误信息" value={step.error_message} /> : null}
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
      <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono text-xs">
        {formatJsonValue(value)}
      </pre>
    </div>
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

function normalizeRunStatus(status: string | null | undefined): ApiRunStatus {
  const value = (status ?? "").toLowerCase();
  if (value === "passed" || value === "pass" || value === "success") return "passed";
  if (value === "failed" || value === "fail") return "failed";
  if (value === "error" || value === "broken") return "error";
  if (value === "skipped") return "skipped";
  return "pending";
}

function Breadcrumb({ project, trail, onJump }: { project: string; trail: Array<{ id: number; name: string }>; onJump: (index: number) => void }) {
  return <nav className="flex min-w-0 items-center gap-1 text-sm"><button className="font-medium hover:underline" onClick={() => onJump(-1)}>{project}</button>{trail.map((item, index) => <span key={item.id} className="flex min-w-0 items-center gap-1"><ChevronRight className="h-3.5 w-3.5 text-muted-foreground" /><button className="max-w-48 truncate hover:underline" onClick={() => onJump(index)}>{item.name}</button></span>)}</nav>;
}

function ModuleCard({ node, onOpen }: { node: ContentNode; onOpen: () => void }) {
  return <button type="button" onClick={onOpen} className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3 text-left hover:border-primary/40 hover:bg-accent/30"><Folder className="h-5 w-5 text-amber-500" /><span className="truncate text-sm font-medium">{node.name}</span><ChevronRight className="ml-auto h-4 w-4 text-muted-foreground" /></button>;
}

function Loading() { return <div className="flex items-center justify-center rounded-lg border py-10 text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />加载中…</div>; }
function Empty({ text }: { text: string }) { return <div className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">{text}</div>; }

function ApiCaseTable({ cases, moduleId, quickEdit, sessionId, selected, onSelected, onEdit, onRun, onDelete, onDiagnose, onShowRunDetail, onReorder, onSaved, newRows, onFirstInput, onRemoveNewRow }: {
  cases: ApiCase[];
  moduleId: number;
  quickEdit: boolean;
  sessionId: string | null;
  selected: Set<number>;
  onSelected: (next: Set<number>) => void;
  onEdit: (row: ApiCase) => void;
  onRun: (row: ApiCase) => void;
  onDelete: (row: ApiCase) => void;
  onDiagnose: (row: ApiCase) => void;
  onShowRunDetail: (row: ApiCase) => void;
  onReorder: (row: ApiCase, direction: "up" | "down") => void;
  onSaved: () => void;
  newRows: string[];
  onFirstInput: (rowId: string) => void;
  onRemoveNewRow: (rowId: string) => void;
}) {
  const allSelected = cases.length > 0 && cases.every((row) => selected.has(row.id));
  const gridClass = quickEdit
    ? "grid-cols-[36px_minmax(180px,1.1fr)_90px_minmax(180px,1.2fr)_minmax(220px,1.4fr)_minmax(220px,1.4fr)_90px_120px]"
    : "grid-cols-[36px_1.1fr_100px_1.5fr_120px_150px]";
  return <div className="overflow-x-auto rounded-lg border bg-card">
    <div className={cn("grid min-w-max items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs text-muted-foreground", gridClass)}>
      <Checkbox checked={allSelected} onCheckedChange={() => onSelected(allSelected ? new Set() : new Set(cases.map((row) => row.id)))} />
      <span>用例名称</span><span>方法</span><span>路径</span>
      {quickEdit ? <><span>请求头</span><span>请求参数</span><span>排序</span></> : <span>最近结果</span>}
      <span className="text-right">操作</span>
    </div>
    {cases.map((row, index) => quickEdit ? (
      <QuickEditRow key={row.id} row={row} sessionId={sessionId} checked={selected.has(row.id)} onChecked={() => { const next = new Set(selected); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); onSelected(next); }} onEdit={() => onEdit(row)} onDelete={() => onDelete(row)} onUp={() => index > 0 && onReorder(row, "up")} onDown={() => index < cases.length - 1 && onReorder(row, "down")} onSaved={onSaved} />
    ) : (
      <div key={row.id} className={cn("grid items-center gap-2 border-b px-3 py-2.5 text-sm last:border-b-0 hover:bg-muted/30", gridClass)}>
        <Checkbox checked={selected.has(row.id)} onCheckedChange={() => { const next = new Set(selected); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); onSelected(next); }} />
        <button className="flex min-w-0 items-center gap-2 text-left hover:underline" onClick={() => onEdit(row)}><FileText className="h-4 w-4 shrink-0 text-sky-500" /><span className="truncate">{row.name}</span></button>
        <Badge variant="outline" className="w-fit font-mono">{row.method ?? "GET"}</Badge>
        <span className="truncate font-mono text-xs" title={row.path ?? ""}>{row.path || "--"}</span>
        <StatusBadge
          status={row.latest_run?.status ?? "pending"}
          clickable={row.latest_run != null}
          onClick={() => row.latest_run && onShowRunDetail(row)}
        />
        <div className="flex justify-end gap-1"><Button variant="ghost" size="icon" className="h-8 w-8" title="运行" onClick={() => onRun(row)}><Play className="h-4 w-4" /></Button><Button variant="ghost" size="icon" className="h-8 w-8 text-primary" title="AI 分析执行结果" onClick={() => onDiagnose(row)}><Sparkles className="h-4 w-4" /></Button><Button variant="ghost" size="icon" className="h-8 w-8" title="编辑" onClick={() => onEdit(row)}><Pencil className="h-4 w-4" /></Button><Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" title="删除" onClick={() => onDelete(row)}><Trash2 className="h-4 w-4" /></Button></div>
      </div>
    ))}
    {quickEdit ? newRows.map((rowId, index) => (
      <QuickCreateRow
        key={rowId}
        moduleId={moduleId}
        sessionId={sessionId}
        isTrailing={index === newRows.length - 1}
        onFirstInput={() => onFirstInput(rowId)}
        onCreated={() => onRemoveNewRow(rowId)}
        onRemove={() => onRemoveNewRow(rowId)}
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

function QuickEditRow({ row, sessionId, checked, onChecked, onEdit, onDelete, onUp, onDown, onSaved }: {
  row: ApiCase; sessionId: string | null; checked: boolean; onChecked: () => void; onEdit: () => void; onDelete: () => void; onUp: () => void; onDown: () => void; onSaved: () => void;
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
      await apiCasesApi.update(row.id, { module_id: row.module_id, name: name.trim(), method, path, headers, params }, sessionId ?? undefined);
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
    <div className="flex justify-end gap-1"><Button variant="ghost" size="icon" className="h-8 w-8" disabled={!dirty || saving} onClick={save}>{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}</Button><Button variant="ghost" size="icon" className="h-8 w-8" onClick={onEdit}><Pencil className="h-4 w-4" /></Button><Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={onDelete}><Trash2 className="h-4 w-4" /></Button></div>
  </div>;
}

function QuickCreateRow({ moduleId, sessionId, isTrailing, onFirstInput, onCreated, onRemove, onSaved }: { moduleId: number; sessionId: string | null; isTrailing: boolean; onFirstInput: () => void; onCreated: () => void; onRemove: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [method, setMethod] = useState("GET");
  const [path, setPath] = useState("");
  const [headers, setHeaders] = useState("");
  const [params, setParams] = useState("");
  const [saving, setSaving] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);
  const spawnedRef = useRef(false);
  const hasContent = Boolean(name.trim() || path.trim() || headers.trim() || params.trim());
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
      await apiCasesApi.create({ module_id: moduleId, name: name.trim() || "未命名 API 用例", method, path, headers, params, case_type: "api" }, sessionId ?? undefined);
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
    <Input className="h-8" value={name} onChange={(event) => { setName(event.target.value); markInput(); }} placeholder="输入任意内容后自动追加下一行" />
    <Select value={method} onValueChange={(value) => { setMethod(value); markInput(); }}><SelectTrigger className="h-8"><SelectValue /></SelectTrigger><SelectContent>{METHODS.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select>
    <Input className="h-8 font-mono text-xs" value={path} onChange={(event) => { setPath(event.target.value); markInput(); }} placeholder="/api/example" />
    <Input className="h-8 font-mono text-xs" value={headers} onChange={(event) => { setHeaders(event.target.value); markInput(); }} placeholder='{"Authorization":"Bearer ${token}"}' />
    <Input className="h-8 font-mono text-xs" value={params} onChange={(event) => { setParams(event.target.value); markInput(); }} placeholder='{"key":"value"}' />
    <span className="text-xs text-muted-foreground">新增到末尾</span>
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
  firstModel,
  onInvalidate,
  onEditCase,
  onClose,
}: {
  open: boolean;
  quickEdit: boolean;
  moduleId: number | null;
  firstModel: string;
  onInvalidate: () => void;
  onEditCase: (row: ApiCase) => void;
  onClose: () => void;
}) {
  const analysisAbortRef = useRef<AbortController | null>(null);
  const editQuery = useQuery({
    queryKey: ["api-edit-history", moduleId],
    queryFn: () => apiCasesApi.editHistory(moduleId!),
    enabled: open && quickEdit && moduleId != null,
    staleTime: 0,
  });
  const testQuery = useQuery({
    queryKey: ["api-test-history", moduleId],
    queryFn: () => apiCasesApi.testHistory(moduleId!),
    enabled: open && !quickEdit && moduleId != null,
    staleTime: 0,
  });
  const loading = quickEdit ? editQuery.isLoading : testQuery.isLoading;
  const [analysis, setAnalysis] = useState<ReportAnalysisState | null>(null);
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

  const analyzeReport = async (reportId: number) => {
    analysisAbortRef.current?.abort();
    const controller = new AbortController();
    analysisAbortRef.current = controller;
    setAnalysis({ reportId, stage: "collecting", preview: null });
    try {
      const preview = await reportsApi.analysisPreview(reportId, controller.signal);
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

  const applyAssertionSuggestion = async (suggestion: ReportAnalysisSuggestion) => {
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
      await apiCasesApi.update(detail.id, {
        module_id: detail.module_id,
        name: detail.name,
        assertion: JSON.stringify(assertionRulesToLegacyMap(nextAssertion.steps), null, 2),
        steps: nextAssertion.steps,
        case_type: "api",
      });
      toast.success("已应用断言，重新运行后可验证效果");
      onInvalidate();
    } catch (error) {
      toast.error(messageOf(error));
    }
  };

  const applyExtractSuggestion = async (suggestion: ReportAnalysisSuggestion) => {
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
      await apiCasesApi.update(detail.id, {
        module_id: detail.module_id,
        name: detail.name,
        extract_data: JSON.stringify(extractRulesToLegacyMap(nextExtract.steps), null, 2),
        steps: nextExtract.steps,
        case_type: "api",
      });
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
    for (const item of applicable) {
      await applyAssertionSuggestion(item);
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
    for (const item of applicable) {
      await applyExtractSuggestion(item);
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
      const list = await apiCasesApi.list({ moduleId: moduleIdForAction, pageSize: 0 });
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

  return (
    <>
      <Dialog open={open} onOpenChange={(value) => !value && onClose()}><DialogContent className="max-w-4xl"><DialogHeader><DialogTitle>{quickEdit ? "编辑记录" : "测试记录"}</DialogTitle><DialogDescription>{quickEdit ? "同一次快速编辑中的改动按会话聚合。" : "API 自动执行结果按报告聚合。点报告右侧「AI 全面分析」可批量诊断所有用例。"}</DialogDescription></DialogHeader><div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">{loading ? <Loading /> : quickEdit ? <EditRecords records={editQuery.data ?? []} onRollback={rollbackRecord} /> : <TestRecords reports={testQuery.data ?? []} onAnalyze={analyzeReport} />}</div><DialogFooter><Button variant="outline" onClick={onClose}>关闭</Button></DialogFooter></DialogContent></Dialog>
      <ReportAnalysisDialog
        state={analysis}
        run={aiRunQuery.data ?? null}
        onApplyAssertion={applyAssertionSuggestion}
        onApplyAssertions={applyAssertionSuggestions}
        onApplyExtract={applyExtractSuggestion}
        onApplyExtracts={applyExtractSuggestions}
        onApplyOrder={applyOrderSuggestion}
        onEditCase={openSuggestedCase}
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
  onEditCase,
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
  onEditCase: (caseId: number | null) => Promise<void>;
  onCancel: () => void;
  onClose: () => void;
}) {
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
      <DialogContent className="max-w-4xl">
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
          <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
            <AnalysisStageBar stage={stage} />
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="font-medium">规则诊断已输出</span>
              <span className="text-xs text-muted-foreground">用例 {output?.summary.total_cases ?? 0}</span>
              <span className="text-xs text-muted-foreground">建议 {output?.summary.total_suggestions ?? 0}</span>
              <span className="text-xs text-emerald-700">高置信 {high}</span>
              <span className="text-xs text-amber-700">需审核 {review}</span>
              <span className="text-xs text-red-700">需人工 {manual}</span>
              {autoOrder.length > 0 ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto h-7 px-2 text-xs"
                  onClick={async () => {
                    for (const item of autoOrder) await onApplyOrder(item);
                  }}
                >
                  <ArrowUp className="h-3.5 w-3.5" /> 应用全部顺序调整（{autoOrder.length}）
                </Button>
              ) : null}
              {autoExtract.length > 0 ? (
                <Button
                  size="sm"
                  variant="outline"
                  className={autoOrder.length === 0 ? "ml-auto h-7 px-2 text-xs" : "h-7 px-2 text-xs"}
                  onClick={() => void onApplyExtracts(autoExtract)}
                >
                  <Save className="h-3.5 w-3.5" /> 应用全部提取修复（{autoExtract.length}）
                </Button>
              ) : null}
              {autoApplicable.length > 0 ? (
                <Button
                  size="sm"
                  className={autoOrder.length === 0 && autoExtract.length === 0 ? "ml-auto h-7 px-2 text-xs" : "h-7 px-2 text-xs"}
                  onClick={() => void onApplyAssertions(autoApplicable)}
                >
                  <Save className="h-3.5 w-3.5" /> 应用全部高置信断言（{autoApplicable.length}）
                </Button>
              ) : null}
            </div>
            {aiOutput?.ai_summary ? (
              <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-3 text-xs leading-relaxed">{aiOutput.ai_summary}</pre>
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
                onEditCase={onEditCase}
              />
            ))}
          </div>
        )}
        <DialogFooter>
          {cancellable ? <Button variant="destructive" onClick={onCancel}>中断分析</Button> : null}
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
  onEditCase,
}: {
  group: ReturnType<typeof groupAnalysisSuggestions>[number];
  onApplyAssertion: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onApplyExtract: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onApplyOrder: (suggestion: ReportAnalysisSuggestion) => Promise<void>;
  onEditCase: (caseId: number | null) => Promise<void>;
}) {
  const [busy, setBusy] = useState<"apply" | "extract" | "order" | "edit" | null>(null);
  const runAction = async (kind: "apply" | "extract" | "order" | "edit", item?: ReportAnalysisSuggestion) => {
    setBusy(kind);
    try {
      if (kind === "apply" && item) await onApplyAssertion(item);
      else if (kind === "extract" && item) await onApplyExtract(item);
      else if (kind === "order" && item) await onApplyOrder(item);
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
    const map = new Map<string, ApiCaseEditRecord[]>();
    records.forEach((record) => { const key = record.session_id ?? `single-${record.id}`; map.set(key, [...(map.get(key) ?? []), record]); });
    return [...map.entries()].map(([key, items]) => ({ key, items, time: Math.max(...items.map((item) => new Date(item.created_at).getTime())) })).sort((a, b) => b.time - a.time);
  }, [records]);
  if (!groups.length) return <Empty text="该模块还没有编辑记录" />;
  return <>{groups.map((group) => { const counts = { create: 0, update: 0, delete: 0 }; group.items.forEach((item) => { counts[item.action] += 1; }); const canRollbackBatch = group.items.filter((item) => item.rollback_available && item.batch_id).length > 1; return <details key={group.key} className="rounded-lg border bg-card px-3 py-2"><summary className="cursor-pointer list-none"><div className="flex items-center gap-3"><span className="font-medium">{formatTime(new Date(group.time).toISOString())} 编辑记录</span><span className="ml-auto text-xs text-muted-foreground">新增 {counts.create} · 修改 {counts.update} · 删除 {counts.delete}</span></div></summary><div className="mt-2 divide-y border-t">{group.items.map((item) => <div key={item.id} className="py-2 text-sm"><div className="flex items-center gap-2"><Badge variant="outline">{item.action}</Badge><span>{item.case_name}</span>{item.rollback_status && item.rollback_status !== "none" ? <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">已回滚</span> : item.rollback_available ? <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">可回滚</span> : null}<span className="ml-auto text-xs text-muted-foreground">{item.operator ?? "System"}</span>{item.rollback_available && item.batch_id ? <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => onRollback(item)}>回滚</Button> : null}{canRollbackBatch && item.rollback_available && item.batch_id ? <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => onRollback(item, true)}>整次回滚</Button> : null}</div>{item.changes.length ? <ul className="mt-1 space-y-0.5 pl-16 text-xs text-muted-foreground">{item.changes.map((change, index) => <li key={`${change.field}-${index}`}>{change.field}：<span className="line-through">{String(change.old || "空")}</span> → <span className="text-foreground">{String(change.new || "空")}</span></li>)}</ul> : null}</div>)}</div></details>; })}</>;
}
