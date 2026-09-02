/** 知识库独立目录树（左栏，替代模块树）。自包含：查询 + 选择 + 增删改移。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Folder, FolderOpen, FolderPlus, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { ApiError, knowledgeFoldersApi } from "@/lib/api";
import type { KnowledgeFolderNode } from "@/types/domain";

export function KnowledgeFolderTree({
  projectId,
  selectedFolderId,
  onSelect,
}: {
  projectId: number;
  selectedFolderId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const foldersQuery = useQuery({
    queryKey: ["knowledge-folders", projectId],
    queryFn: () => knowledgeFoldersApi.list(projectId),
    enabled: Number.isFinite(projectId),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["knowledge-folders", projectId] });

  const createFolder = useMutation({
    mutationFn: (v: { name: string; parent_id: number | null }) =>
      knowledgeFoldersApi.create({ project_id: projectId, name: v.name, parent_id: v.parent_id }),
    onSuccess: () => { toast.success("已创建目录"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const renameFolder = useMutation({
    mutationFn: (v: { id: number; name: string }) => knowledgeFoldersApi.update(v.id, { name: v.name }),
    onSuccess: () => { toast.success("已重命名"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const removeFolder = useMutation({
    mutationFn: (id: number) => knowledgeFoldersApi.remove(id),
    onSuccess: (_d, id) => { toast.success("已删除目录"); if (id === selectedFolderId) onSelect(null); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });

  const toggle = (id: number) =>
    setExpanded((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const onCreate = (parentId: number | null) => {
    const name = window.prompt(parentId == null ? "新建根目录名称" : "新建子目录名称");
    if (name && name.trim()) createFolder.mutate({ name: name.trim(), parent_id: parentId });
  };
  const onRename = (f: KnowledgeFolderNode) => {
    const name = window.prompt("重命名目录", f.name);
    if (name && name.trim()) renameFolder.mutate({ id: f.id, name: name.trim() });
  };
  const onDelete = (f: KnowledgeFolderNode) => {
    if (window.confirm(`删除目录「${f.name}」？其中的文档与子目录会上移到父级，不会被删除。`))
      removeFolder.mutate(f.id);
  };

  const renderNode = (f: KnowledgeFolderNode, depth: number) => {
    const hasChildren = f.children.length > 0;
    const isOpen = expanded.has(f.id);
    const isSel = selectedFolderId === f.id;
    return (
      <div key={f.id}>
        <div
          className={cn(
            "group flex items-center gap-1 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-muted transition-colors",
            isSel && "bg-primary/10 text-primary font-medium",
          )}
          style={{ marginLeft: depth * 16 }}
          onClick={() => { if (hasChildren) toggle(f.id); onSelect(f.id); }}
        >
          {hasChildren
            ? isOpen ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />
            : <span className="w-3 shrink-0" />}
          {isSel || isOpen ? <FolderOpen className="h-4 w-4 text-blue-500 shrink-0" /> : <Folder className="h-4 w-4 text-amber-500 shrink-0" />}
          <span className="flex-1 truncate">{f.name}</span>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-6 w-6 opacity-0 group-hover:opacity-100"
                onClick={(e) => e.stopPropagation()}>
                <MoreHorizontal className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onCreate(f.id); }}>
                <FolderPlus className="h-4 w-4 mr-1" />新建子目录
              </DropdownMenuItem>
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onRename(f); }}>
                <Pencil className="h-4 w-4 mr-1" />重命名
              </DropdownMenuItem>
              <DropdownMenuItem className="text-destructive" onClick={(e) => { e.stopPropagation(); onDelete(f); }}>
                <Trash2 className="h-4 w-4 mr-1" />删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {hasChildren && isOpen && <div>{f.children.map((c) => renderNode(c, depth + 1))}</div>}
      </div>
    );
  };

  const roots = foldersQuery.data ?? [];

  return (
    <div className="w-64 shrink-0 border-r bg-muted/20 p-3 overflow-y-auto">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted-foreground">知识库目录</span>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => onCreate(null)}>
          <Plus className="h-3 w-3" />
        </Button>
      </div>
      <div
        className={cn(
          "flex items-center gap-1 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-muted mb-0.5",
          selectedFolderId === null && "bg-primary/10 text-primary font-medium",
        )}
        onClick={() => onSelect(null)}
      >
        <span className="w-3 shrink-0" />
        <Folder className="h-4 w-4 text-muted-foreground shrink-0" />
        <span className="flex-1 truncate">全部文档</span>
      </div>
      {foldersQuery.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : roots.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4">暂无目录，点右上角 [+] 新建</p>
      ) : (
        <div className="space-y-0.5">{roots.map((f) => renderNode(f, 0))}</div>
      )}
    </div>
  );
}
