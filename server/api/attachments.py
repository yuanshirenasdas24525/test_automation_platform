"""/api/requirements/{id}/attachments —— 需求附件 CRUD。

两种附件：
  - kind=link：外部 URL，name 是链接标题
  - kind=file：multipart upload，落盘 data/attachments/req_{id}/{uuid}_{orig}，
    URL 走 FastAPI 静态挂载 /attachments/req_{id}/{uuid}_{orig}

约束：
  - 单文件 ≤ ATTACHMENT_MAX_BYTES（默认 20 MB），超出 413
  - 后缀白名单：图片 / PDF / Office / 文本 / 压缩包；可执行文件拒绝
  - 删 file 类型同时删本地文件，找不到不抛错（容忍手动清理）
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Optional

import pydantic
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from server.api.deps import DBDep
from database.models import (
    Attachment,
    Requirement,
    User,
    ATTACHMENT_KIND_FILE,
    ATTACHMENT_KIND_LINK,
    ALL_ATTACHMENT_KINDS,
)


# 仓库根：server/api/attachments.py → 上两级
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ATTACHMENTS_DIR = _PROJECT_ROOT / "data" / "attachments"

ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

# 白名单后缀（小写）；空集合表示不允许
ALLOWED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    ".pdf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".log", ".json", ".xml",
    ".zip", ".tar", ".gz", ".7z", ".rar",
}

# 危险后缀（明确禁掉，避免成为分发渠道）
BLOCKED_EXTS = {
    ".exe", ".sh", ".bat", ".cmd", ".dmg", ".app", ".msi",
    ".so", ".dll", ".dylib", ".jar",
}


router = APIRouter(tags=["attachments"])


class AttachmentLinkCreate(pydantic.BaseModel):
    name: str = pydantic.Field(..., min_length=1, max_length=200)
    url: str = pydantic.Field(..., min_length=1, max_length=500)


def _safe_filename(name: str) -> str:
    """剥危险字符 + 取末段，保留扩展名供白名单判断。"""
    base = Path(name).name  # 去目录
    base = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", base)
    return base[:120] or "file"


def _require_requirement(session, req_id: int) -> Requirement:
    req = session.query(Requirement).filter(Requirement.id == req_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    return req


@router.get("/requirements/{req_id}/attachments")
def list_attachments(req_id: int, db: DBDep):
    _require_requirement(db.session, req_id)
    rows = (
        db.session.query(Attachment)
        .filter(Attachment.requirement_id == req_id)
        .order_by(Attachment.created_at.asc(), Attachment.id.asc())
        .all()
    )
    return {"status": "success", "data": [a.to_dict() for a in rows]}


@router.post("/requirements/{req_id}/attachments")
async def create_attachment(
    req_id: int,
    db: DBDep,
    kind: str = Form(...),
    name: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    uploaded_by_id: Optional[int] = Form(None),
):
    req = _require_requirement(db.session, req_id)

    if kind not in ALL_ATTACHMENT_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"非法 kind，仅 {sorted(ALL_ATTACHMENT_KINDS)}",
        )

    if uploaded_by_id is not None:
        u = db.session.query(User).filter(User.id == uploaded_by_id).first()
        if u is None:
            raise HTTPException(status_code=404, detail="上传用户不存在")

    if kind == ATTACHMENT_KIND_LINK:
        if not (name and url):
            raise HTTPException(status_code=400, detail="link 类型必须传 name + url")
        att = Attachment(
            requirement_id=req.id,
            name=name.strip()[:200],
            kind=ATTACHMENT_KIND_LINK,
            url=url.strip()[:500],
            uploaded_by_id=uploaded_by_id,
        )
        db.session.add(att)
        db.session.flush()
        db.session.refresh(att)
        return {"status": "success", "data": att.to_dict()}

    # kind == file
    if file is None:
        raise HTTPException(status_code=400, detail="file 类型必须上传文件")

    safe_orig = _safe_filename(file.filename or "file")
    ext = Path(safe_orig).suffix.lower()
    if ext in BLOCKED_EXTS:
        raise HTTPException(status_code=400, detail=f"禁止上传该类型文件：{ext}")
    if ext and ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 {ext}；仅支持 图片/PDF/Office/文本/压缩包",
        )

    # 读 + 限大小
    data = await file.read()
    if len(data) > ATTACHMENT_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超出 {ATTACHMENT_MAX_BYTES // 1024 // 1024} MB 限制",
        )

    req_dir = ATTACHMENTS_DIR / f"req_{req.id}"
    req_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}_{safe_orig}"
    target = req_dir / stored
    target.write_bytes(data)

    att = Attachment(
        requirement_id=req.id,
        name=name.strip()[:200] if name else safe_orig,
        kind=ATTACHMENT_KIND_FILE,
        url=f"/attachments/req_{req.id}/{stored}",
        size_bytes=len(data),
        uploaded_by_id=uploaded_by_id,
    )
    db.session.add(att)
    db.session.flush()
    db.session.refresh(att)
    return {"status": "success", "data": att.to_dict()}


@router.delete("/attachments/{att_id}")
def delete_attachment(att_id: int, db: DBDep):
    att = db.session.query(Attachment).filter(Attachment.id == att_id).first()
    if att is None:
        raise HTTPException(status_code=404, detail="附件不存在")

    if att.kind == ATTACHMENT_KIND_FILE:
        # url 形如 /attachments/req_{rid}/{stored}
        rel = att.url.lstrip("/")
        if rel.startswith("attachments/"):
            local = _PROJECT_ROOT / "data" / rel
            try:
                if local.is_file():
                    local.unlink()
            except OSError:
                # 文件丢了就忽略，DB 行还是要清掉
                pass

    db.session.delete(att)
    return {"status": "success", "message": "已删除"}
