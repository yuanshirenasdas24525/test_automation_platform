"""/api/projects/{project_id}/versions/* —— 版本迭代 CRUD。"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import (
    ProjectVersion,
    Module,
    Requirement,
    Task,
    ALL_VERSION_STATUSES,
    ALL_REQUIREMENT_SYSTEM_STATUSES,
    ALL_TASK_TYPES,
    ALL_TASK_STATUSES,
    ALL_BUG_SEVERITIES,
    TASK_TYPE_BUG,
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
# 看板聚合：Req（按 system_status 分桶）+ Task 计数（按 type / status / severity）
# ---------------------------------------------------------------------------
@router.get("/project-versions/{version_id}/board")
def version_board(version_id: int, db: DBDep):
    v = db.session.query(ProjectVersion).filter(ProjectVersion.id == version_id).first()
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")

    reqs = (
        db.session.query(Requirement)
        .filter(Requirement.version_id == version_id)
        .order_by(Requirement.sort_order.asc(), Requirement.id.asc())
        .all()
    )

    requirements_by_status: dict[str, list[dict]] = {
        s: [] for s in ALL_REQUIREMENT_SYSTEM_STATUSES
    }
    requirements_by_status["unassigned"] = []  # system_status IS NULL 的桶
    for r in reqs:
        bucket = r.system_status if r.system_status in requirements_by_status else "unassigned"
        requirements_by_status[bucket].append(r.to_dict())

    # Task 计数：先按 requirement.version_id 拉一次，避免 N+1
    task_rows = (
        db.session.query(Task.type, Task.status, Task.severity)
        .join(Requirement, Requirement.id == Task.requirement_id)
        .filter(Requirement.version_id == version_id)
        .all()
    )

    task_counts_by_type: dict[str, dict] = {}
    for t in ALL_TASK_TYPES:
        if t == TASK_TYPE_BUG:
            task_counts_by_type[t] = {
                "total": 0,
                "by_status": {s: 0 for s in ALL_TASK_STATUSES},
                "by_severity": {s: 0 for s in ALL_BUG_SEVERITIES},
            }
        else:
            task_counts_by_type[t] = {
                "total": 0,
                "by_status": {s: 0 for s in ALL_TASK_STATUSES},
            }

    for row in task_rows:
        bucket = task_counts_by_type.get(row.type)
        if bucket is None:
            continue
        bucket["total"] += 1
        if row.status in bucket["by_status"]:
            bucket["by_status"][row.status] += 1
        if row.type == TASK_TYPE_BUG and row.severity in bucket.get("by_severity", {}):
            bucket["by_severity"][row.severity] += 1

    return {
        "status": "success",
        "data": {
            "version": v.to_dict(),
            "requirements_by_status": requirements_by_status,
            "task_counts_by_type": task_counts_by_type,
        },
    }


# ---------------------------------------------------------------------------
# 按版本列用例（M4）
# ---------------------------------------------------------------------------
def _latest_step_run_map(db, case_ids: list[int]) -> dict[int, dict]:
    """一次拿一组自动化用例的"最近一次执行结果"。

    实现：先 GROUP BY case_id 取 max(report_id)，再 join TestStepReport
    取该 report 下该 case 的代表性 status —— 任一 step 是 failed/error/broken
    则该 case 算 failed；否则按现有 step 的 status 取 max（passed > skipped）。
    简化：直接取该 (case_id, report_id) 下所有 step status 的"最差"那个。
    """
    if not case_ids:
        return {}
    from sqlalchemy import func as sa_func
    from database.models import TestStepReport

    latest_sq = (
        db.session.query(
            TestStepReport.case_id.label("cid"),
            sa_func.max(TestStepReport.report_id).label("rid"),
        )
        .filter(TestStepReport.case_id.in_(case_ids))
        .group_by(TestStepReport.case_id)
        .subquery()
    )
    rows = (
        db.session.query(TestStepReport)
        .join(
            latest_sq,
            (TestStepReport.case_id == latest_sq.c.cid)
            & (TestStepReport.report_id == latest_sq.c.rid),
        )
        .all()
    )

    # 把每个 case 在该 report 下的若干 step status 折成单个 status
    by_case: dict[int, list[TestStepReport]] = {}
    for r in rows:
        by_case.setdefault(r.case_id, []).append(r)

    def _aggregate(steps: list) -> str:
        statuses = [s.status or "" for s in steps]
        # 优先级：error > failed > broken > skipped > passed
        for bad in ("error", "failed", "broken"):
            if bad in statuses:
                return bad
        if "skipped" in statuses and not any(s == "passed" for s in statuses):
            return "skipped"
        if "passed" in statuses:
            return "passed"
        return statuses[0] if statuses else "pending"

    out: dict[int, dict] = {}
    for cid, steps in by_case.items():
        # report_id / 最近时间从任一 step 取（同一 report 的 step 时间相近）
        first = steps[0]
        out[cid] = {
            "status": _aggregate(steps),
            "report_id": first.report_id,
            "executed_at": first.create_time.isoformat() if first.create_time else None,
        }
    return out


@router.get("/project-versions/{version_id}/cases")
def list_version_cases(
    version_id: int,
    db: DBDep,
    module_id: Optional[int] = Query(None),
    case_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="多值逗号分隔；可包含 'pending' 表示从未执行"),
):
    """列本版本绑定的所有自动化用例（含 functional），跨模块、扁平。"""
    from database.models import TestCase

    v = db.session.query(ProjectVersion).filter(ProjectVersion.id == version_id).first()
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")

    q = db.session.query(TestCase).filter(TestCase.version_id == version_id)
    if module_id is not None:
        q = q.filter(TestCase.module_id == module_id)
    if case_type:
        q = q.filter(TestCase.case_type == case_type)

    cases = q.order_by(TestCase.sort_order.asc(), TestCase.id.asc()).all()
    if not cases:
        return {"status": "success", "data": {"items": [], "total": 0}}

    # 拿模块名（避免 N+1）
    mod_ids = list({c.module_id for c in cases if c.module_id is not None})
    mod_name_map = {
        m.id: m.name for m in db.session.query(Module).filter(Module.id.in_(mod_ids)).all()
    }

    latest_map = _latest_step_run_map(db, [c.id for c in cases])

    wanted: Optional[set[str]] = None
    if status:
        wanted = {s.strip().lower() for s in status.split(",") if s.strip()}

    items: list[dict] = []
    for c in cases:
        latest = latest_map.get(c.id)
        current_status = (latest or {}).get("status") or "pending"
        if wanted is not None and current_status not in wanted:
            continue
        items.append({
            "id": c.id,
            "name": c.name,
            "case_type": c.case_type,
            "module_id": c.module_id,
            "module_name": mod_name_map.get(c.module_id, ""),
            "sort_order": c.sort_order,
            "latest_run": latest,  # None or {status, report_id, executed_at}
        })

    return {"status": "success", "data": {"items": items, "total": len(items)}}


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
