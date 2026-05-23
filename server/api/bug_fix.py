"""/api/tasks/{id}/ai-fix —— AI 一键修复 Bug。

端点：
  POST   /api/tasks/{task_id}/ai-fix           → 触发修复
  POST   /api/tasks/{task_id}/ai-fix/rollback  → 回滚修复
  GET    /api/ai/bug-fix-agents                → 获取可用智能体列表
"""
from __future__ import annotations

import pydantic
from fastapi import APIRouter, HTTPException

from server.api.deps import DBDep
from server.services.bug_fix_service import (
    rollback_bug_fix,
    get_available_agents,
)
from database.models import (
    Task,
    AiRun,
    TASK_TYPE_BUG,
    AI_FEATURE_BUG_FIX,
    AI_RUN_STATUS_PENDING,
)

router = APIRouter(tags=["ai-bug-fix"])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
class BugFixRequest(pydantic.BaseModel):
    agent_name: str = "opencode"


class BugFixRollbackRequest(pydantic.BaseModel):
    ai_run_id: int | None = None


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.post("/tasks/{task_id}/ai-fix")
def ai_fix_bug(task_id: int, payload: BugFixRequest, db: DBDep):
    """触发 AI 一键修复 Bug。

    异步：返回 ai_run_id，前端轮询 GET /api/ai/runs/{id} 拿结果。
    """
    from tasks.bug_fix_task import run_bug_fix_task

    task = db.session.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.type != TASK_TYPE_BUG:
        raise HTTPException(status_code=400, detail="仅 Bug 类型支持 AI 修复")

    # 检查智能体
    agents = get_available_agents()
    agent_config = next((a for a in agents if a.name == payload.agent_name), None)
    if agent_config is None:
        available_names = [a.name for a in agents]
        raise HTTPException(
            status_code=400,
            detail=f"智能体 {payload.agent_name!r} 不存在，可选：{available_names}",
        )
    if not agent_config.available:
        raise HTTPException(
            status_code=400,
            detail=f"智能体 {payload.agent_name!r} 不可用（CLI 工具未检测到）",
        )

    # 拿项目信息
    project_id: int | None = None
    git_url: str | None = None
    if task.requirement:
        project_id = task.requirement.project_id
        project = task.requirement.project
        if project and getattr(project, "git_url", None):
            git_url = project.git_url

    # 创建 AiRun + 派发 Celery（与 server/api/ai.py 相同模式）
    run = AiRun(
        feature=AI_FEATURE_BUG_FIX,
        status=AI_RUN_STATUS_PENDING,
        project_id=project_id,
        input_payload={
            "bug_id": task.id,
            "bug_title": task.title,
            "agent_name": payload.agent_name,
        },
        operator=None,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    async_result = run_bug_fix_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "ai_run_id": run.id,
            "celery_task_id": async_result.id,
            "agent_name": payload.agent_name,
            "has_git": bool(git_url),
        },
    }


@router.post("/tasks/{task_id}/ai-fix/rollback")
def ai_fix_rollback(task_id: int, payload: BugFixRollbackRequest, db: DBDep):
    """回滚 AI 修复（删远程分支 + 恢复 Bug 状态）。"""
    task = db.session.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.fix_commit_branch:
        raise HTTPException(status_code=400, detail="该 Bug 没有关联的修复分支，无法回滚")

    git_url: str | None = None
    if task.requirement:
        project = task.requirement.project if task.requirement else None
        if project and getattr(project, "git_url", None):
            git_url = project.git_url

    result = rollback_bug_fix(task, git_url)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["message"])

    return {"status": "success", "data": result}


@router.get("/ai/bug-fix-agents")
def list_bug_fix_agents():
    """获取可用智能体列表（含 CLI 可用性检测结果）。"""
    agents = get_available_agents()
    return {
        "status": "success",
        "data": {
            "agents": [
                {
                    "name": a.name,
                    "agent_type": a.agent_type,
                    "available": a.available,
                    "label": a.label,
                    "description": a.description,
                }
                for a in agents
            ],
        },
    }
