import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { aiModelsApi, ApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { AiModelConfig, AiProvider } from "@/types/domain";

const PROVIDERS: { value: AiProvider; label: string; hint?: string }[] = [
  { value: "openai", label: "OpenAI", hint: "gpt-4o / gpt-4o-mini" },
  { value: "anthropic", label: "Anthropic", hint: "claude-3-5-*" },
  { value: "ollama", label: "Ollama（本地）", hint: "qwen2.5 / llama3" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "azure", label: "Azure OpenAI" },
  { value: "custom", label: "Custom（OpenAI 协议兼容）" },
];

const schema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "请填写别名")
    .max(50, "别名 ≤ 50 字")
    .regex(/^[A-Za-z0-9_.\-]+$/, "只允许字母/数字/._-"),
  provider: z.string().min(1, "请选 provider"),
  model: z.string().trim().min(1, "model 必填").max(120),
  base_url: z.string().max(300).optional().or(z.literal("")),
  api_key: z.string().max(300).optional().or(z.literal("")),
  supports_vision: z.boolean().default(false),
  is_default: z.boolean().default(false),
  enabled: z.boolean().default(true),
});
type FormValues = z.infer<typeof schema>;

const empty: FormValues = {
  name: "",
  provider: "openai",
  model: "",
  base_url: "",
  api_key: "",
  supports_vision: false,
  is_default: false,
  enabled: true,
};

export function AiModelConfigTab() {
  const qc = useQueryClient();
  const listQuery = useQuery({
    queryKey: queryKeys.aiModels(),
    queryFn: () => aiModelsApi.list(),
  });

  const [editing, setEditing] = useState<AiModelConfig | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<AiModelConfig | null>(null);
  const [testingName, setTestingName] = useState<string | null>(null);

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: queryKeys.aiModels() });

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "操作失败";
    toast.error(msg);
  };

  const createMutation = useMutation({
    mutationFn: (v: FormValues) =>
      aiModelsApi.create({
        name: v.name,
        provider: v.provider,
        model: v.model,
        base_url: v.base_url || null,
        api_key: v.api_key || null,
        supports_vision: v.supports_vision,
        is_default: v.is_default,
        enabled: v.enabled,
        extra: {},
      }),
    onSuccess: () => {
      toast.success("已新增模型");
      setCreating(false);
      invalidate();
    },
    onError: handleError,
  });

  const updateMutation = useMutation({
    mutationFn: ({ name, v }: { name: string; v: FormValues }) =>
      aiModelsApi.update(name, {
        provider: v.provider,
        model: v.model,
        base_url: v.base_url || null,
        api_key: v.api_key || null,
        supports_vision: v.supports_vision,
        is_default: v.is_default,
        enabled: v.enabled,
        extra: editing?.extra || {},
      }),
    onSuccess: () => {
      toast.success("已保存");
      setEditing(null);
      invalidate();
    },
    onError: handleError,
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => aiModelsApi.remove(name),
    onSuccess: () => {
      toast.success("已删除");
      setPendingDelete(null);
      invalidate();
    },
    onError: handleError,
  });

  const handleTest = async (cfg: AiModelConfig) => {
    setTestingName(cfg.name);
    try {
      const r = await aiModelsApi.test(cfg.name);
      if (r.ok) {
        toast.success(
          `连接成功 · ${r.latency_ms}ms · 回复：${r.sample.slice(0, 30) || "(空)"}`,
        );
      } else {
        toast.error(`连接失败：${r.error || "未知错误"}`);
      }
    } catch (err) {
      handleError(err);
    } finally {
      setTestingName(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          配置 AI 模型连接。需求分析 / 测试计划等 AI 功能从这里挑模型；勾选 vision 则
          支持图片附件直接喂入大模型。
        </p>
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" /> 新增模型
        </Button>
      </div>

      {listQuery.isLoading ? (
        <Skeleton className="h-48" />
      ) : listQuery.isError ? (
        <div className="rounded border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          加载失败：{listQuery.error instanceof Error ? listQuery.error.message : ""}
        </div>
      ) : (listQuery.data?.length ?? 0) === 0 ? (
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
            {listQuery.data!.map((c) => (
              <div
                key={c.name}
                className="grid grid-cols-[1fr_140px_2fr_120px_120px_180px] items-center gap-2 border-b px-4 py-2 text-sm last:border-b-0"
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium">{c.name}</span>
                  {c.is_default ? (
                    <span
                      className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700"
                      title="默认模型"
                    >
                      <Star className="h-3 w-3" /> 默认
                    </span>
                  ) : null}
                </div>
                <div className="text-xs">{c.provider}</div>
                <div className="truncate font-mono text-xs text-muted-foreground">
                  {c.model}
                </div>
                <div className="text-xs">
                  {c.supports_vision ? (
                    <span className="inline-flex items-center gap-1 text-emerald-600">
                      <Eye className="h-3 w-3" />支持
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </div>
                <div className="text-xs">
                  {c.enabled ? (
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
                    onClick={() => handleTest(c)}
                    disabled={testingName === c.name}
                  >
                    {testingName === c.name ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Plug className="h-4 w-4" />
                    )}
                    测试
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => setEditing(c)}
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-destructive hover:text-destructive"
                    onClick={() => setPendingDelete(c)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <ModelFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        title="新增 AI 模型"
        initial={empty}
        nameDisabled={false}
        submitting={createMutation.isPending}
        onSubmit={(v) => createMutation.mutate(v)}
      />

      <ModelFormDialog
        open={!!editing}
        onClose={() => setEditing(null)}
        title={`编辑 · ${editing?.name ?? ""}`}
        initial={
          editing
            ? {
                name: editing.name,
                provider: editing.provider,
                model: editing.model,
                base_url: editing.base_url ?? "",
                api_key: editing.api_key ?? "",
                supports_vision: editing.supports_vision,
                is_default: editing.is_default,
                enabled: editing.enabled,
              }
            : empty
        }
        nameDisabled
        submitting={updateMutation.isPending}
        onSubmit={(v) =>
          editing && updateMutation.mutate({ name: editing.name, v })
        }
      />

      <Dialog
        open={!!pendingDelete}
        onOpenChange={(o) => !o && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除模型？</DialogTitle>
            <DialogDescription>
              确定要删除 <b>{pendingDelete?.name}</b>？已选用该模型的历史分析记录不受影响。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() =>
                pendingDelete && deleteMutation.mutate(pendingDelete.name)
              }
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}


function ModelFormDialog({
  open,
  onClose,
  title,
  initial,
  nameDisabled,
  submitting,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  initial: FormValues;
  nameDisabled: boolean;
  submitting: boolean;
  onSubmit: (v: FormValues) => void;
}) {
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    values: initial,
  });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={form.handleSubmit((v) => onSubmit(v))}
        >
          <div>
            <Label>别名（唯一）</Label>
            <Input
              {...form.register("name")}
              placeholder="gpt-4o-prod"
              disabled={nameDisabled}
            />
            {form.formState.errors.name ? (
              <p className="mt-1 text-xs text-destructive">
                {form.formState.errors.name.message}
              </p>
            ) : null}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Provider</Label>
              <Select
                value={form.watch("provider")}
                onValueChange={(v) => form.setValue("provider", v)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROVIDERS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
                      {p.hint ? (
                        <span className="ml-1 text-xs text-muted-foreground">
                          ({p.hint})
                        </span>
                      ) : null}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Model</Label>
              <Input
                {...form.register("model")}
                placeholder="gpt-4o / claude-3-5-sonnet-..."
              />
              {form.formState.errors.model ? (
                <p className="mt-1 text-xs text-destructive">
                  {form.formState.errors.model.message}
                </p>
              ) : null}
            </div>
          </div>

          <div>
            <Label>Base URL（可选 · 自建反代 / Azure / Ollama 用）</Label>
            <Input
              {...form.register("base_url")}
              placeholder="https://api.openai.com 或 http://127.0.0.1:11434"
            />
          </div>

          <div>
            <Label>API Key（Ollama 可空）</Label>
            <Input
              {...form.register("api_key")}
              type="password"
              placeholder="sk-..."
              autoComplete="off"
            />
          </div>

          <div className="flex flex-wrap gap-4 pt-2 text-sm">
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.watch("supports_vision")}
                onChange={(e) =>
                  form.setValue("supports_vision", e.target.checked)
                }
              />
              支持 vision（图片可直接喂入）
            </label>
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.watch("is_default")}
                onChange={(e) => form.setValue("is_default", e.target.checked)}
              />
              设为默认
            </label>
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.watch("enabled")}
                onChange={(e) => form.setValue("enabled", e.target.checked)}
              />
              启用
            </label>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
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
