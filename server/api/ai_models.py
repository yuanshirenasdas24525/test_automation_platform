"""/api/ai-models —— 项目级 AI 模型只读列表。

全局 AI 模型配置已移除；增删改和测试请走项目配置页对应接口。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from server.api.deps import DBDep, RequireAdmin
from server.services.ai_model_service import list_ai_models


router = APIRouter(prefix="/ai-models", tags=["ai-models"])


@router.get("")
def list_models(db: DBDep, project_id: int = Query(...)) -> dict[str, Any]:
    items = [c.model_dump() for c in list_ai_models(db.session, project_id=project_id)]
    return {"status": "success", "data": items}


@router.post("", dependencies=[RequireAdmin])
def create_model(payload: dict[str, Any], db: DBDep):
    raise HTTPException(status_code=410, detail="全局 AI 模型接口已移除，请使用项目配置 → AI")


@router.put("/{name}", dependencies=[RequireAdmin])
def update_model(name: str, payload: dict[str, Any], db: DBDep):
    raise HTTPException(status_code=410, detail="全局 AI 模型接口已移除，请使用项目配置 → AI")


@router.delete("/{name}", dependencies=[RequireAdmin])
def delete_model(name: str, db: DBDep):
    raise HTTPException(status_code=410, detail="全局 AI 模型接口已移除，请使用项目配置 → AI")


@router.post("/{name}/test")
def test_model(name: str, db: DBDep):
    raise HTTPException(status_code=410, detail="全局 AI 模型接口已移除，请使用项目配置 → AI 的测试按钮")
