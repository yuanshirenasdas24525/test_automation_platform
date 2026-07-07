"""/api/auth/* —— 登录认证、refresh token、退出登录和修改密码。

access token 短期有效，refresh token 长期有效；密码用 bcrypt 哈希。
refresh token 明文只返回客户端，服务端仅保存哈希值。
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import pydantic
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import selectinload

from database.models import User, UserSession
from server.api.deps import DBDep

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# 密码 & JWT 基础设施
# ---------------------------------------------------------------------------
bearer_scheme = HTTPBearer(auto_error=False)
DEFAULT_SECRET_KEY = "change-me-to-a-random-string-at-least-32-chars"
PRODUCTION_ENVS = {"prod", "production"}


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _is_production_env() -> bool:
    raw = (
        os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("FASTAPI_ENV")
        or ""
    )
    return raw.strip().lower() in PRODUCTION_ENVS


def _get_secret_key() -> str:
    key = os.getenv("JWT_SECRET_KEY")
    if key:
        secret = key.strip()
    else:
        from utils.reload_config import config_center

        secret = str(config_center.get("auth", "jwt_secret_key", default=DEFAULT_SECRET_KEY)).strip()

    if _is_production_env() and (secret == DEFAULT_SECRET_KEY or len(secret) < 32):
        raise RuntimeError("生产环境必须配置长度不少于 32 位的 JWT_SECRET_KEY")

    return secret


SECRET_KEY = _get_secret_key()
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 14
PROTECTED_ADMIN_USERNAME = "admin"
MULTI_SESSION_CLIENT_TYPES = {"api"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_naive() -> datetime:
    return datetime.utcnow()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_access_token(user_id: int, session_id: int | None = None) -> str:
    expire = _now_utc() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": _now_utc(),
        "jti": uuid.uuid4().hex,
        "type": "access",
    }
    if session_id is not None:
        payload["sid"] = str(session_id)
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def _create_refresh_token(user_id: int, session_id: int, jti: str) -> str:
    expire = _now_utc() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "exp": expire,
        "iat": _now_utc(),
        "jti": jti,
        "type": "refresh",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def _decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="token 类型错误")
    return payload


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip() or None
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip() or None
    return request.client.host if request.client else None


def _normalize_client(raw: dict[str, Any] | None) -> dict[str, str | None]:
    raw = raw or {}
    allowed = {
        "session_kind",
        "client_type",
        "client_name",
        "app_version",
        "platform",
        "device_id",
        "device_name",
        "os_name",
        "os_version",
        "browser_name",
        "browser_version",
    }
    data: dict[str, str | None] = {}
    for key in allowed:
        value = raw.get(key)
        text = str(value).strip() if value is not None else ""
        data[key] = text[:128] if text else None
    data["session_kind"] = data["session_kind"] or "password_login"
    data["client_type"] = data["client_type"] or "web"
    return data


def revoke_user_sessions(db: DBDep, user_id: int, reason: str) -> int:
    """吊销某个用户所有未失效会话。用户停用、软删、改密时复用。"""
    now = _now_naive()
    rows = (
        db.session.query(UserSession)
        .filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .all()
    )
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = reason
    return len(rows)


def _revoke_same_client_type_sessions(db: DBDep, user_id: int, client_type: str, reason: str) -> int:
    """同一账号同一客户端类型只保留最新会话，api 这类脚本端允许多会话。"""
    if client_type in MULTI_SESSION_CLIENT_TYPES:
        return 0
    now = _now_naive()
    rows = (
        db.session.query(UserSession)
        .filter(
            UserSession.user_id == user_id,
            UserSession.client_type == client_type,
            UserSession.revoked_at.is_(None),
        )
        .all()
    )
    for row in rows:
        row.revoked_at = now
        row.revoked_reason = reason
    return len(rows)


def _get_session_or_401(db: DBDep, session_id: int, token_hash: str | None = None) -> UserSession:
    session = db.session.query(UserSession).filter(UserSession.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=401, detail="会话不存在")
    if session.revoked_at is not None:
        raise HTTPException(status_code=401, detail="会话已失效")
    if session.expires_at <= _now_naive():
        raise HTTPException(status_code=401, detail="会话已过期")
    if token_hash is not None and session.refresh_token_hash != token_hash:
        raise HTTPException(status_code=401, detail="refresh token 无效")
    return session


# ---------------------------------------------------------------------------
# 依赖：从 Bearer Token 获取当前用户（给其他路由用的）
# ---------------------------------------------------------------------------
def get_current_user(
    db: DBDep,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    if creds is None:
        raise HTTPException(status_code=401, detail="未提供认证 token")
    payload = _decode_token(creds.credentials, "access")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="token 格式错误")

    session_id = payload.get("sid")
    if session_id:
        _get_session_or_401(db, int(session_id))

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
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User | None:
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
    client: dict[str, Any] | None = None


class RefreshRequest(pydantic.BaseModel):
    refresh_token: str = pydantic.Field(..., min_length=1)


class LogoutRequest(pydantic.BaseModel):
    refresh_token: str | None = None


class ChangePasswordRequest(pydantic.BaseModel):
    old_password: str = pydantic.Field(..., min_length=1)
    new_password: str = pydantic.Field(..., min_length=1, max_length=128)


class LoginResponse(pydantic.BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token: str
    user: dict


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.post("/login")
def login(payload: LoginRequest, request: Request, db: DBDep):
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

    client = _normalize_client(payload.client)
    _revoke_same_client_type_sessions(
        db,
        user.id,
        client["client_type"] or "web",
        "replaced_by_new_login",
    )
    refresh_jti = uuid.uuid4().hex
    session = UserSession(
        user_id=user.id,
        refresh_token_hash="pending",
        jti=refresh_jti,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
        expires_at=_now_naive() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        last_used_at=_now_naive(),
        **client,
    )
    db.session.add(session)
    db.session.flush()

    refresh_token = _create_refresh_token(user.id, session.id, refresh_jti)
    session.refresh_token_hash = _hash_token(refresh_token)
    access_token = _create_access_token(user.id, session.id)
    return {
        "status": "success",
        "data": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            # 兼容旧前端字段名。
            "token": access_token,
            "user": user.to_dict(),
        },
    }


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {"status": "success", "data": current_user.to_dict()}


@router.get("/sessions")
def list_sessions(
    db: DBDep,
    current_user: User = Depends(get_current_user),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    current_session_id: int | None = None
    if creds is not None:
        payload = _decode_token(creds.credentials, "access")
        session_id = payload.get("sid")
        current_session_id = int(session_id) if session_id else None

    sessions = (
        db.session.query(UserSession)
        .filter(UserSession.user_id == current_user.id)
        .order_by(UserSession.created_at.desc())
        .all()
    )
    now = _now_naive()
    data = []
    for session in sessions:
        item = session.to_dict()
        item["is_current"] = session.id == current_session_id
        item["is_active"] = session.revoked_at is None and session.expires_at > now
        data.append(item)
    return {"status": "success", "data": data}


@router.post("/refresh")
def refresh_token(payload: RefreshRequest, db: DBDep):
    decoded = _decode_token(payload.refresh_token, "refresh")
    user_id = decoded.get("sub")
    session_id = decoded.get("sid")
    if not user_id or not session_id:
        raise HTTPException(status_code=401, detail="token 格式错误")

    session = _get_session_or_401(db, int(session_id), _hash_token(payload.refresh_token))
    if session.user_id != int(user_id):
        raise HTTPException(status_code=401, detail="token 格式错误")

    user = (
        db.session.query(User)
        .options(selectinload(User.roles))
        .filter(User.id == int(user_id))
        .first()
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")

    session.last_used_at = _now_naive()
    access_token = _create_access_token(user.id, session.id)
    return {
        "status": "success",
        "data": {
            "access_token": access_token,
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "token": access_token,
        },
    }


@router.post("/logout")
def logout(payload: LogoutRequest, db: DBDep):
    if not payload.refresh_token:
        return {"status": "success", "message": "已退出登录"}

    decoded = _decode_token(payload.refresh_token, "refresh")
    session_id = decoded.get("sid")
    if session_id:
        session = db.session.query(UserSession).filter(UserSession.id == int(session_id)).first()
        if session and session.revoked_at is None:
            session.revoked_at = _now_naive()
            session.revoked_reason = "logout"
    return {"status": "success", "message": "已退出登录"}


@router.post("/logout-all")
def logout_all(db: DBDep, current_user: User = Depends(get_current_user)):
    count = revoke_user_sessions(db, current_user.id, "logout_all")
    return {"status": "success", "data": {"revoked": count}, "message": "已退出全部设备"}


@router.put("/password")
def change_password(
    payload: ChangePasswordRequest,
    db: DBDep,
    current_user: User = Depends(get_current_user),
):
    if current_user.username == PROTECTED_ADMIN_USERNAME:
        raise HTTPException(status_code=403, detail="admin 账号为系统内置账号，禁止修改密码")

    if not current_user.password_hash:
        raise HTTPException(status_code=400, detail="当前用户未设置密码")

    if not _verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="旧密码错误")

    current_user.password_hash = _hash_password(payload.new_password)
    revoke_user_sessions(db, current_user.id, "password_changed")
    db.session.flush()
    return {"status": "success", "message": "密码修改成功"}
