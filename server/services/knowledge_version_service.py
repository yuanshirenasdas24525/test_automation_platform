"""知识库文档版本（KnowledgeDocumentVersion）服务层 —— 阶段 2。

每次编辑覆盖正文前存一版当前内容的快照；仅保留最近 MAX_VERSIONS 版。
"""
from __future__ import annotations

from typing import List, Optional

from database.models import KnowledgeDocumentVersion

MAX_VERSIONS = 20


def content_changed(
    old_title: Optional[str],
    old_html: Optional[str],
    new_title: Optional[str],
    new_html: Optional[str],
) -> bool:
    """标题或正文将发生实际变化则 True。new_* 为 None 表示该字段本次不改。"""
    if new_title is not None and new_title != (old_title or ""):
        return True
    if new_html is not None and new_html != (old_html or ""):
        return True
    return False


def snapshot(session, doc, *, editor_id: Optional[int] = None) -> KnowledgeDocumentVersion:
    """把 doc 的当前 title/content_html 存为一版，并裁剪到最近 MAX_VERSIONS 版。"""
    v = KnowledgeDocumentVersion(
        document_id=doc.id,
        title=doc.title,
        content_html=doc.content_html or "",
        editor_id=editor_id,
    )
    session.add(v)
    session.flush()
    _prune(session, doc.id)
    return v


def _prune(session, document_id: int) -> None:
    ids = [
        r[0]
        for r in session.query(KnowledgeDocumentVersion.id)
        .filter(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.id.desc())
        .all()
    ]
    stale = ids[MAX_VERSIONS:]
    if stale:
        session.query(KnowledgeDocumentVersion).filter(
            KnowledgeDocumentVersion.id.in_(stale)
        ).delete(synchronize_session=False)
        session.flush()


def list_versions(session, document_id: int) -> List[KnowledgeDocumentVersion]:
    return (
        session.query(KnowledgeDocumentVersion)
        .filter(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.id.desc())
        .all()
    )


def get_version(session, version_id: int) -> Optional[KnowledgeDocumentVersion]:
    return (
        session.query(KnowledgeDocumentVersion)
        .filter(KnowledgeDocumentVersion.id == version_id)
        .first()
    )


def serialize_version(v: KnowledgeDocumentVersion, *, detail: bool = False) -> dict:
    data = {
        "id": v.id,
        "document_id": v.document_id,
        "title": v.title,
        "editor_id": v.editor_id,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
    if detail:
        data["content_html"] = v.content_html or ""
    return data
