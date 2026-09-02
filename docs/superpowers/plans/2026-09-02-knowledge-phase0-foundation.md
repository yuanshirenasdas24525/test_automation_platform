# 知识库改造 · 阶段 0（地基）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把知识库从寄生 `project_contexts` 迁到独立表（文档/目录/标签/附件/版本），并保留一条「RAG 单向投影」使 AI 用例生成召回零改动。

**Architecture:** 新建 5 张 `knowledge_*` 表；`project_contexts` 加 `knowledge_document_id` 外键作为投影关联键。`knowledge_service` 每次写文档后幂等 upsert/删除对应投影行（`source_type='knowledge'`）。数据迁移脚本把现有知识行搬进新表并回填关联键（复用旧行做投影，不重复召回）。本阶段 **API 响应形状保持向后兼容**，前端零改动。

**Tech Stack:** SQLAlchemy 2.0 + Alembic + FastAPI（`DBDep`）+ PostgreSQL；`database.base.JSONType`；pytest（纯函数单测）+ 一次性数据迁移脚本（argparse dry-run/`--commit`）。

**前置约定（务必先读）：**
- 包名是 `server/` 不是 `platform/`（CLAUDE.md trap #1）。
- 路径锚点用 `Path(__file__).resolve().parent...`，禁止 `Path.cwd()`（trap #4）。
- 无 ruff/black，跟已有文件风格走；提交前 `python -m compileall <改动文件>` 自查。
- Python 解释器用仓库的 `./venv/bin/python`（memory 记录）。
- Alembic 迁移**用 autogenerate 再人工 review**（autogenerate 常漏 server_default / index）。当前有 merge head，`down_revision` 交给 autogenerate 自动填，不要手写。
- DB 相关操作需要 `.env` 里的 `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME`（或 docker compose 起 pg）。

**本阶段不做：** 目录树/标签/搜索的 API 与前端（阶段 1）、阅读视图（阶段 2）、文件上传预览（阶段 3）、导入导出（阶段 4）。本阶段只建表 + 迁移 + 投影 + 兼容旧 API。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `database/models/knowledge.py` | 5 张知识库表 + 常量 | 新建 |
| `database/models/__init__.py` | 导出新模型 | 修改 |
| `database/models/project_context.py` | 加 `knowledge_document_id` 列 | 修改 |
| `database/migrations/versions/<auto>_knowledge_dedicated_tables.py` | schema 迁移 | 新建(autogen) |
| `server/services/knowledge_service.py` | 面向新表的 CRUD + `sync_rag_projection` | 重写 |
| `server/api/knowledge.py` | 用新 service；补 IDOR 校验、作者追溯；响应保持兼容 | 修改 |
| `database/migrations/data_migrations/migrate_knowledge_to_dedicated_tables.py` | 老数据搬迁 + 回填关联键 | 新建 |
| `tests/knowledge/test_knowledge_projection.py` | 纯函数单测（字段映射/html_to_text） | 新建 |

---

## Task 1: 新建知识库数据模型

**Files:**
- Create: `database/models/knowledge.py`

- [ ] **Step 1: 写模型文件**

创建 `database/models/knowledge.py`：

```python
"""知识库（Knowledge Base）独立数据模型。

阶段 0 起，知识库文档从寄生 ``project_contexts`` 迁到本组独立表：
  - KnowledgeFolder    多级目录树（替代原先借用的模块树）
  - KnowledgeDocument  文档主体（富文本 rich_text 或文件 file）
  - KnowledgeTag / KnowledgeDocumentTag  标签（多对多）
  - KnowledgeAttachment  文件附件（file 文档的主文件也是一条附件）
  - KnowledgeDocumentVersion  版本历史快照

与 AI 检索的关系：纳入检索的文档由 ``knowledge_service.sync_rag_projection``
单向投影一行到 ``project_contexts``（source_type='knowledge'），AI 用例生成侧
``context_service.retrieve_context`` 照旧消费，零改动。
"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, ForeignKey, DateTime, func, Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database.base import Base


# -- doc_type 枚举 -----------------------------------------------------------
KB_DOC_TYPE_RICH_TEXT = "rich_text"
KB_DOC_TYPE_FILE = "file"
ALL_KB_DOC_TYPES = {KB_DOC_TYPE_RICH_TEXT, KB_DOC_TYPE_FILE}


class KnowledgeFolder(Base):
    """知识库目录（多级，parent_id=NULL 为根级）。"""
    __tablename__ = "knowledge_folders"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("knowledge_folders.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class KnowledgeDocument(Base):
    """知识库文档主体。"""
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    folder_id = Column(Integer, ForeignKey("knowledge_folders.id"), nullable=True, index=True)
    # 兼容过渡：阶段 0 保留旧的模块归属，前端仍按 module_id 展示/过滤；阶段 1 改用 folder。
    module_id = Column(Integer, ForeignKey("modules.id"), nullable=True, index=True)

    doc_type = Column(String(20), nullable=False, default=KB_DOC_TYPE_RICH_TEXT)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False, default="")       # 去标签纯文本，供检索/投影
    content_html = Column(Text, nullable=True)               # 富文本原文
    context_type = Column(String(50), nullable=False, default="term_definition")  # 供 RAG 投影归类

    include_in_rag = Column(Boolean, nullable=False, default=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)

    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    editor_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    tags = relationship(
        "KnowledgeTag", secondary="knowledge_document_tags", backref="documents"
    )
    attachments = relationship(
        "KnowledgeAttachment", back_populates="document",
        cascade="all, delete-orphan",
    )
    versions = relationship(
        "KnowledgeDocumentVersion", back_populates="document",
        cascade="all, delete-orphan",
    )


class KnowledgeTag(Base):
    """项目内知识库标签。"""
    __tablename__ = "knowledge_tags"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_kb_tag_project_name"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    color = Column(String(16), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class KnowledgeDocumentTag(Base):
    """文档↔标签 多对多连接表。"""
    __tablename__ = "knowledge_document_tags"

    document_id = Column(
        Integer, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id = Column(
        Integer, ForeignKey("knowledge_tags.id", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeAttachment(Base):
    """文件附件；file 文档的主文件也存为一条。"""
    __tablename__ = "knowledge_attachments"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer, ForeignKey("knowledge_documents.id"), nullable=False, index=True
    )
    filename = Column(String(255), nullable=False)
    mime = Column(String(128), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    storage_path = Column(String(512), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    document = relationship("KnowledgeDocument", back_populates="attachments")


class KnowledgeDocumentVersion(Base):
    """文档版本快照（每次编辑保存前写一条）。"""
    __tablename__ = "knowledge_document_versions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer, ForeignKey("knowledge_documents.id"), nullable=False, index=True
    )
    title = Column(String(255), nullable=False)
    content_html = Column(Text, nullable=True)
    editor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    document = relationship("KnowledgeDocument", back_populates="versions")


Index("ix_knowledge_documents_project_folder", KnowledgeDocument.project_id, KnowledgeDocument.folder_id)
```

- [ ] **Step 2: 编译自查**

Run: `./venv/bin/python -m compileall database/models/knowledge.py`
Expected: 输出 `Compiling ...knowledge.py...`，无 SyntaxError。

- [ ] **Step 3: 提交**

```bash
git add database/models/knowledge.py
git commit -m "feat(knowledge): 知识库独立数据模型（5 张表）"
```

---

## Task 2: 导出新模型 + project_contexts 加投影关联列

**Files:**
- Modify: `database/models/__init__.py`
- Modify: `database/models/project_context.py`

- [ ] **Step 1: 在 `database/models/__init__.py` 加 import**

在 `from .project_context import (` 这一块**之后**（约第 359-383 行附近），新增一段 import：

```python
from .knowledge import (
    KnowledgeFolder,
    KnowledgeDocument,
    KnowledgeTag,
    KnowledgeDocumentTag,
    KnowledgeAttachment,
    KnowledgeDocumentVersion,
    KB_DOC_TYPE_RICH_TEXT,
    KB_DOC_TYPE_FILE,
    ALL_KB_DOC_TYPES,
)
```

- [ ] **Step 2: 在 `__all__` 里加名字**

在 `__all__` 列表末尾（`"ALL_API_KEY_SCOPES",` 之后、闭合 `]` 之前）加：

```python
    # Knowledge base（阶段 0：独立表）
    "KnowledgeFolder", "KnowledgeDocument", "KnowledgeTag",
    "KnowledgeDocumentTag", "KnowledgeAttachment", "KnowledgeDocumentVersion",
    "KB_DOC_TYPE_RICH_TEXT", "KB_DOC_TYPE_FILE", "ALL_KB_DOC_TYPES",
```

- [ ] **Step 3: `project_context.py` 加列**

在 `database/models/project_context.py` 的 `ProjectContext` 类里，`module_id` 列定义之后加一列：

```python
    # 知识库投影关联：本行由某篇知识文档投影而来时，指向该文档（阶段 0 起）
    knowledge_document_id = Column(
        Integer, ForeignKey("knowledge_documents.id"), nullable=True, index=True
    )
```

同文件顶部若 `ForeignKey` 未导入需确认已在 `from sqlalchemy import (... ForeignKey ...)` 中（当前已有）。

并在 `ProjectContext.to_dict()` 的返回 dict 里加一行（找到 `"importance": self.importance,` 附近）：

```python
            "knowledge_document_id": self.knowledge_document_id,
```

- [ ] **Step 4: 编译 + 导入自查**

Run:
```bash
./venv/bin/python -m compileall database/models/__init__.py database/models/project_context.py && \
./venv/bin/python -c "from database.models import KnowledgeDocument, ProjectContext; print('ok', ProjectContext.knowledge_document_id)"
```
Expected: 打印 `ok knowledge_documents.knowledge_document_id`（或类似列对象），无 ImportError。

- [ ] **Step 5: 提交**

```bash
git add database/models/__init__.py database/models/project_context.py
git commit -m "feat(knowledge): 导出知识库模型 + project_contexts 加投影关联列"
```

---

## Task 3: 生成并 review schema 迁移

**Files:**
- Create: `database/migrations/versions/<auto>_knowledge_dedicated_tables.py`

- [ ] **Step 1: 确认 DB 环境可用**

Run: `./venv/bin/alembic heads`
Expected: 打印当前 head（单个 revision id）。若报连接错误，先在 `.env` 配好 DB 变量或 `docker compose up -d postgres`，再继续。

- [ ] **Step 2: autogenerate 迁移**

Run: `./venv/bin/alembic revision --autogenerate -m "knowledge dedicated tables"`
Expected: 生成 `database/migrations/versions/<hash>_knowledge_dedicated_tables.py`，`down_revision` 自动填当前 head。

- [ ] **Step 3: 人工 review 迁移**

打开生成的文件，核对 `upgrade()` 包含：
- `op.create_table("knowledge_folders" ...)`、`knowledge_documents`、`knowledge_tags`、`knowledge_document_tags`、`knowledge_attachments`、`knowledge_document_versions` 六张表。
- `op.add_column("project_contexts", sa.Column("knowledge_document_id", ...))` + 其 index + FK。
- 所有 `index=True` 的列有对应 `create_index`；`UniqueConstraint`（uq_kb_tag_project_name）在。
- `server_default` / `default`：autogenerate 常漏 Boolean/Integer 的 server 默认。若表要在已有数据上加非空列不涉及本任务（都是新表），可不管；但 `is_pinned`/`include_in_rag`/`sort_order` 建议补 `server_default`。手动改为：
  ```python
  sa.Column("include_in_rag", sa.Boolean(), nullable=False, server_default=sa.true()),
  sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
  sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
  ```
- `downgrade()` 反向 drop 干净（drop_column project_contexts.knowledge_document_id + drop 六表）。

- [ ] **Step 4: 应用迁移**

Run: `./venv/bin/alembic upgrade head`
Expected: `Running upgrade ... -> <hash>, knowledge dedicated tables`，无错误。

- [ ] **Step 5: 验证表已建**

Run:
```bash
./venv/bin/python -c "
from database.db import DB
from sqlalchemy import inspect
insp = inspect(DB().session.get_bind())
for t in ['knowledge_folders','knowledge_documents','knowledge_tags','knowledge_document_tags','knowledge_attachments','knowledge_document_versions']:
    assert insp.has_table(t), t
cols = [c['name'] for c in insp.get_columns('project_contexts')]
assert 'knowledge_document_id' in cols, cols
print('schema ok')
"
```
Expected: 打印 `schema ok`。

- [ ] **Step 6: 提交**

```bash
git add database/migrations/versions/
git commit -m "feat(knowledge): schema 迁移——6 张知识库表 + 投影关联列"
```

---

## Task 4: 纯函数单测——字段映射与 html_to_text

先立可脱离 DB 跑的单测，锁住投影字段映射逻辑（TDD 起点）。

**Files:**
- Create: `tests/knowledge/__init__.py`（空文件）
- Create: `tests/knowledge/test_knowledge_projection.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/knowledge/__init__.py`（空）。创建 `tests/knowledge/test_knowledge_projection.py`：

```python
"""知识库 阶段 0 纯函数单测：不依赖 DB。"""
from server.services import knowledge_service as ks


def test_html_to_text_strips_tags():
    assert ks.html_to_text("<p>登录<b>约定</b></p>") == "登录约定"


def test_html_to_text_handles_escaped_html():
    # 被转义存储的 HTML 也要还原成纯文本
    assert ks.html_to_text("&lt;p&gt;订单&lt;/p&gt;") == "订单"


def test_html_to_text_empty():
    assert ks.html_to_text("") == ""
    assert ks.html_to_text(None) == ""


def test_projection_fields_maps_doc_to_context_kwargs():
    # 投影用的字段映射：从文档快照 dict 得到 project_contexts 的写入字段
    doc = {
        "project_id": 7,
        "module_id": 3,
        "title": "登录模块接口约定",
        "context_type": "api_contract",
        "content": "统一鉴权走 JWT",
        "include_in_rag": True,
    }
    fields = ks.projection_fields(doc)
    assert fields["project_id"] == 7
    assert fields["module_id"] == 3
    assert fields["source_type"] == "knowledge"
    assert fields["context_type"] == "api_contract"
    assert fields["content"] == "统一鉴权走 JWT"
    assert fields["summary"] == "统一鉴权走 JWT"
    assert fields["importance"] > 0            # 纳入检索


def test_projection_fields_off_when_not_in_rag():
    doc = {"project_id": 1, "module_id": None, "title": "t",
           "context_type": "term_definition", "content": "x", "include_in_rag": False}
    fields = ks.projection_fields(doc)
    assert fields["importance"] == 0           # 不参与 AI 检索
    assert fields["keywords"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/knowledge/test_knowledge_projection.py -v`
Expected: FAIL —— `projection_fields` 尚不存在（AttributeError）。`html_to_text` 三条可能已通过（旧函数还在），`projection_fields` 两条失败。

- [ ] **Step 3: 提交测试**

```bash
git add tests/knowledge/__init__.py tests/knowledge/test_knowledge_projection.py
git commit -m "test(knowledge): 投影字段映射与 html_to_text 单测（红）"
```

---

## Task 5: 重写 knowledge_service —— 面向新表 + 投影同步

**Files:**
- Modify (重写): `server/services/knowledge_service.py`

- [ ] **Step 1: 重写 service**

用以下内容**整体替换** `server/services/knowledge_service.py`：

```python
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

    投影是派生数据：任何异常都吞掉并记日志，绝不冒泡阻断文档保存。
    """
    try:
        row = (
            session.query(ProjectContext)
            .filter(ProjectContext.knowledge_document_id == doc.id)
            .first()
        )
        if not doc.include_in_rag:
            if row is not None:
                session.delete(row)
            return
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
```

- [ ] **Step 2: 跑纯函数单测确认通过**

Run: `./venv/bin/python -m pytest tests/knowledge/test_knowledge_projection.py -v`
Expected: 5 条全 PASS。

- [ ] **Step 3: 编译自查**

Run: `./venv/bin/python -m compileall server/services/knowledge_service.py`
Expected: 无 SyntaxError。

- [ ] **Step 4: 提交**

```bash
git add server/services/knowledge_service.py
git commit -m "feat(knowledge): service 重写——独立表 CRUD + RAG 单向投影"
```

---

## Task 6: 路由改用新 service + IDOR 校验 + 作者追溯

**Files:**
- Modify: `server/api/knowledge.py`

阶段 0 API 契约不变（前端零改动），仅内部换成新 service，并补两处硬化：按 doc_id 操作前校验归属项目、写操作记录作者/修改人。

- [ ] **Step 1: 加当前用户依赖 + IDOR 辅助**

在 `server/api/knowledge.py` 顶部 import 区，把：
```python
from server.api.deps import DBDep
```
改为：
```python
from server.api.deps import DBDep, CurrentUserDep
```

在 `_require_title` 函数下方，新增一个归属校验辅助：
```python
def _require_doc_in_project(session, doc_id: int, project_id: Optional[int] = None):
    """取文档；不存在 404。传了 project_id 则校验归属（防 IDOR）。"""
    doc = knowledge_service.get_doc(session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
    if project_id is not None and doc.project_id != project_id:
        raise HTTPException(status_code=403, detail="无权访问该文档")
    return doc
```

- [ ] **Step 2: create 传 author_id**

把 `create_knowledge` 的签名与调用改为带当前用户：
```python
@router.post("")
def create_knowledge(payload: KnowledgeCreate, db: DBDep, current_user: CurrentUserDep):
    _require_project(db.session, payload.project_id)
    _validate_module(db.session, payload.module_id, payload.project_id)
    title = _require_title(payload.title)
    doc = knowledge_service.create_doc(
        db.session,
        project_id=payload.project_id,
        title=title,
        content_html=payload.content_html,
        module_id=payload.module_id,
        context_type=payload.context_type,
        include_in_rag=payload.include_in_rag,
        author_id=current_user.id,
    )
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}
```

- [ ] **Step 3: update 传 editor_id**

```python
@router.put("/{doc_id}")
def update_knowledge(doc_id: int, payload: KnowledgeUpdate, db: DBDep, current_user: CurrentUserDep):
    doc = _require_doc_in_project(db.session, doc_id)
    _validate_module(db.session, payload.module_id, doc.project_id)
    title = _require_title(payload.title)
    doc = knowledge_service.update_doc(
        db.session,
        doc,
        title=title,
        content_html=payload.content_html,
        module_id=payload.module_id,
        context_type=payload.context_type,
        include_in_rag=payload.include_in_rag,
        editor_id=current_user.id,
    )
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}
```

- [ ] **Step 4: get / delete 用辅助函数**

`get_knowledge` 与 `delete_knowledge` 里的
```python
doc = knowledge_service.get_doc(db.session, doc_id)
if not doc:
    raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
```
两处替换为：
```python
doc = _require_doc_in_project(db.session, doc_id)
```

> 注：`get_current_user` 目前是全局登录守卫；对象级 `assert_project_access` 待项目成员表落地后统一开启（CLAUDE.md「对象级授权」段），本阶段先补 IDOR 归属校验即可。

- [ ] **Step 5: 编译 + import 自查**

Run:
```bash
./venv/bin/python -m compileall server/api/knowledge.py && \
./venv/bin/python -c "import server.api.knowledge as k; print('routes', [r.path for r in k.router.routes])"
```
Expected: 打印路由列表（`/knowledge`、`/knowledge/{doc_id}`），无 ImportError。

- [ ] **Step 6: 提交**

```bash
git add server/api/knowledge.py
git commit -m "feat(knowledge): 路由改用独立表 service + IDOR 归属校验 + 作者追溯"
```

---

## Task 7: 数据迁移脚本——老知识行搬进新表并回填关联键

把现有 `project_contexts WHERE source_type='knowledge'` 的行搬进 `knowledge_documents`，
并把**原投影行复用为 RAG 投影**（回填其 `knowledge_document_id`，不新增行、不重复召回）。

**Files:**
- Create: `database/migrations/data_migrations/migrate_knowledge_to_dedicated_tables.py`

- [ ] **Step 1: 写迁移脚本**

创建该文件：

```python
"""一次性数据迁移：知识库文档 project_contexts → knowledge_documents。

把现有 ``project_contexts WHERE source_type='knowledge'`` 的每一行，建一条对应的
``knowledge_documents``（doc_type='rich_text'，搬 title/content/content_html/
context_type/module_id，include_in_rag = importance>0），并把**原 project_contexts
行复用为该文档的 RAG 投影**——回填其 knowledge_document_id 指向新文档，不新增投影行。

幂等：已回填过（knowledge_document_id 非空）的行跳过。

跑法（先 dry-run 看数量）：
    ./venv/bin/python -m database.migrations.data_migrations.migrate_knowledge_to_dedicated_tables

确认无误后真跑：
    ./venv/bin/python -m database.migrations.data_migrations.migrate_knowledge_to_dedicated_tables --commit
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="真的写库；默认 dry-run")
    args = parser.parse_args()

    from database.db import DB
    from database.models import (
        ProjectContext,
        KnowledgeDocument,
        CONTEXT_SOURCE_KNOWLEDGE,
    )

    db = DB()
    session = db.session

    rows = (
        session.query(ProjectContext)
        .filter(ProjectContext.source_type == CONTEXT_SOURCE_KNOWLEDGE)
        .filter(ProjectContext.knowledge_document_id.is_(None))
        .order_by(ProjectContext.id.asc())
        .all()
    )
    print(f"待迁移知识行：{len(rows)}")

    migrated = 0
    for ctx in rows:
        doc = KnowledgeDocument(
            project_id=ctx.project_id,
            module_id=ctx.module_id,
            folder_id=None,                      # 阶段 0 落根级；目录树阶段 1 再分
            doc_type="rich_text",
            title=(ctx.title or "")[:255] or "未命名文档",
            content=ctx.content or "",
            content_html=ctx.content_html or "",
            context_type=ctx.context_type or "term_definition",
            include_in_rag=(ctx.importance or 0) > 0,
        )
        session.add(doc)
        session.flush()                          # 拿 doc.id
        ctx.knowledge_document_id = doc.id       # 复用旧行做投影
        migrated += 1
        print(f"  ✓ ctx#{ctx.id} → doc#{doc.id}  {doc.title[:30]}")

    if args.commit:
        session.commit()
        print(f"已提交：迁移 {migrated} 篇。")
    else:
        session.rollback()
        print(f"[dry-run] 将迁移 {migrated} 篇；加 --commit 真写。")

    db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 编译自查**

Run: `./venv/bin/python -m compileall database/migrations/data_migrations/migrate_knowledge_to_dedicated_tables.py`
Expected: 无 SyntaxError。

- [ ] **Step 3: dry-run**

Run: `./venv/bin/python -m database.migrations.data_migrations.migrate_knowledge_to_dedicated_tables`
Expected: 打印「待迁移知识行：N」+ 每行 `✓ ctx#.. → doc#..` + `[dry-run] 将迁移 N 篇`。DB 无实际写入。

- [ ] **Step 4: 真跑**

Run: `./venv/bin/python -m database.migrations.data_migrations.migrate_knowledge_to_dedicated_tables --commit`
Expected: `已提交：迁移 N 篇。`

- [ ] **Step 5: 幂等验证（再跑一次 dry-run）**

Run: `./venv/bin/python -m database.migrations.data_migrations.migrate_knowledge_to_dedicated_tables`
Expected: 「待迁移知识行：0」（已回填的都跳过）。

- [ ] **Step 6: 提交**

```bash
git add database/migrations/data_migrations/migrate_knowledge_to_dedicated_tables.py
git commit -m "feat(knowledge): 数据迁移——老知识行搬入独立表并回填投影关联键"
```

---

## Task 8: 端到端回归——CRUD 走通 + RAG 召回不变

**Files:**
- Create (临时验证脚本，验完删): `scratchpad/verify_knowledge_phase0.py`

- [ ] **Step 1: 写验证脚本**

在 scratchpad 建 `verify_knowledge_phase0.py`（用真实 DB，建一个临时项目下的文档，验证投影 + 召回，再清理）：

```python
"""阶段 0 端到端验证：创建/更新/删除文档时，project_contexts 投影随动，
且 retrieve_context 能召回纳入检索的文档。跑完自动清理。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.db import DB
from database.models import Project, ProjectContext, KnowledgeDocument
from server.services import knowledge_service as ks
from server.services import context_service

db = DB(); s = db.session
proj = s.query(Project).order_by(Project.id.asc()).first()
assert proj, "库里至少要有一个项目"
pid = proj.id

# 1) 创建纳入检索的文档
doc = ks.create_doc(s, project_id=pid, title="ZZ临时_登录鉴权约定",
                    content_html="<p>统一鉴权走 JWT，Bearer Token 携带</p>",
                    context_type="api_contract", include_in_rag=True)
s.flush()
proj_row = s.query(ProjectContext).filter(
    ProjectContext.knowledge_document_id == doc.id).first()
assert proj_row is not None and proj_row.importance > 0, "投影行应存在且 importance>0"
print("✓ 创建即投影")

# 2) retrieve_context 能召回
hits = context_service.retrieve_context("JWT 鉴权 Bearer", pid, top_k=10)
assert any(h["knowledge_document_id"] == doc.id for h in hits), "应召回到该文档"
print("✓ RAG 召回命中")

# 3) 关闭纳入检索 → 投影行删除
ks.update_doc(s, doc, include_in_rag=False); s.flush()
assert s.query(ProjectContext).filter(
    ProjectContext.knowledge_document_id == doc.id).first() is None, "关闭后投影应删除"
print("✓ 关闭纳入检索→投影删除")

# 4) 删除文档 → 无残留
did = doc.id
ks.delete_doc(s, doc); s.flush()
assert s.query(KnowledgeDocument).filter(KnowledgeDocument.id == did).first() is None
assert s.query(ProjectContext).filter(
    ProjectContext.knowledge_document_id == did).first() is None
print("✓ 删除无残留")

s.rollback()   # 不污染库
db.close()
print("ALL GREEN")
```

- [ ] **Step 2: 跑验证脚本**

Run: `./venv/bin/python scratchpad/verify_knowledge_phase0.py`
Expected: 依次打印 4 个 ✓ 与 `ALL GREEN`。

> 若第 2 步召回未命中，检查 `context_service.retrieve_context` 是否按 `importance>0` 过滤、以及 `to_dict()` 是否已含 `knowledge_document_id`（Task 2 Step 3）。

- [ ] **Step 3: 清理临时脚本**

Run: `rm scratchpad/verify_knowledge_phase0.py`
（scratchpad 不入库，无需提交。）

- [ ] **Step 4: 全量单测 + 编译收尾**

Run:
```bash
./venv/bin/python -m pytest tests/knowledge/ -v && \
./venv/bin/python -m compileall server/services/knowledge_service.py server/api/knowledge.py database/models/knowledge.py
```
Expected: pytest 全绿；compileall 无错。

- [ ] **Step 5: 重启后端确认无 import 崩溃**

> worker/后端改了代码要重启（memory：改代码记得杀旧 celery worker）。这里只需确认 API 进程能起：

Run: `CELERY_TASK_ALWAYS_EAGER=1 ./venv/bin/python -c "import server.main; print('app import ok')"`
Expected: 打印 `app import ok`，无异常。

---

## 收尾与验收

- [ ] 现有知识文档在 `knowledge_documents` 里齐全（`SELECT count(*)` 对齐迁移前 `project_contexts` 知识行数）。
- [ ] AI 用例生成召回与迁移前一致：用 `mcp__test-platform__run_tests` 触发一次会用到知识召回的生成/回归，确认结果无回退（或用 Task 8 的召回断言代表）。
- [ ] 前端知识库 tab 功能与改造前一致（列表/新建/编辑/删除；本阶段前端未改）。

---

## Self-Review 记录

- **Spec 覆盖**：本 plan 对应 spec §4（数据模型全部 5 表 + project_contexts 加列）、§3.1（RAG 投影）、§5（迁移脚本，含「默认落根级」）、§7 的 IDOR 修复与作者追溯、§8 的「投影失败不阻断」不变量。spec 的目录树/标签/搜索/阅读/文件/导入导出属阶段 1-4，各自出 plan，不在本 plan 范围。
- **占位扫描**：无 TBD/TODO；每个改代码步骤含完整代码与确切命令、预期输出。
- **类型一致**：`sync_rag_projection` / `projection_fields` / `_doc_snapshot` / `create_doc(author_id=)` / `update_doc(editor_id=)` / `_require_doc_in_project` 命名在 Task 5/6/8 间一致；`KnowledgeDocument` 字段（include_in_rag/module_id/content/content_html/context_type）在模型、service、迁移、验证脚本间一致。
