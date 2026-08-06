"""变更调整（change-adjust）REST 路由。

两个端点：
  - POST /change_plan/preview  上传变更文本 + 补充文档/链接 → AI 产出用例级调整大纲(ops)
  - POST /change_plan/apply    对已生成的调整大纲选择性应用（新增/修改/删除真实用例）

两个端点都按"模块所属项目"做对象级授权（`assert_project_access`），
不要在这里自己写归属判断，见 server/api/authz.py。
"""
from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
import pydantic

from database.models import Module, User
from database.models.ai_run import AiRun
from server.api.auth import _get_optional_user
from server.api.authz import assert_project_access
from server.api.deps import DBDep
from server.api.functional_cases import _operator_name
from server.services.change_plan_service import AI_FEATURE_CHANGE_PLAN, plan_apply, plan_preview
from server.services.doc_ingest import ingest_sources

router = APIRouter(prefix="/change_plan", tags=["change-adjust"])

# 可选当前用户：带了有效 token 就解出 User，否则 None（不强制登录，记录 operator 用）。
# `server/api/deps.py` 只有强制登录的 CurrentUserDep，OptionalUserDep 沿用
# cases.py / functional_cases.py 里的同一套本地别名写法。
OptionalUserDep = Annotated[Optional[User], Depends(_get_optional_user)]


def _module_or_404(db: DBDep, module_id: int | None) -> Module:
    if module_id is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    m = db.session.query(Module).filter(Module.id == module_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    return m


@router.post("/preview")
async def change_plan_preview(
    db: DBDep,
    user: OptionalUserDep = None,
    module_id: int = Form(...),
    change_text: str = Form(""),
    model_name: str = Form(""),
    links: str = Form(""),
    files: Optional[list[UploadFile]] = File(None),
):
    module = _module_or_404(db, module_id)
    assert_project_access(db, user, module.project_id)

    file_tuples = [(f.filename or "upload", await f.read()) for f in (files or [])]
    link_list = [s.strip() for s in links.replace(",", "\n").splitlines() if s.strip()]
    ingest = ingest_sources(files=file_tuples, links=link_list)

    data = plan_preview(db, module, model_name, change_text, ingest, operator=_operator_name(user))
    return {"status": "success", "data": data}


class ApplyRequest(pydantic.BaseModel):
    plan_id: int
    selected_op_ids: list[int] = pydantic.Field(default_factory=list)
    confirmed_delete_ids: list[int] = pydantic.Field(default_factory=list)


@router.post("/apply")
def change_plan_apply(payload: ApplyRequest, db: DBDep, user: OptionalUserDep = None):
    run = (
        db.session.query(AiRun)
        .filter(AiRun.id == payload.plan_id, AiRun.feature == AI_FEATURE_CHANGE_PLAN)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="调整计划不存在或已过期")

    input_payload: dict[str, Any] = run.input_payload or {}
    module = _module_or_404(db, input_payload.get("module_id"))
    assert_project_access(db, user, module.project_id)

    model_name = input_payload.get("model_name") or ""
    data = plan_apply(
        db,
        payload.plan_id,
        model_name,
        confirmed_delete_ids=payload.confirmed_delete_ids,
        selected_op_ids=payload.selected_op_ids,
        user=user,
    )
    return {"status": "success", "data": data}
