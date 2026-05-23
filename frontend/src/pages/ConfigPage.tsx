import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  configApi,
  type ConfigItem,
  type ConfigSchemaItem,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import { AiModelConfigTab } from "@/pages/config/tabs/AiModelConfigTab";

/**
 * 配置中心：按 category (api / web / app / global) 切 Tab，
 * 表格展示 group/key/value，可编辑、删除、新增。
 *
 * 后端 `/api/config/save` 是 upsert，所以新增和编辑都走它 ——
 * `add` 接口实际是裸 SQL insert，没 upsert 语义，不建议前端用。
 */

const CATEGORIES = [
  { value: "api", label: "API" },
  { value: "web", label: "Web" },
  { value: "app", label: "App" },
  { value: "ai", label: "AI" },
  { value: "global", label: "全局" },
] as const;

const configSchema = z.object({
  category: z.string().min(1),
  config_group: z.string().trim().min(1, "必填").max(50),
  config_key: z.string().trim().min(1, "必填").max(100),
  config_value: z.string().max(5000).optional().default(""),
});
type ConfigFormValues = z.infer<typeof configSchema>;

export function ConfigPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawCategory = searchParams.get("category") ?? "api";
  const activeCategory =
    CATEGORIES.some((c) => c.value === rawCategory) ? rawCategory : "api";

  const queryClient = useQueryClient();
  const configQuery = useQuery({
    queryKey: queryKeys.config(activeCategory),
    queryFn: () => configApi.list(activeCategory),
  });

  // 推荐配置项：api / app / web 都各自维护一份；不识别的 category 后端会返回空数组
  const schemaQuery = useQuery({
    queryKey: queryKeys.configSchema(activeCategory),
    queryFn: () => configApi.schema(activeCategory),
    staleTime: 5 * 60 * 1000,
  });

  const [editing, setEditing] = useState<ConfigItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [prefill, setPrefill] = useState<ConfigFormValues | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ConfigItem | null>(null);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["config"] });

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "操作失败";
    toast.error(msg);
  };

  const saveMutation = useMutation({
    mutationFn: (body: ConfigFormValues) =>
      configApi.save({
        category: body.category,
        config_group: body.config_group,
        config_key: body.config_key,
        config_value: body.config_value ?? "",
      }),
    onSuccess: () => {
      toast.success("保存成功");
      invalidate();
      setCreating(false);
      setEditing(null);
      setPrefill(null);
    },
    onError: handleError,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => configApi.remove(id),
    onSuccess: () => {
      toast.success("已删除");
      invalidate();
      setPendingDelete(null);
    },
    onError: handleError,
  });

  const handleTabChange = (v: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("category", v);
    setSearchParams(next, { replace: true });
  };

  const items = configQuery.data ?? [];

  // 按 group 聚合，展示更清晰
  const grouped = useMemo(() => {
    const map = new Map<string, ConfigItem[]>();
    for (const it of items) {
      const list = map.get(it.config_group) ?? [];
      list.push(it);
      map.set(it.config_group, list);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [items]);

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">全局模板</h1>
          <p className="text-sm text-muted-foreground">
            全局配置模板，新建项目时可从此处一键导入。
          </p>
        </div>
        <div className="flex gap-2">
          {activeCategory === "ai" ? null : (
            <>
              <Button
                variant="outline"
                onClick={() => configQuery.refetch()}
                disabled={configQuery.isFetching}
              >
                {configQuery.isFetching ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                刷新
              </Button>
              <Button onClick={() => setCreating(true)}>
                <Plus className="h-4 w-4" />
                新增配置项
              </Button>
            </>
          )}
        </div>
      </div>

      <Tabs value={activeCategory} onValueChange={handleTabChange}>
        <TabsList>
          {CATEGORIES.map((c) => (
            <TabsTrigger key={c.value} value={c.value}>
              {c.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {activeCategory === "ai" ? (
        <AiModelConfigTab />
      ) : (
        <>
      {schemaQuery.data && schemaQuery.data.length > 0 ? (
        <RecommendedConfigPanel
          items={schemaQuery.data}
          existing={items}
          onFill={(it) => {
            setPrefill({
              category: activeCategory,
              config_group: it.config_group,
              config_key: it.key,
              config_value: it.example || it.default || "",
            });
            setCreating(true);
          }}
        />
      ) : null}

      {configQuery.isLoading ? (
        <ListSkeleton />
      ) : configQuery.isError ? (
        <ErrorBox
          message={
            configQuery.error instanceof Error
              ? configQuery.error.message
              : "加载失败"
          }
          onRetry={() => configQuery.refetch()}
        />
      ) : grouped.length === 0 ? (
        <EmptyState
          category={activeCategory}
          onCreate={() => setCreating(true)}
        />
      ) : (
        <div className="space-y-4">
          {grouped.map(([group, gitems]) => (
            <Card key={group}>
              <CardContent className="p-0">
                <div className="flex items-center justify-between border-b px-4 py-2">
                  <div className="text-sm font-semibold">{group}</div>
                  <div className="text-xs text-muted-foreground">
                    {gitems.length} 项
                  </div>
                </div>
                <div className="divide-y">
                  {gitems.map((it) => (
                    <div
                      key={it.id}
                      className="grid grid-cols-[200px_1fr_auto] items-center gap-4 px-4 py-2 text-sm"
                    >
                      <div className="flex items-center gap-2 font-mono text-xs">
                        <span>{it.config_key}</span>
                      </div>
                      <div className="truncate font-mono text-xs text-muted-foreground">
                        {it.config_value || (
                          <span className="italic">（空）</span>
                        )}
                      </div>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="h-8 w-8">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onSelect={(e) => {
                              e.preventDefault();
                              setEditing(it);
                            }}
                          >
                            <Pencil className="h-4 w-4" />
                            编辑
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            className="text-destructive focus:text-destructive"
                            onSelect={(e) => {
                              e.preventDefault();
                              setPendingDelete(it);
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                            删除
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Dialogs */}
      <ConfigFormDialog
        open={creating}
        onClose={() => {
          setCreating(false);
          setPrefill(null);
        }}
        title={prefill ? "新增配置项（已预填推荐值）" : "新增配置项"}
        submitting={saveMutation.isPending}
        defaultValues={
          prefill ?? {
            category: activeCategory,
            config_group: "",
            config_key: "",
            config_value: "",
          }
        }
        onSubmit={(v) => saveMutation.mutate(v)}
      />

      <ConfigFormDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="编辑配置项"
        submitting={saveMutation.isPending}
        defaultValues={
          editing
            ? {
                category: editing.category,
                config_group: editing.config_group,
                config_key: editing.config_key,
                config_value: editing.config_value ?? "",
              }
            : {
                category: activeCategory,
                config_group: "",
                config_key: "",
                config_value: "",
              }
        }
        // 编辑时 group/key/category 被后端用来做唯一键查询，保持只读以避免生成孤儿项。
        lockKeys={editing !== null}
        onSubmit={(v) => saveMutation.mutate(v)}
      />

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(v) => !v && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除配置项</DialogTitle>
            <DialogDescription>
              确认删除 「{pendingDelete?.config_group}.{pendingDelete?.config_key}」？
              此操作不可恢复，并会触发配置热更新。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingDelete(null)}
              disabled={deleteMutation.isPending}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() =>
                pendingDelete && deleteMutation.mutate(pendingDelete.id)
              }
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 表单 Dialog
// ---------------------------------------------------------------------------
function ConfigFormDialog({
  open,
  onClose,
  title,
  defaultValues,
  submitting,
  onSubmit,
  lockKeys = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  defaultValues: ConfigFormValues;
  submitting: boolean;
  onSubmit: (v: ConfigFormValues) => void;
  lockKeys?: boolean;
}) {
  const form = useForm<ConfigFormValues>({
    resolver: zodResolver(configSchema),
    defaultValues,
    values: defaultValues,
  });

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Group 相当于小节名，Key 是配置名，Value 是值。保存后会立刻热更新。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((v) => onSubmit(v))}
        >
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>分类</Label>
              <Select
                value={form.watch("category")}
                onValueChange={(v) =>
                  form.setValue("category", v, { shouldDirty: true })
                }
                disabled={lockKeys}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c.value} value={c.value}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cfg-group">Group</Label>
              <Input
                id="cfg-group"
                autoFocus={!lockKeys}
                disabled={lockKeys}
                maxLength={50}
                placeholder="host"
                {...form.register("config_group")}
              />
              {form.formState.errors.config_group ? (
                <p className="text-xs text-destructive">
                  {form.formState.errors.config_group.message}
                </p>
              ) : null}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cfg-key">Key</Label>
            <Input
              id="cfg-key"
              disabled={lockKeys}
              maxLength={100}
              placeholder="url"
              {...form.register("config_key")}
            />
            {form.formState.errors.config_key ? (
              <p className="text-xs text-destructive">
                {form.formState.errors.config_key.message}
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="cfg-value">Value</Label>
            <Textarea
              id="cfg-value"
              rows={4}
              maxLength={5000}
              placeholder="https://api.example.com"
              {...form.register("config_value")}
            />
          </div>

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

// ---------------------------------------------------------------------------
// 推荐配置项 —— 仅 web 分类有
// ---------------------------------------------------------------------------
function RecommendedConfigPanel({
  items,
  existing,
  onFill,
}: {
  items: ConfigSchemaItem[];
  existing: ConfigItem[];
  onFill: (item: ConfigSchemaItem) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  const existingKeys = useMemo(() => {
    const s = new Set<string>();
    for (const it of existing) {
      s.add(`${it.config_group}.${it.config_key}`);
    }
    return s;
  }, [existing]);

  return (
    <Card className="border-dashed bg-muted/30">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-sm font-semibold">推荐配置项</div>
            <div className="text-xs text-muted-foreground">
              当前分类下常用的配置键模板。点「填入」会把示例值预填进"新增"表单，
              你可以再改值后保存。已有同名键会标记"已配置"并禁用按钮，避免重复创建。
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? "展开" : "收起"}
          </Button>
        </div>
        {!collapsed ? (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {items.map((it) => {
              const already = existingKeys.has(`${it.config_group}.${it.key}`);
              return (
                <div
                  key={`${it.config_group}.${it.key}`}
                  className="flex items-start justify-between gap-3 rounded border bg-background px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="text-xs font-semibold">
                        {it.config_group}.{it.key}
                      </code>
                      <span
                        className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground"
                        title={`类型：${it.type}`}
                      >
                        {it.type}
                      </span>
                      {it.applies_to && it.applies_to.length > 0 ? (
                        <span
                          className="text-[10px] text-muted-foreground"
                          title="适用引擎"
                        >
                          {it.applies_to.join(" · ")}
                        </span>
                      ) : null}
                      {already ? (
                        <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:text-emerald-400">
                          已配置
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {it.description}
                    </div>
                    <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                      默认: {it.default || "（空）"} · 示例: {it.example || "—"}
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onFill(it)}
                    disabled={already}
                    title={already ? "已存在该键，去下方编辑" : "填入新增表单"}
                  >
                    填入
                  </Button>
                </div>
              );
            })}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 空态 / 骨架 / 错误
// ---------------------------------------------------------------------------
function EmptyState({
  category,
  onCreate,
}: {
  category: string;
  onCreate: () => void;
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
        <div className="text-sm text-muted-foreground">
          {category.toUpperCase()} 分类下还没有配置项。
        </div>
        <Button variant="outline" onClick={onCreate}>
          <Plus className="h-4 w-4" />
          新增第一条
        </Button>
      </CardContent>
    </Card>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Card key={i}>
          <CardContent className="space-y-2 py-4">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-2/3" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ErrorBox({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="border-destructive/50">
      <CardContent className="flex flex-col items-start gap-3 py-6">
        <div className="text-sm text-destructive">加载失败：{message}</div>
        <Button onClick={onRetry} variant="outline" size="sm">
          重试
        </Button>
      </CardContent>
    </Card>
  );
}
