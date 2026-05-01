"""/api/projects/{project_id}/versions/* —— 版本迭代 CRUD。"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import (
    ProjectVersion,
    Module,
    ALL_VERSION_STATUSES,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class VersionCreate(pydantic.BaseModel):
    version_name: str = pydantic.Field(..., min_length=1, max_length=100)
    display_name: Optional[str] = None
    status: Optional[str] = "planning"
    module_ids: Optional[list[int]] = []
    frontend_versions: Optional[list[dict]] = []
    backend_versions: Optional[list[dict]] = []
    release_notes: Optional[str] = ""
    test_plan_items: Optional[list[dict]] = []
    requirement_doc_items: Optional[list[dict]] = []
    design_doc_items: Optional[list[dict]] = []
    ui_prototype_items: Optional[list[dict]] = []
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


class VersionUpdate(pydantic.BaseModel):
    version_name: Optional[str] = None
    display_name: Optional[str] = None
    status: Optional[str] = None
    sort_order: Optional[int] = None
    frontend_versions: Optional[list[dict]] = None
    backend_versions: Optional[list[dict]] = None
    release_notes: Optional[str] = None
    test_plan_items: Optional[list[dict]] = None
    requirement_doc_items: Optional[list[dict]] = None
    design_doc_items: Optional[list[dict]] = None
    ui_prototype_items: Optional[list[dict]] = None
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


class VersionModuleUpdate(pydantic.BaseModel):
    module_ids: list[int] = []


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/versions")
def list_versions(
    project_id: int,
    db: DBDep,
    status: Optional[str] = Query(None),
):
    q = (
        db.session.query(ProjectVersion)
        .filter(ProjectVersion.project_id == project_id)
    )
    if status:
        if status not in ALL_VERSION_STATUSES:
            raise HTTPException(status_code=400, detail=f"非法 status，合法：{sorted(ALL_VERSION_STATUSES)}")
        q = q.filter(ProjectVersion.status == status)

    rows = q.order_by(ProjectVersion.sort_order.desc(), ProjectVersion.created_at.desc()).all()
    return {"status": "success", "data": [r.to_dict() for r in rows]}


@router.post("/projects/{project_id}/versions")
def create_version(project_id: int, payload: VersionCreate, db: DBDep):
    """创建版本迭代。可选关联 module_ids。"""
    if payload.status not in ALL_VERSION_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status，合法：{sorted(ALL_VERSION_STATUSES)}")

    from sqlalchemy import func as sa_func
    max_sort = (
        db.session.query(sa_func.max(ProjectVersion.sort_order))
        .filter(ProjectVersion.project_id == project_id)
        .scalar()
        or 0
    )

    v = ProjectVersion(
        project_id=project_id,
        version_name=payload.version_name,
        display_name=payload.display_name,
        status=payload.status,
        sort_order=max_sort + 1,
        frontend_versions=payload.frontend_versions or [],
        backend_versions=payload.backend_versions or [],
        release_notes=payload.release_notes or "",
        test_plan_items=payload.test_plan_items or [],
        requirement_doc_items=payload.requirement_doc_items or [],
        design_doc_items=payload.design_doc_items or [],
        ui_prototype_items=payload.ui_prototype_items or [],
        planned_start_at=_parse_dt(payload.planned_start_at),
        planned_end_at=_parse_dt(payload.planned_end_at),
    )

    # 关联模块
    if payload.module_ids:
        modules = (
            db.session.query(Module)
            .filter(
                Module.project_id == project_id,
                Module.id.in_(payload.module_ids),
            )
            .all()
        )
        v.associated_modules = modules

    db.session.add(v)
    db.session.flush()
    db.session.refresh(v)
    db.commit()

    return {"status": "success", "data": v.to_dict()}


@router.get("/projects/{project_id}/versions/{version_id}")
def get_version(project_id: int, version_id: int, db: DBDep):
    v = (
        db.session.query(ProjectVersion)
        .filter(ProjectVersion.id == version_id, ProjectVersion.project_id == project_id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"status": "success", "data": v.to_dict()}


@router.put("/projects/{project_id}/versions/{version_id}")
def update_version(project_id: int, version_id: int, payload: VersionUpdate, db: DBDep):
    v = (
        db.session.query(ProjectVersion)
        .filter(ProjectVersion.id == version_id, ProjectVersion.project_id == project_id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")

    if payload.status is not None and payload.status not in ALL_VERSION_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status，合法：{sorted(ALL_VERSION_STATUSES)}")

    for field in (
        "version_name", "display_name", "status", "sort_order",
        "release_notes",
    ):
        val = getattr(payload, field, None)
        if val is not None:
            setattr(v, field, val)

    for json_field in (
        "frontend_versions", "backend_versions",
        "test_plan_items", "requirement_doc_items",
        "design_doc_items", "ui_prototype_items",
    ):
        val = getattr(payload, json_field, None)
        if val is not None:
            setattr(v, json_field, val)

    if payload.planned_start_at is not None:
        v.planned_start_at = _parse_dt(payload.planned_start_at)
    if payload.planned_end_at is not None:
        v.planned_end_at = _parse_dt(payload.planned_end_at)

    db.commit()
    return {"status": "success", "data": v.to_dict()}


@router.delete("/projects/{project_id}/versions/{version_id}")
def delete_version(project_id: int, version_id: int, db: DBDep):
    v = (
        db.session.query(ProjectVersion)
        .filter(ProjectVersion.id == version_id, ProjectVersion.project_id == project_id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    db.session.delete(v)
    db.commit()
    return {"status": "success", "message": "已删除"}


@router.put("/projects/{project_id}/versions/{version_id}/modules")
def update_version_modules(project_id: int, version_id: int, payload: VersionModuleUpdate, db: DBDep):
    v = (
        db.session.query(ProjectVersion)
        .filter(ProjectVersion.id == version_id, ProjectVersion.project_id == project_id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")

    modules = (
        db.session.query(Module)
        .filter(
            Module.project_id == project_id,
            Module.id.in_(payload.module_ids),
        )
        .all()
    )
    v.associated_modules = modules
    db.commit()

    return {"status": "success", "data": v.to_dict()}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _parse_dt(s):
    if not s:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None
