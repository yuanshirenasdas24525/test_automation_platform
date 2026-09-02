# 知识库改造 · 阶段 3b（文件预览 · 前端）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端接上 3a：上传文件成「文件文档」、富文本文档挂附件，并在线预览图片/PDF/docx/xlsx（不支持的兜底下载）。

**Architecture:** 因为下载走鉴权路由（Bearer token，`<img src>` 不带 token），预览统一用**带鉴权的 fetch 取 Blob** 再渲染：图片/PDF 走 objectURL、docx 走 `docx-preview`、xlsx/csv 走 `xlsx(SheetJS)` 转 HTML 表格、md/txt/json 直接 text、其他兜底下载。新增 `FilePreview`（按类型渲染单个附件）与 `AttachmentList`（富文本文档的附件区：列表+上传+删除+预览弹窗）。面板加「上传文件」入口（建文件文档）；卡片区分文件文档；阅读抽屉：文件文档=FilePreview，富文本=正文+附件区。

**Tech Stack:** React 19 + TS strict + Tailwind + shadcn/ui + react-query + sonner；`docx-preview@^0.4`、`xlsx@^0.18`（已装并提交 package.json）。

**前置约定：**
- 前端命令在 `frontend/` 下；`npm run typecheck` / `npm run lint`（严）/ `npm run build`。全仓 lint 有 3 条**既有**无关警告；本阶段改动文件单独跑 `npx eslint <files> --max-warnings 0` 必须 0 warning。
- `request<T>()` 已支持 FormData（自动带 token、不套 JSON header）。`getToken()` 从 `@/lib/api` 导出用于鉴权 blob fetch。
- 有 `venv`/`node_modules` 软链，**永远别 `git add -A`**。

**本阶段不做：** 拖拽排序附件；docx/xlsx 的多 sheet 全量 tab（先渲染首个 sheet + sheet 选择器可留后续）；文件文本抽取喂 RAG。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `frontend/src/types/domain.ts` | `KnowledgeAttachment` 类型；`KnowledgeDoc` 补 `doc_type`/`attachments` | 修改 |
| `frontend/src/lib/api.ts` | `uploadFileDoc`/`addAttachment`/`deleteAttachment`/`fetchAttachmentBlob` | 修改 |
| `frontend/src/pages/knowledge/FilePreview.tsx` | 单附件按类型预览（含兜底下载） | 新建 |
| `frontend/src/pages/knowledge/AttachmentList.tsx` | 富文本文档附件区（列表+上传+删除+预览弹窗） | 新建 |
| `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx` | 「上传文件」入口 + 文件文档卡片区分 | 修改 |
| `frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx` | 文件文档=FilePreview；富文本=正文+AttachmentList | 修改 |

---

## Task 1: API + 类型

**Files:** Modify `frontend/src/types/domain.ts`, `frontend/src/lib/api.ts`

- [ ] **Step 1: 类型（`domain.ts`）**

新增：
```typescript
export interface KnowledgeAttachment {
  id: number;
  document_id: number;
  filename: string;
  mime?: string | null;
  size_bytes?: number | null;
  created_at?: string | null;
}
```
在 `KnowledgeDoc` 里补两个可选字段（`is_pinned` 附近）：
```typescript
  doc_type?: string;
  attachments?: KnowledgeAttachment[];
```

- [ ] **Step 2: API（`api.ts`）**

在 `knowledgeApi` 里（`remove` 之前）新增四个方法：
```typescript
  uploadFileDoc(projectId: number, folderId: number | null, file: File) {
    const fd = new FormData();
    fd.append("project_id", String(projectId));
    if (folderId != null) fd.append("folder_id", String(folderId));
    fd.append("file", file);
    return request<KnowledgeDoc>("/api/knowledge/upload", { method: "POST", body: fd });
  },
  addAttachment(docId: number, file: File) {
    const fd = new FormData();
    fd.append("file", file);
    return request<KnowledgeAttachment>(`/api/knowledge/${docId}/attachments`, { method: "POST", body: fd });
  },
  deleteAttachment(attachmentId: number) {
    return request<{ id: number }>(`/api/knowledge/attachments/${attachmentId}`, { method: "DELETE" });
  },
  async fetchAttachmentBlob(attachmentId: number, disposition: "inline" | "attachment" = "inline"): Promise<Blob> {
    const token = getToken();
    const res = await fetch(`/api/knowledge/attachments/${attachmentId}/download?disposition=${disposition}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`下载失败（${res.status}）`);
    return res.blob();
  },
```
`KnowledgeAttachment` 加入 api.ts 顶部从 `@/types/domain` 的 import；`getToken` 已在 api.ts 定义（同文件内直接可用）。

- [ ] **Step 3: typecheck + 提交**

Run: `cd frontend && npm run typecheck`（通过）
```bash
git add frontend/src/types/domain.ts frontend/src/lib/api.ts
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 文件 API（上传/加附件/删/鉴权取字节）+ 类型

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: FilePreview 组件

**Files:** Create `frontend/src/pages/knowledge/FilePreview.tsx`

- [ ] **Step 1: 写组件（完整代码）**

```tsx
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
```

> 安全：xlsx `sheet_to_html` 对单元格值做 HTML 转义，`dangerouslySetInnerHTML` 用其输出可接受；docx-preview 渲染样式化文本/表格，不执行脚本。文件由已登录成员上传，风险可控。

- [ ] **Step 2: typecheck + lint（本文件）**

Run: `cd frontend && npm run typecheck && npx eslint src/pages/knowledge/FilePreview.tsx --max-warnings 0`
Expected: 通过。若 `docx-preview`/`xlsx` 无类型声明报错，装 `@types` 或在文件顶部加 `// @ts-expect-error`（docx-preview 自带类型；xlsx 自带类型，一般无需）。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/knowledge/FilePreview.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): FilePreview——图片/PDF/docx/xlsx/文本 在线预览

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: AttachmentList 组件（富文本文档附件区）

**Files:** Create `frontend/src/pages/knowledge/AttachmentList.tsx`

- [ ] **Step 1: 写组件（完整代码）**

```tsx
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
```

- [ ] **Step 2: typecheck + lint（本文件）+ 提交**

Run: `cd frontend && npm run typecheck && npx eslint src/pages/knowledge/AttachmentList.tsx --max-warnings 0`
```bash
git add frontend/src/pages/knowledge/AttachmentList.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): AttachmentList——附件列表/上传/删除/预览弹窗

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 面板「上传文件」入口 + 文件文档卡片区分

**Files:** Modify `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx`

- [ ] **Step 1: 上传入口**

- import 补：`import { FileUp, FileText } from "lucide-react";`（合并进现有 lucide import）。加 `import { useRef } from "react";`（合并进现有 react import）。
- 组件内加：
```tsx
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadFile = useMutation({
    mutationFn: (file: File) => knowledgeApi.uploadFileDoc(projectId, selectedFolderId, file),
    onSuccess: () => { toast.success("已上传文件"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const onPickFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach((f) => uploadFile.mutate(f));
    if (fileInputRef.current) fileInputRef.current.value = "";
  };
```
- 工具条里「新建文档」按钮**左侧**加一个「上传文件」按钮 + 隐藏 input：
```tsx
        <Button size="sm" variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploadFile.isPending}>
          <FileUp className="h-4 w-4 mr-1" />{uploadFile.isPending ? "上传中…" : "上传文件"}
        </Button>
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => onPickFiles(e.target.files)} />
```

- [ ] **Step 2: 文件文档卡片区分**

在卡片标题 `<h3>` 前，若是文件文档加个图标；把分类芯片区对文件文档改成「文件」芯片。具体：在 `<h3 className="flex-1 ...">{d.title}</h3>` 改为：
```tsx
                <h3 className="flex-1 font-semibold text-sm leading-snug line-clamp-2 flex items-center gap-1">
                  {d.doc_type === "file" && <FileText className="h-3.5 w-3.5 shrink-0 text-amber-600" />}
                  <span className="truncate">{d.title}</span>
                </h3>
```
并在分类/入库芯片那一行，最前面对文件文档加一个芯片（在 `<span className="rounded bg-muted ...">{TYPE_LABELS...}</span>` 之前）：
```tsx
                {d.doc_type === "file" && (
                  <span className="rounded bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">文件</span>
                )}
```

- [ ] **Step 3: typecheck + lint（本文件）+ 提交**

Run: `cd frontend && npm run typecheck && npx eslint src/pages/knowledge/KnowledgeBasePanel.tsx --max-warnings 0`
```bash
git add frontend/src/pages/knowledge/KnowledgeBasePanel.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 面板加「上传文件」入口 + 文件文档卡片区分

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 阅读抽屉集成（文件文档=FilePreview / 富文本=正文+附件区）

**Files:** Modify `frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx`

- [ ] **Step 1: import**

```tsx
import { FilePreview } from "./FilePreview";
import { AttachmentList } from "./AttachmentList";
```

- [ ] **Step 2: 阅读模式按 doc_type 分支**

当前阅读模式（`mode === "read"`）的内容是「左正文(ref) + 右 TOC」。改成：文件文档直接整块 FilePreview；富文本仍是正文+TOC，并在正文容器**内**、`<RichTextViewer>` 之后追加 `AttachmentList`。

把 `mode === "read"` 分支的最外层结构改为：
```tsx
      ) : mode === "read" ? (
        doc.doc_type === "file" ? (
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
            {metaBar}
            {doc.attachments && doc.attachments.length > 0 ? (
              <FilePreview attachment={doc.attachments[0]} />
            ) : (
              <div className="py-16 text-center text-sm text-muted-foreground">该文件文档暂无附件</div>
            )}
          </div>
        ) : (
          <div className="flex-1 overflow-hidden flex">
            <div ref={contentRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-4" style={{ fontSize: FONT_PX[fontIdx] }}>
              {metaBar}
              <RichTextViewer source={doc.content_html ?? ""} />
              <div className="pt-2 border-t">
                <AttachmentList docId={doc.id} attachments={doc.attachments ?? []} />
              </div>
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
        )
      ) : (
```
（即：把原来的单一 read 分支替换为「文件文档 / 富文本」二选一；富文本分支保留原 TOC 逻辑并加 AttachmentList。历史模式分支不变。）

- [ ] **Step 3: 字号按钮对文件文档隐藏（可选小改）**

footer 里 `{mode === "read" && (<Button ...字号...>)}` 改为 `{mode === "read" && doc?.doc_type !== "file" && (...)}`，文件文档没正文不需要字号。

- [ ] **Step 4: typecheck + lint（本文件）+ 提交**

Run: `cd frontend && npm run typecheck && npx eslint src/pages/knowledge/KnowledgeDocViewDrawer.tsx --max-warnings 0`
```bash
git add frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 抽屉集成——文件文档预览 / 富文本附件区

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 构建 + 浏览器核对

- [ ] **Step 1: typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: 通过，产出 dist。docx-preview/xlsx 会进 bundle（体积增大，chunk 警告可忽略）。

- [ ] **Step 2: 本阶段文件 lint**

Run: `cd frontend && npx eslint src/pages/knowledge/*.tsx --max-warnings 0`
Expected: 0 warning。

- [ ] **Step 3: 浏览器核对（起预览后端，用户登录看）**

1. 知识库 tab 顶部「上传文件」→ 选个 PDF/图片/docx/xlsx → 列表里出现文件卡片（带文件图标+「文件」芯片）→ 点开抽屉在线预览。
2. 打开一篇富文本文档 → 抽屉正文下方「附件」区可上传/预览/删除附件。
3. 各格式预览：图片、PDF、Word(docx)、Excel(xlsx/csv) 都能在抽屉/弹窗里渲染；不支持的格式显示下载按钮。
4. 删文件文档 → 文件从列表消失（后端已清盘）。

---

## 收尾与验收（3b）

- [ ] 上传文件成文件文档 + 卡片区分 + 抽屉预览；富文本文档附件区可上传/预览/删除。
- [ ] 图片/PDF/docx/xlsx 在线预览可用，其余兜底下载；预览走鉴权 blob（不裸奔 URL）。
- [ ] typecheck/build 通过，本阶段文件 lint 0 warning。

---

## Self-Review 记录

- **Spec 覆盖**：对应 spec 阶段 3 前端——上传（文件文档 + 富文本附件，用户选“两者都做”）、全格式预览（图片/PDF 原生 + docx-preview + SheetJS，用户选“全部”）、兜底下载。
- **占位扫描**：FilePreview/AttachmentList 全代码；API 全代码；面板/抽屉给精确插入与替换片段。无 TODO。
- **类型一致**：`uploadFileDoc`/`addAttachment`/`deleteAttachment`/`fetchAttachmentBlob`（api）、`KnowledgeAttachment`/`KnowledgeDoc.doc_type/attachments`（types）、`FilePreview({attachment})`/`AttachmentList({docId,attachments})` props 在各处一致。
- **鉴权**：预览/下载都走 `fetchAttachmentBlob`（带 Bearer 的 fetch → Blob → objectURL），规避 `<img src>` 不带 token；后端下载路由 `CurrentUserDep` + nosniff。
- **兼容**：仅新增；富文本文档 `doc_type='rich_text'`、`attachments` 为空数组时 AttachmentList 显示“暂无附件”，不影响既有阅读。
