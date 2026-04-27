"""/api/modules/* 路由。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

from server.api.deps import DBDep
from database.models import Module, ModuleCreate, Project

router = APIRouter(prefix="/modules", tags=["modules"])


def _get_module_ancestors(module: Module, db) -> list[dict]:
    """从当前模块沿 parent_id 往上拼到顶（不含自己），顺序：根 → 父。"""
    ancestors: list[dict] = []
    current = module
    while current.parent_id:
        parent = (
            db.session.query(Module).filter(Module.id == current.parent_id).first()
        )
        if not parent:
            break
        ancestors.append({"id": parent.id, "name": parent.name})
        current = parent
    ancestors.reverse()
    return ancestors


def _get_project_root(module: Module, db) -> dict | None:
    project = (
        db.session.query(Project).filter(Project.id == module.project_id).first()
    )
    if not project:
        return None
    # id=None 是前端面包屑里约定的「根节点」约定值
    return {"id": None, "name": project.name}


@router.post("")
def create_module(module: ModuleCreate, db: DBDep):
    if module.project_id is None or not module.name:
        raise HTTPException(status_code=400, detail="名称不能为空")

    db_module = Module(**module.model_dump())
    db.session.add(db_module)
    db.session.flush()
    db.session.refresh(db_module)
    return {
        "status": "success",
        "data": {
            "id": db_module.id,
            "name": db_module.name,
            "parent_id": db_module.parent_id,
            "project_id": db_module.project_id,
        },
    }


@router.get("/{module_id}")
def get_module_detail(module_id: int, db: DBDep):
    """返回模块详情 + 从根到父的祖先链（前端面包屑用）。"""
    module = db.session.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="模块不存在")

    ancestors = _get_module_ancestors(module, db)
    project_root = _get_project_root(module, db)
    if project_root:
        ancestors.insert(0, project_root)

    return {
        "status": "success",
        "data": {
            "id": module.id,
            "name": module.name,
            "parent_id": module.parent_id,
            "project_id": module.project_id,
            "ancestors": ancestors,
        },
    }


@router.put("/{module_id}")
def update_module(
    module_id: int,
    db: DBDep,
    name: str = Body(..., embed=True),
):
    db_module = db.session.query(Module).filter(Module.id == module_id).first()
    if not db_module:
        raise HTTPException(status_code=404, detail="模块不存在")

    db_module.name = name
    return {"status": "success", "message": "修改成功"}


@router.delete("/{module_id}")
def delete_module(module_id: int, db: DBDep):
    db_module = db.session.query(Module).filter(Module.id == module_id).first()
    if not db_module:
        raise HTTPException(status_code=404, detail="模块不存在")

    # 关系上 cascade="all, delete-orphan"，子模块 / 用例会一起删。
    db.session.delete(db_module)
    return {"status": "success", "message": "模块已删除"}


# ---------------------------------------------------------------------------
# 把模块挪到不同的父节点下（同项目内）
#
# 上下文：以前模块只能"上下移"调整顺序（走 /api/reorder 改 sort_order），但跨父
# 节点的"移动"没有接口 —— 用户只能"删了重建 + 挪用例"，体验非常差。
#
# 这个接口做：
#   1. 校验目标父节点是同一个项目下的（或为 None=项目根），避免越权 / 跨项目搬运；
#   2. 防环：目标 parent 不能是模块自己，也不能是模块的后代（否则会形成自引用环，
#      左侧文件管理器会进入死循环）；
#   3. 把目标父节点下当前最大的 sort_order + 1 挂到这条模块上，让"移过来"自然出现
#      在末尾，符合用户直觉（不会插到中间打乱顺序）；
#   4. 不带"重排兄弟"，因为现有 reorder 接口已经能解决调位需求 —— 单一职责。
# ---------------------------------------------------------------------------
class _MoveModulePayload(BaseModel):
    target_parent_id: Optional[int] = None  # None => 项目根


def _is_descendant_of(db, candidate_parent_id: int, ancestor_id: int) -> bool:
    """判定 candidate_parent_id 是否就是 ancestor_id 的后代（含自身）。

    用于防环：不能把模块 A 挪到 A 自身或 A 的子孙下。
    走广度优先，从 ancestor 一路往下展开 children；找到 candidate 即为后代。
    """
    if candidate_parent_id == ancestor_id:
        return True
    frontier = [ancestor_id]
    while frontier:
        children = (
            db.session.query(Module.id).filter(Module.parent_id.in_(frontier)).all()
        )
        next_ids = [r[0] for r in children]
        if not next_ids:
            return False
        if candidate_parent_id in next_ids:
            return True
        frontier = next_ids
    return False


@router.patch("/{module_id}/move")
def move_module(module_id: int, payload: _MoveModulePayload, db: DBDep):
    """把模块挪到 target_parent_id 下；target_parent_id=null 即项目根。"""
    src = db.session.query(Module).filter(Module.id == module_id).first()
    if src is None:
        raise HTTPException(status_code=404, detail="模块不存在")

    target_parent_id = payload.target_parent_id

    # 同 parent 的"无效移动"直接当成功 —— 避免前端要做对比逻辑
    if (src.parent_id or None) == (target_parent_id or None):
        return {
            "status": "success",
            "message": "目标父节点与当前一致，已忽略",
            "data": {"id": src.id, "parent_id": src.parent_id, "sort_order": src.sort_order},
        }

    # 校验目标 parent：必须存在且属于同一个 project
    if target_parent_id is not None:
        target = (
            db.session.query(Module)
            .filter(Module.id == target_parent_id)
            .first()
        )
        if target is None:
            raise HTTPException(status_code=404, detail="目标父模块不存在")
        if target.project_id != src.project_id:
            raise HTTPException(
                status_code=400, detail="不能跨项目移动模块"
            )
        # 防环：目标 parent 不能是 src 自己，也不能是 src 的后代
        if _is_descendant_of(db, target_parent_id, src.id):
            raise HTTPException(
                status_code=400,
                detail="不能将模块移动到它自己或其子孙模块下（会形成环）",
            )

    # 计算挂到目标父节点末尾用的 sort_order
    max_order = (
        db.session.query(func.max(Module.sort_order))
        .filter(
            Module.project_id == src.project_id,
            Module.parent_id == target_parent_id,
        )
        .scalar()
    )
    new_order = (max_order or 0) + 1

    src.parent_id = target_parent_id
    src.sort_order = new_order
    db.session.flush()

    return {
        "status": "success",
        "message": "模块已移动",
        "data": {
            "id": src.id,
            "parent_id": src.parent_id,
            "sort_order": src.sort_order,
        },
    }


# ---------------------------------------------------------------------------
# 列出某项目下的所有模块（树形原料），给前端"移动到…"对话框做目标选择用。
# 不是 /content 接口，因为它要返回**所有层**而不只是某层；也排除当前要移动的
# 那个模块自己 + 它的后代（避免用户选了形成环）。
# ---------------------------------------------------------------------------
@router.get("")
def list_modules_for_picker(
    db: DBDep,
    project_id: int,
    exclude_subtree: Optional[int] = None,
):
    """返回项目下所有模块的扁平列表。可选 exclude_subtree 排除某个子树（含自身）。"""
    rows = (
        db.session.query(Module)
        .filter(Module.project_id == project_id)
        .order_by(Module.parent_id.is_(None).desc(), Module.parent_id, Module.sort_order)
        .all()
    )

    # 计算要排除的 id 集合（exclude_subtree 自己 + 全部后代）
    excluded: set[int] = set()
    if exclude_subtree is not None:
        excluded.add(exclude_subtree)
        frontier = [exclude_subtree]
        while frontier:
            children = (
                db.session.query(Module.id)
                .filter(Module.parent_id.in_(frontier))
                .all()
            )
            next_ids = [r[0] for r in children]
            if not next_ids:
                break
            excluded.update(next_ids)
            frontier = next_ids

    data = [
        {
            "id": m.id,
            "name": m.name,
            "parent_id": m.parent_id,
            "sort_order": m.sort_order,
        }
        for m in rows
        if m.id not in excluded
    ]
    return {"status": "success", "data": data}
