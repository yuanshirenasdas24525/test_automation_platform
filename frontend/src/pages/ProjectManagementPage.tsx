/** 项目管理主页：需求池 / 版本迭代 标签页 + 左侧模块树。 */
import { useEffect, useMemo, useState } from "react";
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
  Inbox,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Settings,
  Sparkles,
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PriorityBadge } from "@/components/badges/PriorityBadge";
import { RequirementStatusBadge } from "@/components/badges/RequirementStatusBadge";
import { cn } from "@/lib/utils";
import { ApiError, type ModulePickerNode, modulesApi, versionsApi, requirementsApi } from "@/lib/api";
import type { ProjectVersion, Requirement, VersionStatus } from "@/types/domain";
import { RequirementDetailDrawer } from "./requirements/RequirementDetailDrawer";
import { ProjectConfigTab } from "./config/ProjectConfigTab";

const STATUS_META: Record<VersionStatus, { label: string; tone: string }> = {
  planning: { label: "规划中", tone: "text-blue-700 bg-blue-50 ring-blue-200" },
  developing: { label: "开发中", tone: "text-amber-700 bg-amber-50 ring-amber-200" },
  testing: { label: "测试中", tone: "text-violet-700 bg-violet-50 ring-violet-200" },
  ready_to_release: { label: "待发版", tone: "text-cyan-700 bg-cyan-50 ring-cyan-200" },
  released: { label: "已发布", tone: "text-emerald-700 bg-emerald-50 ring-emerald-200" },
  archived: { label: "已归档", tone: "text-slate-600 bg-slate-100 ring-slate-200" },
};

export function ProjectManagementPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState("pool");
  const [expandedModules, setExpandedModules] = useState<Set<number>>(new Set());
  const [editingModule, setEditingModule] = useState<
    { mode: "create"; parentId?: number | null } | { mode: "rename"; mod: ModulePickerNode } | null
  >(null);
  const [creatingVersion, setCreatingVersion] = useState(false);
  const [editingVersion, setEditingVersion] = useState<ProjectVersion | null>(null);
  const [dragModuleId, setDragModuleId] = useState<number | null>(null);
  const [dropTargetId, setDropTargetId] = useState<number | null>(null);
  const [selectedModuleId, setSelectedModuleId] = useState<number | null>(null);
  const [versionFilterStatus, setVersionFilterStatus] = useState<string>("all");
  const [versionFilterDates, setVersionFilterDates] = useState<{ start: string; end: string }>({ start: "", end: "" });
  const [detailReq, setDetailReq] = useState<Requirement | null>(null);

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

  const requirementsQuery = useQuery({
    queryKey: ["requirements", projectId, "pool", selectedModuleId],
    queryFn: () => requirementsApi.list(projectId, {
      tree: true,
      module_id: selectedModuleId ?? undefined,
    }),
    enabled: Number.isFinite(projectId),
  });

  const invalidateModules = () => queryClient.invalidateQueries({ queryKey: ["modules", projectId] });
  const invalidateRequirements = () => queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });

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

  const removeRequirement = useMutation({
    mutationFn: (rid: number) => requirementsApi.remove(rid),
    onSuccess: () => { toast.success("已删除"); invalidateRequirements(); },
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

  const modules = useMemo(() => modulesQuery.data ?? [], [modulesQuery.data]);
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
      if (next.has(mid)) next.delete(mid);
      else next.add(mid);
      return next;
    });
  };

  const moduleNames = useMemo(() => {
    const m = new Map<number, string>();
    for (const mod of modules) m.set(mod.id, mod.name);
    return m;
  }, [modules]);

  const versionNames = useMemo(() => {
    const m = new Map<number, string>();
    for (const v of versionsQuery.data ?? []) m.set(v.id, v.display_name || v.version_name);
    return m;
  }, [versionsQuery.data]);

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

  const globallyFiltered = allVersions
    .filter((v) => versionFilterStatus === "all" || v.status === versionFilterStatus)
    .filter((v) => {
      if (versionFilterDates.start && v.planned_start_at && v.planned_start_at < versionFilterDates.start) return false;
      if (versionFilterDates.end && v.planned_end_at && v.planned_end_at > versionFilterDates.end) return false;
      return true;
    });

  const versions = selectedModuleId
    ? globallyFiltered.filter((v) => (v.associated_module_ids ?? []).includes(selectedModuleId))
    : globallyFiltered;

  const reqs = requirementsQuery.data ?? [];
  // ---- 需求池树形展开 ----
  const [reqExpanded, setReqExpanded] = useState<Set<number>>(new Set());
  const toggleReqExpand = (rid: number) => {
    setReqExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(rid)) next.delete(rid);
      else next.add(rid);
      return next;
    });
  };

  // ---- 版本删除处理 ----
  const handleVersionDelete = async (v: ProjectVersion) => {
    const confirmed = confirm(
      `删除版本"${v.version_name}"？\n\n` +
      "如果有需求关联到此版本，将自动解除关联移回需求池。"
    );
    if (!confirmed) return;
    try {
      await versionsApi.remove(projectId, v.id);
      toast.success("已删除，关联需求已自动移回需求池");
      queryClient.invalidateQueries({ queryKey: ["versions", projectId] });
      invalidateRequirements();
    } catch (e) {
      toast.error((e as ApiError).message);
    }
  };

  // ---- 模块树渲染 ----
  const moduleTree = (
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
  );

  return (
    <div className="flex flex-col h-[calc(100vh-120px)]">
      <div className="flex flex-1 min-h-0 gap-0">
        {moduleTree}
        <Tabs value={activeTab} onValueChange={(v) => { setActiveTab(v); setSelectedModuleId(null); }} className="flex-1 flex flex-col min-h-0">
          <div className="flex items-center justify-between px-6 pt-4 pb-2 border-b">
            <TabsList>
              <TabsTrigger value="pool"><Inbox className="h-4 w-4 mr-1" />需求池</TabsTrigger>
              <TabsTrigger value="versions"><GanttChart className="h-4 w-4 mr-1" />版本迭代</TabsTrigger>
              <TabsTrigger value="config"><Settings className="h-4 w-4 mr-1" />项目配置</TabsTrigger>
            </TabsList>
            {activeTab === "pool" ? (
              <Button size="sm" variant="ghost" onClick={() => navigate(`/projects/${projectId}/requirements`)}>
                进入需求管理
              </Button>
            ) : (
              <div className="flex items-center gap-2">
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
                <Input type="date" className="h-8 w-32 text-xs" value={versionFilterDates.start}
                  onChange={(e) => setVersionFilterDates((p) => ({ ...p, start: e.target.value }))} placeholder="开始" />
                <span className="text-xs text-muted-foreground">-</span>
                <Input type="date" className="h-8 w-32 text-xs" value={versionFilterDates.end}
                  onChange={(e) => setVersionFilterDates((p) => ({ ...p, end: e.target.value }))} placeholder="结束" />
                <Button size="sm" onClick={() => setCreatingVersion(true)}><Plus className="h-4 w-4" />新建</Button>
              </div>
            )}
          </div>

          {/* ---- 需求池 ---- */}
          {activeTab === "pool" && (
            <div className="flex-1 overflow-y-auto p-6">
              {selectedModuleId && (
                <p className="text-xs mb-3">
                  模块：<span className="font-medium text-blue-600">{moduleNames.get(selectedModuleId)}</span>
                  <Button variant="ghost" size="sm" className="h-5 text-xs ml-1" onClick={() => setSelectedModuleId(null)}>清除</Button>
                </p>
              )}
              {requirementsQuery.isLoading ? (
                <div className="space-y-2"><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /></div>
              ) : reqs.length === 0 ? (
                <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">
                  {selectedModuleId ? "该模块下暂无需求" : "暂无需求，点击上方按钮进入需求管理页创建"}
                </CardContent></Card>
              ) : (
                <div className="overflow-hidden rounded-md border">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-xs text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium w-24">编号</th>
                        <th className="px-3 py-2 text-left font-medium">名称</th>
                        <th className="px-3 py-2 text-left font-medium w-20">优先级</th>
                        <th className="px-3 py-2 text-left font-medium w-32">迭代</th>
                        <th className="px-3 py-2 text-left font-medium w-28">状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reqs.map((r) => (
                        <PoolRequirementRows
                          key={r.id}
                          req={r}
                          expanded={reqExpanded}
                          onToggle={toggleReqExpand}
                          versionNames={versionNames}
                          onViewDetail={(r) => setDetailReq(r)}
                          onDelete={(req: Requirement) => {
                            if (confirm(`删除需求"${req.title}"？同时会删除其子需求。`))
                              removeRequirement.mutate(req.id);
                          }}
                          depth={0}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ---- 版本迭代 ---- */}
          {activeTab === "versions" && (
            <div className="flex-1 overflow-y-auto p-6">
              {selectedModuleId && (
                <p className="text-xs mb-3">
                  模块：<span className="font-medium text-blue-600">{moduleNames.get(selectedModuleId)}</span>
                  <span className="text-muted-foreground ml-1">（仅显示关联迭代，筛选为全局）</span>
                  <Button variant="ghost" size="sm" className="h-5 text-xs ml-1" onClick={() => setSelectedModuleId(null)}>清除</Button>
                </p>
              )}
              {versionsQuery.isLoading ? (
                <div className="space-y-3"><Skeleton className="h-24 w-full" /><Skeleton className="h-24 w-full" /></div>
              ) : versions.length === 0 ? (
                <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">
                  {selectedModuleId ? "该模块下暂无关联版本" : allVersions.length === 0 ? "还没有版本迭代" : "没有符合条件的版本"}
                </CardContent></Card>
              ) : (
                <div className="space-y-3">
                  {versions.map((v) => (
                    <VersionCard key={v.id} version={v} modules={modules} projectId={projectId}
                      onEdit={() => setEditingVersion(v)}
                      onDelete={() => handleVersionDelete(v)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
          {activeTab === "config" && (
            <ProjectConfigTab projectId={projectId} />
          )}
        </Tabs>
      </div>

      <RequirementDetailDrawer
        req={detailReq}
        open={!!detailReq}
        onClose={() => setDetailReq(null)}
        onEdit={() => {
          setDetailReq(null);
          navigate(`/projects/${projectId}/requirements`);
        }}
        onViewRequirement={() => {
          navigate(`/projects/${projectId}/requirements`);
        }}
        moduleNames={moduleNames}
        versionNames={versionNames}
      />

      <ModuleEditDialog state={editingModule} projectId={projectId}
        onClose={() => setEditingModule(null)} onDone={() => { invalidateModules(); setEditingModule(null); }} />
      <VersionCreateDialog open={creatingVersion} projectId={projectId} moduleId={selectedModuleId}
        onClose={() => setCreatingVersion(false)}
        onDone={(v) => { queryClient.invalidateQueries({ queryKey: ["versions", projectId] }); setCreatingVersion(false); if (v) navigate(`/projects/${projectId}/versions/${v.id}`); }} />
      <VersionCreateDialog
        open={!!editingVersion}
        projectId={projectId}
        moduleId={null}
        editingVersion={editingVersion}
        onClose={() => setEditingVersion(null)}
        onDone={() => {
          queryClient.invalidateQueries({ queryKey: ["versions", projectId] });
          setEditingVersion(null);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 需求池树形行
// ---------------------------------------------------------------------------
function PoolRequirementRows({
  req,
  expanded,
  onToggle,
  versionNames,
  onViewDetail,
  onDelete,
  depth,
}: {
  req: Requirement;
  expanded: Set<number>;
  onToggle: (id: number) => void;
  versionNames: Map<number, string>;
  onViewDetail: (r: Requirement) => void;
  onDelete: (r: Requirement) => void;
  depth: number;
}) {
  const children = req.children ?? [];
  const isOpen = expanded.has(req.id);

  return (
    <>
      <tr className="border-t hover:bg-accent/30 cursor-pointer" onClick={() => onViewDetail(req)}>
        <td className="px-3 py-2 text-xs text-muted-foreground font-mono">REQ-{req.id}</td>
        <td className="px-3 py-2">
          <div className="flex items-start gap-1" style={{ paddingLeft: depth * 20 }}>
            {children.length > 0 ? (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onToggle(req.id); }}
                className="mt-0.5 text-muted-foreground hover:text-foreground"
              >
                {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              </button>
            ) : (
              <span className="inline-block w-3.5" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="font-medium">{req.title}</span>
                {req.source === "ai_generated" && (
                  <span className="inline-flex items-center gap-0.5 rounded bg-violet-50 px-1 py-0.5 text-[10px] text-violet-700 ring-1 ring-inset ring-violet-200">
                    <Sparkles className="h-2.5 w-2.5" />AI
                  </span>
                )}
              </div>
              {req.description ? (
                <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{req.description}</div>
              ) : null}
            </div>
          </div>
        </td>
        <td className="px-3 py-2"><PriorityBadge priority={req.priority} /></td>
        <td className="px-3 py-2 text-xs text-muted-foreground">
          {req.version_id && versionNames.has(req.version_id) ? versionNames.get(req.version_id) : "—"}
        </td>
        <td className="px-3 py-2">
          {req.system_status ? (
            <RequirementStatusBadge status={req.system_status} />
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          )}
        </td>
      </tr>
      {isOpen && children.map((c) => (
        <PoolRequirementRows
          key={c.id}
          req={c}
          expanded={expanded}
          onToggle={onToggle}
          versionNames={versionNames}
          onViewDetail={onViewDetail}
          onDelete={onDelete}
          depth={depth + 1}
        />
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// 版本卡片
// ---------------------------------------------------------------------------
function VersionCard({ version, modules, projectId, onEdit, onDelete }: {
  version: ProjectVersion; modules: ModulePickerNode[]; projectId: number; onEdit: () => void; onDelete: () => void;
}) {
  const navigate = useNavigate();
  const meta = STATUS_META[version.status];
  const names = (version.associated_module_ids ?? [])
    .map((mid) => modules.find((m) => m.id === mid)?.name).filter(Boolean) as string[];
  return (
    <Card className="cursor-pointer hover:shadow-sm transition-shadow"
      onClick={() => navigate(`/projects/${projectId}/versions/${version.id}`)}>
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

// ---------------------------------------------------------------------------
// 模块编辑对话框
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// 版本创建对话框
// ---------------------------------------------------------------------------
function VersionCreateDialog({ open, projectId, moduleId, editingVersion, onClose, onDone }: {
  open: boolean; projectId: number; moduleId: number | null;
  editingVersion?: ProjectVersion | null;
  onClose: () => void; onDone: (v?: ProjectVersion) => void;
}) {
  const isEdit = !!editingVersion;
  const [versionName, setVersionName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [status, setStatus] = useState<VersionStatus>("planning");

  useEffect(() => {
    if (open) {
      if (editingVersion) {
        setVersionName(editingVersion.version_name);
        setDisplayName(editingVersion.display_name || "");
        setStatus(editingVersion.status);
      } else {
        setVersionName("");
        setDisplayName("");
        setStatus("planning");
      }
    }
  }, [open, editingVersion]);

  const create = useMutation({
    mutationFn: async () => {
      if (isEdit && editingVersion) {
        return versionsApi.update(projectId, editingVersion.id, {
          version_name: versionName.trim(),
          display_name: displayName.trim() || undefined,
          status,
        } as Parameters<typeof versionsApi.update>[2]);
      }
      return versionsApi.create(projectId, {
        version_name: versionName.trim(),
        display_name: displayName.trim() || undefined,
        status,
        module_ids: moduleId ? [moduleId] : [],
      });
    },
    onSuccess: (v) => { toast.success(isEdit ? "版本已更新" : "版本已创建"); onDone(v); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader><DialogTitle>{isEdit ? "编辑迭代版本" : "新建迭代版本"}</DialogTitle></DialogHeader>
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
            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}{isEdit ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
