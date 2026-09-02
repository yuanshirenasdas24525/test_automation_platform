"""知识库附件（KnowledgeAttachment）服务层 —— 阶段 3。"""
from __future__ import annotations

from typing import List, Optional

from database.models import KnowledgeAttachment
from utils import knowledge_storage as storage


def serialize_attachment(a: KnowledgeAttachment) -> dict:
    return {
        "id": a.id,
        "document_id": a.document_id,
        "filename": a.filename,
        "mime": a.mime,
        "size_bytes": a.size_bytes,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def list_attachments(session, document_id: int) -> List[KnowledgeAttachment]:
    return (
        session.query(KnowledgeAttachment)
        .filter(KnowledgeAttachment.document_id == document_id)
        .order_by(KnowledgeAttachment.id.asc())
        .all()
    )


def get_attachment(session, attachment_id: int) -> Optional[KnowledgeAttachment]:
    return (
        session.query(KnowledgeAttachment)
        .filter(KnowledgeAttachment.id == attachment_id)
        .first()
    )


def create_attachment(
    session, doc, *, filename: str, mime: Optional[str], data: bytes, uploaded_by: Optional[int] = None
) -> KnowledgeAttachment:
    rel, size = storage.save_bytes(doc.project_id, filename, data)
    a = KnowledgeAttachment(
        document_id=doc.id,
        filename=(filename or "文件")[:255],
        mime=(mime or "")[:128],
        size_bytes=size,
        storage_path=rel,
        uploaded_by=uploaded_by,
    )
    session.add(a)
    session.flush()
    return a


def delete_attachment(session, a: KnowledgeAttachment) -> None:
    storage.delete_file(a.storage_path)   # 磁盘删除失败只记日志，不阻断
    session.delete(a)
    session.flush()
