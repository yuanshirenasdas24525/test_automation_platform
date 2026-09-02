# 知识库改造 · 阶段 1a（分类与检索 · 后端）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给知识库加独立目录树、标签、全文搜索的**后端 API**（表在阶段 0 已建），前端留到阶段 1b。

**Architecture:** 复用阶段 0 已建的 `knowledge_folders` / `knowledge_tags` / `knowledge_document_tags` 表。新增两个 service（folder / tag）与两个 REST 路由（`/api/knowledge/folders`、`/api/knowledge/tags`）；扩展现有 `/api/knowledge` 列表端点支持 `q`（标题+正文 ILIKE 全文搜）、`folder_id`、`tag_id` 过滤；文档创建/更新接受 `folder_id` 与 `tag_ids`；序列化补 `folder_id` / `tags` / `is_pinned`；加置顶开关端点。全程**向后兼容**——阶段 0 前端仍能用（旧 `module_id` 过滤保留）。

**Tech Stack:** SQLAlchemy 2.0 + FastAPI（`DBDep` / `CurrentUserDep`）+ PostgreSQL；pytest 纯函数单测 + 端到端验证脚本。

**前置约定（务必先读）：**
- 包名 `server/` 不是 `platform/`；路径锚点用 `Path(__file__).resolve()...`，禁止 `Path.cwd()`。
- Python 用 `./venv/bin/python`（worktree 内 venv 是软链）。无 ruff/black，跟已有文件风格。
- **DB 命令需先 source .env**：`set -a && . ./.env && set +a && <cmd>`（`database/db.py` 不自动加载 dotenv）。
- 加新 REST 资源：`server/api/<name>.py` 写 router → `server/api/__init__.py` 导出 → `server/main.py` 的两处 router 列表都加（第 92 行 import 列表 / 第 208 行循环列表；照 `knowledge_router` 的样子加）。
- 响应统一 `{status, data?, message?}`；路由用 `db: DBDep`，**不手动 commit**。
- 对象级授权：本阶段沿用阶段 0 口径——只做「存在性 + 归属」校验，`assert_project_access` 待项目成员表落地统一开。
- 有 `venv` 软链在目录里，**永远别 `git add -A`**，只 add 指定文件。

**本阶段不做：** 前端（阶段 1b）、阅读视图（阶段 2）、文件上传（阶段 3）、导入导出（阶段 4）、tsvector/GIN（数据量大再说，ILIKE 起步）。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `server/services/knowledge_folder_service.py` | 目录 CRUD + 树构建 + 安全删除（子项上移） | 新建 |
| `server/services/knowledge_tag_service.py` | 标签 CRUD + 给文档设标签 | 新建 |
| `server/services/knowledge_service.py` | 扩展：list 支持 q/folder_id/tag_id；create/update 接 folder_id/tag_ids；serialize 补字段；置顶 | 修改 |
| `server/api/knowledge_folders.py` | `/api/knowledge/folders` 路由 | 新建 |
| `server/api/knowledge_tags.py` | `/api/knowledge/tags` 路由 | 新建 |
| `server/api/knowledge.py` | list 加 query 参数；create/update 加字段；加 `PATCH /{id}/pin` | 修改 |
| `server/api/__init__.py` + `server/main.py` | 注册两个新 router | 修改 |
| `tests/knowledge/test_folder_tree.py` | 目录树构建纯函数单测 | 新建 |
| `tests/knowledge/test_tag_and_search.py` | 标签去重/搜索过滤 纯函数单测 | 新建 |

---

## Task 1: 目录树纯函数单测（红）

**Files:**
- Create: `tests/knowledge/test_folder_tree.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/knowledge/test_folder_tree.py`：

```python
"""知识库目录树构建 纯函数单测（不依赖 DB）。"""
from server.services import knowledge_folder_service as kfs


def _f(id, parent_id, name, sort_order=0):
    return {"id": id, "parent_id": parent_id, "name": name, "sort_order": sort_order}


def test_build_tree_nests_children_under_parents():
    rows = [
        _f(1, None, "接口规范", 0),
        _f(2, 1, "登录鉴权", 0),
        _f(3, 1, "订单交易", 1),
        _f(4, None, "业务规则", 1),
    ]
    tree = kfs.build_folder_tree(rows)
    assert [n["id"] for n in tree] == [1, 4]              # 两个根，按 sort_order
    assert [c["id"] for c in tree[0]["children"]] == [2, 3]
    assert tree[1]["children"] == []


def test_build_tree_sorts_by_sort_order_then_id():
    rows = [_f(2, None, "b", 5), _f(1, None, "a", 5), _f(3, None, "c", 1)]
    tree = kfs.build_folder_tree(rows)
    assert [n["id"] for n in tree] == [3, 1, 2]           # sort_order 升序，同序按 id


def test_build_tree_orphan_parent_treated_as_root():
    # parent_id 指向不存在的父（脏数据）→ 当根处理，不丢失
    rows = [_f(9, 99, "孤儿", 0)]
    tree = kfs.build_folder_tree(rows)
    assert [n["id"] for n in tree] == [9]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./venv/bin/python -m pytest tests/knowledge/test_folder_tree.py -v`
Expected: FAIL —— `knowledge_folder_service` 不存在（ModuleNotFoundError / AttributeError）。

- [ ] **Step 3: 提交**

```bash
git add tests/knowledge/test_folder_tree.py
git commit -m "$(cat <<'EOF'
test(knowledge): 目录树构建单测（红）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 目录 service（含树构建 + 安全删除）

**Files:**
- Create: `server/services/knowledge_folder_service.py`

- [ ] **Step 1: 写 service**

创建 `server/services/knowledge_folder_service.py`：

```python
"""知识库目录（KnowledgeFolder）服务层 —— 阶段 1a。

多级目录树的 CRUD。删除目录时把其直接子目录与直接文档「上移」到父级（或根），
不级联删除文档，避免误删知识。
"""
from __future__ import annotations

from typing import List, Optional

from database.models import KnowledgeFolder, KnowledgeDocument


# ---------------------------------------------------------------------------
# 纯函数（被单测覆盖）
# ---------------------------------------------------------------------------

def serialize_folder(f: KnowledgeFolder) -> dict:
    return {
        "id": f.id,
        "project_id": f.project_id,
        "parent_id": f.parent_id,
        "name": f.name,
        "sort_order": f.sort_order,
    }


def build_folder_tree(rows: List[dict]) -> List[dict]:
    """把扁平目录行（dict，含 id/parent_id/name/sort_order）组装成嵌套树。

    每个节点加 ``children`` 列表。排序：sort_order 升序，同序按 id 升序。
    parent_id 指向不存在的父的行当作根，避免脏数据丢节点。
    """
    by_id = {r["id"]: {**r, "children": []} for r in rows}
    roots: List[dict] = []
    for r in rows:
        node = by_id[r["id"]]
        parent = by_id.get(r["parent_id"]) if r["parent_id"] is not None else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    def _sort(nodes: List[dict]) -> None:
        nodes.sort(key=lambda n: (n.get("sort_order", 0), n["id"]))
        for n in nodes:
            _sort(n["children"])

    _sort(roots)
    return roots


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def list_tree(session, project_id: int) -> List[dict]:
    folders = (
        session.query(KnowledgeFolder)
        .filter(KnowledgeFolder.project_id == project_id)
        .all()
    )
    return build_folder_tree([serialize_folder(f) for f in folders])


def get_folder(session, folder_id: int) -> Optional[KnowledgeFolder]:
    return session.query(KnowledgeFolder).filter(KnowledgeFolder.id == folder_id).first()


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------

def _next_sort_order(session, project_id: int, parent_id: Optional[int]) -> int:
    q = session.query(KnowledgeFolder).filter(
        KnowledgeFolder.project_id == project_id,
        KnowledgeFolder.parent_id.is_(parent_id) if parent_id is None
        else KnowledgeFolder.parent_id == parent_id,
    )
    return q.count()


def create_folder(
    session, *, project_id: int, name: str, parent_id: Optional[int] = None
) -> KnowledgeFolder:
    folder = KnowledgeFolder(
        project_id=project_id,
        parent_id=parent_id,
        name=(name or "").strip()[:255] or "未命名目录",
        sort_order=_next_sort_order(session, project_id, parent_id),
    )
    session.add(folder)
    session.flush()
    return folder


def update_folder(
    session,
    folder: KnowledgeFolder,
    *,
    name: Optional[str] = None,
    parent_id: Optional[int] = ...,   # ... = 不改；None = 移到根
) -> KnowledgeFolder:
    if name is not None:
        folder.name = name.strip()[:255] or folder.name
    if parent_id is not ...:
        if parent_id == folder.id:
            raise ValueError("目录不能移动到自身下")
        folder.parent_id = parent_id
    session.flush()
    return folder


def delete_folder(session, folder: KnowledgeFolder) -> None:
    """删除目录：直接子目录与直接文档上移到父级（folder.parent_id），不删文档。"""
    parent_id = folder.parent_id
    session.query(KnowledgeFolder).filter(
        KnowledgeFolder.parent_id == folder.id
    ).update({KnowledgeFolder.parent_id: parent_id}, synchronize_session=False)
    session.query(KnowledgeDocument).filter(
        KnowledgeDocument.folder_id == folder.id
    ).update({KnowledgeDocument.folder_id: parent_id}, synchronize_session=False)
    session.delete(folder)
    session.flush()
```

- [ ] **Step 2: 跑目录树单测确认通过**

Run: `./venv/bin/python -m pytest tests/knowledge/test_folder_tree.py -v`
Expected: 3 passed。

- [ ] **Step 3: 编译**

Run: `./venv/bin/python -m compileall server/services/knowledge_folder_service.py`
Expected: 无 SyntaxError。

- [ ] **Step 4: 提交**

```bash
git add server/services/knowledge_folder_service.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 目录 service——树构建 + 安全删除（子项上移）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 目录 REST 路由 + 注册

**Files:**
- Create: `server/api/knowledge_folders.py`
- Modify: `server/api/__init__.py`, `server/main.py`

- [ ] **Step 1: 写路由**

创建 `server/api/knowledge_folders.py`：

```python
"""/api/knowledge/folders/* —— 知识库目录树 CRUD。"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from database.models import Project
from server.api.deps import DBDep, CurrentUserDep
from server.services import knowledge_folder_service as kfs

router = APIRouter(prefix="/knowledge/folders", tags=["knowledge"])


class FolderCreate(pydantic.BaseModel):
    project_id: int
    name: str
    parent_id: Optional[int] = None


class FolderUpdate(pydantic.BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    move_to_root: bool = False   # True 时把 parent_id 置空（区分「不改」与「移到根」）


def _require_project(session, project_id: int) -> Project:
    p = session.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return p


def _require_folder_in_project(session, folder_id: int, project_id: Optional[int] = None):
    f = kfs.get_folder(session, folder_id)
    if not f:
        raise HTTPException(status_code=404, detail=f"目录不存在：{folder_id}")
    if project_id is not None and f.project_id != project_id:
        raise HTTPException(status_code=403, detail="无权访问该目录")
    return f


@router.get("")
def list_folders(db: DBDep, project_id: int = Query(...)):
    _require_project(db.session, project_id)
    return {"status": "success", "data": kfs.list_tree(db.session, project_id)}


@router.post("")
def create_folder(payload: FolderCreate, db: DBDep, current_user: CurrentUserDep):
    _require_project(db.session, payload.project_id)
    if payload.parent_id is not None:
        _require_folder_in_project(db.session, payload.parent_id, payload.project_id)
    f = kfs.create_folder(
        db.session, project_id=payload.project_id, name=payload.name, parent_id=payload.parent_id
    )
    return {"status": "success", "data": kfs.serialize_folder(f)}


@router.put("/{folder_id}")
def update_folder(folder_id: int, payload: FolderUpdate, db: DBDep, current_user: CurrentUserDep):
    f = _require_folder_in_project(db.session, folder_id)
    # 计算 parent_id 入参：move_to_root=True → None；否则给了 parent_id 才改
    if payload.move_to_root:
        new_parent = None
    elif payload.parent_id is not None:
        _require_folder_in_project(db.session, payload.parent_id, f.project_id)
        new_parent = payload.parent_id
    else:
        new_parent = ...  # 不改
    try:
        f = kfs.update_folder(db.session, f, name=payload.name, parent_id=new_parent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "data": kfs.serialize_folder(f)}


@router.delete("/{folder_id}")
def delete_folder(folder_id: int, db: DBDep, current_user: CurrentUserDep):
    f = _require_folder_in_project(db.session, folder_id)
    kfs.delete_folder(db.session, f)
    return {"status": "success", "data": {"id": folder_id}}
```

- [ ] **Step 2: 注册 router（`server/api/__init__.py`）**

在 `from .knowledge import router as knowledge_router`（约第 24 行）**下面**加：
```python
from .knowledge_folders import router as knowledge_folders_router
```
在 `__all__` 里 `"knowledge_router",`（约第 62 行）**下面**加：
```python
    "knowledge_folders_router",
```

- [ ] **Step 3: 注册 router（`server/main.py`）**

`server/main.py` 有两处引用 `knowledge_router`（约第 92 行的 import 列表、第 208 行的挂载循环列表）。在**两处**的 `knowledge_router,` 下各加一行：
```python
    knowledge_folders_router,
```
（第 92 行那处是 `from server.api import (...)` 的名字列表；第 208 行那处是 `for router in (...)` 循环列表。两处都要加，照 `knowledge_router` 的写法。）

- [ ] **Step 4: 编译 + 路由自查**

Run:
```bash
./venv/bin/python -m compileall server/api/knowledge_folders.py server/api/__init__.py server/main.py && \
set -a && . ./.env && set +a && CELERY_TASK_ALWAYS_EAGER=1 ./venv/bin/python -c "
from server.main import app
paths = sorted({r.path for r in app.routes if 'knowledge/folders' in getattr(r,'path','')})
print(paths)
assert '/api/knowledge/folders' in paths, paths
print('folders router mounted')
"
```
Expected: 打印含 `/api/knowledge/folders` 和 `/api/knowledge/folders/{folder_id}` 的列表 + `folders router mounted`。

- [ ] **Step 5: 提交**

```bash
git add server/api/knowledge_folders.py server/api/__init__.py server/main.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 目录 REST 路由 /api/knowledge/folders + 注册

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 标签/搜索纯函数单测（红）

**Files:**
- Create: `tests/knowledge/test_tag_and_search.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/knowledge/test_tag_and_search.py`：

```python
"""标签规范化 + 搜索关键字规范化 纯函数单测。"""
from server.services import knowledge_tag_service as kts
from server.services import knowledge_service as ks


def test_normalize_tag_name_trims_and_caps_length():
    assert kts.normalize_tag_name("  核心链路  ") == "核心链路"
    assert kts.normalize_tag_name("x" * 100) == "x" * 64      # 截到 64
    assert kts.normalize_tag_name("") == ""


def test_dedupe_tag_ids_preserves_order():
    assert kts.dedupe_tag_ids([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_normalize_search_query():
    # 搜索词：去首尾空白；空/纯空白 → None（表示不过滤）
    assert ks.normalize_search_query("  JWT 鉴权 ") == "JWT 鉴权"
    assert ks.normalize_search_query("   ") is None
    assert ks.normalize_search_query(None) is None
```

- [ ] **Step 2: 跑确认失败**

Run: `./venv/bin/python -m pytest tests/knowledge/test_tag_and_search.py -v`
Expected: FAIL —— `knowledge_tag_service` 不存在、`ks.normalize_search_query` 未定义。

- [ ] **Step 3: 提交**

```bash
git add tests/knowledge/test_tag_and_search.py
git commit -m "$(cat <<'EOF'
test(knowledge): 标签规范化与搜索词规范化单测（红）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 标签 service + 搜索词规范化

**Files:**
- Create: `server/services/knowledge_tag_service.py`
- Modify: `server/services/knowledge_service.py`（加 `normalize_search_query`）

- [ ] **Step 1: 写标签 service**

创建 `server/services/knowledge_tag_service.py`：

```python
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
    # 连接表 knowledge_document_tags 的 FK 是 ondelete=CASCADE，删标签即断开所有文档关联
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
```

- [ ] **Step 2: 给 knowledge_service 加 `normalize_search_query`**

在 `server/services/knowledge_service.py` 里 `html_to_text` 函数**之后**加：

```python
def normalize_search_query(q: Optional[str]) -> Optional[str]:
    """搜索词规范化：去首尾空白；空/纯空白返回 None（表示不按关键字过滤）。"""
    if not q:
        return None
    s = q.strip()
    return s or None
```

- [ ] **Step 3: 跑标签/搜索单测确认通过**

Run: `./venv/bin/python -m pytest tests/knowledge/test_tag_and_search.py -v`
Expected: 3 passed。

- [ ] **Step 4: 编译**

Run: `./venv/bin/python -m compileall server/services/knowledge_tag_service.py server/services/knowledge_service.py`
Expected: 无 SyntaxError。

- [ ] **Step 5: 提交**

```bash
git add server/services/knowledge_tag_service.py server/services/knowledge_service.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 标签 service + 搜索词规范化

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 标签 REST 路由 + 注册

**Files:**
- Create: `server/api/knowledge_tags.py`
- Modify: `server/api/__init__.py`, `server/main.py`

- [ ] **Step 1: 写路由**

创建 `server/api/knowledge_tags.py`：

```python
"""/api/knowledge/tags/* —— 知识库标签 CRUD。"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from database.models import Project
from server.api.deps import DBDep, CurrentUserDep
from server.services import knowledge_tag_service as kts

router = APIRouter(prefix="/knowledge/tags", tags=["knowledge"])


class TagCreate(pydantic.BaseModel):
    project_id: int
    name: str
    color: Optional[str] = None


class TagUpdate(pydantic.BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


def _require_project(session, project_id: int) -> Project:
    p = session.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return p


def _require_tag_in_project(session, tag_id: int, project_id: Optional[int] = None):
    t = kts.get_tag(session, tag_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"标签不存在：{tag_id}")
    if project_id is not None and t.project_id != project_id:
        raise HTTPException(status_code=403, detail="无权访问该标签")
    return t


@router.get("")
def list_tags(db: DBDep, project_id: int = Query(...)):
    _require_project(db.session, project_id)
    return {"status": "success", "data": [kts.serialize_tag(t) for t in kts.list_tags(db.session, project_id)]}


@router.post("")
def create_tag(payload: TagCreate, db: DBDep, current_user: CurrentUserDep):
    _require_project(db.session, payload.project_id)
    try:
        t = kts.get_or_create_tag(db.session, project_id=payload.project_id, name=payload.name, color=payload.color)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "data": kts.serialize_tag(t)}


@router.put("/{tag_id}")
def update_tag(tag_id: int, payload: TagUpdate, db: DBDep, current_user: CurrentUserDep):
    t = _require_tag_in_project(db.session, tag_id)
    t = kts.update_tag(db.session, t, name=payload.name, color=payload.color)
    return {"status": "success", "data": kts.serialize_tag(t)}


@router.delete("/{tag_id}")
def delete_tag(tag_id: int, db: DBDep, current_user: CurrentUserDep):
    t = _require_tag_in_project(db.session, tag_id)
    kts.delete_tag(db.session, t)
    return {"status": "success", "data": {"id": tag_id}}
```

- [ ] **Step 2: 注册（`server/api/__init__.py`）**

在 `from .knowledge_folders import router as knowledge_folders_router` 下面加：
```python
from .knowledge_tags import router as knowledge_tags_router
```
在 `__all__` 里 `"knowledge_folders_router",` 下面加：
```python
    "knowledge_tags_router",
```

- [ ] **Step 3: 注册（`server/main.py`）**

在两处 `knowledge_folders_router,` 下面各加：
```python
    knowledge_tags_router,
```

- [ ] **Step 4: 编译 + 路由自查**

Run:
```bash
./venv/bin/python -m compileall server/api/knowledge_tags.py server/api/__init__.py server/main.py && \
set -a && . ./.env && set +a && CELERY_TASK_ALWAYS_EAGER=1 ./venv/bin/python -c "
from server.main import app
paths = sorted({r.path for r in app.routes if 'knowledge/tags' in getattr(r,'path','')})
print(paths)
assert '/api/knowledge/tags' in paths, paths
print('tags router mounted')
"
```
Expected: 打印含 `/api/knowledge/tags` 与 `/api/knowledge/tags/{tag_id}` + `tags router mounted`。

- [ ] **Step 5: 提交**

```bash
git add server/api/knowledge_tags.py server/api/__init__.py server/main.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 标签 REST 路由 /api/knowledge/tags + 注册

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 文档端点扩展——folder_id / tag_ids / 搜索过滤 / 序列化 / 置顶

**Files:**
- Modify: `server/services/knowledge_service.py`
- Modify: `server/api/knowledge.py`

- [ ] **Step 1: service —— list_docs 支持过滤 + create/update 接 folder_id/tag_ids + serialize 补字段 + 置顶**

在 `server/services/knowledge_service.py` 做四处改动：

(a) 文件顶部 import 补充（把现有 `from database.models import (...)` 块加两个名字 `KnowledgeTag` 不需要——标签集通过 tag service 设置；这里需要在函数内延迟 import tag service 以免循环）。**无需改 import 块**。

(b) 替换 `list_docs`，支持 `q` / `folder_id` / `tag_id` 过滤：

```python
def list_docs(
    session,
    project_id: int,
    module_id: Optional[int] = None,
    *,
    folder_id: Optional[int] = None,
    tag_id: Optional[int] = None,
    q: Optional[str] = None,
) -> List[KnowledgeDocument]:
    query = session.query(KnowledgeDocument).filter(KnowledgeDocument.project_id == project_id)
    if module_id is not None:
        query = query.filter(KnowledgeDocument.module_id == module_id)
    if folder_id is not None:
        query = query.filter(KnowledgeDocument.folder_id == folder_id)
    if tag_id is not None:
        from database.models import KnowledgeDocumentTag
        query = query.join(
            KnowledgeDocumentTag, KnowledgeDocumentTag.document_id == KnowledgeDocument.id
        ).filter(KnowledgeDocumentTag.tag_id == tag_id)
    kw = normalize_search_query(q)
    if kw:
        like = f"%{kw}%"
        query = query.filter(
            (KnowledgeDocument.title.ilike(like)) | (KnowledgeDocument.content.ilike(like))
        )
    return query.order_by(
        KnowledgeDocument.is_pinned.desc(),
        KnowledgeDocument.updated_at.desc(),
        KnowledgeDocument.id.desc(),
    ).all()
```

(c) `create_doc` 加 `folder_id` 与 `tag_ids` 形参并落地。把 `create_doc` 签名与函数体改为：

```python
def create_doc(
    session,
    *,
    project_id: int,
    title: str,
    content_html: str,
    module_id: Optional[int] = None,
    folder_id: Optional[int] = None,
    context_type: Optional[str] = None,
    include_in_rag: bool = True,
    tag_ids: Optional[List[int]] = None,
    author_id: Optional[int] = None,
) -> KnowledgeDocument:
    content = html_to_text(content_html)
    doc = KnowledgeDocument(
        project_id=project_id,
        module_id=module_id,
        folder_id=folder_id,
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
    session.flush()
    if tag_ids is not None:
        from server.services import knowledge_tag_service as kts
        kts.set_document_tags(session, doc, tag_ids)
    sync_rag_projection(session, doc)
    return doc
```

(d) `update_doc` 加 `folder_id` 与 `tag_ids`。把签名与相关分支改为（保留原有 title/content/module/context/include/editor 逻辑，新增 folder_id 与 tag_ids 处理）：

```python
def update_doc(
    session,
    doc: KnowledgeDocument,
    *,
    title: Optional[str] = None,
    content_html: Optional[str] = None,
    module_id: Optional[int] = ...,
    folder_id: Optional[int] = ...,
    context_type: Optional[str] = None,
    include_in_rag: Optional[bool] = None,
    tag_ids: Optional[List[int]] = None,
    editor_id: Optional[int] = None,
) -> KnowledgeDocument:
    if title is not None:
        doc.title = title.strip()[:255]
    if content_html is not None:
        doc.content_html = content_html
        doc.content = html_to_text(content_html)
    if module_id is not ...:
        doc.module_id = module_id
    if folder_id is not ...:
        doc.folder_id = folder_id
    if context_type is not None:
        doc.context_type = _normalize_context_type(context_type)
    if include_in_rag is not None:
        doc.include_in_rag = include_in_rag
    if editor_id is not None:
        doc.editor_id = editor_id
    if tag_ids is not None:
        from server.services import knowledge_tag_service as kts
        kts.set_document_tags(session, doc, tag_ids)
    session.flush()
    sync_rag_projection(session, doc)
    return doc
```

(e) 加置顶开关：

```python
def set_pinned(session, doc: KnowledgeDocument, pinned: bool) -> KnowledgeDocument:
    doc.is_pinned = bool(pinned)
    session.flush()
    return doc
```

(f) `serialize` 补 `folder_id` / `is_pinned` / `tags`：把 `serialize` 的 `data` dict 里加三个键（在 `"include_in_rag": ...` 之后）：

```python
        "folder_id": doc.folder_id,
        "is_pinned": bool(doc.is_pinned),
        "tags": [
            {"id": t.id, "name": t.name, "color": t.color}
            for t in (doc.tags or [])
        ],
```

- [ ] **Step 2: 路由 —— list 加 query 参数 / create/update 加字段 / 加置顶端点**

在 `server/api/knowledge.py`：

(a) `KnowledgeCreate` 与 `KnowledgeUpdate` 两个 pydantic 模型各加两个字段（在 `include_in_rag` 附近）：
```python
    folder_id: Optional[int] = None
    tag_ids: Optional[list[int]] = None
```

(b) `list_knowledge` 端点加 query 参数并透传：
```python
@router.get("")
def list_knowledge(
    db: DBDep,
    project_id: int = Query(..., description="项目 id 必填"),
    module_id: Optional[int] = Query(None),
    folder_id: Optional[int] = Query(None),
    tag_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="标题/正文全文搜索"),
):
    _require_project(db.session, project_id)
    docs = knowledge_service.list_docs(
        db.session, project_id, module_id, folder_id=folder_id, tag_id=tag_id, q=q
    )
    return {"status": "success", "data": [knowledge_service.serialize(d) for d in docs]}
```

(c) `create_knowledge` 调用 `create_doc` 时透传 `folder_id` 和 `tag_ids`（在 `create_doc(...)` 调用里补两个实参）：
```python
        folder_id=payload.folder_id,
        tag_ids=payload.tag_ids,
```

(d) `update_knowledge` 调用 `update_doc` 时透传（补两个实参；folder_id 用 payload 的值，None 表示移到根）：
```python
        folder_id=payload.folder_id,
        tag_ids=payload.tag_ids,
```
> 注：update 的 `folder_id=None` 语义为「移到根」；本阶段前端总会传 folder_id（根级时传 null），符合此语义。

(e) 加置顶开关端点（放在 `delete_knowledge` 之前或之后均可）：
```python
class PinUpdate(pydantic.BaseModel):
    pinned: bool


@router.patch("/{doc_id}/pin")
def pin_knowledge(doc_id: int, payload: PinUpdate, db: DBDep, current_user: CurrentUserDep):
    doc = _require_doc_in_project(db.session, doc_id)
    doc = knowledge_service.set_pinned(db.session, doc, payload.pinned)
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}
```
（`pydantic` 已在文件顶部 import。）

- [ ] **Step 3: 编译 + 单测（阶段 0 的 5 条 + 新纯函数仍绿）**

Run:
```bash
./venv/bin/python -m compileall server/services/knowledge_service.py server/api/knowledge.py && \
./venv/bin/python -m pytest tests/knowledge/ -q
```
Expected: compile 无错；pytest 全绿（阶段0 的 5 + 目录树 3 + 标签/搜索 3 = 11 passed）。

- [ ] **Step 4: 提交**

```bash
git add server/services/knowledge_service.py server/api/knowledge.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 文档端点扩展——folder_id/tag_ids/搜索过滤/序列化补字段/置顶

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 端到端验证（目录/标签/搜索/置顶全链路）

**Files:**
- Create（临时，验完删）: `scratchpad/verify_phase1a.py`

- [ ] **Step 1: 写验证脚本**

`mkdir -p scratchpad` 后创建 `scratchpad/verify_phase1a.py`：

```python
"""阶段 1a 端到端验证：目录 CRUD/上移、标签设/删、搜索过滤、置顶排序。commit+清理。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from database.db import DB
from database.models import Project, KnowledgeFolder, KnowledgeDocument, KnowledgeTag
from server.services import knowledge_service as ks
from server.services import knowledge_folder_service as kfs
from server.services import knowledge_tag_service as kts

db = DB(); s = db.session
pid = s.query(Project).order_by(Project.id.asc()).first().id
made = {"docs": [], "folders": [], "tags": []}
try:
    # 目录：建父子，删父→子上移到根
    parent = kfs.create_folder(s, project_id=pid, name="ZZ父目录"); s.flush()
    child = kfs.create_folder(s, project_id=pid, name="ZZ子目录", parent_id=parent.id); s.commit()
    made["folders"] += [parent.id, child.id]
    tree = kfs.list_tree(s, pid)
    assert any(n["id"] == parent.id and any(c["id"] == child.id for c in n["children"]) for n in tree)
    print("✓ 目录树父子正确")
    kfs.delete_folder(s, parent); s.commit(); made["folders"].remove(parent.id)
    child = kfs.get_folder(s, child.id)
    assert child.parent_id is None, "删父后子应上移到根"
    print("✓ 删目录子项上移")

    # 标签：建两个，设到文档，搜索/过滤
    t1 = kts.get_or_create_tag(s, project_id=pid, name="ZZ核心", color="#f00")
    t2 = kts.get_or_create_tag(s, project_id=pid, name="ZZ次要"); s.commit()
    made["tags"] += [t1.id, t2.id]
    d = ks.create_doc(s, project_id=pid, title="ZZ登录鉴权文档",
                      content_html="<p>统一鉴权走 JWT，Bearer Token</p>",
                      folder_id=child.id, tag_ids=[t1.id, t2.id, t1.id], include_in_rag=True); s.commit()
    made["docs"].append(d.id)
    ser = ks.serialize(d)
    assert ser["folder_id"] == child.id and {x["id"] for x in ser["tags"]} == {t1.id, t2.id}
    print("✓ 文档落目录 + 标签去重设置")

    # 搜索：按关键字
    hits = ks.list_docs(s, pid, q="JWT")
    assert any(x.id == d.id for x in hits); print("✓ 全文搜索命中")
    # 过滤：按标签
    hits = ks.list_docs(s, pid, tag_id=t1.id)
    assert any(x.id == d.id for x in hits); print("✓ 标签过滤命中")
    # 过滤：按目录
    hits = ks.list_docs(s, pid, folder_id=child.id)
    assert any(x.id == d.id for x in hits); print("✓ 目录过滤命中")

    # 置顶：另建一篇更晚更新，但把 d 置顶，d 应排在前
    d2 = ks.create_doc(s, project_id=pid, title="ZZ更晚文档", content_html="<p>xyz</p>", include_in_rag=False); s.commit()
    made["docs"].append(d2.id)
    ks.set_pinned(s, d, True); s.commit()
    ordered = ks.list_docs(s, pid)
    di = next(i for i, x in enumerate(ordered) if x.id == d.id)
    d2i = next(i for i, x in enumerate(ordered) if x.id == d2.id)
    assert di < d2i, "置顶文档应排在未置顶之前"
    print("✓ 置顶排序生效")

    # 删标签 → 文档标签关联断开（不删文档）
    kts.delete_tag(s, kts.get_tag(s, t1.id)); s.commit(); made["tags"].remove(t1.id)
    assert ks.get_doc(s, d.id) is not None
    assert t1.id not in {x["id"] for x in ks.serialize(ks.get_doc(s, d.id))["tags"]}
    print("✓ 删标签断开关联、不删文档")
    print("ALL GREEN")
finally:
    for did in made["docs"]:
        x = s.query(KnowledgeDocument).filter(KnowledgeDocument.id == did).first()
        if x: ks.delete_doc(s, x)
    for tid in made["tags"]:
        x = s.query(KnowledgeTag).filter(KnowledgeTag.id == tid).first()
        if x: s.delete(x)
    for fid in made["folders"]:
        x = s.query(KnowledgeFolder).filter(KnowledgeFolder.id == fid).first()
        if x: s.delete(x)
    s.commit(); db.close()
```

- [ ] **Step 2: 跑验证**

Run: `set -a && . ./.env && set +a && ./venv/bin/python scratchpad/verify_phase1a.py`
Expected: 依次 8 个 ✓ 与 `ALL GREEN`。

- [ ] **Step 3: 确认无污染**

Run: `set -a && . ./.env && set +a && ./venv/bin/python -c "
from database.db import DB
from database.models import KnowledgeDocument, KnowledgeFolder, KnowledgeTag
s=DB().session
import sqlalchemy as sa
n=sum([
  s.query(KnowledgeDocument).filter(KnowledgeDocument.title.like('ZZ%')).count(),
  s.query(KnowledgeFolder).filter(KnowledgeFolder.name.like('ZZ%')).count(),
  s.query(KnowledgeTag).filter(KnowledgeTag.name.like('ZZ%')).count(),
])
print('残留 ZZ 测试数据(应0):', n)
"`
Expected: `残留 ZZ 测试数据(应0): 0`。

- [ ] **Step 4: 删临时脚本 + 全量单测 + app import**

Run:
```bash
rm scratchpad/verify_phase1a.py
./venv/bin/python -m pytest tests/knowledge/ -q && \
set -a && . ./.env && set +a && CELERY_TASK_ALWAYS_EAGER=1 ./venv/bin/python -c "import server.main; print('app import ok')"
```
Expected: pytest 11 passed；打印 `app import ok`。

---

## 收尾与验收

- [ ] 三个新能力后端可用：目录树 CRUD（含删除上移）、标签 CRUD + 文档打标签、列表端点的 q/folder_id/tag_id 过滤 + 置顶排序。
- [ ] 向后兼容：阶段 0 前端调用 `/api/knowledge?project_id=..&module_id=..` 仍工作；serialize 只增字段不删字段。
- [ ] AI 召回不受影响（本阶段未碰投影逻辑；置顶/目录/标签都不进投影）。

---

## Self-Review 记录

- **Spec 覆盖**：对应 spec 阶段 1「独立多级目录树 + 标签 + 全文搜索」的**后端**部分。目录树=Task 2/3；标签=Task 5/6 + Task 7 文档打标签；全文搜索（ILIKE 起步）+ folder/tag 过滤=Task 7 list_docs；置顶=Task 7（阅读体验里也会用，这里先出后端）。前端（左树右列 UI）属阶段 1b，单独出 plan。
- **占位扫描**：无 TBD/TODO；每个改代码步骤含完整代码与确切命令、预期输出。
- **类型一致**：`build_folder_tree`/`serialize_folder`/`list_tree`/`create_folder`/`update_folder`/`delete_folder`、`normalize_tag_name`/`dedupe_tag_ids`/`serialize_tag`/`get_or_create_tag`/`set_document_tags`、`normalize_search_query`/`list_docs(folder_id,tag_id,q)`/`set_pinned` 在 service、路由、验证脚本间命名一致；pydantic 字段 `folder_id`/`tag_ids`/`pinned` 与 service 形参对齐。
- **兼容性**：list_docs 保留位置参数 `module_id`；create/update 新增形参均为可选（默认 `...`/None），不破坏阶段 0 调用。
