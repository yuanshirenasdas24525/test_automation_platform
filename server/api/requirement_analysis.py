"""/api/requirements/{rid}/analysis-documents 与 /api/analysis-documents/* —— M6 AI 分析文档 CRUD + 版本历史。

设计要点：
  - 一个需求可拥有 N 份独立 AnalysisDocument，每份维护自己的 git-like 版本链
  - 触发分析：POST /api/requirements/{rid}/analysis-documents
      body: { model_names: [str], user_prompt?: str }
      → 为每个 model 异步创建一个 AiRun，handler=`requirement_analyze`，handler 内
        建好 document + 第一条 version
  - 编辑保存（PUT 文档）= 追加一条 version + 更新 document.current_*
  - diff 在前端用 diff-match-patch 计算，这里只把两个版本的 markdown 一起返回
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import selectinload

from celery_app import celery_app
from database.models import (
    AiRun,
    AI_RUN_STATUS_PENDING,
    Requirement,
    RequirementAnalysisDocument,
    RequirementAnalysisVersion,
    User,
)
from server.api.deps import CurrentUserDep, DBDep
from server.services.ai_model_service import get_ai_model


router = APIRouter(tags=["requirement-analysis"])


# ---------------------------------------------------------------------------
# Pydantic 体
# ---------------------------------------------------------------------------
class TriggerAnalysisBody(BaseModel):
    model_names: list[str] = Field(default_factory=list)
    user_prompt: Optional[str] = None
    document_title: Optional[str] = None
    analysis_type: Optional[str] = None


ANALYSIS_TYPES = {
    "clarify",
    "testability",
    "delivery",
    "full",
    "market",
    "industry",
}


class UpdateDocumentBody(BaseModel):
    markdown: str
    change_summary: Optional[str] = None
    title: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _user_label(db_session, user_id: Optional[int]) -> str:
    if not user_id:
        return ""
    u = db_session.query(User).filter(User.id == user_id).one_or_none()
    if u is None:
        return f"#{user_id}"
    return u.full_name or u.username or f"#{user_id}"


def _require_requirement(db_session, rid: int) -> Requirement:
    req = db_session.query(Requirement).filter(Requirement.id == rid).one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail=f"需求 #{rid} 不存在")
    return req


def _require_document(db_session, doc_id: int) -> RequirementAnalysisDocument:
    doc = (
        db_session.query(RequirementAnalysisDocument)
        .filter(RequirementAnalysisDocument.id == doc_id)
        .one_or_none()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail=f"分析文档 #{doc_id} 不存在")
    return doc


def _doc_summary(doc: RequirementAnalysisDocument, db_session) -> dict[str, Any]:
    d = doc.to_dict(include_markdown=False)
    d["created_by_label"] = _user_label(db_session, doc.created_by_id)
    return d


# ---------------------------------------------------------------------------
# 触发分析 + 列文档
# ---------------------------------------------------------------------------
@router.get("/requirements/{rid}/analysis-documents")
def list_documents(rid: int, db: DBDep):
    _require_requirement(db.session, rid)
    docs = (
        db.session.query(RequirementAnalysisDocument)
        .filter(RequirementAnalysisDocument.requirement_id == rid)
        .order_by(RequirementAnalysisDocument.created_at.desc())
        .all()
    )
    items = [_doc_summary(d, db.session) for d in docs]

    # 关联 ai_runs（为前端展示运行状态/失败标识用）
    run_ids = [d.ai_run_id for d in docs if d.ai_run_id]
    runs_by_id: dict[int, dict] = {}
    if run_ids:
        rows = db.session.query(AiRun).filter(AiRun.id.in_(run_ids)).all()
        for r in rows:
            runs_by_id[r.id] = {
                "id": r.id,
                "status": r.status,
                "error": r.error,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
            }
    for it in items:
        if it.get("ai_run_id") and it["ai_run_id"] in runs_by_id:
            it["ai_run"] = runs_by_id[it["ai_run_id"]]

    return {"status": "success", "data": items}


@router.post("/requirements/{rid}/analysis-documents")
def trigger_analysis(
    rid: int,
    body: TriggerAnalysisBody,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """触发 AI 分析：为每个 model_name 单独建一个 AiRun + 异步派发。"""
    req = _require_requirement(db.session, rid)
    if not body.model_names:
        raise HTTPException(status_code=400, detail="model_names 不能为空")
    analysis_type = (body.analysis_type or "full").strip()
    if analysis_type not in ANALYSIS_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                "analysis_type 必须是 "
                "clarify / testability / delivery / full / market / industry 之一"
            ),
        )

    # 校验所有模型都存在且 enabled
    cfgs = []
    for name in body.model_names:
        cfg = get_ai_model(db.session, name)
        if cfg is None:
            raise HTTPException(status_code=400, detail=f"模型 {name!r} 不存在")
        if not cfg.enabled:
            raise HTTPException(status_code=400, detail=f"模型 {name!r} 未启用")
        cfgs.append(cfg)

    runs: list[dict[str, Any]] = []
    for cfg in cfgs:
        ai_run = AiRun(
            feature="requirement_analyze",
            status=AI_RUN_STATUS_PENDING,
            project_id=req.project_id,
            input_payload={
                "requirement_id": rid,
                "model_name": cfg.name,
                "user_prompt": body.user_prompt or "",
                "document_title": body.document_title or "",
                "analysis_type": analysis_type,
                "created_by_id": current_user.id,
            },
            operator=current_user.username,
        )
        db.session.add(ai_run)
        db.session.flush()

        # 派发到 celery
        celery_app.send_task("tasks.dispatch_ai_task", args=[ai_run.id])

        runs.append({
            "run_id": ai_run.id,
            "model_name": cfg.name,
            "model_label": f"{cfg.provider} / {cfg.model}",
        })

    return {"status": "success", "data": {"runs": runs}}


# ---------------------------------------------------------------------------
# 文档详情 / 编辑 / 删除
# ---------------------------------------------------------------------------
@router.get("/analysis-documents/{doc_id}")
def get_document(doc_id: int, db: DBDep):
    doc = _require_document(db.session, doc_id)
    data = doc.to_dict(include_markdown=True)
    data["created_by_label"] = _user_label(db.session, doc.created_by_id)
    return {"status": "success", "data": data}


@router.put("/analysis-documents/{doc_id}")
def save_document(
    doc_id: int,
    body: UpdateDocumentBody,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """保存编辑 = 追加一条新版本 + 更新 document 当前指针。"""
    doc = _require_document(db.session, doc_id)
    md = (body.markdown or "").strip("\ufeff")
    if not md.strip():
        raise HTTPException(status_code=400, detail="markdown 不能为空")

    if body.title:
        doc.title = body.title[:200]

    new_version_no = (doc.current_version or 0) + 1
    version = RequirementAnalysisVersion(
        document_id=doc.id,
        version_no=new_version_no,
        markdown=md,
        change_summary=(body.change_summary or "").strip()[:500] or None,
        author_id=current_user.id,
        is_ai_generated=False,
    )
    db.session.add(version)

    doc.current_markdown = md
    doc.current_version = new_version_no
    db.session.flush()

    return {
        "status": "success",
        "data": {
            "document": doc.to_dict(include_markdown=False),
            "new_version_no": new_version_no,
        },
    }


@router.delete("/analysis-documents/{doc_id}")
def delete_document(doc_id: int, db: DBDep):
    doc = _require_document(db.session, doc_id)
    db.session.delete(doc)
    db.session.flush()
    return {"status": "success", "data": {"deleted": doc_id}}


# ---------------------------------------------------------------------------
# 版本列表 / 单版本 / diff / 导出
# ---------------------------------------------------------------------------
@router.get("/analysis-documents/{doc_id}/versions")
def list_versions(doc_id: int, db: DBDep):
    _require_document(db.session, doc_id)
    rows = (
        db.session.query(RequirementAnalysisVersion)
        .filter(RequirementAnalysisVersion.document_id == doc_id)
        .order_by(RequirementAnalysisVersion.version_no.desc())
        .all()
    )
    items = []
    for r in rows:
        item = r.to_dict(include_markdown=False)
        item["author_label"] = _user_label(db.session, r.author_id)
        items.append(item)
    return {"status": "success", "data": items}


@router.get("/analysis-documents/{doc_id}/versions/{version_no}")
def get_version(doc_id: int, version_no: int, db: DBDep):
    row = (
        db.session.query(RequirementAnalysisVersion)
        .filter(
            RequirementAnalysisVersion.document_id == doc_id,
            RequirementAnalysisVersion.version_no == version_no,
        )
        .one_or_none()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"版本 v{version_no} 不存在",
        )
    data = row.to_dict(include_markdown=True)
    data["author_label"] = _user_label(db.session, row.author_id)
    return {"status": "success", "data": data}


@router.get("/analysis-documents/{doc_id}/versions/{a}/diff/{b}")
def get_versions_diff(doc_id: int, a: int, b: int, db: DBDep):
    """返回两个版本的 markdown，diff 由前端计算。"""
    rows = (
        db.session.query(RequirementAnalysisVersion)
        .filter(
            RequirementAnalysisVersion.document_id == doc_id,
            RequirementAnalysisVersion.version_no.in_([a, b]),
        )
        .all()
    )
    by_no = {r.version_no: r for r in rows}
    if a not in by_no or b not in by_no:
        raise HTTPException(status_code=404, detail="某个版本不存在")
    return {
        "status": "success",
        "data": {
            "before": by_no[a].to_dict(include_markdown=True),
            "after": by_no[b].to_dict(include_markdown=True),
        },
    }


@router.get("/analysis-documents/{doc_id}/export")
def export_document(doc_id: int, db: DBDep):
    doc = _require_document(db.session, doc_id)
    safe_title = (doc.title or f"analysis_{doc.id}").replace("/", "_").replace("\\", "_")
    filename = f"{safe_title}.md"
    return Response(
        content=(doc.current_markdown or ""),
        media_type="text/markdown; charset=utf-8",
        headers={
            # 用 RFC 5987 编码方式兼容中文文件名
            "Content-Disposition": (
                f'attachment; filename="{doc_id}.md"; '
                f"filename*=UTF-8''{_url_quote(filename)}"
            ),
        },
    )


def _url_quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")
