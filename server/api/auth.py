"""/api/auth/* —— 登录认证 + 修改密码。

JWT token 7 天有效，密码用 bcrypt 哈希。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import pydantic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import selectinload

from server.api.deps import DBDep
from database.models import User, Role, ALL_ROLE_CODES

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# 密码 & JWT 基础设施
# ---------------------------------------------------------------------------
bearer_scheme = HTTPBearer(auto_error=False)


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _get_secret_key() -> str:
    key = os.getenv("JWT_SECRET_KEY")
    if key:
        return key
    from utils.read_conf import read_conf
    return read_conf.get_dict("auth").get("jwt_secret_key", "change-me-to-a-random-string-at-least-32-chars")


SECRET_KEY = _get_secret_key()
TOKEN_EXPIRE_DAYS = 7


def _create_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


# ---------------------------------------------------------------------------
# 依赖：从 Bearer Token 获取当前用户（给其他路由用的）
# ---------------------------------------------------------------------------
def get_current_user(
    db: DBDep,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail="未提供认证 token")
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="token 无效或已过期")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="token 格式错误")

    user = (
        db.session.query(User)
        .options(selectinload(User.roles))
        .filter(User.id == int(user_id))
        .first()
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


def _get_optional_user(
    db: DBDep,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[User]:
    try:
        return get_current_user(db, creds)
    except HTTPException:
        return None


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class LoginRequest(pydantic.BaseModel):
    username: str = pydantic.Field(..., min_length=1, max_length=64)
    password: str = pydantic.Field(..., min_length=1)


class ChangePasswordRequest(pydantic.BaseModel):
    old_password: str = pydantic.Field(..., min_length=1)
    new_password: str = pydantic.Field(..., min_length=1, max_length=128)


class LoginResponse(pydantic.BaseModel):
    token: str
    user: dict


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.post("/login")
def login(payload: LoginRequest, db: DBDep):
    user = (
        db.session.query(User)
        .options(selectinload(User.roles))
        .filter(User.username == payload.username)
        .first()
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.password_hash or not _verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = _create_token(user.id)
    return {
        "status": "success",
        "data": {
            "token": token,
            "user": user.to_dict(),
        },
    }


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {"status": "success", "data": current_user.to_dict()}


@router.put("/password")
def change_password(
    payload: ChangePasswordRequest,
    db: DBDep,
    current_user: User = Depends(get_current_user),
):
    if not current_user.password_hash:
        raise HTTPException(status_code=400, detail="当前用户未设置密码")

    if not _verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="旧密码错误")

    current_user.password_hash = _hash_password(payload.new_password)
    db.session.flush()
    return {"status": "success", "message": "密码修改成功"}
