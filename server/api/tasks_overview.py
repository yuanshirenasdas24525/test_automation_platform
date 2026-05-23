"""
/api/tasks/* —— 全局任务看板 API。

GET /api/tasks/in-progress  聚合所有进行中的异步任务（AI + 执行 + 系统）。
                           可选 project_id / limit 过滤。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

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
