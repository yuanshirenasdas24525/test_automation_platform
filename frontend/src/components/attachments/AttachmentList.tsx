import { useRef, useState, DragEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import { File as FileIcon, Link2, Plus, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { attachmentsApi } from "@/lib/api";
import { useUserId } from "@/lib/current-user";
import type { Attachment } from "@/types/domain";

interface AttachmentListProps {
  requirementId: number;
}

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function AttachmentList({ requirementId }: AttachmentListProps) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkName, setLinkName] = useState("");
  const [linkUrl, setLinkUrl] = useState("");
  const uploaderId = useUserId();
  const [dragOver, setDragOver] = useState(false);

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const f = e.dataTransfer?.files?.[0];
    if (f) uploadMutation.mutate(f);
  };

  const listQuery = useQuery({
    queryKey: ["attachments", requirementId],
    queryFn: () => attachmentsApi.list(requirementId),
    enabled: Number.isFinite(requirementId) && requirementId > 0,
  });

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["attachments", requirementId] });

  const linkMutation = useMutation({
    mutationFn: () =>
      attachmentsApi.createLink(requirementId, {
        name: linkName.trim(),
        url: linkUrl.trim(),
        uploaded_by_id: uploaderId,
      }),
    onSuccess: () => {
      toast.success("已添加链接");
      setLinkOpen(false);
      setLinkName("");
      setLinkUrl("");
      invalidate();
    },
    onError: (err: unknown) => {
      const msg = err instanceof ApiError ? err.message : "添加链接失败";
      toast.error(msg);
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      attachmentsApi.uploadFile(requirementId, file, {
        uploaded_by_id: uploaderId,
      }),
    onSuccess: () => {
      toast.success("已上传");
      invalidate();
    },
    onError: (err: unknown) => {
      const msg = err instanceof ApiError ? err.message : "上传失败";
      toast.error(msg);
    },
  });

  const removeMutation = useMutation({
    mutationFn: (id: number) => attachmentsApi.remove(id),
    onSuccess: () => {
      toast.success("已删除");
      invalidate();
    },
    onError: (err: unknown) => {
      const msg = err instanceof ApiError ? err.message : "删除失败";
      toast.error(msg);
    },
  });

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    uploadMutation.mutate(f);
    e.target.value = "";
  };

  const attachments: Attachment[] = listQuery.data ?? [];

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setLinkOpen(true)}
        >
          <Plus className="mr-1 h-3 w-3" /> 添加链接
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={uploadMutation.isPending}
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="mr-1 h-3 w-3" />
          {uploadMutation.isPending ? "上传中…" : "上传文件"}
        </Button>
        <input
          ref={fileRef}
          type="file"
          className="hidden"
          onChange={handleFile}
        />
      </div>

      {attachments.length === 0 && (
        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`rounded border border-dashed p-3 text-center text-xs transition-colors ${
            dragOver
              ? "border-primary bg-primary/5 text-primary"
              : "border-slate-200 text-muted-foreground"
          }`}
        >
          {dragOver ? "释放以上传文件" : "暂无附件，可拖拽文件到此处上传"}
        </div>
      )}

      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <ul className="divide-y divide-slate-100 rounded border border-slate-200">
        {attachments.map((att) => (
          <li
            key={att.id}
            className="flex items-center gap-2 px-2 py-1.5 text-sm"
          >
            {att.kind === "link" ? (
              <Link2 className="h-4 w-4 text-blue-500" />
            ) : (
              <FileIcon className="h-4 w-4 text-slate-500" />
            )}
            <a
              href={att.url}
              target="_blank"
              rel="noreferrer"
              className="flex-1 truncate text-blue-700 hover:underline"
              title={att.name}
            >
              {att.name}
            </a>
            {att.kind === "file" && (
              <span className="text-xs text-muted-foreground">
                {formatSize(att.size_bytes)}
              </span>
            )}
            <button
              type="button"
              onClick={() => removeMutation.mutate(att.id)}
              className="text-slate-400 hover:text-red-500"
              aria-label="删除附件"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </li>
        ))}
        </ul>
      </div>

      <Dialog open={linkOpen} onOpenChange={setLinkOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>添加链接</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="att-link-name">标题</Label>
              <Input
                id="att-link-name"
                value={linkName}
                onChange={(e) => setLinkName(e.target.value)}
                placeholder="比如：TAPD 原型"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="att-link-url">URL</Label>
              <Input
                id="att-link-url"
                value={linkUrl}
                onChange={(e) => setLinkUrl(e.target.value)}
                placeholder="https://…"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLinkOpen(false)}>
              取消
            </Button>
            <Button
              disabled={
                !linkName.trim() ||
                !linkUrl.trim() ||
                linkMutation.isPending
              }
              onClick={() => linkMutation.mutate()}
            >
              {linkMutation.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
