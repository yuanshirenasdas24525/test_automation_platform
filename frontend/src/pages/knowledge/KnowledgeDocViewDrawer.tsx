/** 知识库文档预览抽屉：阅读模式(正文+TOC大纲+字号) / 历史模式(版本列表+回滚)。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BookOpen, History, Pencil, RotateCcw, Sparkles, Type } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SideDrawer } from "@/components/ui/side-drawer";
import { RichTextViewer } from "@/components/editor/RichTextViewer";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ApiError, knowledgeApi } from "@/lib/api";
import { KNOWLEDGE_CONTEXT_TYPES, type KnowledgeDocVersion } from "@/types/domain";

const TYPE_LABELS = new Map<string, string>(KNOWLEDGE_CONTEXT_TYPES.map((t) => [t.value, t.label]));
const FONT_PX = [14, 15.5, 17.5];

interface TocItem { id: string; text: string; level: number }

export function KnowledgeDocViewDrawer({
  open,
  docId,
  moduleNames,
  onClose,
  onEdit,
}: {
  open: boolean;
  docId: number | null;
  moduleNames: Map<number, string>;
  onClose: () => void;
  onEdit: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"read" | "history">("read");
  const [fontIdx, setFontIdx] = useState(1);
  const [toc, setToc] = useState<TocItem[]>([]);
  const [previewVersionId, setPreviewVersionId] = useState<number | null>(null);
  const [pendingRestore, setPendingRestore] = useState<KnowledgeDocVersion | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (open) { setMode("read"); setPreviewVersionId(null); } }, [open, docId]);

  const detailQuery = useQuery({
    queryKey: ["knowledge", "detail", docId],
    queryFn: () => knowledgeApi.get(docId as number),
    enabled: open && docId != null,
  });
  const doc = detailQuery.data;

  const versionsQuery = useQuery({
    queryKey: ["knowledge", "versions", docId],
    queryFn: () => knowledgeApi.versions(docId as number),
    enabled: open && docId != null && mode === "history",
  });
  const previewQuery = useQuery({
    queryKey: ["knowledge", "version", docId, previewVersionId],
    queryFn: () => knowledgeApi.getVersion(docId as number, previewVersionId as number),
    enabled: previewVersionId != null,
  });

  const restore = useMutation({
    mutationFn: (vid: number) => knowledgeApi.restoreVersion(docId as number, vid),
    onSuccess: () => {
      toast.success("已回滚到该版本");
      queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      setMode("read"); setPreviewVersionId(null);
    },
    onError: (e) => toast.error((e as ApiError).message),
  });

  useEffect(() => {
    if (mode !== "read" || !doc) { setToc([]); return; }
    const el = contentRef.current;
    if (!el) return;
    const timer = window.setTimeout(() => {
      const hs = Array.from(el.querySelectorAll("h1,h2,h3")) as HTMLElement[];
      const items: TocItem[] = hs.map((h, i) => {
        h.id = `kb-toc-${i}`;
        h.style.scrollMarginTop = "8px";
        return { id: h.id, text: (h.textContent || `小节 ${i + 1}`).trim(), level: Number(h.tagName[1]) };
      });
      setToc(items);
    }, 150);
    return () => window.clearTimeout(timer);
  }, [doc?.content_html, mode, doc]);

  const scrollTo = (id: string) =>
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  const metaBar = useMemo(() => doc && (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span className="rounded bg-muted px-1.5 py-0.5">{TYPE_LABELS.get(doc.context_type) ?? doc.context_type}</span>
      <span>模块：{doc.module_id != null ? moduleNames.get(doc.module_id) ?? "—" : "根级"}</span>
      {doc.updated_at ? <span>更新：{doc.updated_at.slice(0, 16).replace("T", " ")}</span> : null}
      {doc.include_in_rag ? (
        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700">
          <Sparkles className="h-3 w-3" />已入库 AI
        </span>
      ) : (
        <span className="rounded-full border bg-muted px-2 py-0.5 font-medium">仅人读</span>
      )}
    </div>
  ), [doc, moduleNames]);

  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      storageKey="knowledge-view-drawer-width"
      defaultWidth={860}
      minWidth={620}
      title={<><BookOpen className="h-[17px] w-[17px] text-primary" /><span className="truncate">{doc?.title ?? "知识文档"}</span></>}
      footer={
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <Button variant={mode === "read" ? "secondary" : "ghost"} size="sm" onClick={() => setMode("read")}>
              <BookOpen className="h-4 w-4 mr-1" />阅读
            </Button>
            <Button variant={mode === "history" ? "secondary" : "ghost"} size="sm" onClick={() => { setMode("history"); setPreviewVersionId(null); }}>
              <History className="h-4 w-4 mr-1" />历史版本
            </Button>
            {mode === "read" && (
              <Button variant="ghost" size="sm" title="字号" onClick={() => setFontIdx((i) => (i + 1) % FONT_PX.length)}>
                <Type className="h-4 w-4 mr-1" />字号
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>关闭</Button>
            <Button onClick={() => doc && onEdit(doc.id)} disabled={!doc}><Pencil className="h-4 w-4 mr-1" />编辑</Button>
          </div>
        </div>
      }
    >
      {detailQuery.isLoading || !doc ? (
        <div className="flex-1 py-16 text-center text-sm text-muted-foreground">加载中…</div>
      ) : mode === "read" ? (
        <div className="flex-1 overflow-hidden flex">
          <div ref={contentRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-4" style={{ fontSize: FONT_PX[fontIdx] }}>
            {metaBar}
            <RichTextViewer source={doc.content_html ?? ""} />
          </div>
          {toc.length > 0 && (
            <nav className="w-52 shrink-0 border-l overflow-y-auto p-3">
              <div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">本页目录</div>
              <div className="space-y-0.5">
                {toc.map((t) => (
                  <button key={t.id} onClick={() => scrollTo(t.id)}
                    className="block w-full truncate text-left text-xs text-muted-foreground hover:text-foreground py-1 border-l-2 border-transparent hover:border-primary"
                    style={{ paddingLeft: 8 + (t.level - 1) * 10 }}>
                    {t.text}
                  </button>
                ))}
              </div>
            </nav>
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-hidden flex">
          <div className="w-56 shrink-0 border-r overflow-y-auto p-2">
            {versionsQuery.isLoading ? (
              <div className="p-3 text-xs text-muted-foreground">加载中…</div>
            ) : (versionsQuery.data ?? []).length === 0 ? (
              <div className="p-3 text-xs text-muted-foreground">暂无历史版本（编辑过才会有）</div>
            ) : (
              (versionsQuery.data ?? []).map((v) => (
                <button key={v.id} onClick={() => setPreviewVersionId(v.id)}
                  className={`block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-muted ${previewVersionId === v.id ? "bg-primary/10 text-primary" : ""}`}>
                  <div className="truncate font-medium">{v.title}</div>
                  <div className="text-[11px] text-muted-foreground tabular-nums">{v.created_at ? v.created_at.slice(0, 16).replace("T", " ") : ""}</div>
                </button>
              ))
            )}
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {previewVersionId == null ? (
              <div className="py-16 text-center text-sm text-muted-foreground">← 选择一个历史版本预览</div>
            ) : previewQuery.isLoading || !previewQuery.data ? (
              <div className="py-16 text-center text-sm text-muted-foreground">加载中…</div>
            ) : (
              <>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">预览历史版本 · {previewQuery.data.created_at?.slice(0, 16).replace("T", " ")}</span>
                  <Button size="sm" variant="outline" onClick={() => setPendingRestore(previewQuery.data!)}>
                    <RotateCcw className="h-4 w-4 mr-1" />回滚到此版本
                  </Button>
                </div>
                <RichTextViewer source={previewQuery.data.content_html ?? ""} />
              </>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingRestore != null}
        title="回滚到历史版本"
        description={pendingRestore ? `确定把文档回滚到「${pendingRestore.title}」（${pendingRestore.created_at?.slice(0, 16).replace("T", " ")}）？当前内容会先存为一版历史。` : ""}
        confirmText="回滚"
        destructive={false}
        onConfirm={() => { if (pendingRestore) restore.mutate(pendingRestore.id); setPendingRestore(null); }}
        onCancel={() => setPendingRestore(null)}
      />
    </SideDrawer>
  );
}
