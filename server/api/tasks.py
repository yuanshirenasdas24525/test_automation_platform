"""/api/tasks/* —— Task CRUD + 多维过滤 + 状态级联触发。

设计：
  - 严格 type / status / severity 校验
  - bug 必须有 parent_task_id 且 severity 必填
  - PUT / DELETE 末尾调 task_service.recompute_requirement_status，
    把 Req.system_status 滚到当前 Task 状态聚合后的值
  - DELETE 真删（Task 没有软删字段）；调用方真要"软删"自行 PUT status=closed
"""
from __future__ import annotations

from typing import List, Optional
from datetime import datetime

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from server.services.task_service import recompute_requirement_status
from database.models import (
    Task,
    ALL_TASK_TYPES,
    ALL_TASK_STATUSES,
    ALL_BUG_SEVERITIES,
    TASK_TYPE_BUG,
    TASK_STATUS_PENDING,
    TASK_STATUS_DEV_DOING,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(pydantic.BaseModel):
    requirement_id: int
    title: str = pydantic.Field(..., min_length=1, max_length=255)
    type: str
    status: Optional[str] = TASK_STATUS_PENDING
    severity: Optional[str] = None
    assignee_dev_id: Optional[int] = None
    assignee_test_id: Optional[int] = None
    created_by_id: int
    parent_task_id: Optional[int] = None
    description: Optional[str] = None
    metadata: Optional[dict] = None
    estimated_hours: Optional[float] = None


class TaskFromTestFailure(pydantic.BaseModel):
    parent_task_id: int
    severity: str
    title: str = pydantic.Field(..., min_length=1, max_length=255)
    created_by_id: int
    related_case_id: Optional[int] = None
    description: Optional[str] = None
    metadata: Optional[dict] = None


class TaskUpdate(pydantic.BaseModel):
    title: Optional[str] = pydantic.Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = None
    severity: Optional[str] = None
    assignee_dev_id: Optional[int] = None
    assignee_test_id: Optional[int] = None
    metadata: Optional[dict] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    closed_at: Optional[datetime] = None


# ============ CRUD ============

@router.post("")
def create_task(payload: TaskCreate, db: DBDep):
    if payload.type not in ALL_TASK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 type，可选: {sorted(ALL_TASK_TYPES)}",
        )
    if payload.status is not None and payload.status not in ALL_TASK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 status，可选: {sorted(ALL_TASK_STATUSES)}",
        )

    severity = payload.severity
    if payload.type == TASK_TYPE_BUG:
        if payload.parent_task_id is None:
            raise HTTPException(status_code=400, detail="bug 必须指定 parent_task_id")
        if severity is None or severity not in ALL_BUG_SEVERITIES:
            raise HTTPException(
                status_code=400,
                detail=f"bug 必须指定 severity ∈ {sorted(ALL_BUG_SEVERITIES)}",
            )
    else:
        # 非 bug 不带 severity
        severity = None

    task = Task(
        requirement_id=payload.requirement_id,
        title=payload.title,
        type=payload.type,
        status=payload.status or TASK_STATUS_PENDING,
        severity=severity,
        assignee_dev_id=payload.assignee_dev_id,
        assignee_test_id=payload.assignee_test_id,
        created_by_id=payload.created_by_id,
        parent_task_id=payload.parent_task_id,
        description=payload.description,
        task_metadata=payload.metadata,
        estimated_hours=payload.estimated_hours,
    )
    db.session.add(task)
    db.session.flush()
    db.session.refresh(task)
    recompute_requirement_status(task.requirement_id, db.session)
    return {"status": "success", "data": task.to_dict()}


@router.post("/from-test-failure")
def create_task_from_test_failure(payload: TaskFromTestFailure, db: DBDep):
    """测试失败 → 一键建 bug：自动 type=bug / status=dev_doing，
    继承 parent_task 的 assignee_dev_id / requirement_id。"""
    if payload.severity not in ALL_BUG_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 severity，可选: {sorted(ALL_BUG_SEVERITIES)}",
        )
    parent = db.session.query(Task).filter(Task.id == payload.parent_task_id).first()
    if parent is None:
        raise HTTPException(status_code=404, detail="parent_task 不存在")

    bug = Task(
        requirement_id=parent.requirement_id,
        parent_task_id=parent.id,
        title=payload.title,
        description=payload.description,
        type=TASK_TYPE_BUG,
        status=TASK_STATUS_DEV_DOING,
        severity=payload.severity,
        assignee_dev_id=parent.assignee_dev_id,
        created_by_id=payload.created_by_id,
        related_case_id=payload.related_case_id,
        task_metadata=payload.metadata,
    )
    db.session.add(bug)
    db.session.flush()
    db.session.refresh(bug)
    recompute_requirement_status(parent.requirement_id, db.session)
    return {"status": "success", "data": bug.to_dict()}


@router.get("")
def list_tasks(
    db: DBDep,
    requirement_id: Optional[int] = Query(None),
    assignee_dev_id: Optional[int] = Query(None),
    assignee_test_id: Optional[int] = Query(None),
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    created_by_id: Optional[int] = Query(None),
    closed_at_after: Optional[str] = Query(None),
    parent_task_id: Optional[int] = Query(None),
):
    query = db.session.query(Task)
    if requirement_id is not None:
        query = query.filter(Task.requirement_id == requirement_id)
    if assignee_dev_id is not None:
        query = query.filter(Task.assignee_dev_id == assignee_dev_id)
    if assignee_test_id is not None:
        query = query.filter(Task.assignee_test_id == assignee_test_id)
    if type:
        if type not in ALL_TASK_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"非法 type，可选: {sorted(ALL_TASK_TYPES)}",
            )
        query = query.filter(Task.type == type)
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        bad = [s for s in wanted if s not in ALL_TASK_STATUSES]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"非法 status: {bad}，可选: {sorted(ALL_TASK_STATUSES)}",
            )
        if wanted:
            query = query.filter(Task.status.in_(wanted))
    if created_by_id is not None:
        query = query.filter(Task.created_by_id == created_by_id)
    if closed_at_after:
        try:
            dt = datetime.fromisoformat(closed_at_after)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="closed_at_after 格式非法，需 ISO 8601",
            )
        query = query.filter(Task.closed_at >= dt)
    if parent_task_id is not None:
        query = query.filter(Task.parent_task_id == parent_task_id)

    rows = query.order_by(Task.created_at.desc()).all()
    return {"status": "success", "data": [t.to_dict() for t in rows]}


@router.get("/{task_id}")
def get_task(task_id: int, db: DBDep):
    task = db.session.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"status": "success", "data": task.to_dict()}


@router.put("/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, db: DBDep):
    task = db.session.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ALL_TASK_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 status，可选: {sorted(ALL_TASK_STATUSES)}",
        )
    if "severity" in data and data["severity"] is not None and data["severity"] not in ALL_BUG_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 severity，可选: {sorted(ALL_BUG_SEVERITIES)}",
        )

    old_status = task.status
    for k, v in data.items():
        if k == "metadata":
            task.task_metadata = v
        else:
            setattr(task, k, v)
    db.session.flush()

    if "status" in data and data["status"] != old_status:
        recompute_requirement_status(task.requirement_id, db.session)

    db.session.refresh(task)
    return {"status": "success", "data": task.to_dict()}


@router.delete("/{task_id}")
def delete_task(task_id: int, db: DBDep):
    task = db.session.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    requirement_id = task.requirement_id
    db.session.delete(task)
    db.session.flush()
    recompute_requirement_status(requirement_id, db.session)
    return {"status": "success", "message": "已删除"}
