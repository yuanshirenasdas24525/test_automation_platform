/** 富文本文档的附件区：列表 + 上传 + 删除 + 预览弹窗。 */
import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Eye, FileText, Trash2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ApiError, knowledgeApi } from "@/lib/api";
import type { KnowledgeAttachment } from "@/types/domain";
import { FilePreview } from "./FilePreview";

function humanSize(n?: number | null): string {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)}K`;
  return `${(n / 1024 / 1024).toFixed(1)}M`;
}

export function AttachmentList({ docId, attachments }: { docId: number; attachments: KnowledgeAttachment[] }) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<KnowledgeAttachment | null>(null);
  const [pendingDelete, setPendingDelete] = useState<KnowledgeAttachment | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["knowledge"] });

  const upload = useMutation({
    mutationFn: (file: File) => knowledgeApi.addAttachment(docId, file),
    onSuccess: () => { toast.success("已上传附件"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => knowledgeApi.deleteAttachment(id),
    onSuccess: () => { toast.success("已删除附件"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });

  const onPick = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach((f) => upload.mutate(f));
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">附件（{attachments.length}）</span>
        <Button size="sm" variant="ghost" onClick={() => inputRef.current?.click()} disabled={upload.isPending}>
          <Upload className="h-4 w-4 mr-1" />{upload.isPending ? "上传中…" : "上传附件"}
        </Button>
        <input ref={inputRef} type="file" multiple className="hidden" onChange={(e) => onPick(e.target.files)} />
      </div>
      {attachments.length === 0 ? (
        <div className="rounded border border-dashed p-4 text-center text-xs text-muted-foreground">暂无附件</div>
      ) : (
        <div className="space-y-1">
          {attachments.map((a) => (
            <div key={a.id} className="group flex items-center gap-2 rounded border px-2 py-1.5">
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="flex-1 truncate text-sm">{a.filename}</span>
              <span className="text-[11px] tabular-nums text-muted-foreground">{humanSize(a.size_bytes)}</span>
              <Button size="icon" variant="ghost" className="h-7 w-7" title="预览" onClick={() => setPreview(a)}>
                <Eye className="h-3.5 w-3.5" />
              </Button>
              <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive" title="删除" onClick={() => setPendingDelete(a)}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      <Dialog open={preview != null} onOpenChange={(v) => { if (!v) setPreview(null); }}>
        <DialogContent className="max-w-4xl">
          <DialogHeader><DialogTitle className="truncate">{preview?.filename}</DialogTitle></DialogHeader>
          {preview ? <div className="max-h-[75vh] overflow-auto">{<FilePreview attachment={preview} />}</div> : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={pendingDelete != null}
        title="删除附件"
        description={pendingDelete ? `确定删除附件「${pendingDelete.filename}」？` : ""}
        onConfirm={() => { if (pendingDelete) remove.mutate(pendingDelete.id); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
