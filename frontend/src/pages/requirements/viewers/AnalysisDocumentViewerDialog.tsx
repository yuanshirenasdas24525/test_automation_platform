import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Check,
  Download,
  History,
  Loader2,
  Pencil,
  Save,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownEditor } from "@/components/editor/MarkdownEditor";
import { MarkdownView } from "@/components/editor/MarkdownView";
import { VersionDiffViewer } from "@/components/diff/VersionDiffViewer";
import { analysisDocsApi, ApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { AnalysisVersion } from "@/types/domain";

interface Props {
  open: boolean;
  docId: number | null;
  onClose: () => void;
  onMutated?: () => void;
}

export function AnalysisDocumentViewerDialog({
  open,
  docId,
  onClose,
  onMutated,
}: Props) {
  const qc = useQueryClient();
  const docQuery = useQuery({
    queryKey: docId ? queryKeys.analysisDoc(docId) : ["analysis_doc", "noop"],
    queryFn: () =>
      docId ? analysisDocsApi.get(docId) : Promise.reject(new Error("no docId")),
    enabled: open && !!docId,
  });

  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<string>("");
  const [title, setTitle] = useState<string>("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);
  const [changeSummary, setChangeSummary] = useState("");

  useEffect(() => {
    if (docQuery.data) {
      setDraft(docQuery.data.current_markdown ?? "");
      setTitle(docQuery.data.title);
      setMode("view");
    }
  }, [docQuery.data?.id, docQuery.data?.current_version]);

  useEffect(() => {
    if (!open) {
      setHistoryOpen(false);
      setSaveDialogOpen(false);
      setPendingDelete(false);
      setChangeSummary("");
    }
  }, [open]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!docId) throw new Error("缺少 docId");
      return analysisDocsApi.save(docId, {
        markdown: draft,
        change_summary: changeSummary.trim() || undefined,
        title: title.trim() || undefined,
      });
    },
    onSuccess: () => {
      toast.success("已保存为新版本");
      setMode("view");
      setSaveDialogOpen(false);
      setChangeSummary("");
      if (docId) {
        qc.invalidateQueries({ queryKey: queryKeys.analysisDoc(docId) });
        qc.invalidateQueries({ queryKey: queryKeys.analysisVersions(docId) });
      }
      onMutated?.();
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "保存失败";
      toast.error(msg);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!docId) throw new Error("缺少 docId");
      return analysisDocsApi.remove(docId);
    },
    onSuccess: () => {
      toast.success("已删除");
      onMutated?.();
      onClose();
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "删除失败";
      toast.error(msg);
    },
  });

  const handleExport = async () => {
    if (!docId) return;
    try {
      await analysisDocsApi.export(docId, docQuery.data?.title);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "导出失败";
      toast.error(msg);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
        <DialogContent className="flex max-h-[90vh] max-w-5xl flex-col">
          <DialogHeader>
            <DialogTitle>
              {docQuery.data ? (
                <div className="flex items-center gap-2">
                  <span>{docQuery.data.title}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                    v{docQuery.data.current_version}
                  </span>
                </div>
              ) : (
                "AI 分析文档"
              )}
            </DialogTitle>
            <DialogDescription>
              {docQuery.data?.model_label ? `生成模型：${docQuery.data.model_label}` : null}
              {docQuery.data?.created_at
                ? `  · 创建于 ${docQuery.data.created_at.slice(0, 19).replace("T", " ")}`
                : null}
            </DialogDescription>
          </DialogHeader>

          {/* 工具栏 */}
          <div className="flex items-center gap-2 border-b pb-2">
            {mode === "view" ? (
              <Button size="sm" variant="outline" onClick={() => setMode("edit")}>
                <Pencil className="h-4 w-4" /> 编辑
              </Button>
            ) : (
              <>
                <Button size="sm" onClick={() => setSaveDialogOpen(true)}>
                  <Save className="h-4 w-4" /> 保存为新版本
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if (docQuery.data) {
                      setDraft(docQuery.data.current_markdown ?? "");
                      setTitle(docQuery.data.title);
                    }
                    setMode("view");
                  }}
                >
                  <X className="h-4 w-4" /> 取消编辑
                </Button>
              </>
            )}
            <Button size="sm" variant="outline" onClick={handleExport}>
              <Download className="h-4 w-4" /> 导出 .md
            </Button>
            <Button
              size="sm"
              variant={historyOpen ? "default" : "outline"}
              onClick={() => setHistoryOpen((v) => !v)}
            >
              <History className="h-4 w-4" /> 历史
            </Button>
            <div className="ml-auto" />
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive hover:text-destructive"
              onClick={() => setPendingDelete(true)}
            >
              <Trash2 className="h-4 w-4" /> 删除
            </Button>
          </div>

          {/* 主体 */}
          <div className="flex min-h-[50vh] flex-1 overflow-hidden">
            <div className="flex-1 overflow-auto p-1">
              {docQuery.isLoading ? (
                <div className="py-8 text-center text-sm text-muted-foreground">
                  <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> 加载中…
                </div>
              ) : docQuery.isError ? (
                <div className="rounded border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
                  加载失败：
                  {docQuery.error instanceof Error ? docQuery.error.message : ""}
                </div>
              ) : mode === "edit" ? (
                <div className="space-y-3 p-1">
                  <div>
                    <Label>标题</Label>
                    <Input value={title} onChange={(e) => setTitle(e.target.value)} />
                  </div>
                  <MarkdownEditor value={draft} onChange={setDraft} height={520} />
                </div>
              ) : (
                <div className="prose-sm max-w-none p-1">
                  <MarkdownView source={docQuery.data?.current_markdown ?? ""} />
                </div>
              )}
            </div>

            {historyOpen && docId ? (
              <div className="ml-2 w-[280px] shrink-0 overflow-auto border-l pl-2">
                <VersionPanel docId={docId} currentVersion={docQuery.data?.current_version ?? 1} />
              </div>
            ) : null}
          </div>

          <DialogFooter className="border-t pt-2">
            <Button variant="outline" onClick={onClose}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 保存说明 mini dialog */}
      <Dialog open={saveDialogOpen} onOpenChange={(v) => !v && setSaveDialogOpen(false)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>保存为新版本</DialogTitle>
            <DialogDescription>
              填一条提交说明，方便后续查看历史时定位修改内容。
            </DialogDescription>
          </DialogHeader>
          <Textarea
            rows={3}
            placeholder="例如：补充了边界测试的描述、调整测试策略章节"
            value={changeSummary}
            onChange={(e) => setChangeSummary(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
              {saveMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Check className="h-4 w-4" />
              )}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={pendingDelete} onOpenChange={(v) => !v && setPendingDelete(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除分析文档？</DialogTitle>
            <DialogDescription>
              该文档及其所有版本将被永久删除。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(false)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}


function VersionPanel({
  docId,
  currentVersion,
}: {
  docId: number;
  currentVersion: number;
}) {
  const versionsQuery = useQuery({
    queryKey: queryKeys.analysisVersions(docId),
    queryFn: () => analysisDocsApi.listVersions(docId),
  });

  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const [diff, setDiff] = useState<{
    before: AnalysisVersion;
    after: AnalysisVersion;
  } | null>(null);
  const [loadingDiff, setLoadingDiff] = useState(false);

  const handleSelect = (v: number) => {
    if (a === null) setA(v);
    else if (b === null) setB(v);
    else {
      setA(v);
      setB(null);
    }
  };

  const handleCompare = async () => {
    if (a === null || b === null) return;
    setLoadingDiff(true);
    try {
      const r = await analysisDocsApi.getDiff(docId, Math.min(a, b), Math.max(a, b));
      setDiff(r);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "拉取 diff 失败";
      toast.error(msg);
    } finally {
      setLoadingDiff(false);
    }
  };

  return (
    <div className="space-y-2 text-sm">
      <div className="text-xs font-semibold text-muted-foreground">版本历史</div>
      {versionsQuery.isLoading ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (versionsQuery.data?.length ?? 0) === 0 ? (
        <div className="text-xs text-muted-foreground">无</div>
      ) : (
        <div className="space-y-1">
          {versionsQuery.data!.map((v) => {
            const isCurrent = v.version_no === currentVersion;
            const isSel = v.version_no === a || v.version_no === b;
            return (
              <button
                key={v.id}
                type="button"
                onClick={() => handleSelect(v.version_no)}
                className={[
                  "w-full rounded border px-2 py-1 text-left text-xs",
                  isSel
                    ? "border-violet-500 bg-violet-50"
                    : "border-border hover:bg-muted/40",
                ].join(" ")}
              >
                <div className="flex items-center justify-between">
                  <span className="font-semibold">v{v.version_no}</span>
                  {isCurrent ? (
                    <span className="rounded bg-emerald-100 px-1 py-0.5 text-emerald-700">
                      当前
                    </span>
                  ) : null}
                  {v.is_ai_generated ? (
                    <span className="rounded bg-violet-100 px-1 py-0.5 text-violet-700">
                      AI
                    </span>
                  ) : null}
                </div>
                <div className="mt-1 text-[10px] text-muted-foreground">
                  {v.created_at?.slice(0, 16).replace("T", " ")}
                  {v.author_label ? ` · ${v.author_label}` : ""}
                </div>
                {v.change_summary ? (
                  <div className="mt-1 line-clamp-2 text-[11px] text-foreground/70">
                    {v.change_summary}
                  </div>
                ) : null}
              </button>
            );
          })}
        </div>
      )}

      {a !== null || b !== null ? (
        <div className="rounded border bg-muted/30 p-2 text-xs">
          已选：v{a ?? "?"} ↔ v{b ?? "?"}
          <div className="mt-1 flex gap-1">
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2"
              onClick={() => {
                setA(null);
                setB(null);
                setDiff(null);
              }}
            >
              清除
            </Button>
            <Button
              size="sm"
              className="h-6 px-2"
              onClick={handleCompare}
              disabled={a === null || b === null || loadingDiff}
            >
              {loadingDiff ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              比较
            </Button>
          </div>
        </div>
      ) : null}

      {diff ? (
        <div className="mt-2">
          <VersionDiffViewer
            before={diff.before.markdown ?? ""}
            after={diff.after.markdown ?? ""}
            beforeLabel={`v${diff.before.version_no}`}
            afterLabel={`v${diff.after.version_no}`}
          />
        </div>
      ) : null}
    </div>
  );
}
