# 知识库改造 · 阶段 3a（文件托管 · 后端）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 知识库文件上传/下载/删除的**后端**：本地磁盘存储 + 附件 service + 鉴权下载路由 + 「文件文档」(doc_type=file) 创建；前端留到 3b。

**Architecture:** 复用阶段 0 已建的 `knowledge_attachments` 表。新增本地存储工具 `utils/knowledge_storage.py`（落盘 `data/knowledge/<project_id>/<uuid><ext>`，扩展名白名单 + 50MB 上限 + uuid 命名）；附件 service；新路由 `server/api/knowledge_files.py`（上传成文件文档 / 给文档加附件 / **鉴权下载**(FileResponse) / 删附件）。`knowledge_service.serialize` 补 `doc_type`（列表用）+ `attachments`（详情用）。文件文档默认 `include_in_rag=False`（仅人读，不投影）。

**Tech Stack:** FastAPI（`UploadFile`/`File`/`Form`/`FileResponse`）+ SQLAlchemy + 本地磁盘；pytest 纯函数单测 + 端到端验证。

**前置约定（务必先读）：**
- 包名 `server/`；路径锚点用 `Path(__file__).resolve().parent...`，禁止 `Path.cwd()`。
- Python `./venv/bin/python`；DB 命令先 `set -a && . ./.env && set +a`。python-multipart 已装。
- 加新 REST 资源：router 文件 → `server/api/__init__.py` 导出 → `server/main.py` 两处列表都加（照 `knowledge_router`）。
- 下载走**鉴权路由**（`CurrentUserDep` + FileResponse），**不做静态挂载**（与需求附件的静态挂载不同，知识库文件可能敏感）。前端用带 Bearer 的 fetch 取字节，故 `<img src>` 不带 token 的问题在 3b 用 blob 解决。
- 有 `venv`/`node_modules` 软链，**永远别 `git add -A`**。

**本阶段不做：** 前端（3b）；文件文本抽取喂 RAG（本轮文件仅人读）；缩略图/转码。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `utils/knowledge_storage.py` | 落盘/读/删 + 白名单/大小校验（纯函数可测） | 新建 |
| `server/services/knowledge_attachment_service.py` | 附件 CRUD + 序列化 | 新建 |
| `server/services/knowledge_service.py` | `create_file_doc`；`serialize` 补 `doc_type`/`attachments` | 修改 |
| `server/api/knowledge_files.py` | 上传/加附件/下载/删附件 路由 | 新建 |
| `server/api/__init__.py` + `server/main.py` | 注册路由 | 修改 |
| `tests/knowledge/test_storage.py` | 白名单/大小/扩展名 纯函数单测 | 新建 |

---

## Task 1: 存储白名单纯函数单测（红）

**Files:** Create `tests/knowledge/test_storage.py`

- [ ] **Step 1: 写失败测试**

```python
"""知识库文件存储 纯函数单测（不落盘）。"""
from utils import knowledge_storage as st


def test_safe_ext_whitelist():
    assert st.safe_ext("a.PDF") == ".pdf"
    assert st.safe_ext("b.docx") == ".docx"
    assert st.safe_ext("c.png") == ".png"
    assert st.safe_ext("evil.exe") == ""
    assert st.safe_ext("noext") == ""


def test_is_allowed():
    assert st.is_allowed("x.xlsx") is True
    assert st.is_allowed("x.sh") is False


def test_within_size():
    assert st.within_size(1) is True
    assert st.within_size(st.MAX_SIZE_BYTES) is True
    assert st.within_size(0) is False
    assert st.within_size(st.MAX_SIZE_BYTES + 1) is False
```

- [ ] **Step 2: 跑确认失败**

Run: `./venv/bin/python -m pytest tests/knowledge/test_storage.py -v`
Expected: FAIL —— `utils.knowledge_storage` 不存在。

- [ ] **Step 3: 提交**

```bash
git add tests/knowledge/test_storage.py
git commit -m "$(cat <<'EOF'
test(knowledge): 文件存储白名单/大小 单测（红）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 存储工具（落盘/读/删）

**Files:** Create `utils/knowledge_storage.py`

- [ ] **Step 1: 写工具**

```python
"""知识库文件本地存储 —— 阶段 3。

落盘 data/knowledge/<project_id>/<uuid><ext>；扩展名白名单 + 50MB 上限 + uuid 命名。
storage_path 存「相对 data/ 的路径」（如 knowledge/7/abcd.pdf），便于迁移。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Tuple

_ROOT = Path(__file__).resolve().parent.parent   # utils/ 的上一级 = 仓库根
_DATA = _ROOT / "data"

MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

ALLOWED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv",
    ".ppt", ".pptx", ".md", ".txt", ".json",
}


def safe_ext(filename: str) -> str:
    """取小写扩展名；不在白名单返回 ''（调用方据此拒绝）。"""
    ext = Path(filename or "").suffix.lower()
    return ext if ext in ALLOWED_EXTS else ""


def is_allowed(filename: str) -> bool:
    return safe_ext(filename) != ""


def within_size(size: int) -> bool:
    return 0 < size <= MAX_SIZE_BYTES


def save_bytes(project_id: int, filename: str, data: bytes) -> Tuple[str, int]:
    """存字节，返回 (相对 data/ 的存储路径, 字节数)。扩展名白名单由调用方保证。"""
    ext = Path(filename or "").suffix.lower()
    rel_dir = Path("knowledge") / str(project_id)
    (_DATA / rel_dir).mkdir(parents=True, exist_ok=True)
    rel_path = str(rel_dir / f"{uuid.uuid4().hex}{ext}")
    (_DATA / rel_path).write_bytes(data)
    return rel_path, len(data)


def abs_path(storage_path: str) -> Path:
    return _DATA / storage_path


def delete_file(storage_path: str) -> None:
    try:
        p = abs_path(storage_path)
        if p.is_file():
            p.unlink()
    except Exception:  # noqa: BLE001 —— 磁盘删除失败不阻断 DB 事务
        import logging
        logging.getLogger(__name__).warning("删除知识库文件失败：%s", storage_path)
```

- [ ] **Step 2: 单测转绿 + 编译**

Run:
```bash
./venv/bin/python -m pytest tests/knowledge/test_storage.py -v && \
./venv/bin/python -m compileall utils/knowledge_storage.py
```
Expected: 3 passed；compile 无错。

- [ ] **Step 3: 提交**

```bash
git add utils/knowledge_storage.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 文件本地存储工具（白名单/50MB/uuid 命名）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 附件 service + create_file_doc + serialize 补字段

**Files:**
- Create: `server/services/knowledge_attachment_service.py`
- Modify: `server/services/knowledge_service.py`

- [ ] **Step 1: 附件 service**

创建 `server/services/knowledge_attachment_service.py`：

```python
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
```

- [ ] **Step 2: `knowledge_service.py` —— create_file_doc + serialize**

(a) 在 `create_doc` 之后新增：
```python
def create_file_doc(
    session, *, project_id: int, filename: str, data: bytes, mime: Optional[str] = None,
    folder_id: Optional[int] = None, author_id: Optional[int] = None,
):
    """上传文件即建一篇「文件文档」(doc_type=file)，把文件存为其附件。默认仅人读。"""
    from server.services import knowledge_attachment_service as kas
    doc = KnowledgeDocument(
        project_id=project_id,
        folder_id=folder_id,
        doc_type="file",
        title=(filename or "文件")[:255],
        content="",
        content_html="",
        context_type="term_definition",
        include_in_rag=False,
        author_id=author_id,
        editor_id=author_id,
    )
    session.add(doc)
    session.flush()
    kas.create_attachment(session, doc, filename=filename, mime=mime, data=data, uploaded_by=author_id)
    return doc
```

(b) 在 `serialize` 的 `data` dict 里，`"is_pinned": ...` 之后加 `doc_type`：
```python
        "doc_type": doc.doc_type,
```
并在 `if detail:` 块里（`data["content_html"] = ...` 之后）加 attachments：
```python
        data["attachments"] = [
            {"id": a.id, "filename": a.filename, "mime": a.mime, "size_bytes": a.size_bytes}
            for a in (doc.attachments or [])
        ]
```

(c) 让 `delete_doc` 顺带清磁盘文件（附件 DB 行随后走 ORM cascade 删除，磁盘不能漏）。在 `delete_doc` 函数体最前面加：
```python
    from utils import knowledge_storage as storage
    for a in list(doc.attachments or []):
        storage.delete_file(a.storage_path)
```
（放在删投影行/`session.delete(doc)` 之前。这样任何路径删文档都会清盘，不依赖前端先删附件。）

- [ ] **Step 3: 编译**

Run: `./venv/bin/python -m compileall server/services/knowledge_attachment_service.py server/services/knowledge_service.py`
Expected: 无错。

- [ ] **Step 4: 提交**

```bash
git add server/services/knowledge_attachment_service.py server/services/knowledge_service.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 附件 service + 文件文档创建 + serialize 补 doc_type/attachments

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 文件 REST 路由 + 注册

**Files:**
- Create: `server/api/knowledge_files.py`
- Modify: `server/api/__init__.py`, `server/main.py`

- [ ] **Step 1: 写路由**

创建 `server/api/knowledge_files.py`：

```python
"""/api/knowledge/* 文件相关端点 —— 上传成文件文档 / 给文档加附件 / 鉴权下载 / 删附件。"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from database.models import Project
from server.api.deps import DBDep, CurrentUserDep
from server.services import knowledge_service
from server.services import knowledge_attachment_service as kas
from utils import knowledge_storage as storage

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def _read_validated(file: UploadFile):
    filename = file.filename or "文件"
    if not storage.is_allowed(filename):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{filename}")
    data = await file.read()
    if not storage.within_size(len(data)):
        mb = storage.MAX_SIZE_BYTES // 1024 // 1024
        raise HTTPException(status_code=400, detail=f"文件为空或超过大小上限 {mb}MB")
    return data, filename, (file.content_type or "")


def _require_doc(session, doc_id: int):
    doc = knowledge_service.get_doc(session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
    return doc


@router.post("/upload")
async def upload_file_doc(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Form(...),
    folder_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
):
    if not db.session.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    data, filename, mime = await _read_validated(file)
    doc = knowledge_service.create_file_doc(
        db.session, project_id=project_id, filename=filename, data=data,
        mime=mime, folder_id=folder_id, author_id=current_user.id,
    )
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}


@router.post("/{doc_id}/attachments")
async def add_attachment(doc_id: int, db: DBDep, current_user: CurrentUserDep, file: UploadFile = File(...)):
    doc = _require_doc(db.session, doc_id)
    data, filename, mime = await _read_validated(file)
    a = kas.create_attachment(db.session, doc, filename=filename, mime=mime, data=data, uploaded_by=current_user.id)
    return {"status": "success", "data": kas.serialize_attachment(a)}


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: int, db: DBDep, current_user: CurrentUserDep,
    disposition: str = Query("inline"),
):
    a = kas.get_attachment(db.session, attachment_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"附件不存在：{attachment_id}")
    _require_doc(db.session, a.document_id)   # 归属存在性
    p = storage.abs_path(a.storage_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="文件已丢失")
    dtype = "attachment" if disposition == "attachment" else "inline"
    cd = f"{dtype}; filename*=UTF-8''{quote(a.filename or 'file')}"
    return FileResponse(
        str(p),
        media_type=a.mime or "application/octet-stream",
        headers={"Content-Disposition": cd},
    )


@router.delete("/attachments/{attachment_id}")
def remove_attachment(attachment_id: int, db: DBDep, current_user: CurrentUserDep):
    a = kas.get_attachment(db.session, attachment_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"附件不存在：{attachment_id}")
    kas.delete_attachment(db.session, a)
    return {"status": "success", "data": {"id": attachment_id}}
```

> 注意路由顺序：`/knowledge/{doc_id}/attachments`（doc_id 是 int）与 `/knowledge/attachments/{attachment_id}/download`（"attachments" 是字面量）不冲突——FastAPI 不会把 "attachments" 当作 int 的 doc_id。

- [ ] **Step 2: 注册（`__init__.py` + `main.py`）**

`server/api/__init__.py`：在 `from .knowledge_tags import router as knowledge_tags_router` 下加：
```python
from .knowledge_files import router as knowledge_files_router
```
`__all__` 里 `"knowledge_tags_router",` 下加：
```python
    "knowledge_files_router",
```
`server/main.py`：两处 `knowledge_tags_router,` 下各加：
```python
    knowledge_files_router,
```

- [ ] **Step 3: 编译 + 路由自查**

Run:
```bash
./venv/bin/python -m compileall server/api/knowledge_files.py server/api/__init__.py server/main.py && \
set -a && . ./.env && set +a && CELERY_TASK_ALWAYS_EAGER=1 ./venv/bin/python -c "
from server.main import app
ps = sorted({r.path for r in app.routes if 'knowledge' in getattr(r,'path','') and ('upload' in r.path or 'attachments' in r.path)})
print(ps)
assert '/api/knowledge/upload' in ps and '/api/knowledge/attachments/{attachment_id}/download' in ps, ps
print('file routes mounted')
"
```
Expected: 打印文件路由 + `file routes mounted`。

- [ ] **Step 4: 提交**

```bash
git add server/api/knowledge_files.py server/api/__init__.py server/main.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 文件 REST 路由（上传/加附件/鉴权下载/删）+ 注册

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 后端端到端验证

**Files:** 临时脚本（验完删）

- [ ] **Step 1: 写 `scratchpad/verify_phase3a.py`**

```python
"""阶段 3a 后端验证：上传成文件文档、加附件、读回字节、删附件清盘、删文档级联。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from database.db import DB
from database.models import Project, KnowledgeDocument, KnowledgeAttachment
from server.services import knowledge_service as ks
from server.services import knowledge_attachment_service as kas
from utils import knowledge_storage as storage

db = DB(); s = db.session
pid = s.query(Project).order_by(Project.id.asc()).first().id
made = []
try:
    # 1) 上传成文件文档
    doc = ks.create_file_doc(s, project_id=pid, filename="ZZ需求.pdf", data=b"%PDF-1.4 fake", mime="application/pdf", author_id=None)
    s.commit(); made.append(doc.id)
    assert doc.doc_type == "file" and doc.include_in_rag is False
    atts = kas.list_attachments(s, doc.id)
    assert len(atts) == 1 and atts[0].filename == "ZZ需求.pdf"
    assert storage.abs_path(atts[0].storage_path).is_file(), "文件应已落盘"
    print("✓ 上传成文件文档 + 落盘")

    # 2) serialize 详情含 doc_type + attachments
    ser = ks.serialize(doc, detail=True)
    assert ser["doc_type"] == "file" and len(ser["attachments"]) == 1
    print("✓ serialize 含 doc_type/attachments")

    # 3) 读回字节
    assert storage.abs_path(atts[0].storage_path).read_bytes() == b"%PDF-1.4 fake"
    print("✓ 读回字节一致")

    # 4) 给一篇富文本文档加附件
    note = ks.create_doc(s, project_id=pid, title="ZZ笔记", content_html="<p>x</p>", include_in_rag=False); s.commit(); made.append(note.id)
    a2 = kas.create_attachment(s, note, filename="ZZ表.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", data=b"PKxlsx", uploaded_by=None); s.commit()
    p2 = storage.abs_path(a2.storage_path)
    assert p2.is_file()
    print("✓ 富文本文档加附件")

    # 5) 删附件清盘
    kas.delete_attachment(s, a2); s.commit()
    assert not p2.is_file(), "删附件应清磁盘文件"
    print("✓ 删附件清磁盘")

    # 6) 删文档：delete_doc 清磁盘 + 级联删附件 DB 行
    fpath = storage.abs_path(kas.list_attachments(s, doc.id)[0].storage_path)
    ks.delete_doc(s, doc); s.commit(); made.remove(doc.id)
    assert not fpath.is_file(), "删文档应清磁盘文件"
    assert s.query(KnowledgeAttachment).filter(KnowledgeAttachment.document_id == doc.id).count() == 0
    print("✓ 删文档清盘 + 级联删附件 DB 行")
    print("ALL GREEN")
finally:
    for did in made:
        d = s.query(KnowledgeDocument).filter(KnowledgeDocument.id == did).first()
        if d:
            ks.delete_doc(s, d)   # 已清盘
    s.commit(); db.close()
```

> 说明：`delete_doc` 现在会先清磁盘文件、再删 DB（附件 DB 行走 ORM `cascade="all, delete-orphan"`）。任何路径删文档都清盘，不依赖前端先删附件。

- [ ] **Step 2: 跑 + 无污染 + 清理 + 全量单测**

Run:
```bash
set -a && . ./.env && set +a && ./venv/bin/python scratchpad/verify_phase3a.py
set -a && . ./.env && set +a && ./venv/bin/python -c "
from database.db import DB
from database.models import KnowledgeDocument
print('残留 ZZ 文档(应0):', DB().session.query(KnowledgeDocument).filter(KnowledgeDocument.title.like('ZZ%')).count())
"
rm scratchpad/verify_phase3a.py
./venv/bin/python -m pytest tests/knowledge/ -q
```
Expected: 6 个 ✓ + `ALL GREEN`；`残留 ZZ 文档(应0): 0`；pytest 18 passed（15 + 存储 3）。

---

## 收尾与验收（3a）

- [ ] 文件上传成文件文档（doc_type=file，仅人读）+ 落盘；富文本文档可加附件；鉴权下载返回字节；删附件清磁盘。
- [ ] serialize 列表含 `doc_type`（前端据此渲染文件卡片），详情含 `attachments`。
- [ ] 白名单拒绝非法扩展名、超限拒绝；后端 18 单测通过。
- [ ] 向后兼容：旧 rich_text 文档 serialize 多了 `doc_type='rich_text'`/`attachments=[]`，不破坏 3b 前的前端。

---

## Self-Review 记录

- **Spec 覆盖**：对应 spec 阶段 3 的后端——文件本地存储（`data/knowledge/`）、鉴权下载（非静态挂载）、白名单+50MB+uuid、文件文档 + 富文本附件（用户选“两者都做”）。文本抽取喂 RAG 本轮不做（文件默认仅人读）。
- **占位扫描**：storage/attachment service/router 全代码；serialize/register 精确片段；无 TODO。
- **类型一致**：`safe_ext`/`is_allowed`/`within_size`/`save_bytes`/`abs_path`/`delete_file`（storage）、`create_attachment`/`list_attachments`/`get_attachment`/`delete_attachment`/`serialize_attachment`（attachment svc）、`create_file_doc`（knowledge svc）、路由 4 端点 在计划各处一致。
- **安全**：下载走 `CurrentUserDep`；uuid 命名不用原名落盘；扩展名白名单；50MB 上限；删磁盘失败不阻断 DB。
- **删除清盘**：`delete_doc` 已加「先清磁盘文件再删 DB」，任何路径删文档都不漏盘；删单个附件走 `delete_attachment` 也清盘。磁盘删除失败只记日志、不阻断事务。
