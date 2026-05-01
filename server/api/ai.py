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
    AI_FEATURE_TEST_PLAN,
)

router = APIRouter(prefix="/ai", tags=["ai"])


# ---------------------------------------------------------------------------
# 请求体 schema
# ---------------------------------------------------------------------------
class RequirementParseRequest(pydantic.BaseModel):
    """POST /api/ai/requirement_parse"""
    project_id: int
    text: Optional[str] = None
    file_path: Optional[str] = None
    analysis_mode: Optional[str] = "standard"
    operator: Optional[str] = None


class TestPlanGenRequest(pydantic.BaseModel):
    """POST /api/ai/test_plan"""
    project_id: int
    requirement_ids: Optional[list[int]] = None
    module_ids: Optional[list[int]] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    resource_notes: Optional[str] = None
    analysis_mode: Optional[str] = "standard"
    operator: Optional[str] = None


class ProjectContextCreateRequest(pydantic.BaseModel):
    """POST /api/ai/project_contexts — 手动添加上下文"""
    project_id: int
    context_type: str = pydantic.Field(..., min_length=2, max_length=50)
    title: str = pydantic.Field(..., min_length=1, max_length=255)
    content: str = pydantic.Field(..., min_length=10)
    summary: Optional[str] = None
    module_id: Optional[int] = None
    tags: Optional[list[str]] = None
    keywords: Optional[list[str]] = None
    importance: Optional[int] = 3


# ---------------------------------------------------------------------------
# Feature 1: AI 需求分析（V2 增强版）
# ---------------------------------------------------------------------------
@router.post("/requirement_parse")
def submit_requirement_parse(payload: RequirementParseRequest, db: DBDep):
    """提交 AI 需求分析任务（V2）。

    支持：
      - 文本粘贴（text 字段）或文档上传（file_path 字段）
      - 分析模式选择（quick / standard / deep / multi_model）
      - 项目上下文自动检索和注入

    异步：返回 ai_run_id，前端轮询 /api/ai/runs/{id} 拿结果。
    """
    from tasks.ai_tasks import dispatch_ai_task

    text = (payload.text or "").strip()
    file_path = (payload.file_path or "").strip()

    if not text and not file_path:
        raise HTTPException(
            status_code=400,
            detail="请提供 text（文本内容）或 file_path（文件路径），至少提供一个",
        )

    if text and file_path:
        raise HTTPException(
            status_code=400,
            detail="text 和 file_path 不能同时提供，请二选一",
        )

    analysis_mode = (payload.analysis_mode or "standard").strip()
    if analysis_mode not in ("quick", "standard", "deep", "multi_model"):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 analysis_mode: {analysis_mode!r}（合法：quick / standard / deep / multi_model）",
        )

    input_payload = {
        "analysis_mode": analysis_mode,
    }
    if text:
        input_payload["text"] = text
    if file_path:
        input_payload["file_path"] = file_path

    run = AiRun(
        feature=AI_FEATURE_REQUIREMENT_PARSE,
        status=AI_RUN_STATUS_PENDING,
        project_id=payload.project_id,
        input_payload=input_payload,
        operator=payload.operator,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    async_result = dispatch_ai_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "ai_run_id": run.id,
            "celery_task_id": async_result.id,
            "feature": run.feature,
            "analysis_mode": analysis_mode,
        },
    }


# ---------------------------------------------------------------------------
# Feature 2: AI 生成测试计划
# ---------------------------------------------------------------------------
@router.post("/test_plan")
def submit_test_plan_gen(payload: TestPlanGenRequest, db: DBDep):
    """提交"AI 生成测试计划"任务。"""
    from tasks.ai_tasks import dispatch_ai_task

    run = AiRun(
        feature=AI_FEATURE_TEST_PLAN,
        status=AI_RUN_STATUS_PENDING,
        project_id=payload.project_id,
        input_payload={
            "requirement_ids": payload.requirement_ids or [],
            "module_ids": payload.module_ids or [],
            "time_start": payload.time_start,
            "time_end": payload.time_end,
            "resource_notes": payload.resource_notes,
            "analysis_mode": payload.analysis_mode or "standard",
        },
        operator=payload.operator,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    async_result = dispatch_ai_task.delay(run.id)
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
# 项目上下文（Project Context）管理
# ---------------------------------------------------------------------------
@router.get("/project_contexts")
def list_project_contexts(
    db: DBDep,
    project_id: int = Query(...),
    context_type: Optional[str] = Query(None),
    module_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """列出项目的所有上下文片段，支持按类型/模块过滤。"""
    from database.models import ProjectContext
    from server.services.context_service import get_context_stats

    q = db.session.query(ProjectContext).filter(ProjectContext.project_id == project_id)
    if context_type:
        q = q.filter(ProjectContext.context_type == context_type)
    if module_id is not None:
        q = q.filter(ProjectContext.module_id == module_id)

    rows = q.order_by(ProjectContext.importance.desc(), ProjectContext.created_at.desc()).limit(limit).all()

    return {
        "status": "success",
        "data": {
            "contexts": [r.to_dict() for r in rows],
            "stats": get_context_stats(project_id),
        },
    }


@router.post("/project_contexts")
def create_project_context(payload: ProjectContextCreateRequest, db: DBDep):
    """手动添加一条项目上下文。"""
    from database.models import ProjectContext

    ctx = ProjectContext(
        project_id=payload.project_id,
        module_id=payload.module_id,
        source_type="manual",
        context_type=payload.context_type,
        title=payload.title[:255],
        content=payload.content,
        summary=payload.summary,
        tags=payload.tags or [],
        keywords=payload.keywords or [],
        importance=payload.importance or 3,
    )
    db.session.add(ctx)
    db.session.flush()
    db.session.refresh(ctx)
    db.commit()

    return {"status": "success", "data": ctx.to_dict()}


@router.put("/project_contexts/{context_id}")
def update_project_context(context_id: int, payload: ProjectContextCreateRequest, db: DBDep):
    """更新一条项目上下文。"""
    from database.models import ProjectContext

    ctx = db.session.query(ProjectContext).filter(ProjectContext.id == context_id).first()
    if ctx is None:
        raise HTTPException(status_code=404, detail="上下文条目不存在")

    ctx.context_type = payload.context_type or ctx.context_type
    ctx.title = payload.title[:255]
    ctx.content = payload.content
    ctx.summary = payload.summary
    ctx.module_id = payload.module_id
    ctx.tags = payload.tags or []
    ctx.keywords = payload.keywords or []
    ctx.importance = payload.importance or 3
    db.commit()

    return {"status": "success", "data": ctx.to_dict()}


@router.delete("/project_contexts/{context_id}")
def delete_project_context(context_id: int, db: DBDep):
    """删除一条项目上下文。"""
    from database.models import ProjectContext

    ctx = db.session.query(ProjectContext).filter(ProjectContext.id == context_id).first()
    if ctx is None:
        raise HTTPException(status_code=404, detail="上下文条目不存在")
    db.session.delete(ctx)
    db.commit()
    return {"status": "success", "message": "已删除"}


@router.get("/project_contexts/stats")
def get_project_context_stats(
    db: DBDep,
    project_id: int = Query(...),
):
    """获取项目上下文的统计信息。"""
    from server.services.context_service import get_context_stats

    return {"status": "success", "data": get_context_stats(project_id)}


# ---------------------------------------------------------------------------
# 分析记录查询
# ---------------------------------------------------------------------------
@router.get("/analyses")
def list_analyses(
    db: DBDep,
    project_id: int = Query(...),
    limit: int = Query(20, ge=1, le=200),
):
    """列出项目的 AI 分析记录。"""
    from database.models import RequirementAnalysis

    rows = (
        db.session.query(RequirementAnalysis)
        .filter(RequirementAnalysis.project_id == project_id)
        .order_by(RequirementAnalysis.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"status": "success", "data": [r.to_dict() for r in rows]}


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
