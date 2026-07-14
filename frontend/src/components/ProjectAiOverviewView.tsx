import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { aiModelsApi, projectsApi } from "@/lib/api";
import { Button } from "@/components/ui/button";

/**
 * 项目 AI 概览（模块关联图谱）。项目详情页面板 + AI 生成弹窗共用同一份数据。
 * AI 读取项目所有模块 → {summary, modules[], relations[]}，持久化在项目上，可重新生成。
 */
export function ProjectAiOverviewView({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const [modelName, setModelName] = useState("");
  const [generating, setGenerating] = useState(false);

  const overviewQuery = useQuery({
    queryKey: ["project-ai-overview", projectId],
    queryFn: () => projectsApi.getAiOverview(projectId),
  });
  const modelsQuery = useQuery({
    queryKey: ["ai-models", projectId],
    queryFn: () => aiModelsApi.list(projectId),
  });
  const models = (modelsQuery.data ?? []).filter((m) => m.enabled);

  useEffect(() => {
    if (!modelName && models.length) setModelName(models[0].name);
  }, [models, modelName]);

  const overview = overviewQuery.data?.overview ?? null;
  const updatedAt = overviewQuery.data?.updated_at ?? null;

  const generate = async () => {
    if (!modelName) {
      toast.error("请先选择 AI 模型");
      return;
    }
    setGenerating(true);
    try {
      const res = await projectsApi.genAiOverview(projectId, modelName);
      qc.setQueryData(["project-ai-overview", projectId], res);
      toast.success("项目概览已生成");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium">项目概览 · 模块关联</div>
          {updatedAt ? (
            <div className="text-xs text-muted-foreground">更新于 {new Date(updatedAt).toLocaleString()}</div>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
            className="h-8 w-52 rounded-md border border-input bg-background px-2 text-xs"
          >
            {models.length === 0 ? <option value="">（无可用模型）</option> : null}
            {models.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}（{m.provider}/{m.model}）
              </option>
            ))}
          </select>
          <Button size="sm" variant="outline" onClick={generate} disabled={generating || !modelName}>
            {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            {overview ? "重新生成" : "生成概览"}
          </Button>
        </div>
      </div>

      {overviewQuery.isLoading ? (
        <div className="text-sm text-muted-foreground">加载中…</div>
      ) : !overview ? (
        <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          还没有项目概览。选模型点「生成概览」，AI 会读取项目所有模块，梳理模块职责与关联关系；
          之后按模块生成用例时会自动据此设计跨模块联动用例。
        </div>
      ) : (
        <div className="space-y-3">
          {overview.summary ? <p className="text-sm leading-relaxed">{overview.summary}</p> : null}
          {overview.modules.length ? (
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">模块职责</div>
              <ul className="space-y-0.5 text-sm">
                {overview.modules.map((m, i) => (
                  <li key={i}>
                    <span className="font-medium">{m.name}</span>：{m.purpose}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {overview.relations.length ? (
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">
                模块关联（跨模块联动用例的依据）
              </div>
              <ul className="space-y-1 text-sm">
                {overview.relations.map((r, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                      {r.from} → {r.to}
                    </span>
                    <span className="text-muted-foreground">{r.relation}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
