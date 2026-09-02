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


def _is_self_or_descendant(session, folder_id: int, candidate_parent_id: Optional[int]) -> bool:
    """把 folder 移到 candidate_parent 下是否会成环：candidate 是 folder 自身或其后代则 True。

    从 candidate 沿 parent_id 向上走到根，途中遇到 folder_id 即成环。带 seen 兜底脏数据死循环。
    """
    cur = candidate_parent_id
    seen = set()
    while cur is not None:
        if cur == folder_id:
            return True
        if cur in seen:
            break
        seen.add(cur)
        cur = (
            session.query(KnowledgeFolder.parent_id)
            .filter(KnowledgeFolder.id == cur)
            .scalar()
        )
    return False


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
        if parent_id is not None and _is_self_or_descendant(session, folder.id, parent_id):
            raise ValueError("目录不能移动到自身或其子目录下")
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
