import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronRight,
  FileText,
  Loader2,
  RotateCw,
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
import { analysisDocsApi } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { AnalysisDocument, Requirement } from "@/types/domain";

import { AnalysisDocumentViewerDialog } from "../viewers/AnalysisDocumentViewerDialog";

interface Props {
  open: boolean;
  onClose: () => void;
  requirement: Requirement | null;
}

export function AnalysisDocumentListDialog({ open, onClose, requirement }: Props) {
  const qc = useQueryClient();
  const docsQuery = useQuery({
    queryKey: requirement
      ? queryKeys.analysisDocs(requirement.id)
      : ["analysis_docs", "noop"],
    queryFn: () =>
      requirement ? analysisDocsApi.listByRequirement(requirement.id) : Promise.resolve([]),
    enabled: open && !!requirement,
    refetchInterval: open ? 5000 : false,   // 后台 5s 自刷新拿运行状态
  });

  const [viewDocId, setViewDocId] = useState<number | null>(null);

  useEffect(() => {
    if (!open) setViewDocId(null);
  }, [open]);

  const handleRefresh = () => docsQuery.refetch();

  return (
    <>
      <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              <span className="inline-flex items-center gap-2">
                <FileText className="h-5 w-5 text-violet-600" />
                AI 分析文档
              </span>
            </DialogTitle>
            <DialogDescription>
              {requirement ? (
                <>
                  需求 <b>#{requirement.id} {requirement.title}</b> 的 AI 分析文档列表
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>

          <div className="flex items-center justify-end">
            <Button variant="outline" size="sm" onClick={handleRefresh}>
              {docsQuery.isFetching ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCw className="h-4 w-4" />
              )}
              刷新
            </Button>
          </div>

          {docsQuery.isLoading ? (
            <div className="py-6 text-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> 加载中…
            </div>
          ) : (docsQuery.data?.length ?? 0) === 0 ? (
            <div className="rounded border border-dashed p-6 text-center text-sm text-muted-foreground">
              还没有分析文档。在需求行点 <b>Bot</b> 按钮发起 AI 分析。
            </div>
          ) : (
            <div className="divide-y rounded border">
              {docsQuery.data!.map((d) => (
                <DocRow
                  key={d.id}
                  doc={d}
                  onOpen={() => setViewDocId(d.id)}
                />
              ))}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={onClose}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AnalysisDocumentViewerDialog
        open={viewDocId !== null}
        docId={viewDocId}
        onClose={() => setViewDocId(null)}
        onMutated={() => {
          if (requirement) {
            qc.invalidateQueries({
              queryKey: queryKeys.analysisDocs(requirement.id),
            });
          }
        }}
      />
    </>
  );
}


function DocRow({ doc, onOpen }: { doc: AnalysisDocument; onOpen: () => void }) {
  const status = doc.ai_run?.status;
  const failed = status === "failed";
  const running = status === "running" || status === "pending";

  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={running || failed}
      className="grid w-full grid-cols-[1fr_auto] items-center gap-3 px-3 py-3 text-left text-sm hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-70"
    >
      <div className="space-y-1">
        <div className="flex items-center gap-2 font-medium">
          {failed ? (
            <AlertTriangle className="h-4 w-4 text-destructive" />
          ) : running ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : (
            <FileText className="h-4 w-4 text-violet-600" />
          )}
          <span className="truncate">{doc.title}</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            v{doc.current_version}
          </span>
          {failed ? (
            <span className="rounded bg-destructive/10 px-1.5 py-0.5 text-xs text-destructive">
              失败
            </span>
          ) : running ? (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
              生成中
            </span>
          ) : null}
        </div>
        <div className="text-xs text-muted-foreground">
          {doc.model_label || "—"} · {doc.created_at?.slice(0, 19).replace("T", " ")}
          {doc.created_by_label ? ` · ${doc.created_by_label}` : ""}
        </div>
        {failed && doc.ai_run?.error ? (
          <div className="line-clamp-2 rounded bg-destructive/5 px-2 py-1 text-xs text-destructive">
            {doc.ai_run.error}
          </div>
        ) : null}
      </div>
      <ChevronRight className="h-4 w-4 text-muted-foreground" />
    </button>
  );
}
