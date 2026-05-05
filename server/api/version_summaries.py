"""/api/version-summaries/* —— 版本测试汇总按需生成 + 强制重算。

  GET  /api/version-summaries/{version_id}  → 优先读缓存，无则调 service.generate
  POST /api/version-summaries/{version_id}/regenerate → 强制重算
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.api.deps import DBDep
from server.services.version_summary_service import generate as generate_summary
from database.models import ProjectVersion, VersionTestSummary

router = APIRouter(prefix="/version-summaries", tags=["version-summaries"])


@router.get("/{version_id}")
def get_version_summary(version_id: int, db: DBDep):
    version = db.session.query(ProjectVersion).filter(ProjectVersion.id == version_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="版本不存在")

    summary = (
        db.session.query(VersionTestSummary)
        .filter(VersionTestSummary.version_id == version_id)
        .first()
    )
    if summary is not None:
        return {"status": "success", "data": summary.to_dict()}

    data = generate_summary(version_id, db.session)
    return {"status": "success", "data": data}


@router.post("/{version_id}/regenerate")
def regenerate_version_summary(version_id: int, db: DBDep):
    version = db.session.query(ProjectVersion).filter(ProjectVersion.id == version_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="版本不存在")

    data = generate_summary(version_id, db.session)
    return {"status": "success", "data": data}
