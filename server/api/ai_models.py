"""/api/ai-models —— AI 模型连接配置 CRUD。

每条记录 = 一个用户起名的模型连接（name 唯一）；存储底层走 config_store
(category='ai', config_group=<name>, ...)，详见 server/services/ai_model_service.py。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException

from database.schemas.ai_config import (
    AiModelConfig,
    AiModelConfigUpsert,
    AiModelTestResult,
    ALL_AI_PROVIDERS,
)
from server.api.deps import DBDep, RequireAdmin
from server.services.ai_model_service import (
    delete_ai_model,
    get_ai_model,
    list_ai_models,
    upsert_ai_model,
)


router = APIRouter(prefix="/ai-models", tags=["ai-models"])


def _validate(cfg: AiModelConfigUpsert) -> None:
    if cfg.provider not in ALL_AI_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider 必须是 {sorted(ALL_AI_PROVIDERS)} 之一，"
                f"实际：{cfg.provider}"
            ),
        )
    if not cfg.model:
        raise HTTPException(status_code=400, detail="model 不能为空")


@router.get("")
def list_models(db: DBDep) -> dict[str, Any]:
    items = [c.model_dump() for c in list_ai_models(db.session)]
    return {"status": "success", "data": items}


@router.post("", dependencies=[RequireAdmin])
def create_model(payload: dict[str, Any], db: DBDep):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")

    body = {k: v for k, v in payload.items() if k != "name"}
    cfg = AiModelConfigUpsert(**body)
    _validate(cfg)

    if get_ai_model(db.session, name) is not None:
        raise HTTPException(
            status_code=409, detail=f"模型 {name} 已存在；请用 PUT 更新"
        )

    saved = upsert_ai_model(
        db.session, name, AiModelConfig(name=name, **cfg.model_dump())
    )
    return {"status": "success", "data": saved.model_dump()}


@router.put("/{name}", dependencies=[RequireAdmin])
def update_model(name: str, payload: AiModelConfigUpsert, db: DBDep):
    _validate(payload)
    if get_ai_model(db.session, name) is None:
        raise HTTPException(status_code=404, detail=f"模型 {name} 不存在")
    saved = upsert_ai_model(
        db.session, name, AiModelConfig(name=name, **payload.model_dump())
    )
    return {"status": "success", "data": saved.model_dump()}


@router.delete("/{name}", dependencies=[RequireAdmin])
def delete_model(name: str, db: DBDep):
    n = delete_ai_model(db.session, name)
    if n == 0:
        raise HTTPException(status_code=404, detail=f"模型 {name} 不存在")
    return {"status": "success", "data": {"deleted": n}}


@router.post("/{name}/test")
def test_model(name: str, db: DBDep):
    cfg = get_ai_model(db.session, name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"模型 {name} 不存在")

    start = time.time()
    try:
        from ai_gateway.gateway import provider_ping

        sample = provider_ping(cfg)
        result = AiModelTestResult(
            ok=True,
            latency_ms=int((time.time() - start) * 1000),
            sample=sample[:200],
        )
    except Exception as e:  # noqa: BLE001
        result = AiModelTestResult(
            ok=False,
            latency_ms=int((time.time() - start) * 1000),
            sample="",
            error=str(e)[:500],
        )
    return {"status": "success", "data": result.model_dump()}
