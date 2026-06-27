"""
/api/tasks/* —— 全局任务看板 API。

GET /api/tasks/in-progress  聚合所有进行中的异步任务（AI + 执行 + 系统）。
                           可选 project_id / limit 过滤。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep
from server.services.task_registry import task_registry

# 触发任务模块的注册：FastAPI 进程不跑 Celery worker，不会自动 import tasks/。
# 这里显式 import 一下子模块，让 module-level 的 task_registry.register(...) 执行。
import tasks.ai_tasks  # noqa: F401
import tasks.run_test_task  # noqa: F401

router = APIRouter(prefix="/tasks-overview", tags=["tasks_overview"])


@router.get("/in-progress")
def get_in_progress_tasks(
    db: DBDep,
    project_id: Optional[int] = Query(None, description="按项目过滤"),
    limit: int = Query(50, ge=1, le=200, description="最大返回条数"),
):
    tasks = task_registry.get_all_in_progress(
        db.session,
        project_id=project_id,
        limit=limit,
    )
    return {"status": "success", "data": tasks}


@router.post("/{type_key}/{task_id}/cancel")
def cancel_in_progress_task(type_key: str, task_id: int, db: DBDep):
    """终止任务看板里的任务。

    当前只有 AI 任务能可靠终止，因为 ai_runs 表保存了 Celery task id。
    测试执行任务暂未保存 Celery task id，先拒绝，避免给用户造成"已终止"的假象。
    """
    if type_key.startswith("ai_"):
        from celery_app import celery_app
        from database.models import (
            AI_RUN_STATUS_CANCELLED,
            AI_RUN_STATUS_PENDING,
            AI_RUN_STATUS_RUNNING,
            AiRun,
        )

        run = db.session.query(AiRun).filter(AiRun.id == task_id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="AI 任务不存在")
        if run.status not in (AI_RUN_STATUS_PENDING, AI_RUN_STATUS_RUNNING):
            return {"status": "success", "message": "任务已结束，无需终止"}

        if run.celery_task_id:
            try:
                celery_app.control.revoke(run.celery_task_id, terminate=True)
            except Exception:
                # revoke 失败不阻塞状态更新，worker 侧若已结束也会自然收敛。
                pass

        run.status = AI_RUN_STATUS_CANCELLED
        run.error = "用户从任务列表终止"
        run.ended_at = datetime.now()
        return {"status": "success", "message": "已终止 AI 任务"}

    if type_key.startswith("test_run_"):
        raise HTTPException(
            status_code=409,
            detail="测试执行任务暂不支持从任务列表终止：当前报告表未保存 Celery task id",
        )

    raise HTTPException(status_code=400, detail="该任务类型暂不支持终止")
