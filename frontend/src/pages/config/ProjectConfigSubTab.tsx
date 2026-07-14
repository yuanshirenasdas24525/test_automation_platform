/**
 * ProjectConfigSubTab —— 项目配置卡片列表。
 */
import { useMemo, useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  Pencil,
  Plus,
  Loader2,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
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
import { configApi, type ConfigItem, type ConfigSchemaItem } from "@/lib/api";

type Category = "api" | "web" | "app" | "other";

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
export function ProjectConfigSubTab({ projectId, category }: { projectId: number; category: Category }) {
  const queryClient = useQueryClient();

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["project-config", projectId, category],
    queryFn: () => configApi.list(category, projectId),
  });

  const schemaQuery = useQuery({
    queryKey: ["config-schema", category],
    queryFn: () => configApi.schema(category),
    staleTime: 5 * 60 * 1000,
  });

  const [editing, setEditing] = useState<ConfigItem | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState<ConfigItem | null>(null); // null=全新, ConfigItem=预填推荐值
  const [addToGroup, setAddToGroup] = useState<string | null>(null); // 快捷添加到指定组

  const grouped = useMemo(() => {
    const map = new Map<string, ConfigItem[]>();
    for (const it of items) {
      const arr = map.get(it.config_group);
      if (arr) { arr.push(it); } else { map.set(it.config_group, [it]); }
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [items]);

  const saveMutation = useMutation({
    mutationFn: (item: Omit<ConfigItem, "id">) => configApi.save(item),
    onSuccess: () => {
      toast.success("已保存");
      queryClient.invalidateQueries({ queryKey: ["project-config", projectId, category] });
      setCreateOpen(false);
      setCreating(null);
      setEditing(null);
      setAddToGroup(null);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => configApi.remove(id),
    onSuccess: () => {
      toast.success("已删除");
      queryClient.invalidateQueries({ queryKey: ["project-config", projectId, category] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  if (isLoading) {
    return <div className="flex items-center justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="space-y-4 p-4">
      {/* 工具栏 */}
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => {
          setCreating(null);
          setCreateOpen(true);
        }}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          添加配置
        </Button>
      </div>

      {/* 推荐配置面板（默认收起） */}
      {schemaQuery.data && schemaQuery.data.length > 0 ? (
        <RecommendedPanel
          items={schemaQuery.data}
          existing={items}
          onFill={(schemaItem) => {
            setCreating({
              id: 0, config_group: schemaItem.config_group, config_key: schemaItem.key,
              config_value: schemaItem.example || schemaItem.default || "", category, project_id: projectId,
            });
            setCreateOpen(true);
          }}
        />
      ) : null}

      {/* 配置组卡片 */}
      {grouped.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          暂无 {CATEGORY_LABELS[category]} 配置。
        </div>
      ) : (
        <div className="space-y-4">
          {grouped.map(([group, rows]) => (
            <Card key={group}>
              <CardContent className="p-0">
                <div className="flex items-center justify-between border-b px-4 py-2">
                  <div className="text-sm font-semibold">{group}</div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1 text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => setAddToGroup(group)}
                  >
                    <Plus className="h-3 w-3" />添加项
                  </Button>
                </div>
                <div className="divide-y">
                  {rows.map((item) => (
                    <div key={item.id} className="grid grid-cols-[200px_1fr_auto] items-center gap-4 px-4 py-2 text-sm">
                      <div className="flex items-center gap-2 font-mono text-xs">
                        <span>{item.config_key}</span>
                      </div>
                      <div className="truncate font-mono text-xs text-muted-foreground">
                        {item.config_value || <span className="italic">（空）</span>}
                      </div>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="h-8 w-8">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onSelect={(e) => { e.preventDefault(); setEditing(item); }}>
                            <Pencil className="h-4 w-4" />编辑
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem className="text-destructive focus:text-destructive"
                            onSelect={(e) => { e.preventDefault(); deleteMutation.mutate(item.id); }}>
                            <Trash2 className="h-4 w-4" />删除
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

      {/* 编辑 / 新增弹窗 */}
      <ConfigFormDialog
        open={createOpen || !!editing || addToGroup != null}
        onOpenChange={(v) => {
          if (!v) { setCreateOpen(false); setCreating(null); setEditing(null); setAddToGroup(null); }
        }}
        initial={editing ?? creating}
        prefillGroup={addToGroup}
        category={category}
        projectId={projectId}
        onSave={(item) => saveMutation.mutate(item)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 推荐配置面板（默认收起）
// ---------------------------------------------------------------------------
function RecommendedPanel({
  items, existing, onFill,
}: {
  items: ConfigSchemaItem[];
  existing: ConfigItem[];
  onFill: (item: ConfigSchemaItem) => void;
}) {
  const [collapsed, setCollapsed] = useState(true);

  const existingKeys = useMemo(() => {
    const s = new Set<string>();
    for (const it of existing) s.add(`${it.config_group}.${it.config_key}`);
    return s;
  }, [existing]);

  return (
    <Card className="border-dashed bg-muted/30">
      <div
        className="flex cursor-pointer items-center justify-between gap-4 px-4 py-3 select-none"
        onClick={() => setCollapsed((v) => !v)}
      >
        <div>
          <div className="text-sm font-semibold">推荐配置项</div>
          <div className="text-xs text-muted-foreground">
            当前分类下常用的配置键模板。点「填入」会把示例值预填进表单后保存。
          </div>
        </div>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          {collapsed ? "展开" : "收起"}
        </div>
      </div>
      {!collapsed ? (
        <div className="grid grid-cols-1 gap-2 border-t px-4 pb-4 pt-3 md:grid-cols-2">
          {items.map((it) => {
            const already = existingKeys.has(`${it.config_group}.${it.key}`);
            return (
              <div key={`${it.config_group}.${it.key}`} className="flex items-start justify-between gap-3 rounded border bg-background px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="text-xs font-semibold">{it.config_group}.{it.key}</code>
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">{it.type}</span>
                    {already ? <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-700">已配置</span> : null}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{it.description}</div>
                  <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                    默认: {it.default || "（空）"} · 示例: {it.example || "—"}
                  </div>
                </div>
                <Button size="sm" variant="secondary" disabled={already} onClick={() => onFill(it)}>
                  填入
                </Button>
              </div>
            );
          })}
        </div>
      ) : null}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 表单弹窗
// ---------------------------------------------------------------------------
function ConfigFormDialog({
  open, onOpenChange, initial, prefillGroup, category, projectId, onSave,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  initial: ConfigItem | null;
  prefillGroup: string | null;
  category: Category;
  projectId: number;
  onSave: (item: Omit<ConfigItem, "id">) => void;
}) {
  const isEdit = !!initial?.id;
  const [group, setGroup] = useState("");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");

  // 弹窗打开时回填值
  useEffect(() => {
    if (open) {
      if (initial) {
        setGroup(initial.config_group ?? "");
        setKey(initial.config_key ?? "");
        setValue(initial.config_value ?? "");
      } else if (prefillGroup) {
        setGroup(prefillGroup);
        setKey("");
        setValue("");
      } else {
        setGroup("");
        setKey("");
        setValue("");
      }
    }
  }, [open, initial, prefillGroup]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑配置" : "新增配置"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs">配置组</Label>
            <Input
              className="h-8"
              value={group}
              onChange={(e) => setGroup(e.target.value)}
              placeholder="例如 host"
              disabled={isEdit || !!prefillGroup}
            />
          </div>
          <div>
            <Label className="text-xs">键</Label>
            <Input
              className="h-8"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="例如 url"
              disabled={isEdit}
            />
          </div>
          <div>
            <Label className="text-xs">值</Label>
            <Input className="h-8" value={value} onChange={(e) => setValue(e.target.value)} placeholder="https://..." />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>取消</Button>
          <Button size="sm" onClick={() => {
            if (!group.trim() || !key.trim()) { toast.error("配置组和键不能为空"); return; }
            onSave({ config_group: group.trim(), config_key: key.trim(), config_value: value, category, project_id: projectId });
          }}>保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const CATEGORY_LABELS: Record<Category, string> = { api: "API", web: "Web", app: "App", other: "其他" };
