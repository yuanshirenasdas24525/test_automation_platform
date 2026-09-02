"""知识库标签（KnowledgeTag）服务层 —— 阶段 1a。

项目内标签的 CRUD，以及「给文档设置标签集」（整体替换文档的标签关联）。
"""
from __future__ import annotations

from typing import List, Optional

from database.models import KnowledgeTag, KnowledgeDocument


def normalize_tag_name(name: Optional[str]) -> str:
    return (name or "").strip()[:64]


def dedupe_tag_ids(tag_ids: List[int]) -> List[int]:
    """去重并保序。"""
    seen = set()
    out = []
    for t in tag_ids or []:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def serialize_tag(t: KnowledgeTag) -> dict:
    return {"id": t.id, "project_id": t.project_id, "name": t.name, "color": t.color}


def list_tags(session, project_id: int) -> List[KnowledgeTag]:
    return (
        session.query(KnowledgeTag)
        .filter(KnowledgeTag.project_id == project_id)
        .order_by(KnowledgeTag.name.asc())
        .all()
    )


def get_tag(session, tag_id: int) -> Optional[KnowledgeTag]:
    return session.query(KnowledgeTag).filter(KnowledgeTag.id == tag_id).first()


def get_or_create_tag(session, *, project_id: int, name: str, color: Optional[str] = None) -> KnowledgeTag:
    n = normalize_tag_name(name)
    if not n:
        raise ValueError("标签名不能为空")
    existing = (
        session.query(KnowledgeTag)
        .filter(KnowledgeTag.project_id == project_id, KnowledgeTag.name == n)
        .first()
    )
    if existing:
        if color and existing.color != color:
            existing.color = color
            session.flush()
        return existing
    tag = KnowledgeTag(project_id=project_id, name=n, color=color)
    session.add(tag)
    session.flush()
    return tag


def update_tag(session, tag: KnowledgeTag, *, name: Optional[str] = None, color: Optional[str] = None) -> KnowledgeTag:
    if name is not None:
        n = normalize_tag_name(name)
        if n:
            tag.name = n
    if color is not None:
        tag.color = color
    session.flush()
    return tag


def delete_tag(session, tag: KnowledgeTag) -> None:
    # 删标签：SQLAlchemy 依 M2M 关系自动清 knowledge_document_tags 关联行，不删文档
    session.delete(tag)
    session.flush()


def set_document_tags(session, doc: KnowledgeDocument, tag_ids: List[int]) -> None:
    """整体替换文档的标签集。只接受属于同项目的标签，忽略越权/不存在的 id。"""
    ids = dedupe_tag_ids(tag_ids)
    if not ids:
        doc.tags = []
        session.flush()
        return
    tags = (
        session.query(KnowledgeTag)
        .filter(KnowledgeTag.id.in_(ids), KnowledgeTag.project_id == doc.project_id)
        .all()
    )
    doc.tags = tags
    session.flush()
