/**
 * ProjectAiConfigTab —— 项目级 AI 模型配置（样式对齐全局 AiModelConfigTab）。
 *
 * 数据来源：configApi.list('ai', projectId)（按 config_group 聚合为模型对象）。
 */
import { useMemo, useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CheckCircle2,
  Eye,
  Loader2,
  Pencil,
  Plug,
  Plus,
  Star,
  Trash2,
  XCircle,
  Braces,
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { configApi } from "@/lib/api";
import type { ConfigItem } from "@/lib/api";

// ---------------------------------------------------------------------------
// 类型 & 工具
// ---------------------------------------------------------------------------
interface AiModelLike {
  name: string;
  provider: string;
  model: string;
  base_url: string;
  api_key: string;
  supports_vision: boolean;
  is_default: boolean;
  enabled: boolean;
}

interface EmbeddingConfigLike {
  provider: string;
  model: string;
  base_url: string;
  api_key: string;
  dim: string;
}

function rowsToEmbeddingConfig(items: ConfigItem[]): EmbeddingConfigLike {
  const kvs = new Map<string, string>();
  for (const item of items) {
    if (item.config_group === "rag_embedding" && item.category === "ai") {
      kvs.set(item.config_key, item.config_value);
    }
  }
  return {
    provider: kvs.get("provider") || "",
    model: kvs.get("model") || "",
    base_url: kvs.get("base_url") || "",
    api_key: kvs.get("api_key") || "",
    dim: kvs.get("dim") || "",
  };
}

function rowsToModels(items: ConfigItem[]): AiModelLike[] {
  const byGroup = new Map<string, Map<string, string>>();
  for (const item of items) {
    if (!byGroup.has(item.config_group)) {
      byGroup.set(item.config_group, new Map());
    }
    byGroup.get(item.config_group)!.set(item.config_key, item.config_value);
  }

  const out: AiModelLike[] = [];
  for (const [name, kvs] of byGroup) {
    if (name === "rag_embedding") continue;
    if (!kvs.has("model") || !kvs.get("model")) continue;
    out.push({
      name,
      provider: kvs.get("provider") || "",
      model: kvs.get("model") || "",
      base_url: kvs.get("base_url") || "",
      api_key: kvs.get("api_key") || "",
      supports_vision: (kvs.get("supports_vision") || "").toLowerCase() === "true",
      is_default: (kvs.get("is_default") || "").toLowerCase() === "true",
      enabled: kvs.get("enabled") !== "false",
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
export function ProjectAiConfigTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();

  const { data: rawItems = [], isLoading, isError, error } = useQuery({
    queryKey: ["project-config", projectId, "ai"],
    queryFn: () => configApi.list("ai", projectId),
  });

  const models = useMemo(() => rowsToModels(rawItems), [rawItems]);
  const embeddingConfig = useMemo(() => rowsToEmbeddingConfig(rawItems), [rawItems]);

  const [editing, setEditing] = useState<AiModelLike | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<AiModelLike | null>(null);
  const [testingName, setTestingName] = useState<string | null>(null);
  const [testingEmbedding, setTestingEmbedding] = useState(false);
  const [editingEmbedding, setEditingEmbedding] = useState(false);

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["project-config", projectId, "ai"] });

  const handleSave = async (m: AiModelLike) => {
    // 写入各 key-value 行
    const rows: Omit<ConfigItem, "id">[] = [
      { config_group: m.name, config_key: "provider", config_value: m.provider, category: "ai", project_id: projectId },
      { config_group: m.name, config_key: "model", config_value: m.model, category: "ai", project_id: projectId },
      { config_group: m.name, config_key: "base_url", config_value: m.base_url, category: "ai", project_id: projectId },
      { config_group: m.name, config_key: "api_key", config_value: m.api_key, category: "ai", project_id: projectId },
      { config_group: m.name, config_key: "supports_vision", config_value: m.supports_vision ? "true" : "false", category: "ai", project_id: projectId },
      { config_group: m.name, config_key: "is_default", config_value: m.is_default ? "true" : "false", category: "ai", project_id: projectId },
      { config_group: m.name, config_key: "enabled", config_value: m.enabled ? "true" : "false", category: "ai", project_id: projectId },
    ];

    // 如果设为默认，先把其他模型的 is_default 干掉
    if (m.is_default) {
      for (const item of rawItems) {
        if (item.config_key === "is_default" && item.config_group !== m.name && item.config_value === "true") {
          await configApi.save({ ...item, config_value: "false" });
        }
      }
    }

    for (const row of rows) {
      await configApi.save(row);
    }
  };

  const handleDelete = async (name: string) => {
    if (name === "rag_embedding") {
      toast.error("不能通过 Chat 模型列表删除 Embedding 配置");
      return;
    }
    const toDelete = rawItems.filter((i) => i.config_group === name);
    for (const item of toDelete) {
      await configApi.remove(item.id);
    }
    invalidate();
  };

  const handleTest = async (m: AiModelLike) => {
    setTestingName(m.name);
    try {
      const r = await configApi.testAiModel(projectId, m.name);
      if (r.ok) {
        toast.success(`连接成功 · ${r.result ?? "ok"}`);
      } else {
        toast.error(`连接失败：${r.error || "未知错误"}`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "测试失败");
    } finally {
      setTestingName(null);
    }
  };

  const handleEmbeddingSave = async (e: EmbeddingConfigLike) => {
    const rows: Omit<ConfigItem, "id">[] = [
      { config_group: "rag_embedding", config_key: "provider", config_value: e.provider, category: "ai", project_id: projectId },
      { config_group: "rag_embedding", config_key: "model", config_value: e.model, category: "ai", project_id: projectId },
      { config_group: "rag_embedding", config_key: "base_url", config_value: e.base_url, category: "ai", project_id: projectId },
      { config_group: "rag_embedding", config_key: "api_key", config_value: e.api_key, category: "ai", project_id: projectId },
      { config_group: "rag_embedding", config_key: "dim", config_value: e.dim, category: "ai", project_id: projectId },
    ];
    for (const row of rows) {
      if (row.config_value || row.config_key === "api_key") {
        await configApi.save(row);
      }
    }
  };

  const handleEmbeddingTest = async () => {
    setTestingEmbedding(true);
    try {
      const r = await configApi.testEmbedding(projectId);
      if (r.ok) {
        toast.success(`连接成功 · ${r.result ?? "ok"}`);
      } else {
        toast.error(`连接失败：${r.error || "未知错误"}`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "测试失败");
    } finally {
      setTestingEmbedding(false);
    }
  };

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          该项目专用的 AI 模型配置。需求分析 / Bug 修复 / 测试计划等 AI 功能将优先使用此处的模型。
        </p>
        <Button onClick={() => setCreating(true)} size="sm">
          <Plus className="h-4 w-4" /> 新增模型
        </Button>
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          加载失败：{error instanceof Error ? error.message : ""}
        </div>
      ) : models.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center text-sm text-muted-foreground">
            还没有 AI 模型。点右上角"新增模型"配置第一个。
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="grid grid-cols-[1fr_140px_2fr_120px_120px_180px] items-center gap-2 border-b px-4 py-2 text-xs font-semibold text-muted-foreground">
              <div>别名</div>
              <div>Provider</div>
              <div>Model</div>
              <div>Vision</div>
              <div>状态</div>
              <div className="text-right">操作</div>
            </div>
            {models.map((m) => (
              <div
                key={m.name}
                className="grid grid-cols-[1fr_140px_2fr_120px_120px_180px] items-center gap-2 border-b px-4 py-2 text-sm last:border-b-0"
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium">{m.name}</span>
                  {m.is_default ? (
                    <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700" title="默认模型">
                      <Star className="h-3 w-3" /> 默认
                    </span>
                  ) : null}
                </div>
                <div className="text-xs">{m.provider}</div>
                <div className="truncate font-mono text-xs text-muted-foreground">{m.model}</div>
                <div className="text-xs">
                  {m.supports_vision ? (
                    <span className="inline-flex items-center gap-1 text-emerald-600">
                      <Eye className="h-3 w-3" />支持
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </div>
                <div className="text-xs">
                  {m.enabled ? (
                    <span className="inline-flex items-center gap-1 text-emerald-600">
                      <CheckCircle2 className="h-3 w-3" />已启用
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-muted-foreground">
                      <XCircle className="h-3 w-3" />已停用
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-end gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleTest(m)}
                    disabled={testingName === m.name}
                  >
                    {testingName === m.name ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Plug className="h-4 w-4" />
                    )}
                    测试
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setEditing(m)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost" size="icon" className="h-8 w-8 text-destructive hover:text-destructive"
                    onClick={() => setPendingDelete(m)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Embedding 模型配置 */}
      <Card>
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <Braces className="h-4 w-4 text-violet-500" />
          <span className="text-sm font-semibold">Embedding 模型配置</span>
          <span className="text-xs text-muted-foreground">
            用于 RAG 代码索引的向量化模型
          </span>
        </div>
        <CardContent className="p-4">
          {embeddingConfig.provider && embeddingConfig.model ? (
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4 text-sm">
                <div>
                  <span className="text-xs text-muted-foreground">Provider</span>
                  <div className="font-medium">{embeddingConfig.provider}</div>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">Model</span>
                  <div className="font-mono text-xs text-muted-foreground">{embeddingConfig.model}</div>
                </div>
                {embeddingConfig.base_url ? (
                  <div>
                    <span className="text-xs text-muted-foreground">Base URL</span>
                    <div className="font-mono text-xs text-muted-foreground max-w-[240px] truncate" title={embeddingConfig.base_url}>
                      {embeddingConfig.base_url}
                    </div>
                  </div>
                ) : null}
                {embeddingConfig.dim ? (
                  <div>
                    <span className="text-xs text-muted-foreground">维度</span>
                    <div className="text-xs">{embeddingConfig.dim}</div>
                  </div>
                ) : null}
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleEmbeddingTest}
                  disabled={testingEmbedding}
                >
                  {testingEmbedding ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Plug className="h-4 w-4" />
                  )}
                  测试
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setEditingEmbedding(true)}>
                  <Pencil className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div className="text-sm text-muted-foreground">
                尚未配置 Embedding 模型。RAG 代码索引功能需要此配置才能工作。
              </div>
              <Button size="sm" variant="outline" onClick={() => setEditingEmbedding(true)}>
                <Plus className="h-3.5 w-3.5 mr-1" />
                配置
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 新增 / 编辑弹窗 */}
      <AiModelFormDialog
        open={creating || !!editing}
        onClose={() => { setCreating(false); setEditing(null); }}
        title={editing ? `编辑 · ${editing.name}` : "新增 AI 模型"}
        initial={editing ?? undefined}
        submitting={false}
        onSubmit={async (m) => {
          await handleSave(m);
          if (editing) {
            // 如果改了名字，删除旧的
            if (m.name !== editing.name) {
              await handleDelete(editing.name);
            }
          }
          setCreating(false);
          setEditing(null);
          toast.success(editing ? "已保存" : "已新增模型");
          invalidate();
        }}
      />

      {/* Embedding 编辑弹窗 */}
      <EmbeddingFormDialog
        open={editingEmbedding}
        onClose={() => setEditingEmbedding(false)}
        initial={embeddingConfig}
        submitting={false}
        onSubmit={async (e) => {
          await handleEmbeddingSave(e);
          setEditingEmbedding(false);
          toast.success("Embedding 配置已保存");
          invalidate();
        }}
      />

      {/* 删除确认 */}
      <Dialog open={!!pendingDelete} onOpenChange={(o) => !o && setPendingDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除模型？</DialogTitle>
            <DialogDescription>
              确定要删除 <b>{pendingDelete?.name}</b>？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>取消</Button>
            <Button variant="destructive" onClick={async () => {
              if (pendingDelete) {
                await handleDelete(pendingDelete.name);
                toast.success("已删除");
                setPendingDelete(null);
              }
            }}>
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 模型表单弹窗（简化版，对齐 AiModelConfigTab 的 ModelFormDialog）
// ---------------------------------------------------------------------------
function AiModelFormDialog({
  open, onClose, title, initial, submitting, onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  initial?: AiModelLike;
  submitting: boolean;
  onSubmit: (m: AiModelLike) => void;
}) {
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState("");
  const [base_url, setBaseUrl] = useState("");
  const [api_key, setApiKey] = useState("");
  const [supportsVision, setSupportsVision] = useState(false);
  const [isDefault, setIsDefault] = useState(false);
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    if (open) {
      setName(initial?.name ?? "");
      setProvider(initial?.provider ?? "openai");
      setModel(initial?.model ?? "");
      setBaseUrl(initial?.base_url ?? "");
      setApiKey(initial?.api_key ?? "");
      setSupportsVision(initial?.supports_vision ?? false);
      setIsDefault(initial?.is_default ?? false);
      setEnabled(initial?.enabled ?? true);
    }
  }, [open, initial]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div>
            <Label>别名</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="deepseek" disabled={!!initial} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Provider</Label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              >
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="ollama">Ollama</option>
                <option value="deepseek">DeepSeek</option>
                <option value="azure">Azure OpenAI</option>
                <option value="custom">Custom</option>
              </select>
            </div>
            <div>
              <Label>Model</Label>
              <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
            </div>
          </div>
          <div>
            <Label>Base URL（可选）</Label>
            <Input value={base_url} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.deepseek.com" />
          </div>
          <div>
            <Label>API Key</Label>
            <Input value={api_key} onChange={(e) => setApiKey(e.target.value)} type="password" placeholder="sk-..." autoComplete="off" />
          </div>
          <div className="flex flex-wrap gap-4 pt-2 text-sm">
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={supportsVision} onChange={(e) => setSupportsVision(e.target.checked)} />
              支持 vision
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
              设为默认
            </label>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              启用
            </label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button disabled={submitting || !name.trim() || !model.trim()} onClick={() => onSubmit({
            name, provider, model, base_url, api_key, supports_vision: supportsVision, is_default: isDefault, enabled,
          })}>
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Embedding 模型表单弹窗
// ---------------------------------------------------------------------------
function EmbeddingFormDialog({
  open, onClose, initial, submitting, onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  initial: EmbeddingConfigLike;
  submitting: boolean;
  onSubmit: (e: EmbeddingConfigLike) => void;
}) {
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");
  const [base_url, setBaseUrl] = useState("");
  const [api_key, setApiKey] = useState("");
  const [dim, setDim] = useState("");

  useEffect(() => {
    if (open) {
      setProvider(initial.provider || "ollama");
      setModel(initial.model || "");
      setBaseUrl(initial.base_url || "");
      setApiKey(initial.api_key || "");
      setDim(initial.dim || "");
    }
  }, [open, initial]);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>配置 Embedding 模型</DialogTitle>
          <DialogDescription>
            用于 RAG 代码向量化索引。Chat 模型不能用于 Embedding，需使用专用模型。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Provider</Label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              >
                <option value="ollama">Ollama</option>
                <option value="openai">OpenAI</option>
                <option value="deepseek">DeepSeek</option>
                <option value="azure">Azure OpenAI</option>
                <option value="custom">Custom</option>
              </select>
            </div>
            <div>
              <Label>Model</Label>
              <Input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="nomic-embed-text"
              />
            </div>
          </div>
          <div>
            <Label>Base URL（可选）</Label>
            <Input
              value={base_url}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://host.docker.internal:11434"
            />
          </div>
          <div>
            <Label>API Key（可选，Ollama 无需）</Label>
            <Input
              value={api_key}
              onChange={(e) => setApiKey(e.target.value)}
              type="password"
              placeholder="sk-..."
              autoComplete="off"
            />
          </div>
          <div>
            <Label>向量维度（可选，自动检测）</Label>
            <Input
              value={dim}
              onChange={(e) => setDim(e.target.value)}
              placeholder="768"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              nomic-embed-text = 768，text-embedding-3-small = 1536
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button
            disabled={submitting || !provider.trim() || !model.trim()}
            onClick={() => onSubmit({ provider, model, base_url, api_key, dim })}
          >
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
