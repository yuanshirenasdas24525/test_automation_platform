"""API Key 管理 —— 签发 / 列表 / 吊销（仅管理员）。

- 明文 key 只在签发响应里出现一次，之后任何接口都拿不回来（库里只有哈希）；
- 吊销是置 is_active=False，不物理删除（保留审计线索）；
- API Key 自身鉴权的请求摸不到本路由（auth._API_KEY_DENY_PREFIXES 拦截），
  避免"用 key 再造 key"的提权。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

import pydantic
from fastapi import APIRouter, HTTPException

from database.models import ALL_API_KEY_SCOPES, ApiKey
from server.api.deps import CurrentUserDep, DBDep, RequireAdmin

router = APIRouter(prefix="/api-keys", tags=["api-keys"], dependencies=[RequireAdmin])

_KEY_PREFIX = "tap_"


class ApiKeyCreateRequest(pydantic.BaseModel):
    name: str = pydantic.Field(..., min_length=1, max_length=100)
    scopes: list[str] = pydantic.Field(..., min_length=1)
    expires_days: int | None = pydantic.Field(None, ge=1, le=3650)  # 空 = 永不过期


def _serialize(key: ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "key_prefix": key.key_prefix,
        "scopes": key.scopes or [],
        "is_active": key.is_active,
        "created_by": key.created_by,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "create_time": key.create_time.isoformat() if key.create_time else None,
    }


@router.post("")
def create_api_key(payload: ApiKeyCreateRequest, db: DBDep, current_user: CurrentUserDep):
    invalid = set(payload.scopes) - ALL_API_KEY_SCOPES
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"未知 scope：{sorted(invalid)}，可选：{sorted(ALL_API_KEY_SCOPES)}",
        )

    raw_key = _KEY_PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(
        name=payload.name,
        key_prefix=raw_key[:12],
        key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        scopes=sorted(set(payload.scopes)),
        created_by=current_user.id,
        expires_at=(
            datetime.now() + timedelta(days=payload.expires_days)
            if payload.expires_days else None
        ),
    )
    db.session.add(key)
    db.session.flush()
    return {
        "status": "success",
        "data": {**_serialize(key), "api_key": raw_key},  # 明文仅此一次
        "message": "请立即保存 api_key，之后无法再次查看",
    }


@router.get("")
def list_api_keys(db: DBDep):
    rows = db.session.query(ApiKey).order_by(ApiKey.id.desc()).all()
    return {"status": "success", "data": [_serialize(k) for k in rows]}


@router.delete("/{key_id}")
def revoke_api_key(key_id: int, db: DBDep):
    key = db.session.query(ApiKey).filter(ApiKey.id == key_id).first()
    if key is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    key.is_active = False
    return {"status": "success", "message": f"API Key「{key.name}」已吊销"}
