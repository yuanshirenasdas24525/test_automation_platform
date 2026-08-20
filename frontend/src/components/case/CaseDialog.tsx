import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronRight, Info, Loader2, Lock, LockOpen, X } from "lucide-react";
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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { HighlightedInput, HighlightedTextarea } from "@/components/ui/highlighted-textarea";
import { StepEditor, validateStepsForCategory } from "@/components/case/step-editor";
import { casesApi, configApi } from "@/lib/api";
import { isCaseLocked } from "@/lib/case-manual-adjustment";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";
import {
  JsonValidateButton,
  checkAssertion,
  checkExtract,
  checkHeaders,
  checkJson,
  toFieldText,
  type JsonCheck,
} from "@/lib/json-field";
import { manualAdjustmentInfo } from "@/lib/case-manual-adjustment";
import type {
  CaseType,
  ContentNode,
  TestCaseHookDraft,
  TestStepDraft,
} from "@/types/domain";

const HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"] as const;

const caseSchema = z.object({
  name: z.string().trim().min(1, "请输入用例名").max(100, "最多 100 字"),
  description: z.string().max(10000, "最多 10000 字").optional(),
  method: z.string().optional(),
  path: z.string().optional(),
  headers: z.string().optional(),
  data_type: z.string().optional(),
  params: z.string().optional(),
  extract_data: z.string().optional(),
  assertion: z.string().optional(),
  sql_query: z.string().optional(),
  target_db_group: z.string().optional(),
  wait_time: z.coerce.number().int().min(0).max(3600).optional(),
  repeat_count: z.coerce.number().int().min(1).max(100).optional(),
  skip: z.boolean().optional(),
  steps: z.array(z.any()).optional(),
  pre_hook: z.array(z.any()).optional(),
  case_type: z.string().optional(),
  variables: z.record(z.unknown()).optional(),
});

export type CaseFormValues = z.infer<typeof caseSchema>;

function parseJsonMap(raw: unknown): Record<string, unknown> | null {
  if (raw == null || raw === "") return null;
  if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!text) return null;
  try {
    const parsed = JSON.parse(text) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

function extractRulesToConfigText(rules: TestStepDraft["extract"]): string {
  if (!Array.isArray(rules) || rules.length === 0) return "";
  const map: Record<string, unknown> = {};
  for (const rule of rules) {
    const name = String(rule?.name ?? "").trim();
    const source = String(rule?.from ?? "response.body").toLowerCase();
    const expression = source === "value"
      ? rule?.value
      : rule?.jsonpath ?? rule?.path;
    if (name && expression !== undefined && expression !== null) {
      map[name] = expression;
    }
  }
  return Object.keys(map).length ? JSON.stringify(map, null, 2) : "";
}

function assertionRulesToConfigText(rules: TestStepDraft["assertion"]): string {
  if (!Array.isArray(rules) || rules.length === 0) return "";
  const map: Record<string, unknown> = {};
  for (const rule of rules) {
    const target = String(rule?.target ?? "").trim();
    if (!target) continue;
    const type = String(rule?.type ?? "").toLowerCase();
    if (type === "is_not_null" || type === "not_empty" || type === "not_null") {
      map[target] = "not_empty";
    } else if (type === "is_null" || type === "null") {
      map[target] = "is_null";
    } else {
      map[target] = rule?.expected;
    }
  }
  return Object.keys(map).length ? JSON.stringify(map, null, 2) : "";
}

function extractConfigToRules(raw: unknown): TestStepDraft["extract"] {
  const map = parseJsonMap(raw);
  if (!map) return [];
  return Object.entries(map)
    .map(([name, expression]) => {
      const text = typeof expression === "string" ? expression.trim() : "";
      const isJsonPath = text.startsWith("$") && !text.startsWith("${");
      return isJsonPath
        ? { name, from: "response.body", jsonpath: expression }
        : { name, from: "value", value: expression };
    })
    .filter((rule) => rule.name.trim());
}

function assertionConfigToRules(raw: unknown): TestStepDraft["assertion"] {
  const map = parseJsonMap(raw);
  if (!map) return [];
  const rules: Record<string, unknown>[] = [];
  for (const [target, expected] of Object.entries(map)) {
    const key = target.trim();
    const marker = typeof expected === "string" ? expected.trim().toLowerCase() : "";
    if (!key) continue;
    if (["not_empty", "not_null", "notempty", "notnull", "@notempty", "@notnull", "非空"].includes(marker)) {
      rules.push({ type: "is_not_null", target: key, expected: null });
    } else if (["is_null", "null", "@null", "为空", "空"].includes(marker)) {
      rules.push({ type: "is_null", target: key, expected: null });
    } else {
      rules.push({
        type: key.startsWith("$") ? "jsonpath" : "equal",
        target: key,
        expected,
      });
    }
  }
  return rules;
}

function hydrateHttpStepConfig(step: TestStepDraft): TestStepDraft {
  if (step.step_type !== "http_request") return step;
  const config = { ...(step.config || {}) };
  const extractText = extractRulesToConfigText(step.extract);
  const assertionText = assertionRulesToConfigText(step.assertion);
  if (extractText) config.extract_data = extractText;
  if (assertionText) config.assertion = assertionText;
  return { ...step, config };
}

function normalizeHttpStepForSubmit(step: TestStepDraft): TestStepDraft {
  if (step.step_type !== "http_request") return step;
  const config = { ...(step.config || {}) };
  const extract = extractConfigToRules(config.extract_data);
  const assertion = assertionConfigToRules(config.assertion);
  delete config.extract_data;
  delete config.assertion;
  return {
    ...step,
    config,
    extract,
    assertion,
  };
}

function hookToStepDraft(hook: TestCaseHookDraft, index: number): TestStepDraft {
  const config = { ...(hook.config || {}) };
  const rawExtract = config.extract_data ?? config.extract;
  const rawAssertion = config.assertion;
  const extract = Array.isArray(rawExtract)
    ? rawExtract as Record<string, unknown>[]
    : extractConfigToRules(rawExtract);
  const assertion = Array.isArray(rawAssertion)
    ? rawAssertion as Record<string, unknown>[]
    : assertionConfigToRules(rawAssertion);
  delete config.extract;
  return hydrateHttpStepConfig({
    step_order: index,
    step_name: hook.step_name || `前置步骤 ${index + 1}`,
    step_type: hook.type || "http_request",
    skip: hook.skip ?? false,
    config,
    extract,
    assertion,
    wait_before: hook.wait_before ?? 0,
    timeout: hook.timeout ?? 30,
    retry: hook.retry ?? 0,
    on_failure: hook.on_failure ?? "stop",
  });
}

function stepDraftToHook(step: TestStepDraft): TestCaseHookDraft {
  const normalized = normalizeHttpStepForSubmit(step);
  const config = { ...(normalized.config || {}) };
  const extractMap = parseJsonMap(extractRulesToConfigText(normalized.extract));
  const assertionMap = parseJsonMap(assertionRulesToConfigText(normalized.assertion));
  delete config.extract_data;
  delete config.assertion;
  if (extractMap) config.extract_data = extractMap;
  if (assertionMap) config.assertion = assertionMap;
  return {
    type: normalized.step_type,
    step_name: normalized.step_name,
    skip: normalized.skip ?? false,
    wait_before: normalized.wait_before ?? 0,
    timeout: normalized.timeout ?? 30,
    retry: normalized.retry ?? 0,
    on_failure: normalized.on_failure ?? "stop",
    config,
  };
}

function buildSingleHttpStep(values: CaseFormValues): TestStepDraft {
  return {
    step_order: 0,
    step_name: values.name || "API 请求",
    step_type: "http_request",
    skip: false,
    config: {
      method: (values.method || "GET").toUpperCase(),
      path: values.path || "",
      headers: values.headers || "",
      data_type: values.data_type || "application/json",
      params: values.params || "",
      sql_query: values.sql_query || "",
      target_db_group: values.target_db_group || "",
    },
    extract: extractConfigToRules(values.extract_data),
    assertion: assertionConfigToRules(values.assertion),
    wait_before: Number(values.wait_time || 0),
    timeout: 60,
    retry: 0,
    on_failure: "stop",
  };
}

export function CaseDialog({
  projectId,
  state,
  category,
  onClose,
  onSubmit,
  submitting,
  onLockChanged,
}: {
  projectId: number;
  state:
    | { mode: "create"; moduleId: number; insertAt?: number }
    | { mode: "edit"; node: ContentNode }
    | null;
  category: CaseType;
  onClose: () => void;
  onSubmit: (values: CaseFormValues) => void;
  submitting: boolean;
  onLockChanged?: () => void;
}) {
  const existing = state?.mode === "edit" ? state.node : null;
  const [caseLocked, setCaseLocked] = useState(false);
  const [lockBusy, setLockBusy] = useState(false);
  useEffect(() => {
    setCaseLocked(existing ? isCaseLocked(existing) : false);
  }, [existing]);
  const toggleCaseLock = async () => {
    if (!existing) return;
    const next = !caseLocked;
    setLockBusy(true);
    try {
      await casesApi.setLock(existing.id, next);
      setCaseLocked(next);
      onLockChanged?.();
      toast.success(next ? "已锁定" : "已解锁");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "操作失败");
    } finally {
      setLockBusy(false);
    }
  };

  const caseDetailQuery = useQuery({
    queryKey: existing ? queryKeys.case(existing.id) : ["case", -1],
    queryFn: () => casesApi.get(existing!.id),
    enabled: !!existing && state?.mode === "edit",
    staleTime: 0,
    refetchOnMount: "always",
  });

  const detail = caseDetailQuery.data;
  const manualAdjustment = manualAdjustmentInfo(detail ?? existing ?? {});

  const databaseConnectionsQuery = useQuery({
    queryKey: ["database-connections", projectId],
    queryFn: () => configApi.databaseConnections(projectId),
    enabled: state !== null && category === "api",
  });
  const databaseConnections = useMemo(
    () => databaseConnectionsQuery.data ?? [],
    [databaseConnectionsQuery.data],
  );

  const defaults = useMemo<CaseFormValues>(() => {
    if (existing) {
      const src = detail ?? (existing as unknown as Record<string, unknown>);
      const steps = ((detail?.steps as TestStepDraft[] | undefined) ?? []).map(hydrateHttpStepConfig);
      // API 用例以 steps 为唯一执行来源。只有一个 HTTP step 时，单请求编辑器必须从
      // 该 step 回填，不能继续依赖可能为空或已过期的用例顶层兼容字段。
      const singleHttpStep = category === "api" && steps.length === 1 && steps[0].step_type === "http_request"
        ? steps[0]
        : null;
      const singleConfig = singleHttpStep?.config ?? {};
      return {
        name: toFieldText(src.name),
        description: toFieldText(src.description),
        method: toFieldText(singleConfig.method ?? src.method) || (category === "api" ? "GET" : ""),
        path: toFieldText(singleConfig.path ?? src.path),
        headers: toFieldText(singleConfig.headers ?? src.headers),
        data_type: toFieldText(singleConfig.data_type ?? src.data_type) || "application/json",
        // 生成用例的请求体在 config.json（新版分区式，runner 从这里读）；手工单请求
        // 编辑器历史上写进 config.params（遗留式）。回显时优先 json，兼容旧的 params，
        // 否则会读空只显示 placeholder（看着像"参数丢了"）。
        params: toFieldText(singleConfig.json ?? singleConfig.body ?? singleConfig.params ?? src.params),
        extract_data: toFieldText(singleConfig.extract_data ?? src.extract_data),
        assertion: toFieldText(singleConfig.assertion ?? src.assertion),
        sql_query: toFieldText(singleConfig.sql_query ?? src.sql_query),
        target_db_group: toFieldText(singleConfig.target_db_group),
        wait_time: singleHttpStep?.wait_before ?? (src.wait_time as number) ?? 0,
        repeat_count: (src.repeat_count as number) ?? 1,
        skip: (src.skip as boolean) ?? false,
        steps,
        pre_hook: ((src.pre_hook as TestCaseHookDraft[] | undefined) ?? []).map(hookToStepDraft),
        case_type: (src.case_type as string | undefined) ?? category,
        variables: (src.variables as Record<string, unknown> | null | undefined) ?? {},
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
      target_db_group: "",
      wait_time: 0,
      repeat_count: 1,
      skip: false,
      steps: [],
      pre_hook: [],
      case_type: category,
      variables: {},
    };
  }, [existing, detail, category]);

  const form = useForm<CaseFormValues>({
    resolver: zodResolver(caseSchema),
    defaultValues: defaults,
    values: defaults,
  });

  const isApi = category === "api";

  useEffect(() => {
    if (!isApi || databaseConnectionsQuery.isLoading) return;
    const current = form.getValues("target_db_group") || "";
    const stillExists = databaseConnections.some((item) => item.name === current);
    const next = stillExists ? current : (databaseConnections[0]?.name ?? "");
    if (next !== current) form.setValue("target_db_group", next);
  }, [databaseConnections, databaseConnectionsQuery.isLoading, form, isApi, existing?.id]);

  const isStepEditorCase = category === "web" || category === "android" || category === "ios" || category === "mixed";
  const currentSteps = (form.watch("steps") as TestStepDraft[] | undefined) ?? [];
  const currentPreHooks = (form.watch("pre_hook") as TestStepDraft[] | undefined) ?? [];
  const [apiMode, setApiMode] = useState<"single" | "multi">("single");
  const [preHooksOpen, setPreHooksOpen] = useState(false);
  const detailStepCount = (detail?.steps as TestStepDraft[] | undefined)?.length ?? 0;
  const detailPreHookCount = detail?.pre_hook?.length ?? 0;
  const loadingDetail = !!existing && caseDetailQuery.isLoading;

  useEffect(() => {
    if (!isApi || loadingDetail) return;
    setApiMode(detailStepCount > 1 ? "multi" : "single");
  }, [isApi, loadingDetail, detailStepCount, existing?.id, state?.mode]);

  useEffect(() => {
    if (loadingDetail) return;
    setPreHooksOpen(detailPreHookCount > 0);
  }, [loadingDetail, detailPreHookCount, existing?.id, state?.mode]);

  const title =
    state?.mode === "edit"
      ? "编辑用例"
      : state?.mode === "create" && state.insertAt !== undefined
        ? "在此处插入用例"
        : "新建用例";

  // 加解密开关（三态）：写用例变量 rel_crypto。default=不写(跟随全局策略)/on=1/off=0
  const relCryptoRaw = (form.watch("variables") as Record<string, unknown> | undefined)?.rel_crypto;
  const relCryptoMode = (() => {
    if (relCryptoRaw === undefined || relCryptoRaw === null || relCryptoRaw === "") return "default";
    return ["1", "true", "on", "yes", "y"].includes(String(relCryptoRaw).trim().toLowerCase())
      ? "on"
      : "off";
  })();
  const setRelCryptoMode = (mode: string) => {
    const cur = { ...((form.watch("variables") as Record<string, unknown> | undefined) ?? {}) };
    if (mode === "default") delete cur.rel_crypto;
    else cur.rel_crypto = mode === "on" ? "1" : "0";
    form.setValue("variables", cur, { shouldDirty: true });
  };

  return (
    <Dialog open={state !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[92vh] w-[calc(100vw-2rem)] max-w-[1120px] overflow-y-auto p-4 sm:p-6">
        <DialogHeader>
          <div className="flex items-center justify-between gap-3 pr-8">
            <DialogTitle>{title}</DialogTitle>
            {existing ? (
              <Button
                type="button"
                variant={caseLocked ? "secondary" : "outline"}
                size="sm"
                disabled={lockBusy}
                onClick={toggleCaseLock}
                className="shrink-0"
              >
                {caseLocked ? <Lock className="mr-1.5 h-4 w-4" /> : <LockOpen className="mr-1.5 h-4 w-4" />}
                {caseLocked ? "已锁定 · 点击解锁" : "锁定用例"}
              </Button>
            ) : null}
          </div>
          <DialogDescription>
            用例的必填字段是名称。HTTP 相关字段只有 API 项目需要填。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => {
            const preHookSteps = (values.pre_hook as TestStepDraft[] | undefined) ?? [];
            const normalizedPreHooks = preHookSteps.map(stepDraftToHook);
            const valuesWithHooks = {
              ...values,
              pre_hook: normalizedPreHooks as unknown as CaseFormValues["pre_hook"],
            };
            if (isStepEditorCase || (isApi && apiMode === "multi")) {
              const stepValidationErrors = validateStepsForCategory(
                category,
                ((values.steps as TestStepDraft[] | undefined) ?? []),
              );
              if (stepValidationErrors.length > 0) {
                form.setError("steps", { type: "validate", message: stepValidationErrors[0] });
                return;
              }
            }

            if (!isApi) {
              onSubmit(valuesWithHooks);
              return;
            }

            const editorSteps = (values.steps as TestStepDraft[] | undefined) ?? [];
            const normalized = editorSteps.map(normalizeHttpStepForSubmit).map((step) => {
              if (step.step_type !== "http_request") return step;
              const selected = String(step.config?.target_db_group ?? "");
              const valid = databaseConnections.some((item) => item.name === selected);
              return {
                ...step,
                config: {
                  ...step.config,
                  target_db_group: valid ? selected : (databaseConnections[0]?.name ?? ""),
                },
              };
            });
            const finalSteps = apiMode === "multi" ? normalized : [buildSingleHttpStep(values)];
            onSubmit({
              ...valuesWithHooks,
              steps: finalSteps as unknown as CaseFormValues["steps"],
            });
          })}
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
            <Textarea
              id="case-desc"
              rows={4}
              className="max-h-48 resize-y"
              {...form.register("description")}
            />
            <div className="text-right text-[11px] text-muted-foreground">
              {(form.watch("description") ?? "").length}/10000
            </div>
            {form.formState.errors.description ? (
              <p className="text-xs text-destructive">
                {form.formState.errors.description.message}
              </p>
            ) : null}
          </div>

          {manualAdjustment.pending ? (
            <div className="space-y-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
              <div className="font-medium">需人工调整 · 当前已跳过执行</div>
              {manualAdjustment.reasons.length ? (
                <ul className="list-disc space-y-1 pl-5 text-xs">
                  {manualAdjustment.reasons.map((reason, index) => (
                    <li key={index}>{reason}</li>
                  ))}
                </ul>
              ) : null}
              <label className="flex cursor-pointer items-start gap-2 rounded border border-red-200 bg-background/80 p-2 text-xs text-foreground">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-primary"
                  checked={!form.watch("skip")}
                  onChange={(event) =>
                    form.setValue("skip", !event.target.checked, { shouldDirty: true })
                  }
                />
                <span>
                  <span className="font-medium">我已完成调整，保存后启用该用例</span>
                  <span className="mt-0.5 block text-muted-foreground">
                    请先修正上面的请求、依赖变量或断言；勾选后本次保存会解除标记并允许执行。
                  </span>
                </span>
              </label>
            </div>
          ) : (
            <label className="flex w-fit cursor-pointer items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                className="h-4 w-4 accent-primary"
                checked={form.watch("skip") ?? false}
                onChange={(event) =>
                  form.setValue("skip", event.target.checked, { shouldDirty: true })
                }
              />
              跳过执行
            </label>
          )}

          {isApi ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Label className="text-muted-foreground">加解密</Label>
              <Select value={relCryptoMode} onValueChange={setRelCryptoMode}>
                <SelectTrigger className="h-8 w-[220px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">默认（跟随全局策略）</SelectItem>
                  <SelectItem value="on">强制加密（rel_crypto=1）</SelectItem>
                  <SelectItem value="off">强制不加密（rel_crypto=0）</SelectItem>
                </SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground">
                写入用例变量 rel_crypto，覆盖配置中心的全局/模块策略
              </span>
            </div>
          ) : null}

          {isApi ? (
            <div className="rounded-md border border-amber-300/70 bg-amber-50/40 p-3 dark:bg-amber-950/10">
              <button
                type="button"
                className="flex w-full items-center justify-between gap-3 text-left"
                onClick={() => setPreHooksOpen((value) => !value)}
              >
                <span>
                  <span className="text-sm font-medium">前置步骤（{currentPreHooks.length}）</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    在主步骤前执行，执行内容会完整显示在 Allure 中
                  </span>
                </span>
                {preHooksOpen ? (
                  <ChevronDown className="h-4 w-4 shrink-0" />
                ) : (
                  <ChevronRight className="h-4 w-4 shrink-0" />
                )}
              </button>
              {preHooksOpen ? (
                <div className="mt-3 border-t pt-3">
                  <p className="mb-3 text-xs text-muted-foreground">
                    可修改、排序、跳过或删除。删除全部并保存后，本用例将不再执行任何隐藏前置请求。
                  </p>
                  <StepEditor
                    projectId={projectId}
                    category="api"
                    databaseConnections={databaseConnections}
                    value={currentPreHooks}
                    error={form.formState.errors.pre_hook?.message}
                    onChange={(next) =>
                      form.setValue(
                        "pre_hook",
                        next as unknown as CaseFormValues["pre_hook"],
                        { shouldDirty: true },
                      )
                    }
                  />
                </div>
              ) : null}
            </div>
          ) : null}

          {isApi ? (
            <div className="flex justify-center">
              <div className="inline-flex rounded-full bg-muted p-0.5">
                <button
                  type="button"
                  onClick={() => setApiMode("single")}
                  aria-pressed={apiMode === "single"}
                  className={cn(
                    "rounded-full px-6 py-1.5 text-sm transition-colors",
                    apiMode === "single"
                      ? "bg-background font-medium text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  单请求
                </button>
                <button
                  type="button"
                  onClick={() => setApiMode("multi")}
                  aria-pressed={apiMode === "multi"}
                  className={cn(
                    "rounded-full px-6 py-1.5 text-sm transition-colors",
                    apiMode === "multi"
                      ? "bg-background font-medium text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  多步骤
                </button>
              </div>
            </div>
          ) : null}

          {isStepEditorCase || (isApi && apiMode === "multi") ? (
            <div className="space-y-1.5">
              <Label htmlFor="case-repeat-standalone">重复次数</Label>
              <Input
                id="case-repeat-standalone"
                type="number"
                min={1}
                max={100}
                className="w-32"
                {...form.register("repeat_count")}
              />
              <p className="text-xs text-muted-foreground">
                填 N 则整条用例独立重复执行 N 次，每次在报告里各成一条（默认 1，不重复）。
                各次相互独立、不做跨次对比；需要对比多次请求的结果，请用多步骤用例。
              </p>
            </div>
          ) : null}

          {isApi && apiMode === "multi" ? (
            loadingDetail ? (
              <div className="flex items-center gap-2 rounded border border-dashed p-3 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在加载步骤…
              </div>
            ) : (
              <StepEditor
                projectId={projectId}
                category="api"
                databaseConnections={databaseConnections}
                value={currentSteps}
                error={form.formState.errors.steps?.message}
                onChange={(next) =>
                  form.setValue(
                    "steps",
                    next as unknown as CaseFormValues["steps"],
                    { shouldDirty: true },
                  )
                }
              />
            )
          ) : isApi ? (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-[140px_1fr]">
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
                  <HighlightedInput
                    id="case-path"
                    placeholder="/api/login"
                    {...form.register("path")}
                    value={form.watch("path") ?? ""}
                    onChange={(e) =>
                      form.setValue("path", e.target.value, { shouldDirty: true })
                    }
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="case-dtype">Content-Type</Label>
                  <Input
                    id="case-dtype"
                    placeholder="application/json"
                    {...form.register("data_type")}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="case-wait">等待秒数</Label>
                    <Input
                      id="case-wait"
                      type="number"
                      min={0}
                      {...form.register("wait_time")}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="case-repeat">重复次数</Label>
                    <Input
                      id="case-repeat"
                      type="number"
                      min={1}
                      max={100}
                      {...form.register("repeat_count")}
                    />
                  </div>
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

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
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
                  withJsonButton
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
                  withJsonButton
                />
              </div>

              <HighlightedField
                id="case-sql"
                label="SQL 校验"
                labelAddon={
                  <Select
                    disabled={databaseConnections.length <= 1}
                    value={form.watch("target_db_group") || undefined}
                    onValueChange={(value) =>
                      form.setValue("target_db_group", value, { shouldDirty: true })
                    }
                  >
                    <SelectTrigger className="h-7 w-56 text-xs">
                      <SelectValue placeholder={
                        databaseConnectionsQuery.isLoading
                          ? "正在加载数据库配置…"
                          : "未配置DB链接方式"
                      } />
                    </SelectTrigger>
                    <SelectContent>
                      {databaseConnections.map((connection) => (
                        <SelectItem key={connection.name} value={connection.name}>
                          {connection.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                }
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
          ) : isStepEditorCase ? (
            loadingDetail ? (
              <div className="flex items-center gap-2 rounded border border-dashed p-3 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在加载步骤…
              </div>
            ) : (
              <StepEditor
                projectId={projectId}
                category={category}
                databaseConnections={databaseConnections}
                value={currentSteps}
                error={form.formState.errors.steps?.message}
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

function HighlightedField({
  id,
  label,
  labelAddon,
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
  labelAddon?: ReactNode;
  hint?: ReactNode;
  rows?: number;
  placeholder?: string;
  value: string;
  onChange: (next: string) => void;
  check?: (text: string) => JsonCheck;
  withJsonButton?: boolean;
}) {
  const result: JsonCheck = check ? check(value) : { state: "empty" };
  const showError = result.state === "error" && value.trim().length > 0;
  const showOk = result.state === "ok";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
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
          {labelAddon}
        </div>
        {withJsonButton ? (
          <JsonValidateButton value={value} onChange={onChange} check={check} />
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
        <p className="flex items-start gap-1 text-[11px] text-muted-foreground">
          <Info className="mt-[1px] h-3 w-3 shrink-0 opacity-60" />
          <span>{hint}</span>
        </p>
      ) : null}
    </div>
  );
}

function Code({ children }: { children: ReactNode }) {
  return (
    <code className="mx-0.5 rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-foreground/80">
      {children}
    </code>
  );
}
