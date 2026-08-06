/**
 * 变更调整面板 —— 嵌入「AI 生成接口用例」抽屉的 Tab。
 *
 * 直接展示表单：本次变更 / 新增需求 + 接口文档（文件，支持多选）+ 接口文档链接 + 模型，
 * 提交后由 AI 产出增/改/删调整大纲（ops），勾选确认后应用到 test_cases。
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Sparkles, Upload } from "lucide-react";

import { changeAdjustApi, aiModelsApi, automationCasesApi, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ChangeOp, ChangePlan } from "@/types/domain";

/** 接口文档可接受的文件类型：Swagger/OpenAPI/Postman（json/yaml），或 Word/PDF/MD。 */
const DOC_ACCEPT = ".json,.yaml,.yml,.doc,.docx,.pdf,.md";

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "操作失败";
}

/** 可嵌入的变更调整面板（不含抽屉外壳）。 */
export function ModuleOutlinePanel({
  moduleId,
  projectId,
  mode: _mode = "interface",
  onApplied,
}: {
  moduleId: number | null;
  projectId: number;
  mode?: string;
  onApplied?: () => void;
}) {
  const [changeText, setChangeText] = useState("");
  const [modelName, setModelName] = useState("");
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [docLinks, setDocLinks] = useState("");
  const [plan, setPlan] = useState<ChangePlan | null>(null);
  // 被取消勾选的 op（默认全部勾选=写入；去勾=不写入）。适用于新增/修改/删除所有类型。
  const [unchecked, setUnchecked] = useState<Set<number>>(new Set());

  const modelsQuery = useQuery({
    queryKey: ["ai-models", projectId],
    queryFn: () => aiModelsApi.list(projectId),
  });
  const models = useMemo(() => (modelsQuery.data ?? []).filter((m) => m.enabled), [modelsQuery.data]);
  const effectiveModel = modelName || models.find((m) => m.is_default)?.name || models[0]?.name || "";

  const previewMut = useMutation({
    mutationFn: () =>
      changeAdjustApi.preview({
        moduleId: moduleId as number,
        changeText,
        modelName: effectiveModel,
        links: docLinks,
        files: docFiles,
      }),
    onSuccess: (data) => {
      setPlan(data);
      setUnchecked(new Set());
      if (data.ops.length === 0) toast.info("没有需要调整的测试点");
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const applyMut = useMutation({
    mutationFn: async () => {
      if (!plan) throw new Error("no plan");
      // 勾选=写入；去勾=跳过。delete 需同时进 selected 与 confirmed 才真删。
      const selectedOpIds = plan.ops.filter((op) => !unchecked.has(op.id)).map((op) => op.id);
      const confirmedDeleteIds = plan.ops
        .filter((op) => op.action === "delete" && !unchecked.has(op.id))
        .map((op) => op.id);
      const summary = await changeAdjustApi.apply({ planId: plan.plan_id, selectedOpIds, confirmedDeleteIds });
      // 写入后按 sort_order 统一重编号（与「生成用例」一致，幂等：先剥旧前缀再重排），避免编号重复。
      if (moduleId != null) {
        try {
          await automationCasesApi.renumber(moduleId, { enable: true, caseType: "api" });
        } catch {
          /* 重编号失败不阻断主流程，用例已写入 */
        }
      }
      return summary;
    },
    onSuccess: (summary) => {
      toast.success(`新增 ${summary.added} · 修改 ${summary.modified} · 删除 ${summary.deleted}`);
      if (summary.errors.length > 0) {
        toast.warning(`${summary.errors.length} 项处理失败：${summary.errors.map((e) => e.error).join("；")}`);
      }
      setPlan(null);
      onApplied?.();
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const submit = () => {
    if (moduleId == null) return;
    if (!effectiveModel) { toast.error("请先在 AI 模型配置里启用一个模型"); return; }
    if (!changeText.trim()) { toast.error("请填写本次变更 / 新增需求"); return; }
    previewMut.mutate();
  };

  const toggleOp = (opId: number) => {
    setUnchecked((prev) => {
      const next = new Set(prev);
      if (next.has(opId)) next.delete(opId);
      else next.add(opId);
      return next;
    });
  };

  return (
    <div className="flex flex-col gap-4">
      {/* 本次变更 / 新增需求 */}
      <div>
        <div className="mb-1.5 text-xs font-medium text-secondary-foreground">本次变更 / 新增需求</div>
        <textarea
          value={changeText}
          onChange={(e) => setChangeText(e.target.value)}
          rows={4}
          placeholder="例：新增“手机验证码登录”；登录失败超过 5 次锁定账号 15 分钟…"
          className="w-full resize-none rounded-md border border-input bg-background px-2.5 py-2 text-xs outline-none focus:ring-1 focus:ring-ring"
        />
      </div>

      {/* 接口文档（文件，支持多选） */}
      <div>
        <div className="mb-1.5 text-xs font-medium text-secondary-foreground">接口文档</div>
        <label className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-input px-3 py-2.5 text-xs text-muted-foreground hover:bg-accent">
          <Upload className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate">
            {docFiles.length > 0
              ? docFiles.map((f) => f.name).join("、")
              : "选择文件：Swagger / OpenAPI / Postman（.json/.yaml），或 Word / PDF / MD"}
          </span>
          <input
            type="file"
            accept={DOC_ACCEPT}
            multiple
            className="hidden"
            onChange={(e) => setDocFiles(Array.from(e.target.files ?? []))}
          />
        </label>
      </div>

      {/* 接口文档链接 */}
      <div>
        <div className="mb-1.5 text-xs font-medium text-secondary-foreground">接口文档链接</div>
        <textarea
          value={docLinks}
          onChange={(e) => setDocLinks(e.target.value)}
          rows={2}
          placeholder="Swagger UI / OpenAPI / 在线接口文档，多个换行或逗号分隔"
          className="w-full resize-none rounded-md border border-input bg-background px-2.5 py-2 text-xs outline-none focus:ring-1 focus:ring-ring"
        />
      </div>

      {/* 模型 */}
      <div>
        <div className="mb-1.5 text-xs font-medium text-secondary-foreground">模型</div>
        <select
          value={effectiveModel}
          onChange={(e) => setModelName(e.target.value)}
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs outline-none focus:ring-1 focus:ring-ring"
        >
          {models.length === 0 ? <option value="">（无可用模型）</option> : null}
          {models.map((m) => (
            <option key={m.name} value={m.name}>{m.name}（{m.model}）</option>
          ))}
        </select>
      </div>

      <div className="flex justify-end">
        <button
          onClick={submit}
          disabled={previewMut.isPending || moduleId == null}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-[13px] font-medium text-primary-foreground disabled:opacity-50"
        >
          {previewMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
          规划调整
        </button>
      </div>

      {/* 调整大纲预览 */}
      {plan ? (
        <div className="rounded-md border p-3">
          {plan.warnings.length > 0 ? (
            <div className="mb-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800">
              {plan.warnings.map((w, i) => (
                <div key={i}>{w}</div>
              ))}
            </div>
          ) : null}
          <ChangeOpsList ops={plan.ops} unchecked={unchecked} onToggle={toggleOp} />
          <div className="mt-3 flex items-center justify-end gap-2">
            <button onClick={() => setPlan(null)} className="rounded-md border border-input px-3.5 py-1.5 text-[13px] hover:bg-accent">
              取消
            </button>
            <button
              onClick={() => applyMut.mutate()}
              disabled={applyMut.isPending || plan.ops.length === 0}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-[13px] font-medium text-primary-foreground disabled:opacity-50"
            >
              {applyMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              应用变更
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = {
  add: "新增",
  modify: "修改",
  delete: "删除",
};

function ChangeOpsList({
  ops,
  unchecked,
  onToggle,
}: {
  ops: ChangeOp[];
  unchecked: Set<number>;
  onToggle: (opId: number) => void;
}) {
  if (ops.length === 0) {
    return <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">无变更。</div>;
  }
  const added = ops.filter((o) => o.action === "add").length;
  const modified = ops.filter((o) => o.action === "modify").length;
  const deleted = ops.filter((o) => o.action === "delete").length;
  const willWrite = ops.filter((o) => !unchecked.has(o.id)).length;
  return (
    <>
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-medium">调整大纲（勾选=写入，已选 {willWrite}/{ops.length}）</span>
        <span className="text-muted-foreground">+{added} · ~{modified} · −{deleted}</span>
      </div>
      <div className="overflow-hidden rounded-md border">
        {ops.map((op) => (
          <ChangeOpRow
            key={op.id}
            op={op}
            checked={!unchecked.has(op.id)}
            onToggle={onToggle}
          />
        ))}
      </div>
    </>
  );
}

function ChangeOpRow({
  op,
  checked,
  onToggle,
}: {
  op: ChangeOp;
  checked: boolean;
  onToggle: (opId: number) => void;
}) {
  const map: Record<string, { bg: string; fg: string }> = {
    add: { bg: "bg-green-50", fg: "text-green-800" },
    modify: { bg: "bg-amber-50", fg: "text-amber-800" },
    delete: { bg: "bg-red-50", fg: "text-red-800" },
  };
  const s = map[op.action] ?? map.add;
  return (
    <div className={cn("flex items-start gap-2 border-t px-3 py-2 text-[12px] first:border-t-0", s.bg, s.fg, !checked && "opacity-50")}>
      <input
        type="checkbox"
        checked={checked}
        onChange={() => onToggle(op.id)}
        className="mt-0.5 h-3.5 w-3.5 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={cn("font-medium", op.action === "delete" && "line-through")}>{op.title}</span>
          <span className="text-[10px] opacity-80">{ACTION_LABEL[op.action] ?? op.action}</span>
          {op.target_case_id != null ? (
            <span className="text-[10px] opacity-80">#{op.target_case_id}</span>
          ) : null}
          {op.endpoint ? (
            <span className="rounded bg-black/5 px-1 py-0.5 font-mono text-[10px] opacity-80">
              {op.endpoint.method} {op.endpoint.path}
            </span>
          ) : null}
        </div>
        {op.reason ? <div className="mt-0.5 text-[11px] opacity-80">{op.reason}</div> : null}
      </div>
    </div>
  );
}
