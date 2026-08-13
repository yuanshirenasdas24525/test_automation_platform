/** 知识库文档 只读预览——右侧滑出抽屉（复用 AI 生成用例的 SideDrawer 样式）。 */
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Pencil, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SideDrawer } from "@/components/ui/side-drawer";
import { RichTextViewer } from "@/components/editor/RichTextViewer";
import { knowledgeApi } from "@/lib/api";
import { KNOWLEDGE_CONTEXT_TYPES } from "@/types/domain";

const TYPE_LABELS = new Map<string, string>(
  KNOWLEDGE_CONTEXT_TYPES.map((t) => [t.value, t.label]),
);

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
  const detailQuery = useQuery({
    queryKey: ["knowledge", "detail", docId],
    queryFn: () => knowledgeApi.get(docId as number),
    enabled: open && docId != null,
  });

  const doc = detailQuery.data;

  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      storageKey="knowledge-view-drawer-width"
      defaultWidth={720}
      minWidth={560}
      title={
        <>
          <BookOpen className="h-[17px] w-[17px] text-primary" />
          <span className="truncate">{doc?.title ?? "知识文档"}</span>
        </>
      }
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>关闭</Button>
          <Button onClick={() => doc && onEdit(doc.id)} disabled={!doc}>
            <Pencil className="h-4 w-4 mr-1" />编辑
          </Button>
        </div>
      }
    >
      {detailQuery.isLoading || !doc ? (
        <div className="flex-1 py-16 text-center text-sm text-muted-foreground">加载中…</div>
      ) : (
        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="rounded bg-muted px-1.5 py-0.5">
              {TYPE_LABELS.get(doc.context_type) ?? doc.context_type}
            </span>
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
          <RichTextViewer source={doc.content_html ?? ""} />
        </div>
      )}
    </SideDrawer>
  );
}
