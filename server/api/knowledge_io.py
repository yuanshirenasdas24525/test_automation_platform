"""/api/knowledge/* 导入导出端点 —— 单篇 MD / 整库 Zip 导出 + 批量导入。"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from database.models import Project
from server.api.deps import DBDep, CurrentUserDep
from server.services import knowledge_service
from server.services import knowledge_io_service as io_svc

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _require_project(session, project_id: int) -> Project:
    p = session.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return p


def _validate_folder(session, folder_id: Optional[int], project_id: int) -> None:
    if folder_id is None:
        return
    from server.services import knowledge_folder_service as kfs
    f = kfs.get_folder(session, folder_id)
    if not f or f.project_id != project_id:
        raise HTTPException(status_code=400, detail=f"目录不存在或不属于该项目：{folder_id}")


@router.get("/{doc_id}/export.md")
def export_doc_md(doc_id: int, db: DBDep, current_user: CurrentUserDep):
    doc = knowledge_service.get_doc(db.session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
    md = io_svc.doc_to_markdown(doc)
    fn = f"{io_svc.safe_name(doc.title)}.md"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fn)}"},
    )


@router.get("/export.zip")
def export_zip(
    db: DBDep, current_user: CurrentUserDep,
    project_id: int = Query(...), folder_id: Optional[int] = Query(None),
):
    _require_project(db.session, project_id)
    _validate_folder(db.session, folder_id, project_id)
    data = io_svc.build_export_zip(db.session, project_id, folder_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=knowledge-export.zip"},
    )


@router.post("/import")
async def import_docs(
    db: DBDep, current_user: CurrentUserDep,
    project_id: int = Form(...), folder_id: Optional[int] = Form(None),
    files: List[UploadFile] = File(...),
):
    _require_project(db.session, project_id)
    _validate_folder(db.session, folder_id, project_id)
    payload = [((f.filename or "文件"), await f.read()) for f in files]
    created = io_svc.import_files(
        db.session, project_id=project_id, folder_id=folder_id,
        files=payload, author_id=current_user.id,
    )
    return {"status": "success", "data": [knowledge_service.serialize(d) for d in created]}
