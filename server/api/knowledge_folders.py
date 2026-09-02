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
