"""知识库（Knowledge Base）服务层 —— 阶段 0：独立表 + RAG 单向投影。

写路径：CRUD 落 ``knowledge_documents``（及标签/附件/版本，后续阶段用）；每次写
文档后调用 ``sync_rag_projection`` 幂等地把「纳入检索」的文档投影一行到
``project_contexts``（source_type='knowledge'，knowledge_document_id 关联）。
AI 用例生成侧 ``context_service.retrieve_context`` 照旧按 importance>0 消费，零改动。

投影是派生数据：投影失败**不得**阻断文档保存（见 sync_rag_projection 的兜底）。
"""
from __future__ import annotations

import html as _html
import re
from typing import List, Optional

from database.models import (
    ALL_CONTEXT_TYPES,
    CONTEXT_SOURCE_KNOWLEDGE,
    CONTEXT_TYPE_TERM_DEFINITION,
    KnowledgeDocument,
    ProjectContext,
)

KNOWLEDGE_IMPORTANCE_ON = 3
KNOWLEDGE_IMPORTANCE_OFF = 0

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# 纯函数（被单测覆盖）
# ---------------------------------------------------------------------------

def html_to_text(html: Optional[str]) -> str:
    """富文本 HTML → 纯文本；反复反转义+去标签直到稳定。"""
    if not html:
        return ""
    text = html
    for _ in range(3):
        stripped = _TAG_RE.sub("", _html.unescape(text))
        if stripped == text:
            break
        text = stripped
    return _WS_RE.sub(" ", text).strip()


def normalize_search_query(q: Optional[str]) -> Optional[str]:
    """搜索词规范化：去首尾空白；空/纯空白返回 None（表示不按关键字过滤）。"""
    if not q:
        return None
    s = q.strip()
    return s or None


def _normalize_context_type(context_type: Optional[str]) -> str:
    if context_type and context_type in ALL_CONTEXT_TYPES:
        return context_type
    return CONTEXT_TYPE_TERM_DEFINITION


def _keywords_for(content: str, include_in_rag: bool) -> list:
    if not include_in_rag:
        return []
    from server.services.context_service import _extract_keywords
    return _extract_keywords(content)


def projection_fields(doc: dict) -> dict:
    """从文档快照 dict 计算写入 project_contexts 的字段（纯函数，便于单测）。

    doc 需含：project_id, module_id, title, context_type, content, include_in_rag。
    """
    include = bool(doc.get("include_in_rag"))
    content = doc.get("content") or ""
    return {
        "project_id": doc["project_id"],
        "module_id": doc.get("module_id"),
        "source_type": CONTEXT_SOURCE_KNOWLEDGE,
        "context_type": _normalize_context_type(doc.get("context_type")),
        "title": (doc.get("title") or "").strip()[:255],
        "content": content,
        "content_html": doc.get("content_html") or "",
        "summary": content[:500],
        "keywords": _keywords_for(content, include),
        "importance": KNOWLEDGE_IMPORTANCE_ON if include else KNOWLEDGE_IMPORTANCE_OFF,
    }


def _doc_snapshot(doc: KnowledgeDocument) -> dict:
    return {
        "project_id": doc.project_id,
        "module_id": doc.module_id,
        "title": doc.title,
        "context_type": doc.context_type,
        "content": doc.content or "",
        "content_html": doc.content_html or "",
        "include_in_rag": bool(doc.include_in_rag),
    }


# ---------------------------------------------------------------------------
# RAG 投影同步
# ---------------------------------------------------------------------------

def sync_rag_projection(session, doc: KnowledgeDocument) -> None:
    """把文档投影到 project_contexts（幂等 upsert）；不纳入检索则删除投影行。

    投影是派生数据：用 SAVEPOINT 隔离，投影失败只回滚本嵌套块并记日志，
    绝不把外层事务标脏、绝不阻断文档保存。
    """
    try:
        with session.begin_nested():  # SAVEPOINT
            row = (
                session.query(ProjectContext)
                .filter(ProjectContext.knowledge_document_id == doc.id)
                .first()
            )
            if not doc.include_in_rag:
                if row is not None:
                    session.delete(row)
            else:
                fields = projection_fields(_doc_snapshot(doc))
                if row is None:
                    row = ProjectContext(knowledge_document_id=doc.id, **fields)
                    session.add(row)
                else:
                    for k, v in fields.items():
                        setattr(row, k, v)
            session.flush()
    except Exception:  # noqa: BLE001 —— 投影失败不阻断主流程
        import logging
        logging.getLogger(__name__).exception(
            "sync_rag_projection failed for knowledge_document_id=%s", getattr(doc, "id", None)
        )


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def list_docs(session, project_id: int, module_id: Optional[int] = None) -> List[KnowledgeDocument]:
    q = session.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
    if module_id is not None:
        q = q.filter(KnowledgeDocument.module_id == module_id)
    return q.order_by(
        KnowledgeDocument.is_pinned.desc(),
        KnowledgeDocument.updated_at.desc(),
        KnowledgeDocument.id.desc(),
    ).all()


def get_doc(session, doc_id: int) -> Optional[KnowledgeDocument]:
    return session.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------

def create_doc(
    session,
    *,
    project_id: int,
    title: str,
    content_html: str,
    module_id: Optional[int] = None,
    context_type: Optional[str] = None,
    include_in_rag: bool = True,
    author_id: Optional[int] = None,
) -> KnowledgeDocument:
    content = html_to_text(content_html)
    doc = KnowledgeDocument(
        project_id=project_id,
        module_id=module_id,
        doc_type="rich_text",
        title=(title or "").strip()[:255],
        content=content,
        content_html=content_html or "",
        context_type=_normalize_context_type(context_type),
        include_in_rag=include_in_rag,
        author_id=author_id,
        editor_id=author_id,
    )
    session.add(doc)
    session.flush()          # 拿到 doc.id 供投影关联
    sync_rag_projection(session, doc)
    return doc


def update_doc(
    session,
    doc: KnowledgeDocument,
    *,
    title: Optional[str] = None,
    content_html: Optional[str] = None,
    module_id: Optional[int] = ...,   # ... = 不改
    context_type: Optional[str] = None,
    include_in_rag: Optional[bool] = None,
    editor_id: Optional[int] = None,
) -> KnowledgeDocument:
    if title is not None:
        doc.title = title.strip()[:255]
    if content_html is not None:
        doc.content_html = content_html
        doc.content = html_to_text(content_html)
    if module_id is not ...:
        doc.module_id = module_id
    if context_type is not None:
        doc.context_type = _normalize_context_type(context_type)
    if include_in_rag is not None:
        doc.include_in_rag = include_in_rag
    if editor_id is not None:
        doc.editor_id = editor_id
    session.flush()
    sync_rag_projection(session, doc)
    return doc


def delete_doc(session, doc: KnowledgeDocument) -> None:
    # 先删投影行，再删文档（附件/版本/标签关联走 ORM cascade / FK ondelete）
    session.query(ProjectContext).filter(
        ProjectContext.knowledge_document_id == doc.id
    ).delete(synchronize_session=False)
    session.delete(doc)
    session.flush()


# ---------------------------------------------------------------------------
# 序列化（阶段 0 保持与旧响应形状兼容，前端零改动）
# ---------------------------------------------------------------------------

def serialize(doc: KnowledgeDocument, *, detail: bool = False) -> dict:
    data = {
        "id": doc.id,
        "project_id": doc.project_id,
        "module_id": doc.module_id,
        "title": doc.title,
        "context_type": doc.context_type,
        "summary": (doc.content or "")[:500],
        "include_in_rag": bool(doc.include_in_rag),
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }
    if detail:
        data["content_html"] = doc.content_html or ""
    return data
