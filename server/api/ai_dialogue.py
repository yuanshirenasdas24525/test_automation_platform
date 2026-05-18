"""/api/ai/dialogue/* —— AI Studio M1 "对话写需求" 的 HTTP 入口。

Endpoint 速览：
  POST   /api/ai/dialogue/sessions                 起 session + 立刻跑第一句
  POST   /api/ai/dialogue/sessions/{id}/turns      用户发一句话
  POST   /api/ai/dialogue/sessions/{id}/finalize   "我说完了"，收口出草稿
  POST   /api/ai/dialogue/sessions/{id}/abandon    用户主动放弃
  GET    /api/ai/dialogue/sessions/{id}            拿 session 全状态（含 drafts）
  GET    /api/ai/dialogue/sessions                 列我的 session（按 project 过滤）

模式：所有 POST 都是"立刻入 ai_run(pending) + .delay → 返回 ai_run_id"，
前端用 ``GET /api/ai/runs/{id}`` 轮询拿结果（沿用 ai.py 的现成接口）。

为啥 sessions/{id}/turns 不阻塞返回 assistant 文本？
  - 让 LLM 延迟（5-30s）阻塞 HTTP 连接会拉长 keep-alive，且 nginx 默认 30s 会断
  - 前端轮询 ai_run 状态已经是 M6/M7 既有模式，复用成本低
"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep, CurrentUserDep
from database.models import (
    AiDialogueSession,
    AiRequirementDraft,
    AiRun,
    AI_RUN_STATUS_PENDING,
    AI_DIALOGUE_STATUS_ACTIVE,
    AI_DIALOGUE_STATUS_ABANDONED,
    Project,
)

router = APIRouter(prefix="/ai/dialogue", tags=["ai-dialogue"])


FEATURE_DIALOGUE_TURN = "ai_studio_dialogue_turn"
FEATURE_FINALIZE = "ai_studio_finalize"


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class CreateSessionRequest(pydantic.BaseModel):
    project_id: int
    initial_message: str = pydantic.Field(..., min_length=1, max_length=4000)
    model_name: Optional[str] = None


class TurnRequest(pydantic.BaseModel):
    message: str = pydantic.Field(..., min_length=1, max_length=4000)


# ---------------------------------------------------------------------------
# 创建 session + 跑第一句
# ---------------------------------------------------------------------------
@router.post("/sessions")
def create_session(
    payload: CreateSessionRequest,
    db: DBDep,
    user: CurrentUserDep,
):
    """起一个新的对话 session 并立刻派发第一轮（用户输入 initial_message）。

    返回 ``{session_id, ai_run_id}``；前端拿 ai_run_id 轮询第一句的 assistant 回复。
    """
    from tasks.ai_dialogue_task import run_dialogue_turn_task

    proj = db.session.query(Project).filter(Project.id == payload.project_id).first()
    if proj is None:
        raise HTTPException(status_code=404, detail=f"项目 {payload.project_id} 不存在")

    ds = AiDialogueSession(
        project_id=payload.project_id,
        user_id=user.id,
        status=AI_DIALOGUE_STATUS_ACTIVE,
        turns=[],
        coverage={},
        model_name=payload.model_name,
    )
    db.session.add(ds)
    db.session.flush()
    db.session.refresh(ds)

    # 先建 AiRun，用 session.id + initial_message 当 payload
    run = AiRun(
        feature=FEATURE_DIALOGUE_TURN,
        status=AI_RUN_STATUS_PENDING,
        project_id=payload.project_id,
        input_payload={
            "session_id": ds.id,
            "user_message": payload.initial_message.strip(),
            "turns_count_before": 0,
        },
        operator=user.username if hasattr(user, "username") else None,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    # 必须 commit —— EAGER 模式下 task 启动时会直接 query AiRun
    db.commit()

    async_result = run_dialogue_turn_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "session_id": ds.id,
            "ai_run_id": run.id,
            "celery_task_id": async_result.id,
        },
    }


# ---------------------------------------------------------------------------
# 后续 turn
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/turns")
def post_turn(
    session_id: int,
    payload: TurnRequest,
    db: DBDep,
    user: CurrentUserDep,
):
    """在已存在的 session 上发一句话。"""
    from tasks.ai_dialogue_task import run_dialogue_turn_task

    ds = _get_session_or_404(db, session_id)
    if ds.status != AI_DIALOGUE_STATUS_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=f"session 已 {ds.status}，不能继续对话",
        )

    turns_count_before = len(ds.turns or [])
    run = AiRun(
        feature=FEATURE_DIALOGUE_TURN,
        status=AI_RUN_STATUS_PENDING,
        project_id=ds.project_id,
        input_payload={
            "session_id": ds.id,
            "user_message": payload.message.strip(),
            "turns_count_before": turns_count_before,
        },
        operator=user.username if hasattr(user, "username") else None,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    async_result = run_dialogue_turn_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "session_id": ds.id,
            "ai_run_id": run.id,
            "celery_task_id": async_result.id,
        },
    }


# ---------------------------------------------------------------------------
# finalize（"我说完了"）
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/finalize")
def finalize_session(
    session_id: int,
    db: DBDep,
    user: CurrentUserDep,
):
    """用户点"我说完了"，触发模型基于完整 turns 产出 markdown + spec。"""
    from tasks.ai_dialogue_task import finalize_dialogue_task

    ds = _get_session_or_404(db, session_id)
    if ds.status != AI_DIALOGUE_STATUS_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=f"session 已 {ds.status}，不能 finalize",
        )
    if not (ds.turns or []):
        raise HTTPException(status_code=400, detail="对话尚无内容，无法 finalize")

    run = AiRun(
        feature=FEATURE_FINALIZE,
        status=AI_RUN_STATUS_PENDING,
        project_id=ds.project_id,
        input_payload={
            "session_id": ds.id,
            "turns_count": len(ds.turns or []),
        },
        operator=user.username if hasattr(user, "username") else None,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    async_result = finalize_dialogue_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "session_id": ds.id,
            "ai_run_id": run.id,
            "celery_task_id": async_result.id,
        },
    }


# ---------------------------------------------------------------------------
# 主动放弃
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/abandon")
def abandon_session(session_id: int, db: DBDep, user: CurrentUserDep):
    """用户放弃当前 session（不影响已落 draft / requirement）。"""
    ds = _get_session_or_404(db, session_id)
    if ds.status == AI_DIALOGUE_STATUS_ACTIVE:
        ds.status = AI_DIALOGUE_STATUS_ABANDONED
    db.commit()
    return {"status": "success", "data": {"session_id": ds.id, "status": ds.status}}


# ---------------------------------------------------------------------------
# 查询：单个 session（含关联 drafts）
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}")
def get_session(session_id: int, db: DBDep):
    """拿 session 全状态，含 turns / coverage / 所有 draft。"""
    ds = _get_session_or_404(db, session_id)
    drafts = (
        db.session.query(AiRequirementDraft)
        .filter(AiRequirementDraft.session_id == ds.id)
        .order_by(AiRequirementDraft.created_at.desc())
        .all()
    )
    return {
        "status": "success",
        "data": {
            **ds.to_dict(),
            "drafts": [d.to_dict() for d in drafts],
        },
    }


# ---------------------------------------------------------------------------
# 查询：list
# ---------------------------------------------------------------------------
@router.get("/sessions")
def list_sessions(
    db: DBDep,
    user: CurrentUserDep,
    project_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    mine_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
):
    """列我的 session（默认仅当前用户；可选按 project_id / status 过滤）。"""
    q = db.session.query(AiDialogueSession)
    if mine_only:
        q = q.filter(AiDialogueSession.user_id == user.id)
    if project_id is not None:
        q = q.filter(AiDialogueSession.project_id == project_id)
    if status_filter:
        q = q.filter(AiDialogueSession.status == status_filter)

    rows = q.order_by(AiDialogueSession.updated_at.desc()).limit(limit).all()
    return {
        "status": "success",
        "data": [r.to_dict() for r in rows],
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_session_or_404(db, session_id: int) -> AiDialogueSession:
    ds = db.session.query(AiDialogueSession).filter(
        AiDialogueSession.id == session_id
    ).first()
    if ds is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在")
    return ds
