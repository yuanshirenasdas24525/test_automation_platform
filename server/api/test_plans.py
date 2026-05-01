"""/api/test_plans/* —— 项目下的测试计划 CRUD。

数据来源：
  - AI 生成（POST /api/ai/test_plan → handler 写一行）
  - 用户手工新建 / 编辑 markdown 内容
"""
from __future__ import annotations

from typing import Any, List, Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import (
    Project,
    TestPlan,
    TEST_PLAN_STATUS_DRAFT,
    ALL_TEST_PLAN_STATUSES,
    TEST_PLAN_SOURCE_MANUAL,
)

router = APIRouter(prefix="/test_plans", tags=["test_plans"])


class TestPlanCreate(pydantic.BaseModel):
    project_id: int
    title: str = pydantic.Field(..., min_length=1, max_length=200)
    content: Optional[str] = None
    summary: Optional[str] = None
    requirement_ids: Optional[List[int]] = None
    module_ids: Optional[List[int]] = None
    time_range_start: Optional[str] = None  # ISO 字符串
    time_range_end: Optional[str] = None
    resource_notes: Optional[str] = None
    status: Optional[str] = TEST_PLAN_STATUS_DRAFT


class TestPlanUpdate(pydantic.BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    requirement_ids: Optional[List[int]] = None
    module_ids: Optional[List[int]] = None
    time_range_start: Optional[str] = None
    time_range_end: Optional[str] = None
    resource_notes: Optional[str] = None
    status: Optional[str] = None


def _parse_dt(s: Optional[str]) -> Any:
    if not s:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


@router.post("")
def create_test_plan(payload: TestPlanCreate, db: DBDep):
    proj = db.session.query(Project).filter(Project.id == payload.project_id).first()
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    if payload.status and payload.status not in ALL_TEST_PLAN_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status：{payload.status}")

    plan = TestPlan(
        project_id=payload.project_id,
        title=payload.title.strip(),
        content=payload.content,
        summary=payload.summary,
        requirement_ids=payload.requirement_ids or [],
        module_ids=payload.module_ids or [],
        time_range_start=_parse_dt(payload.time_range_start),
        time_range_end=_parse_dt(payload.time_range_end),
        resource_notes=payload.resource_notes,
        status=payload.status or TEST_PLAN_STATUS_DRAFT,
        source=TEST_PLAN_SOURCE_MANUAL,
    )
    db.session.add(plan)
    db.session.flush()
    db.session.refresh(plan)
    return {"status": "success", "data": plan.to_dict()}


@router.get("")
def list_test_plans(
    db: DBDep,
    project_id: int = Query(..., description="项目 id 必填"),
    status: Optional[str] = Query(None),
):
    q = db.session.query(TestPlan).filter(TestPlan.project_id == project_id)
    if status:
        if status not in ALL_TEST_PLAN_STATUSES:
            raise HTTPException(status_code=400, detail=f"非法 status：{status}")
        q = q.filter(TestPlan.status == status)
    rows = q.order_by(TestPlan.created_at.desc()).all()
    return {"status": "success", "data": [r.to_dict() for r in rows]}


@router.get("/{plan_id}")
def get_test_plan(plan_id: int, db: DBDep):
    plan = db.session.query(TestPlan).filter(TestPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    return {"status": "success", "data": plan.to_dict()}


@router.put("/{plan_id}")
def update_test_plan(plan_id: int, payload: TestPlanUpdate, db: DBDep):
    plan = db.session.query(TestPlan).filter(TestPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")

    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ALL_TEST_PLAN_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status：{data['status']}")

    # 时间字段需要解析
    if "time_range_start" in data:
        plan.time_range_start = _parse_dt(data.pop("time_range_start"))
    if "time_range_end" in data:
        plan.time_range_end = _parse_dt(data.pop("time_range_end"))

    for k, v in data.items():
        setattr(plan, k, v)

    db.session.flush()
    return {"status": "success", "data": plan.to_dict()}


@router.delete("/{plan_id}")
def delete_test_plan(plan_id: int, db: DBDep):
    plan = db.session.query(TestPlan).filter(TestPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    db.session.delete(plan)
    return {"status": "success", "message": "已删除"}
