/**
 * 变更调整面板 —— 嵌入「AI 生成接口用例」抽屉的 Tab。
 *
 * 纯输入表单：本次变更 / 新增需求 + 接口文档（文件，支持多选）+ 接口文档链接 + 模型，
 * 提交后由 AI 产出增/改/删调整大纲（ops）；面板本身不再展示大纲或应用变更 —— 规划成功后
 * 通过 onPlanned 把 ops 转成的测试点交给「生成用例」Tab，走与普通生成一致的审阅 + 写入闭环
 * （新增/修改会经过详细用例生成 + 契约编译，删除则在写入阶段直接调 casesApi.remove）。
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Sparkles, Upload } from "lucide-react";

import { changeAdjustApi, aiModelsApi, ApiError } from "@/lib/api";
import type { AiOutlinePoint } from "@/types/domain";

/** 接口文档可接受的文件类型：Swagger/OpenAPI/Postman（json/yaml），或 Word/PDF/MD。 */
const DOC_ACCEPT = ".json,.yaml,.yml,.doc,.docx,.pdf,.md";

const ACTION_CATEGORY: Record<string, string> = {
  add: "新增",
  modify: "修改",
  delete: "删除",
};

function errMsg(e: unknown) {
  return e instanceof ApiError ? e.message : "操作失败";
}

/** 可嵌入的变更调整面板（不含抽屉外壳）。 */
export function ModuleOutlinePanel({
  moduleId,
  projectId,
  mode: _mode = "interface",
  onPlanned,
}: {
  moduleId: number | null;
  projectId: number;
  mode?: string;
  /** 规划成功后把 ops 转成的测试点交给外层「生成用例」视图。 */
  onPlanned?: (plan: {
    generationRunId: number;
    points: AiOutlinePoint[];
    apiContract: Record<string, unknown>;
    warnings: string[];
  }) => void;
}) {
  const [changeText, setChangeText] = useState("");
  const [modelName, setModelName] = useState("");
  const [docFiles, setDocFiles] = useState<File[]>([]);
  const [docLinks, setDocLinks] = useState("");

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
      if (data.ops.length === 0) {
        toast.info("没有需要调整的测试点");
        return;
      }
      const points: AiOutlinePoint[] = data.ops.map((op) => ({
        title: op.title,
        category: ACTION_CATEGORY[op.action] ?? "",
        action: op.action,
        target_case_id: op.target_case_id,
        endpoint: op.endpoint,
        reason: op.reason,
      }));
      onPlanned?.({
        generationRunId: data.generation_run_id,
        points,
        apiContract: data.api_contract ?? {},
        warnings: data.warnings,
      });
      toast.success(`已规划 ${points.length} 个测试点，已交给「生成用例」`);
    },
    onError: (e) => toast.error(errMsg(e)),
  });

  const submit = () => {
    if (moduleId == null) return;
    if (!effectiveModel) { toast.error("请先在 AI 模型配置里启用一个模型"); return; }
    if (!changeText.trim()) { toast.error("请填写本次变更 / 新增需求"); return; }
    previewMut.mutate();
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
    </div>
  );
}
