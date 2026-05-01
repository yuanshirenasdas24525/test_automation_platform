/** 项目管理主页：左侧模块树（支持拖拽）+ 右侧版本迭代列表。 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderOpen,
  FolderPlus,
  GanttChart,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { ApiError, type ModulePickerNode, modulesApi, versionsApi } from "@/lib/api";
import type { ProjectVersion, VersionStatus } from "@/types/domain";

const STATUS_META: Record<VersionStatus, { label: string; tone: string }> = {
  planning: { label: "规划中", tone: "text-blue-700 bg-blue-50 ring-blue-200" },
  developing: { label: "开发中", tone: "text-amber-700 bg-amber-50 ring-amber-200" },
  testing: { label: "测试中", tone: "text-violet-700 bg-violet-50 ring-violet-200" },
  released: { label: "已发布", tone: "text-emerald-700 bg-emerald-50 ring-emerald-200" },
  archived: { label: "已归档", tone: "text-slate-600 bg-slate-100 ring-slate-200" },
};

export function ProjectManagementPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [expandedModules, setExpandedModules] = useState<Set<number>>(new Set());
  const [editingModule, setEditingModule] = useState<
    { mode: "create"; parentId?: number | null } | { mode: "rename"; mod: ModulePickerNode } | null
  >(null);
  const [creatingVersion, setCreatingVersion] = useState(false);
  const [dragModuleId, setDragModuleId] = useState<number | null>(null);
  const [dropTargetId, setDropTargetId] = useState<number | null>(null);
  const [selectedModuleId, setSelectedModuleId] = useState<number | null>(null);
  const [versionFilterStatus, setVersionFilterStatus] = useState<string>("all");
  const [versionFilterDates, setVersionFilterDates] = useState<{ start: string; end: string }>({ start: "", end: "" });

  const modulesQuery = useQuery({
    queryKey: ["modules", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: Number.isFinite(projectId),
  });

  const versionsQuery = useQuery({
    queryKey: ["versions", projectId],
    queryFn: () => versionsApi.list(projectId),
    enabled: Number.isFinite(projectId),
  });

  const invalidateModules = () => queryClient.invalidateQueries({ queryKey: ["modules", projectId] });

  const removeModule = useMutation({
    mutationFn: (mid: number) => modulesApi.remove(mid),
    onSuccess: () => { toast.success("已删除"); invalidateModules(); },
    onError: (e) => toast.error((e as ApiError).message),
  });

  const moveModule = useMutation({
    mutationFn: ({ id: mid, targetParentId }: { id: number; targetParentId: number | null }) =>
      modulesApi.move(mid, targetParentId),
    onSuccess: () => { toast.success("已移动"); invalidateModules(); },
    onError: (e) => toast.error((e as ApiError).message),
  });

  const [rootDragOver, setRootDragOver] = useState(false);

  const handleDragStart = (e: React.DragEvent, mid: number) => {
    setDragModuleId(mid);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(mid));
  };
  const handleDragOver = (e: React.DragEvent, mid: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDropTargetId(mid);
  };
  const handleDragLeave = () => setDropTargetId(null);
  const handleDrop = (e: React.DragEvent, targetId: number) => {
    e.preventDefault();
    e.stopPropagation();
    setDropTargetId(null);
    const draggedId = Number(e.dataTransfer.getData("text/plain"));
    setDragModuleId(null);
    if (!draggedId || draggedId === targetId) return;

    const dragged = modules.find((m) => m.id === draggedId);
    const target = modules.find((m) => m.id === targetId);
    if (!dragged || !target) return;

    // 拖到另一个模块上 → 变成其子模块
    moveModule.mutate({ id: draggedId, targetParentId: targetId });
  };
  const handleRootDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setRootDragOver(true);
  };
  const handleRootDragLeave = () => setRootDragOver(false);
  const handleDropToRoot = (e: React.DragEvent) => {
    e.preventDefault();
    setRootDragOver(false);
    const draggedId = Number(e.dataTransfer.getData("text/plain"));
    if (draggedId) moveModule.mutate({ id: draggedId, targetParentId: null });
    setDragModuleId(null);
  };

  // 排序：在同级中上移/下移，使用已有 PATCH /api/reorder
  const reorderModule = (mid: number, direction: "up" | "down") => {
    const m = modules.find((x) => x.id === mid);
    if (!m) return;
    const siblings = modules.filter((x) => (x.parent_id ?? null) === (m.parent_id ?? null)).sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
    const idx = siblings.findIndex((x) => x.id === mid);
    if (idx < 0) return;
    const targetIdx = direction === "up" ? idx - 1 : idx + 1;
    if (targetIdx < 0 || targetIdx >= siblings.length) return;
    [siblings[idx], siblings[targetIdx]] = [siblings[targetIdx], siblings[idx]];
    const items = siblings.map((x, i) => ({ type: "module", id: x.id, new_order: i }));
    modulesApi.reorder(items).then(() => invalidateModules()).catch((e) => toast.error((e as ApiError).message));
  };

  const modules = modulesQuery.data ?? [];
  const roots = modules.filter((m) => !m.parent_id);
  const childrenByParent = new Map<number | null, ModulePickerNode[]>();
  for (const m of modules) {
    const key = m.parent_id ?? null;
    if (!childrenByParent.has(key)) childrenByParent.set(key, []);
    childrenByParent.get(key)!.push(m);
  }

  const toggleExpand = (mid: number) => {
    setExpandedModules((prev) => {
      const next = new Set(prev);
      next.has(mid) ? next.delete(mid) : next.add(mid);
      return next;
    });
  };

  const renderModule = (m: ModulePickerNode, depth: number): React.ReactNode => {
    const children = childrenByParent.get(m.id) ?? [];
    const expanded = expandedModules.has(m.id);
    const hasChildren = children.length > 0;
    const isDragOver = dropTargetId === m.id;
    return (
      <div key={m.id}>
        <div
          draggable
          onDragStart={(e) => handleDragStart(e, m.id)}
          onDragOver={(e) => handleDragOver(e, m.id)}
          onDragLeave={handleDragLeave}
          onDrop={(e) => handleDrop(e, m.id)}
          onDragEnd={() => { setDragModuleId(null); setDropTargetId(null); }}
          className={cn(
            "flex items-center gap-1 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-muted group transition-colors",
            isDragOver && "bg-blue-100 ring-2 ring-blue-400",
            dragModuleId === m.id && "opacity-50",
          )}
          style={{ marginLeft: depth * 16 }}
          onClick={() => { if (hasChildren) toggleExpand(m.id); setSelectedModuleId(m.id); }}
        >
          {hasChildren
            ? expanded ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />
            : <span className="w-3 shrink-0" />}
          {selectedModuleId === m.id ? <FolderOpen className="h-4 w-4 text-blue-500" /> : expanded ? <FolderOpen className="h-4 w-4 text-amber-500" /> : <Folder className="h-4 w-4 text-amber-500" />}
          <span className="flex-1 truncate">{m.name}</span>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-6 w-6 opacity-0 group-hover:opacity-100"
                onClick={(e) => e.stopPropagation()}>
                <MoreHorizontal className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setEditingModule({ mode: "create", parentId: m.id }); }}>
                <FolderPlus className="h-4 w-4 mr-1" />新建子模块
              </DropdownMenuItem>
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setEditingModule({ mode: "rename", mod: m }); }}>
                <Pencil className="h-4 w-4 mr-1" />重命名
              </DropdownMenuItem>
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); reorderModule(m.id, "up"); }}>
                <ArrowUp className="h-4 w-4 mr-1" />上移
              </DropdownMenuItem>
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); reorderModule(m.id, "down"); }}>
                <ArrowDown className="h-4 w-4 mr-1" />下移
              </DropdownMenuItem>
              <DropdownMenuItem className="text-destructive" onClick={(e) => {
                e.stopPropagation();
                if (confirm(`删除模块"${m.name}"及其所有子模块和用例？`)) removeModule.mutate(m.id);
              }}>
                <Trash2 className="h-4 w-4 mr-1" />删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {expanded && children.map((c) => renderModule(c, depth + 1))}
      </div>
    );
  };

  const allVersions = versionsQuery.data ?? [];

  // 全局筛选：状态 + 日期（对所有模块下的版本生效）
  const globallyFiltered = allVersions
    .filter((v) => versionFilterStatus === "all" || v.status === versionFilterStatus)
    .filter((v) => {
      if (versionFilterDates.start && v.planned_start_at && v.planned_start_at < versionFilterDates.start) return false;
      if (versionFilterDates.end && v.planned_end_at && v.planned_end_at > versionFilterDates.end) return false;
      return true;
    });

  // 再叠加模块选中过滤
  const versions = selectedModuleId
    ? globallyFiltered.filter((v) => (v.associated_module_ids ?? []).includes(selectedModuleId))
    : globallyFiltered;

  return (
    <div className="flex h-[calc(100vh-120px)] gap-0">
      <div className="w-64 shrink-0 border-r bg-muted/20 p-3 overflow-y-auto">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-medium text-muted-foreground">模块树 <span className="text-muted-foreground/40">（拖拽移动）</span></span>
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setEditingModule({ mode: "create" })}>
            <Plus className="h-3 w-3" />
          </Button>
        </div>
        <div
          onDragOver={handleRootDragOver}
          onDragLeave={handleRootDragLeave}
          onDrop={handleDropToRoot}
          className={cn("min-h-[40px]", rootDragOver && "bg-blue-100 ring-2 ring-blue-400 rounded")}
        >
          <div className={cn(
            "text-[10px] text-center py-1 rounded border border-dashed transition-colors mb-1",
            rootDragOver ? "border-blue-400 text-blue-600 bg-blue-50" : "border-transparent text-muted-foreground/30",
          )}>
            {rootDragOver ? "释放到根层级" : "拖到此处移到根层级"}
          </div>
          {modulesQuery.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : roots.length === 0 ? (
            <p className="text-xs text-muted-foreground py-4">暂无模块，拖拽或点击 [+] 创建</p>
          ) : (
            <div className="space-y-0.5">{roots.map((m) => renderModule(m, 0))}</div>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold flex items-center gap-2"><GanttChart className="h-5 w-5" />版本迭代</h2>
            {selectedModuleId && (
              <p className="text-xs mt-0.5">
                模块：<span className="font-medium text-blue-600">{modules.find((m) => m.id === selectedModuleId)?.name}</span>
                <span className="text-muted-foreground ml-1">（仅显示关联迭代，筛选为全局）</span>
                <Button variant="ghost" size="sm" className="h-5 text-xs ml-1" onClick={() => setSelectedModuleId(null)}>清除</Button>
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {/* 状态筛选 */}
            <Select value={versionFilterStatus} onValueChange={setVersionFilterStatus}>
              <SelectTrigger className="h-8 w-24 text-xs">
                <SelectValue placeholder="状态" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="planning">规划中</SelectItem>
                <SelectItem value="developing">开发中</SelectItem>
                <SelectItem value="testing">测试中</SelectItem>
                <SelectItem value="released">已发布</SelectItem>
                <SelectItem value="archived">已归档</SelectItem>
              </SelectContent>
            </Select>
            {/* 日期筛选 */}
            <Input type="date" className="h-8 w-32 text-xs" value={versionFilterDates.start}
              onChange={(e) => setVersionFilterDates((p) => ({ ...p, start: e.target.value }))} placeholder="开始" />
            <span className="text-xs text-muted-foreground">-</span>
            <Input type="date" className="h-8 w-32 text-xs" value={versionFilterDates.end}
              onChange={(e) => setVersionFilterDates((p) => ({ ...p, end: e.target.value }))} placeholder="结束" />
            <Button size="sm" onClick={() => setCreatingVersion(true)}><Plus className="h-4 w-4" />新建</Button>
          </div>
        </div>
        {versionsQuery.isLoading ? (
          <div className="space-y-3"><Skeleton className="h-24 w-full" /><Skeleton className="h-24 w-full" /></div>
        ) : versions.length === 0 ? (
          <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">
            {selectedModuleId ? "该模块下暂无关联版本" : allVersions.length === 0 ? "还没有版本迭代" : "没有符合条件的版本"}
          </CardContent></Card>
        ) : (
          <div className="space-y-3">
            {versions.map((v) => (
              <VersionCard key={v.id} version={v} modules={modules}
                onEdit={() => navigate(`/projects/${projectId}/versions/${v.id}`)}
                onDelete={() => {
                  if (confirm(`删除版本"${v.version_name}"？`)) {
                    versionsApi.remove(projectId, v.id).then(() => {
                      toast.success("已删除");
                      queryClient.invalidateQueries({ queryKey: ["versions", projectId] });
                    }).catch((e) => toast.error((e as ApiError).message));
                  }
                }}
              />
            ))}
          </div>
        )}
      </div>

      <ModuleEditDialog state={editingModule} projectId={projectId}
        onClose={() => setEditingModule(null)} onDone={() => { invalidateModules(); setEditingModule(null); }} />
      <VersionCreateDialog open={creatingVersion} projectId={projectId} moduleId={selectedModuleId}
        onClose={() => setCreatingVersion(false)}
        onDone={(v) => { queryClient.invalidateQueries({ queryKey: ["versions", projectId] }); setCreatingVersion(false); navigate(`/projects/${projectId}/versions/${v.id}`); }} />
    </div>
  );
}

function VersionCard({ version, modules, onEdit, onDelete }: {
  version: ProjectVersion; modules: ModulePickerNode[]; onEdit: () => void; onDelete: () => void;
}) {
  const meta = STATUS_META[version.status];
  const names = (version.associated_module_ids ?? [])
    .map((mid) => modules.find((m) => m.id === mid)?.name).filter(Boolean) as string[];
  return (
    <Card className="cursor-pointer hover:shadow-sm transition-shadow" onClick={onEdit}>
      <CardContent className="py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">{version.version_name}</span>
              <span className={cn("rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", meta.tone)}>{meta.label}</span>
            </div>
            {version.display_name && <p className="text-xs text-muted-foreground mt-0.5">{version.display_name}</p>}
            {names.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {names.map((n) => <span key={n} className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-secondary-foreground">{n}</span>)}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={(e) => { e.stopPropagation(); onEdit(); }}><Pencil className="h-3 w-3" /></Button>
            <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive" onClick={(e) => { e.stopPropagation(); onDelete(); }}><Trash2 className="h-3 w-3" /></Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ModuleEditDialog({ state, projectId, onClose, onDone }: {
  state: { mode: "create"; parentId?: number | null } | { mode: "rename"; mod: ModulePickerNode } | null;
  projectId: number; onClose: () => void; onDone: () => void;
}) {
  const [name, setName] = useState("");
  const isRename = state?.mode === "rename";
  useEffect(() => {
    if (state?.mode === "rename") setName(state.mod.name);
    else setName("");
  }, [state]);
  const save = useMutation({
    mutationFn: async () => {
      if (isRename && state?.mode === "rename") return modulesApi.rename(state.mod.id, name.trim());
      const parentId = state?.mode === "create" ? state.parentId : null;
      return modulesApi.create({ project_id: projectId, name: name.trim(), parent_id: parentId ?? undefined });
    },
    onSuccess: () => { toast.success(isRename ? "已重命名" : "已创建"); onDone(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  if (!state) return null;
  return (
    <Dialog open={!!state} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader><DialogTitle>{isRename ? "重命名模块" : "新建模块"}</DialogTitle></DialogHeader>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="模块名称" autoFocus />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={() => save.mutate()} disabled={!name.trim() || save.isPending}>
            {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}{isRename ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VersionCreateDialog({ open, projectId, moduleId, onClose, onDone }: {
  open: boolean; projectId: number; moduleId: number | null; onClose: () => void; onDone: (v: ProjectVersion) => void;
}) {
  const [versionName, setVersionName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [status, setStatus] = useState<VersionStatus>("planning");
  const create = useMutation({
    mutationFn: () => versionsApi.create(projectId, {
      version_name: versionName.trim(),
      display_name: displayName.trim() || undefined,
      status,
      module_ids: moduleId ? [moduleId] : [],
    }),
    onSuccess: (v) => { toast.success("版本已创建"); onDone(v); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader><DialogTitle>新建迭代版本</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1"><Label className="text-xs">版本名 *</Label><Input value={versionName} onChange={(e) => setVersionName(e.target.value)} placeholder="v2.3.0" autoFocus /></div>
          <div className="space-y-1"><Label className="text-xs">展示名</Label><Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="如：2026年Q2" /></div>
          <div className="space-y-1"><Label className="text-xs">状态</Label>
            <Select value={status} onValueChange={(v) => setStatus(v as VersionStatus)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="planning">规划中</SelectItem>
                <SelectItem value="developing">开发中</SelectItem>
                <SelectItem value="testing">测试中</SelectItem>
                <SelectItem value="released">已发布</SelectItem>
                <SelectItem value="archived">已归档</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={() => create.mutate()} disabled={!versionName.trim() || create.isPending}>
            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
