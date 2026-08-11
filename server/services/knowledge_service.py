"""知识库（Knowledge Base）服务层。

知识库文档与 AI 抽取的上下文**共用 `project_contexts` 表**，靠 ``source_type='knowledge'``
区分（方案 A，见 docs/superpowers/specs/2026-08-11-knowledge-base-design.md）：

- ``content_html``：富文本原文，供人阅读/编辑
- ``content``：去标签纯文本，供关键词检索（``context_service.retrieve_context`` 直接消费）
- ``importance``：>0 参与 AI 检索；=0 表示「不纳入 AI 知识库」，只给人读

因为用例生成已在消费 ``project_contexts``，知识文档存进来即被召回——检索零成本。
v1 不做向量 embedding（现有检索是关键词），故也不需要异步任务。
"""
from __future__ import annotations

import html as _html
import re
from typing import List, Optional

from database.models import (
    ALL_CONTEXT_TYPES,
    CONTEXT_SOURCE_KNOWLEDGE,
    CONTEXT_TYPE_TERM_DEFINITION,
    ProjectContext,
)

# importance 约定：纳入检索用默认权重 3，关闭则 0（被 retrieve_context 的 importance>0 过滤掉）
KNOWLEDGE_IMPORTANCE_ON = 3
KNOWLEDGE_IMPORTANCE_OFF = 0

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(html: Optional[str]) -> str:
    """富文本 HTML → 纯文本。

    内容可能是正常 HTML，也可能是被转义存储的 HTML（``&lt;p&gt;…``）——反复
    「反转义 + 去标签」直到稳定，两种情况都还原成纯文本。与前端 ``stripHtml`` 对齐。
    """
    if not html:
        return ""
    text = html
    for _ in range(3):
        stripped = _TAG_RE.sub("", _html.unescape(text))
        if stripped == text:
            break
        text = stripped
    return _WS_RE.sub(" ", text).strip()


def _normalize_context_type(context_type: Optional[str]) -> str:
    """知识文档的分类落到既有上下文类型枚举里，保证能被检索摘要正确归类。"""
    if context_type and context_type in ALL_CONTEXT_TYPES:
        return context_type
    return CONTEXT_TYPE_TERM_DEFINITION


def _keywords_for(content: str, include_in_rag: bool) -> list:
    """纳入检索时抽关键词，否则留空（反正不会被召回）。"""
    if not include_in_rag:
        return []
    from server.services.context_service import _extract_keywords

    return _extract_keywords(content)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def list_docs(
    session, project_id: int, module_id: Optional[int] = None
) -> List[ProjectContext]:
    """列某项目（可选模块）下的知识文档，按更新时间倒序。"""
    q = session.query(ProjectContext).filter(
        ProjectContext.project_id == project_id,
        ProjectContext.source_type == CONTEXT_SOURCE_KNOWLEDGE,
    )
    if module_id is not None:
        q = q.filter(ProjectContext.module_id == module_id)
    return q.order_by(ProjectContext.updated_at.desc(), ProjectContext.id.desc()).all()


def get_doc(session, doc_id: int) -> Optional[ProjectContext]:
    """取单篇知识文档（仅限 source_type=knowledge）。"""
    return (
        session.query(ProjectContext)
        .filter(
            ProjectContext.id == doc_id,
            ProjectContext.source_type == CONTEXT_SOURCE_KNOWLEDGE,
        )
        .first()
    )


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
) -> ProjectContext:
    content = html_to_text(content_html)
    doc = ProjectContext(
        project_id=project_id,
        module_id=module_id,
        source_type=CONTEXT_SOURCE_KNOWLEDGE,
        context_type=_normalize_context_type(context_type),
        title=(title or "").strip()[:255],
        content=content,
        content_html=content_html or "",
        summary=content[:500],
        keywords=_keywords_for(content, include_in_rag),
        importance=KNOWLEDGE_IMPORTANCE_ON if include_in_rag else KNOWLEDGE_IMPORTANCE_OFF,
    )
    session.add(doc)
    session.flush()
    return doc


def update_doc(
    session,
    doc: ProjectContext,
    *,
    title: Optional[str] = None,
    content_html: Optional[str] = None,
    module_id: Optional[int] = ...,  # ... = 不改；None = 移到根级
    context_type: Optional[str] = None,
    include_in_rag: Optional[bool] = None,
) -> ProjectContext:
    if title is not None:
        doc.title = title.strip()[:255]
    if content_html is not None:
        doc.content_html = content_html
        doc.content = html_to_text(content_html)
        doc.summary = doc.content[:500]
    if module_id is not ...:
        doc.module_id = module_id
    if context_type is not None:
        doc.context_type = _normalize_context_type(context_type)
    if include_in_rag is not None:
        doc.importance = (
            KNOWLEDGE_IMPORTANCE_ON if include_in_rag else KNOWLEDGE_IMPORTANCE_OFF
        )
    # 内容或开关变了都重算关键词（关闭则清空）
    doc.keywords = _keywords_for(doc.content or "", (doc.importance or 0) > 0)
    session.flush()
    return doc


def delete_doc(session, doc: ProjectContext) -> None:
    session.delete(doc)
    session.flush()


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def serialize(doc: ProjectContext, *, detail: bool = False) -> dict:
    data = {
        "id": doc.id,
        "project_id": doc.project_id,
        "module_id": doc.module_id,
        "title": doc.title,
        "context_type": doc.context_type,
        "summary": doc.summary,
        "include_in_rag": (doc.importance or 0) > 0,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }
    if detail:
        data["content_html"] = doc.content_html or ""
    return data
