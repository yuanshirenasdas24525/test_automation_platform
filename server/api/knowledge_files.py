"""/api/knowledge/* 文件相关端点 —— 上传成文件文档 / 给文档加附件 / 鉴权下载 / 删附件。"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from database.models import Project
from server.api.deps import DBDep, CurrentUserDep
from server.services import knowledge_service
from server.services import knowledge_attachment_service as kas
from utils import knowledge_storage as storage

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def _read_validated(file: UploadFile):
    filename = file.filename or "文件"
    if not storage.is_allowed(filename):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{filename}")
    data = await file.read()
    if not storage.within_size(len(data)):
        mb = storage.MAX_SIZE_BYTES // 1024 // 1024
        raise HTTPException(status_code=400, detail=f"文件为空或超过大小上限 {mb}MB")
    return data, filename, (file.content_type or "")


def _require_doc(session, doc_id: int):
    doc = knowledge_service.get_doc(session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
    return doc


@router.post("/upload")
async def upload_file_doc(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Form(...),
    folder_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
):
    if not db.session.query(Project).filter(Project.id == project_id).first():
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    if folder_id is not None:
        from server.services import knowledge_folder_service as kfs
        f = kfs.get_folder(db.session, folder_id)
        if not f or f.project_id != project_id:
            raise HTTPException(status_code=400, detail=f"目录不存在或不属于该项目：{folder_id}")
    data, filename, mime = await _read_validated(file)
    doc = knowledge_service.create_file_doc(
        db.session, project_id=project_id, filename=filename, data=data,
        mime=mime, folder_id=folder_id, author_id=current_user.id,
    )
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}


@router.post("/{doc_id}/attachments")
async def add_attachment(doc_id: int, db: DBDep, current_user: CurrentUserDep, file: UploadFile = File(...)):
    doc = _require_doc(db.session, doc_id)
    data, filename, mime = await _read_validated(file)
    a = kas.create_attachment(db.session, doc, filename=filename, mime=mime, data=data, uploaded_by=current_user.id)
    return {"status": "success", "data": kas.serialize_attachment(a)}


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: int, db: DBDep, current_user: CurrentUserDep,
    disposition: str = Query("inline"),
):
    a = kas.get_attachment(db.session, attachment_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"附件不存在：{attachment_id}")
    _require_doc(db.session, a.document_id)   # 归属存在性
    p = storage.abs_path(a.storage_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="文件已丢失")
    dtype = "attachment" if disposition == "attachment" else "inline"
    cd = f"{dtype}; filename*=UTF-8''{quote(a.filename or 'file')}"
    return FileResponse(
        str(p),
        media_type=a.mime or "application/octet-stream",
        headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/attachments/{attachment_id}")
def remove_attachment(attachment_id: int, db: DBDep, current_user: CurrentUserDep):
    a = kas.get_attachment(db.session, attachment_id)
    if not a:
        raise HTTPException(status_code=404, detail=f"附件不存在：{attachment_id}")
    kas.delete_attachment(db.session, a)
    return {"status": "success", "data": {"id": attachment_id}}
