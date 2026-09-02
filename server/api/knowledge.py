"""/api/knowledge/* —— 项目管理「知识库」tab 的文档 CRUD。

知识文档与 AI 上下文共用 ``project_contexts`` 表（source_type='knowledge'）。
详见 server/services/knowledge_service.py 与设计文档。

对象级鉴权：与同级资源（requirements / scripts）保持一致，当前不加登录守卫；
待平台统一开启 project 成员鉴权时，再在此处补 ``assert_project_access``（一处即可）。
"""
from __future__ import annotations

from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from database.models import Module, Project
from server.api.deps import DBDep, CurrentUserDep
from server.services import knowledge_service

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeCreate(pydantic.BaseModel):
    project_id: int
    title: str
    content_html: str = ""
    module_id: Optional[int] = None
    folder_id: Optional[int] = None
    context_type: Optional[str] = None
    include_in_rag: bool = True
    tag_ids: Optional[list[int]] = None


class KnowledgeUpdate(pydantic.BaseModel):
    title: str
    content_html: str = ""
    module_id: Optional[int] = None
    folder_id: Optional[int] = None
    context_type: Optional[str] = None
    include_in_rag: bool = True
    tag_ids: Optional[list[int]] = None


def _require_project(session, project_id: int) -> Project:
    project = session.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在：{project_id}")
    return project


def _validate_module(session, module_id: Optional[int], project_id: int) -> None:
    if module_id is None:
        return
    module = session.query(Module).filter(Module.id == module_id).first()
    if not module or module.project_id != project_id:
        raise HTTPException(status_code=400, detail=f"模块不存在或不属于该项目：{module_id}")


def _validate_folder(session, folder_id: Optional[int], project_id: int) -> None:
    if folder_id is None:
        return
    from server.services import knowledge_folder_service as kfs
    folder = kfs.get_folder(session, folder_id)
    if not folder or folder.project_id != project_id:
        raise HTTPException(status_code=400, detail=f"目录不存在或不属于该项目：{folder_id}")


def _require_title(title: str) -> str:
    t = (title or "").strip()
    if not t:
        raise HTTPException(status_code=400, detail="标题不能为空")
    return t


def _require_doc_in_project(session, doc_id: int, project_id: Optional[int] = None):
    """取文档；不存在 404。

    说明：当前 get/update/delete 调用方都不传 project_id，故此处实际只做「存在性」
    校验。真正的跨项目归属防护（IDOR）依赖平台统一的 assert_project_access，按项目
    约定待「项目成员表」落地后统一开启；届时在此传入并校验 project_id 即可全局生效。
    传了 project_id 时才比对归属（403），为将来接入预留。
    """
    doc = knowledge_service.get_doc(session, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"知识文档不存在：{doc_id}")
    if project_id is not None and doc.project_id != project_id:
        raise HTTPException(status_code=403, detail="无权访问该文档")
    return doc


@router.get("")
def list_knowledge(
    db: DBDep,
    project_id: int = Query(..., description="项目 id 必填"),
    module_id: Optional[int] = Query(None, description="按模块过滤，不传=全部"),
    folder_id: Optional[int] = Query(None, description="按文件夹过滤，不传=全部"),
    tag_id: Optional[int] = Query(None, description="按标签过滤，不传=全部"),
    q: Optional[str] = Query(None, description="标题/正文全文搜索"),
):
    _require_project(db.session, project_id)
    docs = knowledge_service.list_docs(
        db.session, project_id, module_id, folder_id=folder_id, tag_id=tag_id, q=q
    )
    return {"status": "success", "data": [knowledge_service.serialize(d) for d in docs]}


@router.get("/{doc_id}")
def get_knowledge(doc_id: int, db: DBDep):
    doc = _require_doc_in_project(db.session, doc_id)
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}


@router.post("")
def create_knowledge(payload: KnowledgeCreate, db: DBDep, current_user: CurrentUserDep):
    _require_project(db.session, payload.project_id)
    _validate_module(db.session, payload.module_id, payload.project_id)
    _validate_folder(db.session, payload.folder_id, payload.project_id)
    title = _require_title(payload.title)
    doc = knowledge_service.create_doc(
        db.session,
        project_id=payload.project_id,
        title=title,
        content_html=payload.content_html,
        module_id=payload.module_id,
        folder_id=payload.folder_id,
        context_type=payload.context_type,
        include_in_rag=payload.include_in_rag,
        tag_ids=payload.tag_ids,
        author_id=current_user.id,
    )
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}


@router.put("/{doc_id}")
def update_knowledge(doc_id: int, payload: KnowledgeUpdate, db: DBDep, current_user: CurrentUserDep):
    doc = _require_doc_in_project(db.session, doc_id)
    _validate_module(db.session, payload.module_id, doc.project_id)
    _validate_folder(db.session, payload.folder_id, doc.project_id)
    title = _require_title(payload.title)
    doc = knowledge_service.update_doc(
        db.session,
        doc,
        title=title,
        content_html=payload.content_html,
        module_id=payload.module_id,
        folder_id=payload.folder_id,
        context_type=payload.context_type,
        include_in_rag=payload.include_in_rag,
        tag_ids=payload.tag_ids,
        editor_id=current_user.id,
    )
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}


class PinUpdate(pydantic.BaseModel):
    pinned: bool


@router.patch("/{doc_id}/pin")
def pin_knowledge(doc_id: int, payload: PinUpdate, db: DBDep, current_user: CurrentUserDep):
    doc = _require_doc_in_project(db.session, doc_id)
    doc = knowledge_service.set_pinned(db.session, doc, payload.pinned)
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}


@router.delete("/{doc_id}")
def delete_knowledge(doc_id: int, db: DBDep, current_user: CurrentUserDep):
    doc = _require_doc_in_project(db.session, doc_id)
    knowledge_service.delete_doc(db.session, doc)
    return {"status": "success", "data": {"id": doc_id}}
