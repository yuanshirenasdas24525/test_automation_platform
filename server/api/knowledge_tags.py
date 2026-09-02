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
