/** 知识库面板：目录/搜索/标签过滤 + 卡片列表 + 置顶。左侧目录树在父页。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BookOpen, Download, FileText, FileUp, Pin, PinOff, Pencil, Plus, Search, Sparkles, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError, knowledgeApi, knowledgeTagsApi, type ModulePickerNode } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { stripHtml } from "@/lib/utils";
import { KNOWLEDGE_CONTEXT_TYPES, type KnowledgeDoc } from "@/types/domain";
import { KnowledgeDocDialog } from "./KnowledgeDocDialog";
import { KnowledgeDocViewDrawer } from "./KnowledgeDocViewDrawer";

const TYPE_LABELS = new Map<string, string>(KNOWLEDGE_CONTEXT_TYPES.map((t) => [t.value, t.label]));
const ALL_TAGS = "__all__";

export function KnowledgeBasePanel({
  projectId,
  selectedFolderId,
  modules,
  moduleNames,
}: {
  projectId: number;
  selectedFolderId: number | null;
  modules: ModulePickerNode[];
  moduleNames: Map<number, string>;
}) {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [rawSearch, setRawSearch] = useState("");
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState<string>(ALL_TAGS);
  const [pendingDelete, setPendingDelete] = useState<KnowledgeDoc | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setSearch(rawSearch), 300);
    return () => clearTimeout(t);
  }, [rawSearch]);

  const tagsQuery = useQuery({
    queryKey: ["knowledge-tags", projectId],
    queryFn: () => knowledgeTagsApi.list(projectId),
    enabled: Number.isFinite(projectId),
  });

  const docsQuery = useQuery({
    queryKey: ["knowledge", projectId, selectedFolderId, search, tagFilter],
    queryFn: () =>
      knowledgeApi.list(projectId, {
        folder_id: selectedFolderId ?? undefined,
        q: search || undefined,
        tag_id: tagFilter === ALL_TAGS ? undefined : Number(tagFilter),
      }),
    enabled: Number.isFinite(projectId),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["knowledge", projectId] });

  const importInputRef = useRef<HTMLInputElement>(null);
  const importFiles = useMutation({
    mutationFn: (files: File[]) => knowledgeApi.importFiles(projectId, selectedFolderId, files),
    onSuccess: (docs) => { toast.success(`已导入 ${docs.length} 篇`); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const onImportPick = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    importFiles.mutate(Array.from(files));
    if (importInputRef.current) importInputRef.current.value = "";
  };
  const onExportZip = async () => {
    try {
      const blob = await knowledgeApi.exportZip(projectId, selectedFolderId);
      downloadBlob(blob, "knowledge-export.zip");
    } catch (e) { toast.error((e as Error).message); }
  };

  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadFile = useMutation({
    mutationFn: (file: File) => knowledgeApi.uploadFileDoc(projectId, selectedFolderId, file),
    onSuccess: () => { toast.success("已上传文件"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const onPickFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach((f) => uploadFile.mutate(f));
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const remove = useMutation({
    mutationFn: (id: number) => knowledgeApi.remove(id),
    onSuccess: () => { toast.success("已删除"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const pin = useMutation({
    mutationFn: (v: { id: number; pinned: boolean }) => knowledgeApi.pin(v.id, v.pinned),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error((e as ApiError).message),
  });

  const docs = docsQuery.data ?? [];
  const tags = tagsQuery.data ?? [];

  const openCreate = () => { setEditingId(null); setDialogOpen(true); };
  const openEdit = (id: number) => { setViewingId(null); setEditingId(id); setDialogOpen(true); };

  const typeColor = useMemo(() => (t: string) => {
    switch (t) {
      case "api_contract": return "#2563eb";
      case "business_rule": return "#b45309";
      case "data_model": return "#7c3aed";
      default: return "#64748b";
    }
  }, []);

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-8 h-9" placeholder="搜索标题、正文…" value={rawSearch}
            onChange={(e) => setRawSearch(e.target.value)} />
        </div>
        <Select value={tagFilter} onValueChange={setTagFilter}>
          <SelectTrigger className="h-9 w-36"><SelectValue placeholder="全部标签" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_TAGS}>全部标签</SelectItem>
            {tags.map((t) => <SelectItem key={t.id} value={String(t.id)}>{t.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <span className="flex-1" />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline">导入/导出</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => importInputRef.current?.click()}>
              导入 MD/Word…
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onExportZip}>
              <Download className="h-4 w-4 mr-1" />导出{selectedFolderId != null ? "当前目录" : "整库"}(Zip)
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <input ref={importInputRef} type="file" multiple accept=".md,.markdown,.txt,.docx" className="hidden" onChange={(e) => onImportPick(e.target.files)} />
        <Button size="sm" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploadFile.isPending}>
          <FileUp className="h-4 w-4 mr-1" />{uploadFile.isPending ? "上传中…" : "上传文件"}
        </Button>
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => onPickFiles(e.target.files)} />
        <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" />新建文档</Button>
      </div>

      {docsQuery.isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(268px,1fr))] gap-3">
          <Skeleton className="h-32 w-full" /><Skeleton className="h-32 w-full" />
        </div>
      ) : docs.length === 0 ? (
        <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">
          <BookOpen className="mx-auto mb-2 h-6 w-6 opacity-40" />
          {search || tagFilter !== ALL_TAGS ? "没有符合条件的文档" : selectedFolderId != null ? "该目录下暂无文档" : "暂无知识文档，点右上角「新建文档」开始沉淀"}
        </CardContent></Card>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(268px,1fr))] gap-3">
          {docs.map((d) => (
            <article key={d.id}
              className="group relative overflow-hidden rounded-xl border bg-card p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition cursor-pointer"
              onClick={() => setViewingId(d.id)}>
              <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: typeColor(d.context_type) }} />
              <div className="flex items-start gap-1">
                <h3 className="flex-1 font-semibold text-sm leading-snug line-clamp-2 flex items-center gap-1">
                  {d.doc_type === "file" && <FileText className="h-3.5 w-3.5 shrink-0 text-amber-600" />}
                  <span className="truncate">{d.title}</span>
                </h3>
                <div className="flex items-center opacity-0 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
                  <Button variant="ghost" size="icon" className="h-7 w-7"
                    onClick={() => pin.mutate({ id: d.id, pinned: !d.is_pinned })} title={d.is_pinned ? "取消置顶" : "置顶"}>
                    {d.is_pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEdit(d.id)}>
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive"
                    onClick={() => setPendingDelete(d)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {d.is_pinned && <Pin className="h-3.5 w-3.5 text-amber-500 shrink-0 group-hover:hidden" />}
              </div>
              {stripHtml(d.summary ?? "") ? (
                <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{stripHtml(d.summary ?? "")}</p>
              ) : null}
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                {d.doc_type === "file" && (
                  <span className="rounded bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">文件</span>
                )}
                <span className="rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {TYPE_LABELS.get(d.context_type) ?? d.context_type}
                </span>
                {d.include_in_rag ? (
                  <span className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                    <Sparkles className="h-3 w-3" />已入库
                  </span>
                ) : (
                  <span className="rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">仅人读</span>
                )}
                {(d.tags ?? []).map((t) => (
                  <span key={t.id} className="rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground">
                    {t.name}
                  </span>
                ))}
              </div>
              <div className="mt-3 flex items-center gap-2 border-t pt-2 text-[11px] text-muted-foreground tabular-nums">
                {d.updated_at ? d.updated_at.slice(0, 16).replace("T", " ") : "—"}
              </div>
            </article>
          ))}
        </div>
      )}

      <KnowledgeDocViewDrawer
        open={viewingId != null}
        docId={viewingId}
        moduleNames={moduleNames}
        onClose={() => setViewingId(null)}
        onEdit={(id) => openEdit(id)}
      />

      <KnowledgeDocDialog
        open={dialogOpen}
        projectId={projectId}
        modules={modules}
        editingId={editingId}
        defaultFolderId={selectedFolderId}
        onClose={() => setDialogOpen(false)}
        onSaved={() => setDialogOpen(false)}
      />

      <ConfirmDialog
        open={pendingDelete != null}
        title="删除知识文档"
        description={pendingDelete ? `确定删除「${pendingDelete.title}」？此操作不可撤销。` : ""}
        onConfirm={() => { if (pendingDelete) remove.mutate(pendingDelete.id); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
