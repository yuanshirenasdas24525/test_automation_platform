/** 知识库文档 新建 / 编辑弹窗。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RichTextEditor } from "@/components/editor/RichTextEditor";
import { ApiError, knowledgeApi, type ModulePickerNode } from "@/lib/api";
import { KNOWLEDGE_CONTEXT_TYPES } from "@/types/domain";

const NO_MODULE = "__none__";
const DEFAULT_TYPE = "term_definition";

export function KnowledgeDocDialog({
  open,
  projectId,
  modules,
  editingId,
  defaultModuleId,
  onClose,
  onSaved,
}: {
  open: boolean;
  projectId: number;
  modules: ModulePickerNode[];
  editingId: number | null;
  defaultModuleId: number | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const isEdit = editingId != null;

  const [title, setTitle] = useState("");
  const [contentHtml, setContentHtml] = useState("");
  const [moduleId, setModuleId] = useState<string>(NO_MODULE);
  const [contextType, setContextType] = useState<string>(DEFAULT_TYPE);
  const [includeInRag, setIncludeInRag] = useState(true);

  // 编辑时拉详情（含 content_html）
  const detailQuery = useQuery({
    queryKey: ["knowledge", "detail", editingId],
    queryFn: () => knowledgeApi.get(editingId as number),
    enabled: open && isEdit,
  });

  // open / 目标切换时重置表单
  useEffect(() => {
    if (!open) return;
    if (isEdit && detailQuery.data) {
      const d = detailQuery.data;
      setTitle(d.title);
      setContentHtml(d.content_html ?? "");
      setModuleId(d.module_id != null ? String(d.module_id) : NO_MODULE);
      setContextType(d.context_type || DEFAULT_TYPE);
      setIncludeInRag(d.include_in_rag);
    } else if (!isEdit) {
      setTitle("");
      setContentHtml("");
      setModuleId(defaultModuleId != null ? String(defaultModuleId) : NO_MODULE);
      setContextType(DEFAULT_TYPE);
      setIncludeInRag(true);
    }
  }, [open, isEdit, detailQuery.data, defaultModuleId]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        title: title.trim(),
        content_html: contentHtml,
        module_id: moduleId === NO_MODULE ? null : Number(moduleId),
        context_type: contextType,
        include_in_rag: includeInRag,
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
                <Label>所属模块</Label>
                <Select value={moduleId} onValueChange={setModuleId}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_MODULE}>（根级 / 不指定）</SelectItem>
                    {modules.map((m) => (
                      <SelectItem key={m.id} value={String(m.id)}>{m.name}</SelectItem>
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
