"""/api/modules/* 路由。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

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
