import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Bot, Eye, Loader2, Settings2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { aiModelsApi, analysisDocsApi, ApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { AiModelConfig, Requirement } from "@/types/domain";

interface Props {
  open: boolean;
  onClose: () => void;
  requirement: Requirement | null;
  /** 触发成功后调用，让父组件刷新分析文档列表/打开列表对话框。 */
  onTriggered?: (runs: { run_id: number; model_name: string }[]) => void;
}

export function AiAnalysisLauncherDialog({
  open,
  onClose,
  requirement,
  onTriggered,
}: Props) {
  const qc = useQueryClient();
  const modelsQuery = useQuery({
    queryKey: queryKeys.aiModels(),
    queryFn: () => aiModelsApi.list(),
    enabled: open,
  });

  const enabledModels = useMemo(
    () => (modelsQuery.data ?? []).filter((m) => m.enabled),
    [modelsQuery.data],
  );

  const defaultModel = enabledModels.find((m) => m.is_default) ?? enabledModels[0];

  const [primaryName, setPrimaryName] = useState<string>("");
  const [advanced, setAdvanced] = useState(false);
  const [extraNames, setExtraNames] = useState<string[]>([]);
  const [userPrompt, setUserPrompt] = useState("");

  useEffect(() => {
    if (open) {
      setPrimaryName(defaultModel?.name ?? "");
      setAdvanced(false);
      setExtraNames([]);
      setUserPrompt("");
    }
  }, [open, defaultModel?.name]);

  const triggerMutation = useMutation({
    mutationFn: async (model_names: string[]) => {
      if (!requirement) throw new Error("缺少需求");
      return analysisDocsApi.trigger(requirement.id, {
        model_names,
        user_prompt: userPrompt.trim() || undefined,
      });
    },
    onSuccess: (data) => {
      toast.success(`已派发 ${data.runs.length} 个分析任务，稍候刷新列表`);
      if (requirement) {
        qc.invalidateQueries({ queryKey: queryKeys.analysisDocs(requirement.id) });
      }
      onTriggered?.(data.runs);
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
    if (!primaryName) {
      toast.error("请先选一个主模型");
      return;
    }
    const names = [primaryName, ...(advanced ? extraNames : [])];
    const unique = Array.from(new Set(names));
    triggerMutation.mutate(unique);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>
            <span className="inline-flex items-center gap-2">
              <Bot className="h-5 w-5 text-violet-600" />
              AI 需求分析
            </span>
          </DialogTitle>
          <DialogDescription>
            {requirement ? (
              <>
                将基于需求 <b>#{requirement.id} {requirement.title}</b> 的描述、关联模块、依赖需求、子需求、附件等上下文，产出一份 Markdown 分析文档。
              </>
            ) : (
              "请选择一个需求"
            )}
          </DialogDescription>
        </DialogHeader>

        {modelsQuery.isLoading ? (
          <div className="py-4 text-sm text-muted-foreground">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> 加载模型列表…
          </div>
        ) : enabledModels.length === 0 ? (
          <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
            没有启用的 AI 模型。请到「配置中心 → AI」添加并启用至少一个模型。
          </div>
        ) : (
          <div className="space-y-3">
            <ModelPicker
              label="主模型"
              models={enabledModels}
              value={primaryName}
              onChange={setPrimaryName}
            />

            <div>
              <button
                type="button"
                className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                onClick={() => setAdvanced((v) => !v)}
              >
                <Settings2 className="h-3.5 w-3.5" />
                {advanced ? "收起高级选项" : "高级选项（多模型并行）"}
              </button>
            </div>

            {advanced ? (
              <div className="rounded border bg-muted/30 p-3">
                <div className="mb-2 text-xs text-muted-foreground">
                  勾选额外模型 → 后端会为每个模型独立产出一份文档（不合并）。
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {enabledModels
                    .filter((m) => m.name !== primaryName)
                    .map((m) => {
                      const checked = extraNames.includes(m.name);
                      return (
                        <label
                          key={m.name}
                          className="inline-flex items-center gap-2 text-sm"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              setExtraNames((cur) =>
                                e.target.checked
                                  ? [...cur, m.name]
                                  : cur.filter((n) => n !== m.name),
                              );
                            }}
                          />
                          <span className="font-medium">{m.name}</span>
                          <span className="text-xs text-muted-foreground">
                            {m.provider} / {m.model}
                            {m.supports_vision ? (
                              <Eye className="ml-1 inline h-3 w-3 text-emerald-600" />
                            ) : null}
                          </span>
                        </label>
                      );
                    })}
                </div>
              </div>
            ) : null}

            <div>
              <Label>补充说明（可选）</Label>
              <Textarea
                rows={3}
                placeholder="例如：重点关注异常路径与性能；产出测试用例时优先 P0…"
                value={userPrompt}
                onChange={(e) => setUserPrompt(e.target.value)}
              />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={
              triggerMutation.isPending ||
              enabledModels.length === 0 ||
              !primaryName
            }
          >
            {triggerMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            开始分析
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


function ModelPicker({
  label,
  models,
  value,
  onChange,
}: {
  label: string;
  models: AiModelConfig[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1 grid gap-1.5">
        {models.map((m) => {
          const selected = m.name === value;
          return (
            <button
              key={m.name}
              type="button"
              onClick={() => onChange(m.name)}
              className={[
                "flex w-full items-center justify-between rounded border px-3 py-2 text-left text-sm transition",
                selected
                  ? "border-violet-500 bg-violet-50/60"
                  : "border-border hover:bg-muted/50",
              ].join(" ")}
            >
              <div>
                <div className="font-medium">
                  {m.name}
                  {m.is_default ? (
                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                      默认
                    </span>
                  ) : null}
                </div>
                <div className="text-xs text-muted-foreground">
                  {m.provider} / {m.model}
                </div>
              </div>
              {m.supports_vision ? (
                <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
                  <Eye className="h-3 w-3" /> vision
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}
