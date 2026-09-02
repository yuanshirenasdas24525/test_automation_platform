/** 单个知识库附件的按类型在线预览：图片/PDF/docx/xlsx/文本，其余兜底下载。 */
import { useEffect, useRef, useState } from "react";
import { renderAsync } from "docx-preview";
import * as XLSX from "xlsx";
import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { knowledgeApi } from "@/lib/api";
import type { KnowledgeAttachment } from "@/types/domain";

type Kind = "image" | "pdf" | "docx" | "sheet" | "text" | "other";

function kindOf(a: KnowledgeAttachment): Kind {
  const name = (a.filename || "").toLowerCase();
  const mime = (a.mime || "").toLowerCase();
  if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp)$/.test(name)) return "image";
  if (mime.includes("pdf") || name.endsWith(".pdf")) return "pdf";
  if (name.endsWith(".docx")) return "docx";
  if (/\.(xlsx|xls|csv)$/.test(name)) return "sheet";
  if (/\.(md|txt|json)$/.test(name)) return "text";
  return "other";
}

function Loading() {
  return <div className="py-16 text-center text-sm text-muted-foreground">加载中…</div>;
}

export function FilePreview({ attachment }: { attachment: KnowledgeAttachment }) {
  const kind = kindOf(attachment);
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [sheetHtml, setSheetHtml] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const docxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let objUrl: string | null = null;
    let cancelled = false;
    setUrl(null); setText(null); setSheetHtml(null); setErr(null);
    knowledgeApi.fetchAttachmentBlob(attachment.id, "inline").then(async (blob) => {
      if (cancelled) return;
      if (kind === "image" || kind === "pdf") {
        objUrl = URL.createObjectURL(blob);
        setUrl(objUrl);
      } else if (kind === "docx") {
        if (docxRef.current) {
          docxRef.current.innerHTML = "";
          await renderAsync(blob, docxRef.current);
        }
      } else if (kind === "sheet") {
        const wb = XLSX.read(await blob.arrayBuffer());
        const html = XLSX.utils.sheet_to_html(wb.Sheets[wb.SheetNames[0]]);
        if (!cancelled) setSheetHtml(html);
      } else if (kind === "text") {
        const t = await blob.text();
        if (!cancelled) setText(t);
      }
    }).catch((e) => { if (!cancelled) setErr((e as Error)?.message || "预览加载失败"); });
    return () => { cancelled = true; if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [attachment.id, kind]);

  const onDownload = async () => {
    try {
      const blob = await knowledgeApi.fetchAttachmentBlob(attachment.id, "attachment");
      const u = URL.createObjectURL(blob);
      const el = document.createElement("a");
      el.href = u; el.download = attachment.filename; el.click();
      setTimeout(() => URL.revokeObjectURL(u), 1000);
    } catch (e) {
      setErr((e as Error)?.message || "下载失败");
    }
  };

  const downloadBtn = (
    <Button size="sm" variant="outline" onClick={onDownload}><Download className="h-4 w-4 mr-1" />下载</Button>
  );

  if (err) {
    return <div className="p-6 text-center text-sm text-destructive">预览失败：{err}<div className="mt-2">{downloadBtn}</div></div>;
  }
  if (kind === "image") return url ? <img src={url} alt={attachment.filename} className="mx-auto max-w-full rounded" /> : <Loading />;
  if (kind === "pdf") return url ? <iframe title={attachment.filename} src={url} className="h-[72vh] w-full rounded border" /> : <Loading />;
  if (kind === "docx") return <div ref={docxRef} className="rounded bg-white p-2" />;
  if (kind === "sheet") {
    return sheetHtml
      ? <div className="overflow-auto text-xs [&_table]:border-collapse [&_td]:border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:bg-muted [&_th]:px-2 [&_th]:py-1" dangerouslySetInnerHTML={{ __html: sheetHtml }} />
      : <Loading />;
  }
  if (kind === "text") return text != null ? <pre className="whitespace-pre-wrap rounded bg-muted/40 p-3 text-sm">{text}</pre> : <Loading />;
  return (
    <div className="p-8 text-center text-sm text-muted-foreground">
      <div className="mb-3">此格式暂不支持在线预览</div>
      {downloadBtn}
    </div>
  );
}
