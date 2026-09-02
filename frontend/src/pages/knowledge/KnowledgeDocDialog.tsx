/** 知识库文档 新建 / 编辑弹窗。 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RichTextEditor } from "@/components/editor/RichTextEditor";
import { ApiError, knowledgeApi, knowledgeFoldersApi, knowledgeTagsApi, type ModulePickerNode } from "@/lib/api";
import { KNOWLEDGE_CONTEXT_TYPES, type KnowledgeFolderNode } from "@/types/domain";

const NO_FOLDER = "__root__";
const DEFAULT_TYPE = "term_definition";

export function KnowledgeDocDialog({
  open,
  projectId,
  // 调用方（KnowledgeBasePanel）仍传入 modules；弹窗改用目录/标签后暂不消费，保留 prop 形状以兼容上层。
  modules: _modules,
  editingId,
  defaultFolderId,
  onClose,
  onSaved,
}: {
  open: boolean;
  projectId: number;
  modules: ModulePickerNode[];
  editingId: number | null;
  defaultFolderId: number | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const isEdit = editingId != null;

  const [title, setTitle] = useState("");
  const [contentHtml, setContentHtml] = useState("");
  const [folderId, setFolderId] = useState<string>(NO_FOLDER);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [newTag, setNewTag] = useState("");
  const [contextType, setContextType] = useState<string>(DEFAULT_TYPE);
  const [includeInRag, setIncludeInRag] = useState(true);

  // 编辑时拉详情（含 content_html）
  const detailQuery = useQuery({
    queryKey: ["knowledge", "detail", editingId],
    queryFn: () => knowledgeApi.get(editingId as number),
    enabled: open && isEdit,
  });

  const foldersQuery = useQuery({
    queryKey: ["knowledge-folders", projectId],
    queryFn: () => knowledgeFoldersApi.list(projectId),
    enabled: open && Number.isFinite(projectId),
  });
  const tagsQuery = useQuery({
    queryKey: ["knowledge-tags", projectId],
    queryFn: () => knowledgeTagsApi.list(projectId),
    enabled: open && Number.isFinite(projectId),
  });
  const flatFolders = useMemo(() => {
    const out: { id: number; label: string }[] = [];
    const walk = (nodes: KnowledgeFolderNode[], depth: number) => {
      for (const n of nodes) {
        out.push({ id: n.id, label: `${"　".repeat(depth)}${n.name}` });
        if (n.children?.length) walk(n.children, depth + 1);
      }
    };
    walk(foldersQuery.data ?? [], 0);
    return out;
  }, [foldersQuery.data]);

  // open / 目标切换时重置表单
  useEffect(() => {
    if (!open) return;
    if (isEdit && detailQuery.data) {
      const d = detailQuery.data;
      setTitle(d.title);
      setContentHtml(d.content_html ?? "");
      setFolderId(d.folder_id != null ? String(d.folder_id) : NO_FOLDER);
      setTagIds((d.tags ?? []).map((t) => t.id));
      setContextType(d.context_type || DEFAULT_TYPE);
      setIncludeInRag(d.include_in_rag);
      setNewTag("");
    } else if (!isEdit) {
      setTitle("");
      setContentHtml("");
      setFolderId(defaultFolderId != null ? String(defaultFolderId) : NO_FOLDER);
      setTagIds([]);
      setContextType(DEFAULT_TYPE);
      setIncludeInRag(true);
      setNewTag("");
    }
  }, [open, isEdit, detailQuery.data, defaultFolderId]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        title: title.trim(),
        content_html: contentHtml,
        folder_id: folderId === NO_FOLDER ? null : Number(folderId),
        context_type: contextType,
        include_in_rag: includeInRag,
        tag_ids: tagIds,
      };
      return isEdit
        ? knowledgeApi.update(editingId as number, body)
        : knowledgeApi.create({ ...body, project_id: projectId });
    },
    onSuccess: () => {
      toast.success(isEdit ? "已保存" : "已创建");
      queryClient.invalidateQueries({ queryKey: ["knowledge", projectId] });
      onSaved();
    },
    onError: (e) => toast.error((e as ApiError).message),
  });

  const canSave = title.trim().length > 0 && !save.isPending;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑知识文档" : "新建知识文档"}</DialogTitle>
        </DialogHeader>

        {isEdit && detailQuery.isLoading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-[1fr_180px_180px] gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="kb-title">标题</Label>
                <Input id="kb-title" value={title} onChange={(e) => setTitle(e.target.value)}
                  placeholder="如：登录模块接口约定" autoFocus />
              </div>
              <div className="space-y-1.5">
                <Label>所属目录</Label>
                <Select value={folderId} onValueChange={setFolderId}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_FOLDER}>（根级 / 不指定）</SelectItem>
                    {flatFolders.map((f) => (
                      <SelectItem key={f.id} value={String(f.id)}>{f.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>分类</Label>
                <Select value={contextType} onValueChange={setContextType}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {KNOWLEDGE_CONTEXT_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>正文</Label>
              <RichTextEditor value={contentHtml} onChange={setContentHtml} height={300} toolbar="full" />
            </div>

            <div className="space-y-1.5">
              <Label>标签</Label>
              <div className="flex flex-wrap items-center gap-1.5">
                {(tagsQuery.data ?? []).map((t) => {
                  const on = tagIds.includes(t.id);
                  return (
                    <button type="button" key={t.id}
                      className={`rounded-full border px-2.5 py-0.5 text-xs transition ${on ? "border-transparent bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"}`}
                      onClick={() => setTagIds((prev) => on ? prev.filter((x) => x !== t.id) : [...prev, t.id])}>
                      {t.name}
                    </button>
                  );
                })}
                <input
                  className="h-7 w-28 rounded border px-2 text-xs outline-none focus:border-primary"
                  placeholder="+ 新标签回车"
                  value={newTag}
                  onChange={(e) => setNewTag(e.target.value)}
                  onKeyDown={async (e) => {
                    if (e.key === "Enter" && newTag.trim()) {
                      e.preventDefault();
                      try {
                        const tag = await knowledgeTagsApi.create({ project_id: projectId, name: newTag.trim() });
                        setNewTag("");
                        await queryClient.invalidateQueries({ queryKey: ["knowledge-tags", projectId] });
                        setTagIds((prev) => prev.includes(tag.id) ? prev : [...prev, tag.id]);
                      } catch (err) {
                        toast.error((err as ApiError).message);
                      }
                    }
                  }}
                />
              </div>
            </div>

            <label className="flex items-start gap-3 rounded-md border p-3 cursor-pointer">
              <input type="checkbox" className="mt-0.5 h-4 w-4 accent-blue-600"
                checked={includeInRag} onChange={(e) => setIncludeInRag(e.target.checked)} />
              <span className="text-sm">
                <span className="font-medium">纳入 AI 知识库</span>
                <span className="block text-xs text-muted-foreground">
                  开启后本文自动进入 RAG，供 AI 生成用例 / 需求时检索召回；关闭则仅供人阅读。
                </span>
              </span>
            </label>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button onClick={() => save.mutate()} disabled={!canSave}>
            {save.isPending ? "保存中…" : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
