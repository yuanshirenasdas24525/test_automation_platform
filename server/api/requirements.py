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
    User,
    REQUIREMENT_STATUS_DRAFT,
    REQUIREMENT_STATUS_APPROVED,
    REQUIREMENT_STATUS_ARCHIVED,
    ALL_REQUIREMENT_STATUSES,
    ALL_REQUIREMENT_SYSTEM_STATUSES,
    ALL_REQUIREMENT_BUSINESS_STATUSES,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
    REQUIREMENT_BUSINESS_STATUS_ACCEPTED,
    REQUIREMENT_SOURCE_MANUAL,
    ROLE_PM,
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
    version_id: Optional[int] = None
    business_status: Optional[str] = None
    assignee_pm_id: Optional[int] = None


class RequirementUpdate(pydantic.BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    priority: Optional[int] = None
    tags: Optional[List[str]] = None
    depends_on: Optional[List[int]] = None
    status: Optional[str] = None
    version_id: Optional[int] = None
    business_status: Optional[str] = None
    assignee_pm_id: Optional[int] = None


class RequirementAccept(pydantic.BaseModel):
    pm_id: Optional[int] = None


@router.post("")
def create_requirement(payload: RequirementCreate, db: DBDep):
    proj = db.session.query(Project).filter(Project.id == payload.project_id).first()
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    if payload.status and payload.status not in ALL_REQUIREMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status：{payload.status}")
    if payload.business_status and payload.business_status not in ALL_REQUIREMENT_BUSINESS_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 business_status，可选: {sorted(ALL_REQUIREMENT_BUSINESS_STATUSES)}",
        )

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
        version_id=payload.version_id,
        business_status=payload.business_status,
        assignee_pm_id=payload.assignee_pm_id,
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
    version_id: Optional[int] = Query(None),
    system_status: Optional[str] = Query(None),
    business_status: Optional[str] = Query(None),
    assignee_pm_id: Optional[int] = Query(None),
):
    q = db.session.query(Requirement).filter(Requirement.project_id == project_id)
    if status:
        if status not in ALL_REQUIREMENT_STATUSES:
            raise HTTPException(status_code=400, detail=f"非法 status：{status}")
        q = q.filter(Requirement.status == status)
    if source:
        q = q.filter(Requirement.source == source)
    if version_id is not None:
        q = q.filter(Requirement.version_id == version_id)
    if system_status:
        if system_status not in ALL_REQUIREMENT_SYSTEM_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"非法 system_status，可选: {sorted(ALL_REQUIREMENT_SYSTEM_STATUSES)}",
            )
        q = q.filter(Requirement.system_status == system_status)
    if business_status:
        if business_status not in ALL_REQUIREMENT_BUSINESS_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"非法 business_status，可选: {sorted(ALL_REQUIREMENT_BUSINESS_STATUSES)}",
            )
        q = q.filter(Requirement.business_status == business_status)
    if assignee_pm_id is not None:
        q = q.filter(Requirement.assignee_pm_id == assignee_pm_id)
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
    if "business_status" in data and data["business_status"] is not None \
            and data["business_status"] not in ALL_REQUIREMENT_BUSINESS_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 business_status，可选: {sorted(ALL_REQUIREMENT_BUSINESS_STATUSES)}",
        )

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


@router.post("/{req_id}/accept")
def accept_requirement(req_id: int, payload: RequirementAccept, db: DBDep):
    """PM 一键验收：要求 system_status==ready_to_release，
    设 business_status=accepted + accepted_at=now()。"""
    req = db.session.query(Requirement).filter(Requirement.id == req_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")

    if req.system_status != REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE:
        raise HTTPException(
            status_code=409,
            detail="需求未完成开发测试，不能验收",
        )

    if payload.pm_id is not None:
        user = db.session.query(User).filter(User.id == payload.pm_id).first()
        if user is None:
            raise HTTPException(status_code=404, detail="pm 用户不存在")
        if not any(r.code == ROLE_PM for r in user.roles):
            raise HTTPException(status_code=400, detail="该用户没有 pm 角色")
        req.assignee_pm_id = payload.pm_id

    req.business_status = REQUIREMENT_BUSINESS_STATUS_ACCEPTED
    req.accepted_at = func.now()
    db.session.flush()
    db.session.refresh(req)
    return {"status": "success", "data": req.to_dict()}
