/** 项目管理「知识库」tab 主体：文档列表 + 新建/编辑/删除。左模块树由父页复用。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BookOpen, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, knowledgeApi, type ModulePickerNode } from "@/lib/api";
import { stripHtml } from "@/lib/utils";
import { KNOWLEDGE_CONTEXT_TYPES, type KnowledgeDoc } from "@/types/domain";
import { KnowledgeDocDialog } from "./KnowledgeDocDialog";
import { KnowledgeDocViewDialog } from "./KnowledgeDocViewDialog";

const TYPE_LABELS = new Map<string, string>(
  KNOWLEDGE_CONTEXT_TYPES.map((t) => [t.value, t.label]),
);

export function KnowledgeBasePanel({
  projectId,
  selectedModuleId,
  modules,
  moduleNames,
}: {
  projectId: number;
  selectedModuleId: number | null;
  modules: ModulePickerNode[];
  moduleNames: Map<number, string>;
}) {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [viewingId, setViewingId] = useState<number | null>(null);

  const docsQuery = useQuery({
    queryKey: ["knowledge", projectId, selectedModuleId],
    queryFn: () => knowledgeApi.list(projectId, { module_id: selectedModuleId ?? undefined }),
    enabled: Number.isFinite(projectId),
  });

  const remove = useMutation({
    mutationFn: (id: number) => knowledgeApi.remove(id),
    onSuccess: () => {
      toast.success("已删除");
      queryClient.invalidateQueries({ queryKey: ["knowledge", projectId] });
    },
    onError: (e) => toast.error((e as ApiError).message),
  });

  const docs = docsQuery.data ?? [];

  const openCreate = () => { setEditingId(null); setDialogOpen(true); };
  const openEdit = (id: number) => { setViewingId(null); setEditingId(id); setDialogOpen(true); };
  const openView = (doc: KnowledgeDoc) => setViewingId(doc.id);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {selectedModuleId != null ? (
            <>模块：<span className="font-medium text-blue-600">{moduleNames.get(selectedModuleId) ?? "—"}</span> 下的知识文档</>
          ) : (
            <>全部模块 · 共 {docs.length} 篇知识文档</>
          )}
        </p>
        <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" />新建文档</Button>
      </div>

      {docsQuery.isLoading ? (
        <div className="space-y-2"><Skeleton className="h-12 w-full" /><Skeleton className="h-12 w-full" /></div>
      ) : docs.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            <BookOpen className="mx-auto mb-2 h-6 w-6 opacity-40" />
            {selectedModuleId != null ? "该模块下暂无知识文档" : "暂无知识文档，点右上角「新建文档」开始沉淀项目知识"}
          </CardContent>
        </Card>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left font-medium">标题</th>
                <th className="px-3 py-2 text-left font-medium w-24">分类</th>
                <th className="px-3 py-2 text-left font-medium w-24">所属模块</th>
                <th className="px-3 py-2 text-left font-medium w-36">更新时间</th>
                <th className="px-3 py-2 text-left font-medium w-28">AI 知识库</th>
                <th className="px-3 py-2 text-right font-medium w-20"></th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className="border-t hover:bg-accent/30 cursor-pointer" onClick={() => openView(d)}>
                  <td className="px-3 py-2">
                    <div className="font-medium">{d.title}</div>
                    {stripHtml(d.summary) ? (
                      <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{stripHtml(d.summary)}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{TYPE_LABELS.get(d.context_type) ?? d.context_type}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {d.module_id != null ? moduleNames.get(d.module_id) ?? "—" : "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-muted-foreground tabular-nums">
                    {d.updated_at ? d.updated_at.slice(0, 16).replace("T", " ") : "—"}
                  </td>
                  <td className="px-3 py-2">
                    {d.include_in_rag ? (
                      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                        <Sparkles className="h-3 w-3" />已入库
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full border bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                        仅人读
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                    <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEdit(d.id)}>
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive"
                      onClick={() => { if (confirm(`删除知识文档「${d.title}」？`)) remove.mutate(d.id); }}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <KnowledgeDocViewDialog
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
        defaultModuleId={selectedModuleId}
        onClose={() => setDialogOpen(false)}
        onSaved={() => setDialogOpen(false)}
      />
    </div>
  );
}
