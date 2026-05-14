"""/api/ai/case-generation 与 /api/ai/case-drafts/* —— M7 AI 一键生成测试用例。

设计：
  - POST /api/ai/case-generation
        触发：requirement_ids[] × model_names[] → 每对 (req, model) 一个 batch
        每个 batch 对应一个 AiRun（feature=functional_case_gen），handler 见
        tasks/ai_tasks.py::_handle_functional_case_gen
  - GET    /api/ai/case-drafts            list（支持按 requirement_id / batch_id / status 过滤）
  - GET    /api/ai/case-drafts/{id}       单条详情
  - PUT    /api/ai/case-drafts/{id}       内联编辑（仅 pending 可改）
  - DELETE /api/ai/case-drafts/{id}       逻辑删（status -> rejected）
  - POST   /api/ai/case-drafts/commit     批量入库：勾选若干 draft → 写 test_cases
  - GET    /api/ai/case-generation/runs/{id}  查任务进度（细化：把 batch_id 一起带出来）

batch_id 生成策略：每对 (requirement, model) 一个 uuid4。前端 ReviewDialog 用它
分组渲染。
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from database.models import (
    AiCaseDraft,
    AiRun,
    AI_FEATURE_FUNCTIONAL_CASE_GEN,
    AI_RUN_STATUS_PENDING,
    ALL_AI_CASE_DRAFT_STATUSES,
    Requirement,
)
from database.schemas.ai_case_draft import (
    ALL_SCENARIO_MIXES,
    AiCaseDraftUpdate,
    CaseGenerationTriggerRequest,
    CommitDraftsRequest,
)
from server.api.deps import CurrentUserDep, DBDep
from server.services.ai_case_draft_service import (
    batch_commit,
    get_draft,
    list_drafts,
    reject_draft,
    update_draft,
)
from server.services.ai_model_service import get_ai_model


router = APIRouter(prefix="/ai", tags=["ai-case-generation"])


# ---------------------------------------------------------------------------
# 触发：requirement × model 双重笛卡尔积，每对一个 batch
# ---------------------------------------------------------------------------
@router.post("/case-generation")
def trigger_case_generation(
    payload: CaseGenerationTriggerRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    if payload.scenario_mix not in ALL_SCENARIO_MIXES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 scenario_mix: {payload.scenario_mix!r}",
        )

    # 校验需求都存在 + 同项目
    reqs = (
        db.session.query(Requirement)
        .filter(Requirement.id.in_(payload.requirement_ids))
        .all()
    )
    by_id = {r.id: r for r in reqs}
    missing = [rid for rid in payload.requirement_ids if rid not in by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"需求不存在：{missing}",
        )

    # 校验模型都启用
    model_configs = []
    for name in payload.model_names:
        cfg = get_ai_model(db.session, name)
        if cfg is None:
            raise HTTPException(status_code=400, detail=f"模型 {name!r} 不存在")
        if not cfg.enabled:
            raise HTTPException(status_code=400, detail=f"模型 {name!r} 未启用")
        model_configs.append(cfg)

    batches: list[dict] = []
    for rid in payload.requirement_ids:
        req = by_id[rid]
        for cfg in model_configs:
            batch_id = uuid.uuid4().hex
            run = AiRun(
                feature=AI_FEATURE_FUNCTIONAL_CASE_GEN,
                status=AI_RUN_STATUS_PENDING,
                project_id=req.project_id,
                input_payload={
                    "requirement_id": rid,
                    "model_name": cfg.name,
                    "batch_id": batch_id,
                    "analysis_document_id": payload.analysis_document_id,
                    "ui_image_attachment_ids": list(payload.ui_image_attachment_ids or []),
                    "count": payload.count_per_requirement,
                    "scenario_mix": payload.scenario_mix,
                    "user_prompt": payload.user_prompt or "",
                    "created_by_id": current_user.id,
                },
                operator=current_user.username,
            )
            db.session.add(run)
            db.session.flush()

            # 延迟 import，避免 celery_app 与 server.main 互相依赖
            from tasks.ai_tasks import dispatch_ai_task

            async_result = dispatch_ai_task.delay(run.id)
            run.celery_task_id = async_result.id

            batches.append(
                {
                    "batch_id": batch_id,
                    "requirement_id": rid,
                    "run_id": run.id,
                    "model_name": cfg.name,
                    "model_label": f"{cfg.provider} / {cfg.model}",
                }
            )

    return {"status": "success", "data": {"batches": batches}}


# ---------------------------------------------------------------------------
# 草稿列表 / 详情 / 编辑 / 逻辑删
# ---------------------------------------------------------------------------
@router.get("/case-drafts")
def api_list_drafts(
    db: DBDep,
    requirement_id: Optional[int] = Query(None),
    batch_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    if status and status not in ALL_AI_CASE_DRAFT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 status: {status!r}（合法：{sorted(ALL_AI_CASE_DRAFT_STATUSES)}）",
        )
    rows = list_drafts(
        db.session,
        requirement_id=requirement_id,
        batch_id=batch_id,
        status=status,
    )
    return {"status": "success", "data": [r.to_dict() for r in rows]}


@router.get("/case-drafts/{draft_id}")
def api_get_draft(draft_id: int, db: DBDep):
    draft = get_draft(db.session, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"草稿 #{draft_id} 不存在")
    return {"status": "success", "data": draft.to_dict()}


@router.put("/case-drafts/{draft_id}")
def api_update_draft(draft_id: int, body: AiCaseDraftUpdate, db: DBDep):
    patch = body.model_dump(exclude_unset=True, exclude_none=False)
    try:
        draft = update_draft(db.session, draft_id, patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "data": draft.to_dict()}


@router.delete("/case-drafts/{draft_id}")
def api_reject_draft(draft_id: int, db: DBDep):
    try:
        draft = reject_draft(db.session, draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "data": draft.to_dict()}


# ---------------------------------------------------------------------------
# 批量入库
# ---------------------------------------------------------------------------
@router.post("/case-drafts/commit")
def api_commit_drafts(body: CommitDraftsRequest, db: DBDep):
    try:
        created_ids, skipped = batch_commit(
            db.session,
            draft_ids=list(body.draft_ids),
            target_module_id=body.target_module_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "status": "success",
        "data": {
            "created_case_ids": created_ids,
            "skipped": skipped,
        },
    }


# ---------------------------------------------------------------------------
# 任务进度（在通用 /ai/runs/{id} 之上加 batch_id 简便字段）
# ---------------------------------------------------------------------------
@router.get("/case-generation/runs/{run_id}")
def api_get_case_gen_run(run_id: int, db: DBDep):
    run = db.session.query(AiRun).filter(AiRun.id == run_id).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"ai_run #{run_id} 不存在")
    if run.feature != AI_FEATURE_FUNCTIONAL_CASE_GEN:
        raise HTTPException(
            status_code=400,
            detail=f"ai_run #{run_id} 不是 functional_case_gen 任务（feature={run.feature}）",
        )
    data = run.to_dict()
    # 平铺出 batch_id / requirement_id 给前端轮询时省一层
    payload = run.input_payload or {}
    data["batch_id"] = payload.get("batch_id")
    data["requirement_id"] = payload.get("requirement_id")
    data["model_name"] = payload.get("model_name")
    return {"status": "success", "data": data}
