/**
 * M7 · AI 一键生成测试用例：触发表单
 *
 * 三个入口共用同一个 Dialog：
 *   - 分析文档 Viewer 内按钮 → 默认带入 analysisDocumentId
 *   - 需求行操作列按钮 / 列表批量勾选 → 不带 analysisDocumentId（后端走最新）
 *
 * 表单字段：
 *   - 模型多选（必填）
 *   - count_per_requirement（1..30，默认 5）
 *   - scenario_mix（三选一）
 *   - UI 截图：勾选已上传的 image attachment（kind=file 且 mime=image/*）
 *   - 补充说明 user_prompt
 *
 * 触发成功 → toast + onTriggered(batches) 给父组件，父组件负责打开 ReviewDialog。
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Eye, ImageIcon, Loader2, Sparkles } from "lucide-react";

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
import { Textarea } from "@/components/ui/textarea";
import {
  aiCaseGenerationApi,
  aiModelsApi,
  ApiError,
  attachmentsApi,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  Attachment,
  CaseGenerationBatch,
  CaseGenerationScenarioMix,
  Requirement,
} from "@/types/domain";


interface RequirementHandle {
  id: number;
  title?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** 要批量生成的需求列表。单需求场景传 1 个即可。 */
  requirements: RequirementHandle[] | Requirement[];
  /** 来自分析文档 Viewer 入口时传入，固定为该次生成使用的分析文档。 */
  analysisDocumentId?: number | null;
  onTriggered?: (batches: CaseGenerationBatch[]) => void;
}


const SCENARIO_OPTIONS: Array<{
  value: CaseGenerationScenarioMix;
  label: string;
  desc: string;
}> = [
  {
    value: "positive_only",
    label: "正向 only",
    desc: "只覆盖 happy path，速度最快",
  },
  {
    value: "positive_and_negative",
    label: "正向 + 异常",
    desc: "覆盖主流程 + 常见错误分支",
  },
  {
    value: "all_scenarios",
    label: "全场景",
    desc: "正向 / 异常 / 边界 / 安全 一锅端",
  },
];


function isImageAttachment(att: Attachment): boolean {
  if (att.kind !== "file") return false;
  const url = (att.url || "").toLowerCase();
  return (
    url.endsWith(".png") ||
    url.endsWith(".jpg") ||
    url.endsWith(".jpeg") ||
    url.endsWith(".gif") ||
    url.endsWith(".webp") ||
    url.endsWith(".bmp")
  );
}


export function CaseGenerationLauncherDialog({
  open,
  onClose,
  requirements,
  analysisDocumentId,
  onTriggered,
}: Props) {
  const modelsQuery = useQuery({
    queryKey: queryKeys.aiModels(),
    queryFn: () => aiModelsApi.list(),
    enabled: open,
  });

  // 单需求时拉 attachments 列表给 UI 截图勾选；多需求时不展示截图区
  const singleRequirementId =
    requirements.length === 1 ? requirements[0].id : null;

  const attachmentsQuery = useQuery({
    queryKey: ["attachments", singleRequirementId],
    queryFn: () =>
      singleRequirementId != null
        ? attachmentsApi.list(singleRequirementId)
        : Promise.resolve([] as Attachment[]),
    enabled: open && singleRequirementId != null,
  });

  const enabledModels = useMemo(
    () => (modelsQuery.data ?? []).filter((m) => m.enabled),
    [modelsQuery.data],
  );

  const imageAttachments = useMemo(
    () => (attachmentsQuery.data ?? []).filter(isImageAttachment),
    [attachmentsQuery.data],
  );

  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [count, setCount] = useState(5);
  const [scenarioMix, setScenarioMix] =
    useState<CaseGenerationScenarioMix>("positive_and_negative");
  const [userPrompt, setUserPrompt] = useState("");
  const [uiImageIds, setUiImageIds] = useState<number[]>([]);

  useEffect(() => {
    if (open) {
      const def = enabledModels.find((m) => m.is_default) ?? enabledModels[0];
      setSelectedModels(def ? [def.name] : []);
      setCount(5);
      setScenarioMix("positive_and_negative");
      setUserPrompt("");
      setUiImageIds([]);
    }
    // 仅 open 切换时重置，模型加载后再次重置由下面 effect 处理
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // 模型异步加载完成后兜底选默认
  useEffect(() => {
    if (open && selectedModels.length === 0 && enabledModels.length > 0) {
      const def = enabledModels.find((m) => m.is_default) ?? enabledModels[0];
      setSelectedModels([def.name]);
    }
  }, [open, enabledModels, selectedModels.length]);

  const triggerMutation = useMutation({
    mutationFn: () =>
      aiCaseGenerationApi.trigger({
        requirement_ids: requirements.map((r) => r.id),
        analysis_document_id: analysisDocumentId ?? null,
        model_names: selectedModels,
        count_per_requirement: count,
        scenario_mix: scenarioMix,
        user_prompt: userPrompt.trim() || undefined,
        ui_image_attachment_ids: uiImageIds,
      }),
    onSuccess: (data) => {
      toast.success(
        `已派发 ${data.batches.length} 个生成任务（${requirements.length} 需求 × ${selectedModels.length} 模型）`,
      );
      onTriggered?.(data.batches);
      onClose();
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "触发失败";
      toast.error(msg);
    },
  });

  const handleSubmit = () => {
    if (selectedModels.length === 0) {
      toast.error("至少选一个模型");
      return;
    }
    if (requirements.length === 0) {
      toast.error("没有要生成用例的需求");
      return;
    }
    triggerMutation.mutate();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-violet-600" />
              AI 一键生成测试用例
            </span>
          </DialogTitle>
          <DialogDescription>
            将基于需求 + 分析文档 + 模块上下文产出 functional 用例草稿；提交后到 Review 弹窗勾选/编辑后批量入库。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <RequirementSummary requirements={requirements} />

          {modelsQuery.isLoading ? (
            <div className="py-2 text-sm text-muted-foreground">
              <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
              加载模型列表…
            </div>
          ) : enabledModels.length === 0 ? (
            <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
              没有启用的 AI 模型。请到「配置中心 → AI」添加并启用至少一个模型。
            </div>
          ) : (
            <div className="space-y-2">
              <Label>选择模型（可多选，每对"需求 × 模型"一个 batch）</Label>
              <div className="grid grid-cols-2 gap-2">
                {enabledModels.map((m) => {
                  const checked = selectedModels.includes(m.name);
                  return (
                    <label
                      key={m.name}
                      className={[
                        "flex cursor-pointer items-center justify-between gap-2 rounded border px-3 py-2 text-sm",
                        checked
                          ? "border-violet-500 bg-violet-50/60"
                          : "border-border hover:bg-muted/40",
                      ].join(" ")}
                    >
                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            setSelectedModels((cur) =>
                              e.target.checked
                                ? [...cur, m.name]
                                : cur.filter((n) => n !== m.name),
                            );
                          }}
                        />
                        <span>
                          <div className="font-medium">{m.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {m.provider} / {m.model}
                          </div>
                        </span>
                      </div>
                      {m.supports_vision ? (
                        <Eye className="h-4 w-4 text-emerald-600" />
                      ) : null}
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label>每需求生成几条</Label>
              <Input
                type="number"
                min={1}
                max={30}
                value={count}
                onChange={(e) =>
                  setCount(
                    Math.max(
                      1,
                      Math.min(30, Number(e.target.value) || 5),
                    ),
                  )
                }
              />
              <div className="mt-1 text-xs text-muted-foreground">
                范围 1–30，默认 5
              </div>
            </div>

            <div>
              <Label>场景配比</Label>
              <div className="mt-1 grid gap-1">
                {SCENARIO_OPTIONS.map((opt) => {
                  const selected = scenarioMix === opt.value;
                  return (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setScenarioMix(opt.value)}
                      className={[
                        "rounded border px-2 py-1.5 text-left text-xs transition",
                        selected
                          ? "border-violet-500 bg-violet-50/60"
                          : "border-border hover:bg-muted/40",
                      ].join(" ")}
                    >
                      <div className="font-medium">{opt.label}</div>
                      <div className="text-muted-foreground">{opt.desc}</div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {singleRequirementId != null ? (
            <div>
              <Label className="inline-flex items-center gap-1">
                <ImageIcon className="h-3.5 w-3.5" /> UI 截图（可选）
              </Label>
              <div className="mt-1 text-xs text-muted-foreground">
                勾选后，AI 会读取这些截图作为 UI 上下文（vision/ocr 自动选择）。
              </div>
              {attachmentsQuery.isLoading ? (
                <div className="mt-2 text-xs text-muted-foreground">
                  <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
                  加载附件…
                </div>
              ) : imageAttachments.length === 0 ? (
                <div className="mt-2 rounded border border-dashed p-2 text-xs text-muted-foreground">
                  该需求暂无图片附件。可先到附件区上传图片，再回来勾选。
                </div>
              ) : (
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {imageAttachments.map((att) => {
                    const checked = uiImageIds.includes(att.id);
                    return (
                      <label
                        key={att.id}
                        className={[
                          "flex cursor-pointer items-center gap-2 rounded border px-2 py-1.5 text-xs",
                          checked
                            ? "border-violet-500 bg-violet-50/60"
                            : "border-border hover:bg-muted/40",
                        ].join(" ")}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            setUiImageIds((cur) =>
                              e.target.checked
                                ? [...cur, att.id]
                                : cur.filter((id) => id !== att.id),
                            );
                          }}
                        />
                        <span className="truncate">{att.name}</span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          ) : null}

          <div>
            <Label>补充说明（可选）</Label>
            <Textarea
              rows={3}
              placeholder="例：重点覆盖支付失败 / 风控拦截；不要写 UI 控件级步骤；priority 默认 P1…"
              value={userPrompt}
              onChange={(e) => setUserPrompt(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={
              triggerMutation.isPending ||
              enabledModels.length === 0 ||
              selectedModels.length === 0
            }
          >
            {triggerMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            开始生成（{requirements.length} 需求 × {selectedModels.length} 模型）
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


function RequirementSummary({
  requirements,
}: {
  requirements: RequirementHandle[] | Requirement[];
}) {
  // 没有 title 的传入项（比如批量入口只传 id），按需查一下让 UI 友好
  const handles = requirements as RequirementHandle[];
  const needsFetch = handles
    .filter((r) => !("title" in r) || !r.title)
    .map((r) => r.id);
  const fetched = useQueries({
    queries: needsFetch.map((id) => ({
      queryKey: queryKeys.requirement(id),
      queryFn: async () => {
        const { requirementsApi } = await import("@/lib/api");
        return requirementsApi.get(id);
      },
      staleTime: 60_000,
    })),
  });
  const titleById = useMemo(() => {
    const m = new Map<number, string>();
    fetched.forEach((q, idx) => {
      if (q.data) m.set(needsFetch[idx], q.data.title);
    });
    return m;
  }, [fetched, needsFetch]);

  if (handles.length === 0) {
    return (
      <div className="rounded border border-amber-300 bg-amber-50 p-2 text-sm text-amber-800">
        没有要生成用例的需求
      </div>
    );
  }
  if (handles.length === 1) {
    const r = handles[0];
    const title = (r as Requirement).title || titleById.get(r.id) || `#${r.id}`;
    return (
      <div className="rounded bg-muted/40 p-2 text-sm">
        将为需求 <b>#{r.id} {title}</b> 生成用例
      </div>
    );
  }
  return (
    <div className="rounded bg-muted/40 p-2 text-sm">
      <div className="mb-1">将为以下 {handles.length} 个需求批量生成用例：</div>
      <ul className="max-h-32 list-disc overflow-auto pl-5 text-xs">
        {handles.map((r) => {
          const title =
            (r as Requirement).title || titleById.get(r.id) || `#${r.id}`;
          return (
            <li key={r.id}>
              #{r.id} {title}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
