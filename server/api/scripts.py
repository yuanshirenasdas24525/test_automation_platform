from __future__ import annotations

from typing import Any, Literal

import pydantic
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_

from database.models import ALL_SCRIPT_KINDS, ScriptStore
from server.api.deps import DBDep
from utils.script_runtime import run_script


router = APIRouter(prefix="/scripts", tags=["scripts"])

ScriptKind = Literal["function", "crypto_request", "crypto_response"]
ScriptScope = Literal["global", "project"]


class ScriptPayload(pydantic.BaseModel):
    name: str
    kind: ScriptKind
    code: str
    enabled: bool = True
    project_id: int | None = None
    description: str | None = None


class ScriptTestPayload(pydantic.BaseModel):
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None
    headers: dict[str, Any] | None = None
    body: Any = None
    config: dict[str, Any] | None = None
    vars: dict[str, Any] | None = None  # noqa: A003 - 前端字段名就是 vars


@router.get("")
def list_scripts(
    db: DBDep,
    project_id: int | None = Query(None),
    scope: ScriptScope | Literal["available"] | None = Query(None),
    kind: ScriptKind | None = Query(None),
):
    """查询脚本库。

    scope=global 只查全局；scope=project 只查项目；scope=available 查全局+项目。
    """
    query = db.session.query(ScriptStore)
    if kind:
        query = query.filter(ScriptStore.kind == kind)

    if scope == "global":
        query = query.filter(ScriptStore.project_id.is_(None))
    elif scope == "project":
        if project_id is None:
            raise HTTPException(status_code=422, detail="scope=project 时 project_id 必填")
        query = query.filter(ScriptStore.project_id == project_id)
    elif scope == "available":
        if project_id is None:
            query = query.filter(ScriptStore.project_id.is_(None))
        else:
            query = query.filter(or_(ScriptStore.project_id.is_(None), ScriptStore.project_id == project_id))

    rows = query.order_by(ScriptStore.project_id.asc().nullsfirst(), ScriptStore.kind.asc(), ScriptStore.name.asc()).all()
    return {"status": "success", "data": [row.to_dict() for row in rows]}


@router.post("")
def create_script(payload: ScriptPayload, db: DBDep):
    _validate_payload(payload)
    _ensure_unique(db, payload.name, payload.kind, payload.project_id)
    row = ScriptStore(
        name=payload.name.strip(),
        kind=payload.kind,
        code=payload.code,
        enabled=payload.enabled,
        project_id=payload.project_id,
        description=payload.description,
    )
    db.session.add(row)
    db.session.flush()
    return {"status": "success", "data": row.to_dict()}


@router.put("/{script_id}")
def update_script(script_id: int, payload: ScriptPayload, db: DBDep):
    _validate_payload(payload)
    row = _get_script_or_404(db, script_id)
    _ensure_unique(db, payload.name, payload.kind, payload.project_id, exclude_id=script_id)
    row.name = payload.name.strip()
    row.kind = payload.kind
    row.code = payload.code
    row.enabled = payload.enabled
    row.project_id = payload.project_id
    row.description = payload.description
    db.session.flush()
    return {"status": "success", "data": row.to_dict()}


@router.delete("/{script_id}")
def delete_script(script_id: int, db: DBDep):
    row = _get_script_or_404(db, script_id)
    db.session.delete(row)
    db.session.flush()
    return {"status": "success"}


@router.post("/{script_id}/test")
def test_script(script_id: int, payload: ScriptTestPayload, db: DBDep):
    row = _get_script_or_404(db, script_id)
    try:
        result = run_script(
            row.code,
            kind=row.kind,
            args=payload.args,
            kwargs=payload.kwargs,
            headers=payload.headers,
            body=payload.body,
            config=payload.config,
            vars=payload.vars,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "success",
            "data": {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            },
        }
    return {"status": "success", "data": {"ok": True, "result": result}}


def _get_script_or_404(db: DBDep, script_id: int) -> ScriptStore:
    row = db.session.query(ScriptStore).filter(ScriptStore.id == script_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="脚本不存在")
    return row


def _validate_payload(payload: ScriptPayload) -> None:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="脚本名称不能为空")
    if not name.replace("_", "").isalnum() or name[0].isdigit():
        raise HTTPException(status_code=422, detail="脚本名称只能包含字母、数字、下划线，且不能以数字开头")
    if payload.kind not in ALL_SCRIPT_KINDS:
        raise HTTPException(status_code=422, detail=f"不支持的脚本类型：{payload.kind}")
    if "def handler" not in payload.code:
        raise HTTPException(status_code=422, detail="脚本必须定义 handler 函数")


def _ensure_unique(
    db: DBDep,
    name: str,
    kind: str,
    project_id: int | None,
    exclude_id: int | None = None,
) -> None:
    query = db.session.query(ScriptStore).filter(
        ScriptStore.name == name.strip(),
        ScriptStore.kind == kind,
    )
    if project_id is None:
        query = query.filter(ScriptStore.project_id.is_(None))
    else:
        query = query.filter(ScriptStore.project_id == project_id)
    if exclude_id is not None:
        query = query.filter(ScriptStore.id != exclude_id)
    if query.first() is not None:
        scope_label = "全局" if project_id is None else "项目"
        raise HTTPException(status_code=409, detail=f"{scope_label}脚本已存在：{name}")
