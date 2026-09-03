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
    """文件名清洗：非[字母数字中文.\\- 空格]→下划线；空→doc；截断 80。"""
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
