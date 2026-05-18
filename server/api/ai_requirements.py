"""/api/ai/requirement-drafts/* —— AI 对话产出的"需求草稿"管理。

Endpoint：
  GET    /api/ai/requirement-drafts/{id}            拿单份草稿
  PUT    /api/ai/requirement-drafts/{id}            PM 手工编辑 markdown / spec
  POST   /api/ai/requirement-drafts/{id}/commit     入库到 requirements 表
  POST   /api/ai/requirement-drafts/{id}/reject     弃用
  GET    /api/ai/requirement-drafts                 列草稿（按 project / status / session 过滤）

Commit 语义：
  - 新建一行 ``requirements``，title / description / acceptance_criteria / priority 从 spec_json 拆出来
  - 新需求 ``source=ai_generated`` + ``source_dialogue_session_id`` 关联回对话 session
  - draft 自身回填 ``requirement_id`` + 标 ``status=committed``
  - 一份草稿只能 commit 一次；重复 commit 返回 409
"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func as sa_func

from server.api.deps import DBDep, CurrentUserDep
from database.models import (
    AiRequirementDraft,
    AiDialogueSession,
    Requirement,
    AI_REQ_DRAFT_STATUS_PENDING,
    AI_REQ_DRAFT_STATUS_COMMITTED,
    AI_REQ_DRAFT_STATUS_REJECTED,
    REQUIREMENT_STATUS_DRAFT,
    REQUIREMENT_SOURCE_AI,
)

router = APIRouter(prefix="/ai/requirement-drafts", tags=["ai-requirement-drafts"])


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------
class DraftUpdateRequest(pydantic.BaseModel):
    markdown: Optional[str] = None
    spec_json: Optional[dict] = None


class DraftCommitRequest(pydantic.BaseModel):
    """commit 时允许 PM 临时覆盖一些字段（不修改 draft 自身 spec_json）。"""
    title_override: Optional[str] = None
    module_id: Optional[int] = None
    version_id: Optional[int] = None
    parent_requirement_id: Optional[int] = None


# ---------------------------------------------------------------------------
# GET 单份
# ---------------------------------------------------------------------------
@router.get("/{draft_id}")
def get_draft(draft_id: int, db: DBDep):
    """拿一份草稿（含关联 session 简要信息）。"""
    draft = _get_draft_or_404(db, draft_id)
    session_row = (
        db.session.query(AiDialogueSession)
        .filter(AiDialogueSession.id == draft.session_id)
        .first()
    )
    data = draft.to_dict()
    if session_row is not None:
        data["session"] = {
            "id": session_row.id,
            "project_id": session_row.project_id,
            "status": session_row.status,
            "coverage": session_row.coverage or {},
            "turns_count": len(session_row.turns or []),
        }
    return {"status": "success", "data": data}


# ---------------------------------------------------------------------------
# PUT 编辑
# ---------------------------------------------------------------------------
@router.put("/{draft_id}")
def update_draft(draft_id: int, payload: DraftUpdateRequest, db: DBDep):
    """PM 手工调整 markdown 或 spec_json。已 commit 的 draft 不能再改。"""
    draft = _get_draft_or_404(db, draft_id)
    if draft.status == AI_REQ_DRAFT_STATUS_COMMITTED:
        raise HTTPException(status_code=409, detail="草稿已入库，不能再编辑")

    changed = False
    if payload.markdown is not None:
        md = payload.markdown.strip()
        if not md:
            raise HTTPException(status_code=400, detail="markdown 不能空")
        draft.markdown = md
        changed = True

    if payload.spec_json is not None:
        # 至少需要 title 字段；其它字段缺省给空
        spec = _normalize_spec(payload.spec_json)
        if not spec.get("title"):
            raise HTTPException(status_code=400, detail="spec_json.title 不能空")
        draft.spec_json = spec
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="markdown 与 spec_json 至少传一个")

    db.commit()
    return {"status": "success", "data": draft.to_dict()}


# ---------------------------------------------------------------------------
# POST commit —— 草稿入库到 requirements 表
# ---------------------------------------------------------------------------
@router.post("/{draft_id}/commit")
def commit_draft(
    draft_id: int,
    payload: DraftCommitRequest,
    db: DBDep,
    user: CurrentUserDep,
):
    """草稿 → ``requirements`` 表新增一行。

    流程：
      1. 状态守卫：必须 pending_review
      2. 从 spec_json 抽出 title / user_story+desc / acceptance_criteria / priority
      3. 新需求 source=ai_generated + source_dialogue_session_id
      4. draft.requirement_id 回填 + status=committed
    """
    draft = _get_draft_or_404(db, draft_id)
    if draft.status == AI_REQ_DRAFT_STATUS_COMMITTED:
        raise HTTPException(
            status_code=409,
            detail=f"草稿已入库，对应需求 id={draft.requirement_id}",
        )
    if draft.status == AI_REQ_DRAFT_STATUS_REJECTED:
        raise HTTPException(status_code=409, detail="草稿已 reject，不能 commit")

    session_row = (
        db.session.query(AiDialogueSession)
        .filter(AiDialogueSession.id == draft.session_id)
        .first()
    )
    if session_row is None:
        raise HTTPException(status_code=404, detail="关联的对话 session 已被删除")

    spec = _normalize_spec(draft.spec_json or {})
    title = (payload.title_override or spec.get("title") or "未命名需求").strip()[:200]
    if not title:
        raise HTTPException(status_code=400, detail="title 不能空")

    # description = user_story + 完整 markdown 提示（让 PM 列表里能看到全文）
    user_story = spec.get("user_story") or ""
    description_parts = []
    if user_story:
        description_parts.append(user_story)
    description_parts.append(draft.markdown or "")
    description = "\n\n".join(p for p in description_parts if p.strip())

    # 排序：项目内目前最大 sort_order + 1
    max_sort = (
        db.session.query(sa_func.max(Requirement.sort_order))
        .filter(Requirement.project_id == session_row.project_id)
        .scalar()
        or 0
    )

    req = Requirement(
        project_id=session_row.project_id,
        title=title,
        description=description,
        acceptance_criteria=spec.get("acceptance_criteria") or [],
        priority=_clamp_priority(spec.get("priority")),
        tags=[],
        status=REQUIREMENT_STATUS_DRAFT,
        source=REQUIREMENT_SOURCE_AI,
        ai_run_id=draft.ai_run_id,
        sort_order=max_sort + 1,
        spec_json=spec,
        source_dialogue_session_id=session_row.id,
        module_id=payload.module_id,
        version_id=payload.version_id,
        parent_requirement_id=payload.parent_requirement_id,
    )
    db.session.add(req)
    db.session.flush()
    db.session.refresh(req)

    draft.requirement_id = req.id
    draft.status = AI_REQ_DRAFT_STATUS_COMMITTED

    db.commit()
    return {
        "status": "success",
        "data": {
            "draft": draft.to_dict(),
            "requirement": req.to_dict(),
        },
    }


# ---------------------------------------------------------------------------
# POST reject
# ---------------------------------------------------------------------------
@router.post("/{draft_id}/reject")
def reject_draft(draft_id: int, db: DBDep):
    """弃用一份草稿（不删数据，只改状态，便于回溯）。"""
    draft = _get_draft_or_404(db, draft_id)
    if draft.status == AI_REQ_DRAFT_STATUS_COMMITTED:
        raise HTTPException(status_code=409, detail="草稿已入库，不能 reject")
    draft.status = AI_REQ_DRAFT_STATUS_REJECTED
    db.commit()
    return {"status": "success", "data": draft.to_dict()}


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------
@router.get("")
def list_drafts(
    db: DBDep,
    project_id: Optional[int] = Query(None),
    session_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
):
    """列草稿。常见用法：``?project_id=42&status=pending_review``。"""
    q = db.session.query(AiRequirementDraft)
    if session_id is not None:
        q = q.filter(AiRequirementDraft.session_id == session_id)
    if status_filter:
        q = q.filter(AiRequirementDraft.status == status_filter)
    if project_id is not None:
        # 通过 session join 过滤 project
        q = q.join(
            AiDialogueSession,
            AiDialogueSession.id == AiRequirementDraft.session_id,
        ).filter(AiDialogueSession.project_id == project_id)

    rows = q.order_by(AiRequirementDraft.created_at.desc()).limit(limit).all()
    return {"status": "success", "data": [r.to_dict() for r in rows]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _get_draft_or_404(db, draft_id: int) -> AiRequirementDraft:
    draft = (
        db.session.query(AiRequirementDraft)
        .filter(AiRequirementDraft.id == draft_id)
        .first()
    )
    if draft is None:
        raise HTTPException(status_code=404, detail=f"草稿 {draft_id} 不存在")
    return draft


def _normalize_spec(spec: dict) -> dict:
    """把外部传入的 spec 规整成下游可用的形态（字段齐 + 类型对）。

    跟 ``coding_agent.prompt_templates._normalize_finalize_output`` 的 spec 部分对齐。
    """
    spec = spec or {}
    return {
        "title": str(spec.get("title") or "").strip()[:200],
        "user_story": str(spec.get("user_story") or "").strip(),
        "acceptance_criteria": _ensure_str_list(spec.get("acceptance_criteria")),
        "nfr": _ensure_str_list(spec.get("nfr")),
        "module_deps": _ensure_str_list(spec.get("module_deps")),
        "priority": _clamp_priority(spec.get("priority")),
    }


def _ensure_str_list(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def _clamp_priority(v) -> int:
    try:
        p = int(v) if v is not None else 2
    except (TypeError, ValueError):
        return 2
    return max(0, min(3, p))
