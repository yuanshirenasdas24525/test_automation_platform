"""/api/ai/* —— AI 任务的统一 HTTP 入口。

所有 AI 调用都遵循同样的异步模式：
  POST /api/ai/<feature>      → 创建 ai_run + 派发 Celery + 返回 task_id
  GET  /api/ai/runs/<id>      → 查任务详情（含 status / output_payload）
  GET  /api/ai/runs           → 列任务（按项目 / feature / status 过滤）
  POST /api/ai/runs/<id>/cancel → 取消任务（revoke Celery）

每个 feature 自己定义 input_payload 形状（pydantic schema）。
"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from database.models import (
    AiRun,
    AI_RUN_STATUS_PENDING,
    AI_RUN_STATUS_CANCELLED,
    ALL_AI_RUN_STATUSES,
    AI_FEATURE_REQUIREMENT_PARSE,
)

router = APIRouter(prefix="/ai", tags=["ai"])


# ---------------------------------------------------------------------------
# 请求体 schema
# ---------------------------------------------------------------------------
class RequirementParseRequest(pydantic.BaseModel):
    """POST /api/ai/requirement_parse"""
    project_id: int
    text: str = pydantic.Field(
        ...,
        min_length=10,
        description="PRD / 需求文本，至少 10 个字符",
    )
    operator: Optional[str] = None  # 提交人，落 ai_runs.operator


# ---------------------------------------------------------------------------
# Feature 1: AI 需求分析
# ---------------------------------------------------------------------------
@router.post("/requirement_parse")
def submit_requirement_parse(payload: RequirementParseRequest, db: DBDep):
    """提交 AI 需求分析任务。

    异步：返回 ai_run_id，前端轮询 /api/ai/runs/{id} 拿结果。
    """
    from tasks.ai_tasks import dispatch_ai_task

    run = AiRun(
        feature=AI_FEATURE_REQUIREMENT_PARSE,
        status=AI_RUN_STATUS_PENDING,
        project_id=payload.project_id,
        input_payload={"text": payload.text},
        operator=payload.operator,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()  # 让 worker 能看到这条记录

    # 派发 Celery
    async_result = dispatch_ai_task.delay(run.id)

    # 反向更新 celery_task_id（虽然 dispatch_ai_task 自己也会更新，但提前记下来
    # 让前端拿 ai_run_id 时立刻就能拿到 celery_task_id 用于取消）
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "ai_run_id": run.id,
            "celery_task_id": async_result.id,
            "feature": run.feature,
        },
    }


# ---------------------------------------------------------------------------
# 通用查询 / 取消
# ---------------------------------------------------------------------------
@router.get("/runs/{run_id}")
def get_ai_run(run_id: int, db: DBDep):
    run = db.session.query(AiRun).filter(AiRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="ai_run 不存在")
    return {"status": "success", "data": run.to_dict()}


@router.get("/runs")
def list_ai_runs(
    db: DBDep,
    project_id: Optional[int] = Query(None),
    feature: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """列出 AI 任务历史，按 created_at 倒序。"""
    q = db.session.query(AiRun)
    if project_id is not None:
        q = q.filter(AiRun.project_id == project_id)
    if feature:
        q = q.filter(AiRun.feature == feature)
    if status:
        if status not in ALL_AI_RUN_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"非法 status，合法：{sorted(ALL_AI_RUN_STATUSES)}",
            )
        q = q.filter(AiRun.status == status)
    rows = q.order_by(AiRun.created_at.desc()).limit(limit).all()
    return {"status": "success", "data": [r.to_dict() for r in rows]}


@router.post("/runs/{run_id}/cancel")
def cancel_ai_run(run_id: int, db: DBDep):
    """尝试取消还没跑完的任务（revoke Celery + 改状态）。"""
    run = db.session.query(AiRun).filter(AiRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="ai_run 不存在")
    if run.status not in (AI_RUN_STATUS_PENDING, "running"):
        return {"status": "success", "message": "任务已结束，无需取消"}

    # revoke Celery 任务
    if run.celery_task_id:
        try:
            from celery_app import celery_app

            celery_app.control.revoke(run.celery_task_id, terminate=True)
        except Exception:
            # revoke 失败不阻塞 status 更新
            pass

    run.status = AI_RUN_STATUS_CANCELLED
    return {"status": "success", "message": "已取消"}
