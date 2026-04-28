"""/api/requirements/* —— 项目下的需求点 CRUD。

数据来源：
  - 用户手工新建（source=manual）
  - AI 解析 PRD 批量生成（source=ai_generated，由 tasks/ai_tasks.py 写）

后续给 P0-2/3（生成测试计划 / 功能用例）当 input。
"""
from __future__ import annotations

from typing import List, Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func

from server.api.deps import DBDep
from database.models import (
    Project,
    Requirement,
    REQUIREMENT_STATUS_DRAFT,
    REQUIREMENT_STATUS_APPROVED,
    REQUIREMENT_STATUS_ARCHIVED,
    ALL_REQUIREMENT_STATUSES,
    REQUIREMENT_SOURCE_MANUAL,
)

router = APIRouter(prefix="/requirements", tags=["requirements"])


class RequirementCreate(pydantic.BaseModel):
    project_id: int
    title: str = pydantic.Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    priority: int = 2
    tags: Optional[List[str]] = None
    depends_on: Optional[List[int]] = None
    status: Optional[str] = REQUIREMENT_STATUS_DRAFT


class RequirementUpdate(pydantic.BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    priority: Optional[int] = None
    tags: Optional[List[str]] = None
    depends_on: Optional[List[int]] = None
    status: Optional[str] = None


@router.post("")
def create_requirement(payload: RequirementCreate, db: DBDep):
    proj = db.session.query(Project).filter(Project.id == payload.project_id).first()
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    if payload.status and payload.status not in ALL_REQUIREMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status：{payload.status}")

    max_sort = (
        db.session.query(func.max(Requirement.sort_order))
        .filter(Requirement.project_id == payload.project_id)
        .scalar()
        or 0
    )
    req = Requirement(
        project_id=payload.project_id,
        title=payload.title.strip(),
        description=payload.description,
        acceptance_criteria=payload.acceptance_criteria or [],
        priority=payload.priority,
        tags=payload.tags or [],
        depends_on=payload.depends_on or [],
        status=payload.status or REQUIREMENT_STATUS_DRAFT,
        source=REQUIREMENT_SOURCE_MANUAL,
        sort_order=max_sort + 1,
    )
    db.session.add(req)
    db.session.flush()
    db.session.refresh(req)
    return {"status": "success", "data": req.to_dict()}


@router.get("")
def list_requirements(
    db: DBDep,
    project_id: int = Query(..., description="项目 id 必填"),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
):
    q = db.session.query(Requirement).filter(Requirement.project_id == project_id)
    if status:
        if status not in ALL_REQUIREMENT_STATUSES:
            raise HTTPException(status_code=400, detail=f"非法 status：{status}")
        q = q.filter(Requirement.status == status)
    if source:
        q = q.filter(Requirement.source == source)
    rows = q.order_by(Requirement.sort_order.asc(), Requirement.id.asc()).all()
    return {"status": "success", "data": [r.to_dict() for r in rows]}


@router.get("/{req_id}")
def get_requirement(req_id: int, db: DBDep):
    req = db.session.query(Requirement).filter(Requirement.id == req_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    return {"status": "success", "data": req.to_dict()}


@router.put("/{req_id}")
def update_requirement(req_id: int, payload: RequirementUpdate, db: DBDep):
    req = db.session.query(Requirement).filter(Requirement.id == req_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")

    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ALL_REQUIREMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status：{data['status']}")

    for k, v in data.items():
        setattr(req, k, v)
    db.session.flush()
    return {"status": "success", "data": req.to_dict()}


@router.delete("/{req_id}")
def delete_requirement(req_id: int, db: DBDep):
    req = db.session.query(Requirement).filter(Requirement.id == req_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    db.session.delete(req)
    return {"status": "success", "message": "已删除"}
