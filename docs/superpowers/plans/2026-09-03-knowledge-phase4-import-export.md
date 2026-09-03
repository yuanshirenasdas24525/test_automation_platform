# 知识库改造 · 阶段 4（导入导出）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 知识库导入导出（最后一阶段）：单篇导出 Markdown、整库/目录导出 Zip（MD+附件+manifest）、单篇导出 PDF（浏览器打印）；批量导入 Markdown/txt/Word(docx) 建文档。

**Architecture:** 导入导出主体放**后端**（文档内容与附件都在服务端，组 Zip 不必把附件回传浏览器）。新增 `server/services/knowledge_io_service.py`（HTML↔MD、docx→HTML、组 Zip、批量导入，纯转换函数可单测）；REST `server/api/knowledge_io.py`（`GET /{doc_id}/export.md`、`GET /export.zip`、`POST /import`，均鉴权）。前端：面板「导入/导出」菜单（导入文件、导出库 Zip）、抽屉「导出」（Markdown / PDF 打印）。转换库：`markdownify`/`markdown`/`mammoth`（已装并入 requirements）。

**Tech Stack:** FastAPI + markdownify/markdown/mammoth + stdlib zipfile；React 19 + TS strict + react-query + sonner。

**前置约定：**
- 包名 `server/`；Python `./venv/bin/python`；DB 命令先 `set -a && . ./.env && set +a`。
- 加新 REST 资源：router → `server/api/__init__.py` 导出 → `server/main.py` 两处列表都加（照 `knowledge_files_router`）。
- 前端命令在 `frontend/` 下；`npm run typecheck`/`build`；本阶段改动文件单独 `npx eslint <files> --max-warnings 0` 必须 0 warning（全仓那 3 条既有无关警告不算）。
- 导出/导入下载走**鉴权 fetch → Blob**（`getToken()`）；导入用 FormData（`request` 已支持）。
- 有 `venv`/`node_modules` 软链，**永远别 `git add -A`**。

**本阶段不做：** 递归子目录导出（`folder_id=None` 导整库，或指定单层目录）；导入图片/附件解包（导入仅建文档正文）；PDF 服务端渲染（用浏览器打印）。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `server/services/knowledge_io_service.py` | HTML↔MD/docx→HTML/组 Zip/批量导入 | 新建 |
| `server/api/knowledge_io.py` | `export.md`/`export.zip`/`import` 路由 | 新建 |
| `server/api/__init__.py` + `server/main.py` | 注册 | 修改 |
| `tests/knowledge/test_io.py` | 转换纯函数单测 | 新建 |
| `frontend/src/lib/download.ts` | `downloadBlob` + `printHtml` 工具 | 新建 |
| `frontend/src/lib/api.ts` | `exportDocMarkdown`/`exportZip`/`importFiles` | 修改 |
| `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx` | 「导入/导出」菜单 | 修改 |
| `frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx` | 「导出」（MD/PDF） | 修改 |

---

## Task 1: IO 转换纯函数单测（红）

**Files:** Create `tests/knowledge/test_io.py`

- [ ] **Step 1: 写失败测试**

```python
"""知识库导入导出 纯函数单测（不依赖 DB）。"""
from server.services import knowledge_io_service as io


def test_html_to_markdown():
    md = io.html_to_markdown("<h1>标题</h1><p>正文<strong>粗</strong></p>")
    assert "# 标题" in md
    assert "正文" in md and "粗" in md


def test_markdown_to_html():
    html = io.markdown_to_html("# 标题\n\n正文")
    assert "<h1" in html and "标题" in html
    assert "正文" in html


def test_safe_name():
    assert io.safe_name("a/b:c*.md") == "a_b_c_.md"
    assert io.safe_name("") == "doc"
    assert io.safe_name("中文名 ok") == "中文名 ok"


def test_ext_of():
    assert io.ext_of("A.MD") == "md"
    assert io.ext_of("x.docx") == "docx"
    assert io.ext_of("noext") == ""
```

- [ ] **Step 2: 跑确认失败**

Run: `./venv/bin/python -m pytest tests/knowledge/test_io.py -v`
Expected: FAIL —— 模块不存在。

- [ ] **Step 3: 提交**

```bash
git add tests/knowledge/test_io.py
git commit -m "$(cat <<'EOF'
test(knowledge): 导入导出转换 纯函数单测（红）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: IO service

**Files:** Create `server/services/knowledge_io_service.py`

- [ ] **Step 1: 写 service**

```python
"""知识库导入导出服务 —— 阶段 4。

- 导出：HTML→Markdown（markdownify）；整库/目录组 Zip（MD + 附件原件 + manifest.json）。
- 导入：Markdown/txt（markdown→HTML）、Word docx（mammoth→HTML）→ 建文档。
纯转换函数（html_to_markdown / markdown_to_html / docx_to_html / safe_name / ext_of）可单测。
"""
from __future__ import annotations

import io as _io
import json
import re
import zipfile
from typing import List, Optional, Tuple

import markdown as _markdown
import markdownify as _markdownify
import mammoth as _mammoth


_UNSAFE = re.compile(r"[^\w一-鿿.\- ]")


def safe_name(name: str) -> str:
    """文件名清洗：非[字母数字中文.\- 空格]→下划线；空→doc；截断 80。"""
    s = _UNSAFE.sub("_", (name or "").strip())
    return (s[:80] or "doc")


def ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def html_to_markdown(html: Optional[str]) -> str:
    if not html:
        return ""
    return _markdownify.markdownify(html, heading_style="ATX").strip()


def markdown_to_html(md: Optional[str]) -> str:
    if not md:
        return ""
    return _markdown.markdown(md, extensions=["extra", "sane_lists", "nl2br"])


def docx_to_html(data: bytes) -> str:
    result = _mammoth.convert_to_html(_io.BytesIO(data))
    return result.value or ""


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def doc_to_markdown(doc) -> str:
    """单篇文档 → Markdown 文本（标题 + 正文；文件文档给占位说明）。"""
    lines = [f"# {doc.title}", ""]
    if doc.doc_type == "file":
        lines.append("_（文件文档，正文见附件）_")
    else:
        lines.append(html_to_markdown(doc.content_html or ""))
    return "\n".join(lines).strip() + "\n"


def build_export_zip(session, project_id: int, folder_id: Optional[int] = None) -> bytes:
    """整库（folder_id=None）或单层目录 → Zip：docs/*.md + attachments/* + manifest.json。"""
    from server.services import knowledge_service as ks
    from server.services import knowledge_attachment_service as kas
    from utils import knowledge_storage as storage

    docs = ks.list_docs(session, project_id, folder_id=folder_id)
    buf = _io.BytesIO()
    manifest = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in docs:
            zf.writestr(f"docs/{d.id}_{safe_name(d.title)}.md", doc_to_markdown(d))
            atts = kas.list_attachments(session, d.id)
            for a in atts:
                p = storage.abs_path(a.storage_path)
                if p.is_file():
                    zf.writestr(f"attachments/{d.id}_{safe_name(a.filename)}", p.read_bytes())
            manifest.append({
                "id": d.id, "title": d.title, "doc_type": d.doc_type,
                "folder_id": d.folder_id, "context_type": d.context_type,
                "attachments": [a.filename for a in atts],
            })
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def import_files(
    session, *, project_id: int, folder_id: Optional[int],
    files: List[Tuple[str, bytes]], author_id: Optional[int] = None,
) -> list:
    """按扩展名把上传文件转成文档：md/markdown/txt→markdown_to_html；docx→docx_to_html。
    不支持的扩展名跳过。返回创建的文档列表。"""
    from server.services import knowledge_service as ks

    created = []
    for filename, data in files:
        ext = ext_of(filename)
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        if ext in ("md", "markdown", "txt"):
            html = markdown_to_html(data.decode("utf-8", errors="replace"))
        elif ext == "docx":
            html = docx_to_html(data)
        else:
            continue
        doc = ks.create_doc(
            session, project_id=project_id, title=(stem[:255] or "导入文档"),
            content_html=html, folder_id=folder_id, include_in_rag=False, author_id=author_id,
        )
        created.append(doc)
    return created
```

- [ ] **Step 2: 单测转绿 + 编译**

Run:
```bash
./venv/bin/python -m pytest tests/knowledge/test_io.py -v && \
./venv/bin/python -m compileall server/services/knowledge_io_service.py
```
Expected: 4 passed；compile 无错。

- [ ] **Step 3: 提交**

```bash
git add server/services/knowledge_io_service.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 导入导出 service——HTML↔MD/docx→HTML/组 Zip/批量导入

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: IO REST 路由 + 注册

**Files:**
- Create: `server/api/knowledge_io.py`
- Modify: `server/api/__init__.py`, `server/main.py`

- [ ] **Step 1: 写路由**

```python
"""/api/knowledge/* 导入导出端点 —— 单篇 MD / 整库 Zip 导出 + 批量导入。"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from database.models import Project
from server.api.deps import DBDep, CurrentUserDep
from server.services import knowledge_service
from server.services import knowledge_io_service as io_svc

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _require_project(session, project_id: int) -> Project:
    p = session.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return p


def _validate_folder(session, folder_id: Optional[int], project_id: int) -> None:
    if folder_id is None:
        return
    from server.services import knowledge_folder_service as kfs
    f = kfs.get_folder(session, folder_id)
    if not f or f.project_id != project_id:
        raise HTTPException(status_code=400, detail=f"目录不存在或不属于该项目：{folder_id}")


@router.get("/{doc_id}/export.md")
def export_doc_md(doc_id: int, db: DBDep, current_user: CurrentUserDep):
    doc = knowledge_service.get_doc(db.session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
    md = io_svc.doc_to_markdown(doc)
    fn = f"{io_svc.safe_name(doc.title)}.md"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fn)}"},
    )


@router.get("/export.zip")
def export_zip(
    db: DBDep, current_user: CurrentUserDep,
    project_id: int = Query(...), folder_id: Optional[int] = Query(None),
):
    _require_project(db.session, project_id)
    _validate_folder(db.session, folder_id, project_id)
    data = io_svc.build_export_zip(db.session, project_id, folder_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=knowledge-export.zip"},
    )


@router.post("/import")
async def import_docs(
    db: DBDep, current_user: CurrentUserDep,
    project_id: int = Form(...), folder_id: Optional[int] = Form(None),
    files: List[UploadFile] = File(...),
):
    _require_project(db.session, project_id)
    _validate_folder(db.session, folder_id, project_id)
    payload = [((f.filename or "文件"), await f.read()) for f in files]
    created = io_svc.import_files(
        db.session, project_id=project_id, folder_id=folder_id,
        files=payload, author_id=current_user.id,
    )
    return {"status": "success", "data": [knowledge_service.serialize(d) for d in created]}
```

- [ ] **Step 2: 注册（`__init__.py` + `main.py`）**

`server/api/__init__.py`：在 `from .knowledge_files import router as knowledge_files_router` 下加：
```python
from .knowledge_io import router as knowledge_io_router
```
`__all__` 里 `"knowledge_files_router",` 下加：
```python
    "knowledge_io_router",
```
`server/main.py`：两处 `knowledge_files_router,` 下各加：
```python
    knowledge_io_router,
```

- [ ] **Step 3: 编译 + 路由自查**

Run:
```bash
./venv/bin/python -m compileall server/api/knowledge_io.py server/api/__init__.py server/main.py && \
set -a && . ./.env && set +a && CELERY_TASK_ALWAYS_EAGER=1 ./venv/bin/python -c "
from server.main import app
ps = sorted({r.path for r in app.routes if 'export' in getattr(r,'path','') or (getattr(r,'path','')).endswith('/import')})
print(ps)
assert '/api/knowledge/export.zip' in ps and '/api/knowledge/import' in ps and '/api/knowledge/{doc_id}/export.md' in ps, ps
print('io routes mounted')
"
```
Expected: 打印三条 IO 路由 + `io routes mounted`。

- [ ] **Step 4: 提交**

```bash
git add server/api/knowledge_io.py server/api/__init__.py server/main.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 导入导出 REST（export.md/export.zip/import）+ 注册

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 后端端到端验证

**Files:** 临时脚本（验完删）

- [ ] **Step 1: 写 `scratchpad/verify_phase4.py`**

```python
"""阶段 4 后端验证：单篇 MD 导出、整库 Zip（含 md/附件/manifest）、导入 MD+docx 建文档。"""
import io as _io, sys, zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from database.db import DB
from database.models import Project, KnowledgeDocument
from server.services import knowledge_service as ks
from server.services import knowledge_attachment_service as kas
from server.services import knowledge_io_service as io_svc

db = DB(); s = db.session
pid = s.query(Project).order_by(Project.id.asc()).first().id
made = []
try:
    # 富文本文档 + 附件
    d = ks.create_doc(s, project_id=pid, title="ZZ导出文档",
                      content_html="<h2>小节</h2><p>正文<strong>粗体</strong></p>", include_in_rag=False); s.commit(); made.append(d.id)
    kas.create_attachment(s, d, filename="ZZ附件.txt", mime="text/plain", data=b"hello", uploaded_by=None); s.commit()

    # 1) 单篇 MD
    md = io_svc.doc_to_markdown(d)
    assert md.startswith("# ZZ导出文档") and "小节" in md and "粗体" in md
    print("✓ 单篇 MD 导出")

    # 2) 整库 Zip 含 md/附件/manifest
    zbytes = io_svc.build_export_zip(s, pid)
    zf = zipfile.ZipFile(_io.BytesIO(zbytes))
    names = zf.namelist()
    assert any(n.startswith("docs/") and n.endswith(".md") for n in names), names[:5]
    assert any(n.startswith("attachments/") for n in names)
    assert "manifest.json" in names
    print("✓ 整库 Zip 含 md/附件/manifest")

    # 3) 导入 MD
    created = io_svc.import_files(s, project_id=pid, folder_id=None,
                                  files=[("ZZ导入.md", "# 从MD导入\n\n正文一段".encode())], author_id=None); s.commit()
    assert len(created) == 1
    made.append(created[0].id)
    assert "从MD导入" in created[0].title or "从MD导入" in (created[0].content_html or "")
    assert "<h1" in (created[0].content_html or "") or "<p" in (created[0].content_html or "")
    print("✓ 导入 MD 建文档")

    # 4) 导入 docx（用 python-docx 现造一个）
    from docx import Document
    doc = Document(); doc.add_heading("Word标题", level=1); doc.add_paragraph("Word正文")
    b = _io.BytesIO(); doc.save(b)
    created2 = io_svc.import_files(s, project_id=pid, folder_id=None,
                                   files=[("ZZ导入.docx", b.getvalue())], author_id=None); s.commit()
    assert len(created2) == 1
    made.append(created2[0].id)
    assert "Word正文" in (created2[0].content_html or "")
    print("✓ 导入 docx 建文档")
    print("ALL GREEN")
finally:
    for did in made:
        x = s.query(KnowledgeDocument).filter(KnowledgeDocument.id == did).first()
        if x: ks.delete_doc(s, x)
    s.commit(); db.close()
```

- [ ] **Step 2: 跑 + 无污染 + 清理 + 全量单测**

Run:
```bash
set -a && . ./.env && set +a && ./venv/bin/python scratchpad/verify_phase4.py
set -a && . ./.env && set +a && ./venv/bin/python -c "
from database.db import DB
from database.models import KnowledgeDocument
print('残留 ZZ 文档(应0):', DB().session.query(KnowledgeDocument).filter(KnowledgeDocument.title.like('ZZ%')).count())
"
rm scratchpad/verify_phase4.py
./venv/bin/python -m pytest tests/knowledge/ -q
```
Expected: 4 个 ✓ + `ALL GREEN`；`残留 ZZ 文档(应0): 0`；pytest 22 passed（18 + IO 4）。

---

## Task 5: 前端 API + 下载/打印工具

**Files:**
- Create: `frontend/src/lib/download.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 工具（`download.ts`）**

```typescript
/** 前端下载/打印工具。 */
export function downloadBlob(blob: Blob, filename: string): void {
  const u = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = u; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] as string));
}

/** 打开打印窗口渲染标题+正文 HTML，供浏览器「打印/存为 PDF」。 */
export function printHtml(title: string, contentHtml: string): void {
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
    `<style>body{font-family:system-ui,-apple-system,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;line-height:1.7;color:#111}` +
    `h1{font-size:24px}img{max-width:100%}pre{background:#f5f5f5;padding:12px;border-radius:6px;overflow:auto}` +
    `table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px}blockquote{border-left:3px solid #ddd;margin:0;padding-left:12px;color:#555}</style>` +
    `</head><body><h1>${escapeHtml(title)}</h1>${contentHtml}<` + `script>window.onload=function(){window.print()}<` + `/script></body></html>`,
  );
  w.document.close();
}
```

- [ ] **Step 2: API（`api.ts`）**

在 `knowledgeApi` 里（`remove` 之前）加：
```typescript
  async exportDocMarkdown(docId: number): Promise<Blob> {
    const token = getToken();
    const res = await fetch(`/api/knowledge/${docId}/export.md`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`导出失败（${res.status}）`);
    return res.blob();
  },
  async exportZip(projectId: number, folderId: number | null): Promise<Blob> {
    const token = getToken();
    const qs = new URLSearchParams({ project_id: String(projectId) });
    if (folderId != null) qs.set("folder_id", String(folderId));
    const res = await fetch(`/api/knowledge/export.zip?${qs.toString()}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`导出失败（${res.status}）`);
    return res.blob();
  },
  importFiles(projectId: number, folderId: number | null, files: File[]) {
    const fd = new FormData();
    fd.append("project_id", String(projectId));
    if (folderId != null) fd.append("folder_id", String(folderId));
    files.forEach((f) => fd.append("files", f));
    return request<KnowledgeDoc[]>("/api/knowledge/import", { method: "POST", body: fd });
  },
```

- [ ] **Step 3: typecheck + 提交**

Run: `cd frontend && npm run typecheck`（通过）
```bash
git add frontend/src/lib/download.ts frontend/src/lib/api.ts
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 导入导出 API + 下载/打印工具

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 面板导入/导出菜单 + 抽屉导出

**Files:**
- Modify: `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx`
- Modify: `frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx`

- [ ] **Step 1: 面板导入/导出**

imports 补：`import { Download, Upload as UploadIcon } from "lucide-react";`（合并进现有 lucide import，注意别和已有名字冲突——若 `Upload` 已用则用 `UploadIcon` 别名）；`import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";`；`import { downloadBlob } from "@/lib/download";`。

加状态与逻辑（在 hooks 区）：
```tsx
  const importInputRef = useRef<HTMLInputElement>(null);
  const importFiles = useMutation({
    mutationFn: (files: File[]) => knowledgeApi.importFiles(projectId, selectedFolderId, files),
    onSuccess: (docs) => { toast.success(`已导入 ${docs.length} 篇`); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const onImportPick = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    importFiles.mutate(Array.from(files));
    if (importInputRef.current) importInputRef.current.value = "";
  };
  const onExportZip = async () => {
    try {
      const blob = await knowledgeApi.exportZip(projectId, selectedFolderId);
      downloadBlob(blob, "knowledge-export.zip");
    } catch (e) { toast.error((e as Error).message); }
  };
```

工具条里「上传文件」按钮**左侧**加一个「导入/导出」下拉：
```tsx
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline">导入/导出</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => importInputRef.current?.click()}>
              <UploadIcon className="h-4 w-4 mr-1" />导入 MD/Word…
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onExportZip}>
              <Download className="h-4 w-4 mr-1" />导出{selectedFolderId != null ? "当前目录" : "整库"}(Zip)
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <input ref={importInputRef} type="file" multiple accept=".md,.markdown,.txt,.docx" className="hidden" onChange={(e) => onImportPick(e.target.files)} />
```

- [ ] **Step 2: 抽屉导出（Markdown / PDF）**

在 `KnowledgeDocViewDrawer.tsx`：
- import 补：`import { downloadBlob, printHtml } from "@/lib/download";`；lucide 加 `Download`（合并）。
- footer 的右侧「编辑」按钮**左侧**，为**富文本文档**加一个「导出」下拉（用已 import 的 DropdownMenu；若未 import 需补 `@/components/ui/dropdown-menu`）：
```tsx
            {doc && doc.doc_type !== "file" && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost"><Download className="h-4 w-4 mr-1" />导出</Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={async () => {
                    try { downloadBlob(await knowledgeApi.exportDocMarkdown(doc.id), `${doc.title}.md`); }
                    catch (e) { toast.error((e as Error).message); }
                  }}>导出 Markdown</DropdownMenuItem>
                  <DropdownMenuItem onClick={() => printHtml(doc.title, doc.content_html ?? "")}>
                    导出 PDF（打印）
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
```
（`toast` 从 sonner import——drawer 已 import `toast`；`knowledgeApi` 已 import。若 `DropdownMenu` 系列未 import 则补 import。）

- [ ] **Step 3: typecheck + lint（本阶段文件）+ 提交**

Run:
```bash
cd frontend && npm run typecheck && npx eslint src/pages/knowledge/KnowledgeBasePanel.tsx src/pages/knowledge/KnowledgeDocViewDrawer.tsx src/lib/download.ts --max-warnings 0
```
Expected: typecheck 通过；eslint 0 warning。
```bash
git add frontend/src/pages/knowledge/KnowledgeBasePanel.tsx frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 面板导入/导出菜单 + 抽屉导出 Markdown/PDF

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 构建 + 浏览器核对

- [ ] **Step 1: typecheck + build + 本阶段文件 lint**

Run:
```bash
cd frontend && npm run typecheck && npm run build && npx eslint src/pages/knowledge/*.tsx src/lib/download.ts --max-warnings 0
```
Expected: 全过；产出 dist；lint 0 warning。

- [ ] **Step 2: 浏览器核对（起预览后端，用户登录看）**

1. 知识库 tab「导入/导出」→ 导入：选几个 .md/.docx → 列表新增对应文档，正文正确。
2. 「导入/导出」→ 导出整库(Zip)：下载 zip，解压见 docs/*.md + attachments/* + manifest.json。选中某目录时导出「当前目录」。
3. 打开富文本文档 →「导出」→ 导出 Markdown（下载 .md）/ 导出 PDF（弹打印窗，可存 PDF）。
4. 文件文档不显示「导出」（其内容是文件本身，走附件下载）。

---

## 收尾与验收

- [ ] 单篇 MD 导出、整库/目录 Zip 导出（MD+附件+manifest）、单篇 PDF 打印、批量导入 MD/docx 全部可用。
- [ ] 导出/导入走鉴权；导入校验 folder 归属；不支持的扩展名跳过。
- [ ] 后端 22 单测通过；前端 typecheck/build 通过、本阶段文件 lint 0 warning。
- [ ] AI 召回不受影响（导入文档默认仅人读；导出只读不改库）。

---

## Self-Review 记录

- **Spec 覆盖**：对应 spec 阶段 4——导出单篇 MD/PDF + 整库/目录 Zip（MD+附件+manifest），导入 MD/Word。按用户决定：导出三种全做（PDF 走浏览器打印）、导入 MD+docx。
- **占位扫描**：IO service/router 全代码；download 工具全代码；面板/抽屉给精确插入片段。无 TODO。
- **类型一致**：`html_to_markdown`/`markdown_to_html`/`docx_to_html`/`safe_name`/`ext_of`/`doc_to_markdown`/`build_export_zip`/`import_files`（service）、`exportDocMarkdown`/`exportZip`/`importFiles`（api）、`downloadBlob`/`printHtml`（工具）在各处一致。
- **安全**：导出/导入端点 `CurrentUserDep`；导入校验 folder 归属 + 扩展名过滤；导入文档 `include_in_rag=False`（仅人读，不自动进 RAG）；`printHtml` 对 title 转义，正文是文档自有 HTML（可信）。
- **边界**：Zip 导出按 `list_docs(folder_id)` 单层过滤（folder_id=None=整库），不递归子目录（YAGNI）；导入仅建正文文档，不解包图片。
