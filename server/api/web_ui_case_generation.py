"""AI 生成 Web UI 自动化用例的任务、草稿评审与入库接口。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from database.models import (
    AiRun,
    Module,
    Project,
    UiAutomationCaseDraft,
    AI_FEATURE_WEB_UI_CASE_GEN,
    AI_RUN_STATUS_PENDING,
    ALL_UI_AUTO_DRAFT_STATUSES,
)
from database.schemas.ui_automation_case import (
    WebUiCaseDraftCommitRequest,
    WebUiCaseDraftRejectRequest,
    WebUiCaseDraftUpdate,
    WebUiCaseGenerationRequest,
)
from server.api.authz import assert_project_access
from server.api.deps import CurrentUserDep, DBDep
from server.services.ai_model_service import get_ai_model
from server.services.web_ui_case_generation_service import commit_drafts, reject_draft


router = APIRouter(prefix="/ai/web-ui-cases", tags=["ai-web-ui-cases"])


def _draft_or_404(db: DBDep, draft_id: int) -> UiAutomationCaseDraft:
    draft = db.session.query(UiAutomationCaseDraft).filter(UiAutomationCaseDraft.id == draft_id).one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="Web UI 用例草稿不存在")
    return draft


@router.post("/generate")
def generate_web_ui_cases(
    payload: WebUiCaseGenerationRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """创建异步生成任务；生成结果先进入草稿，不直接写正式用例。"""
    project = db.session.query(Project).filter(Project.id == payload.project_id).one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_project_access(db, current_user, project.id)
    cfg = get_ai_model(db.session, payload.model_name, project_id=project.id)
    if cfg is None or not cfg.enabled:
        raise HTTPException(status_code=400, detail=f"AI 模型 {payload.model_name!r} 不存在或未启用")
    target_module = (
        db.session.query(Module)
        .filter(
            Module.id == payload.target_module_id,
            Module.project_id == project.id,
        )
        .one_or_none()
    )
    if target_module is None:
        raise HTTPException(status_code=404, detail="当前用例模块不存在或不属于该项目")

    batch_id = uuid.uuid4().hex
    run = AiRun(
        feature=AI_FEATURE_WEB_UI_CASE_GEN,
        status=AI_RUN_STATUS_PENDING,
        project_id=project.id,
        input_payload={
            **payload.model_dump(),
            "batch_id": batch_id,
            "created_by_id": current_user.id,
        },
        operator=current_user.username,
    )
    db.session.add(run)
    db.session.flush()
    # Celery Worker 必须能立刻查询到 AiRun；这是异步派发场景下必要的事务边界。
    db.commit()
    from tasks.ai_tasks import dispatch_ai_task

    result = dispatch_ai_task.delay(run.id)
    run.celery_task_id = result.id
    db.commit()
    return {
        "status": "success",
        "data": {
            "ai_run_id": run.id,
            "batch_id": batch_id,
            "celery_task_id": result.id,
        },
    }


@router.get("/drafts")
def list_web_ui_case_drafts(
    project_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    batch_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=20),
):
    assert_project_access(db, current_user, project_id)
    if status and status not in ALL_UI_AUTO_DRAFT_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法草稿状态：{status}")
    query = db.session.query(UiAutomationCaseDraft).filter(UiAutomationCaseDraft.project_id == project_id)
    if batch_id:
        query = query.filter(UiAutomationCaseDraft.batch_id == batch_id)
    if status:
        query = query.filter(UiAutomationCaseDraft.status == status)
    rows = query.order_by(UiAutomationCaseDraft.created_at.desc(), UiAutomationCaseDraft.id.desc()).limit(200).all()
    return {"status": "success", "data": [item.to_dict() for item in rows]}


@router.patch("/drafts/{draft_id}")
def update_web_ui_case_draft(
    draft_id: int,
    payload: WebUiCaseDraftUpdate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    draft = _draft_or_404(db, draft_id)
    assert_project_access(db, current_user, draft.project_id)
    if draft.status != "pending":
        raise HTTPException(status_code=409, detail="只有待评审草稿可以修改")
    patch = payload.model_dump(exclude_unset=True)
    if "steps" in patch:
        from server.services.web_ui_case_generation_service import (
            validate_draft_step_edit,
            validate_draft_steps,
        )

        errors = validate_draft_steps(
            patch["steps"],
            allow_manual=bool(draft.manual_reasons),
        )
        if errors:
            raise HTTPException(status_code=422, detail="；".join(errors[:10]))
        edit_errors = validate_draft_step_edit(draft.steps, patch["steps"])
        if edit_errors:
            raise HTTPException(status_code=422, detail="；".join(edit_errors[:10]))
    for key, value in patch.items():
        setattr(draft, key, value)
    db.session.flush()
    return {"status": "success", "data": draft.to_dict()}


@router.post("/drafts/{draft_id}/reject")
def reject_web_ui_case_draft(
    draft_id: int,
    payload: WebUiCaseDraftRejectRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    draft = _draft_or_404(db, draft_id)
    assert_project_access(db, current_user, draft.project_id)
    try:
        reject_draft(db.session, draft, payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "success", "data": draft.to_dict()}


@router.post("/drafts/commit")
def commit_web_ui_case_drafts(
    payload: WebUiCaseDraftCommitRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    module = db.session.query(Module).filter(Module.id == payload.module_id).one_or_none()
    if module is None:
        raise HTTPException(status_code=404, detail="目标模块不存在")
    assert_project_access(db, current_user, module.project_id)
    drafts = db.session.query(UiAutomationCaseDraft).filter(UiAutomationCaseDraft.id.in_(payload.draft_ids)).all()
    if drafts:
        for project_id in {item.project_id for item in drafts}:
            assert_project_access(db, current_user, project_id)
    try:
        created, skipped = commit_drafts(db.session, draft_ids=payload.draft_ids, module_id=payload.module_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": {"created_case_ids": created, "skipped": skipped}}
