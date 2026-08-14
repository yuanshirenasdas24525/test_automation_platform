"""/api/auth/* —— 登录认证、refresh token、退出登录和修改密码。

access token 短期有效，refresh token 长期有效；密码用 bcrypt 哈希。
refresh token 明文只返回客户端，服务端仅保存哈希值。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import pydantic
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import selectinload

from utils.logger import LOGGER

from database.models import ApiKey, User, UserSession
from database.models import (
    API_KEY_SCOPE_AI, API_KEY_SCOPE_EXECUTE, API_KEY_SCOPE_READ,
)
from server.api.deps import DBDep
from utils.rel_crypto import rel_decrypt_json, rel_encrypt

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# 密码 & JWT 基础设施
# ---------------------------------------------------------------------------
bearer_scheme = HTTPBearer(auto_error=False)
# 已知的公开占位符。任何环境都绝不允许用它签发 JWT——否则谁都能伪造任意用户 token。
DEFAULT_SECRET_KEY = "change-me-to-a-random-string-at-least-32-chars"
MIN_SECRET_KEY_LEN = 32


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _resolve_secret_key() -> str:
    """解析 JWT 密钥并做 fail-closed 校验（不区分环境）。

    历史实现只在 ``APP_ENV in {prod,production}`` 时才强制校验，导致大量「其实是
    生产但没标 APP_ENV」的部署静默用公开默认密钥签 JWT——等于任何人可伪造 token。
    现在无论什么环境：缺失 / 等于公开默认值 / 长度不足一律拒绝，逼使用者显式配置。
    """
    key = os.getenv("JWT_SECRET_KEY")
    if key:
        secret = key.strip()
    else:
        from utils.reload_config import config_center

        secret = str(
            config_center.get("auth", "jwt_secret_key", default="")
        ).strip()

    if not secret or secret == DEFAULT_SECRET_KEY or len(secret) < MIN_SECRET_KEY_LEN:
        raise RuntimeError(
            "必须配置 JWT 密钥：设置环境变量 JWT_SECRET_KEY 或配置中心 auth.jwt_secret_key，"
            f"长度不少于 {MIN_SECRET_KEY_LEN} 位的随机串，且不得使用默认占位值。"
        )
    return secret


_SECRET_KEY_CACHE: str | None = None


def _secret_key() -> str:
    """惰性获取并缓存密钥。

    改为惰性求值（而非模块导入期），这样单测/工具链 import 本模块不会触发读配置、
    连库或直接 RuntimeError；只有真正签发/校验 token 时才要求密钥就位。
    """
    global _SECRET_KEY_CACHE
    if _SECRET_KEY_CACHE is None:
        _SECRET_KEY_CACHE = _resolve_secret_key()
    return _SECRET_KEY_CACHE


ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 14
PROTECTED_ADMIN_USERNAME = "admin"
MULTI_SESSION_CLIENT_TYPES = {"api"}


# ---------------------------------------------------------------------------
# 登录失败节流：按 (username, ip) 计数，短时间内多次失败即临时锁定。
#
# 说明：这是进程内实现，多 worker 下各自计数，不是全局强一致；作为在线爆破的
# 第一道减速带足够，若要严格全局限速请接入 Redis。锁定只针对失败尝试，成功登录
# 立即清零。
# ---------------------------------------------------------------------------
LOGIN_MAX_FAILURES = 5           # 窗口内允许的最大失败次数
LOGIN_FAILURE_WINDOW = 300       # 计数窗口（秒）
LOGIN_LOCKOUT_SECONDS = 300      # 触发后的锁定时长（秒）

# 开关：默认开启（安全优先）。测试环境跑回归时可用 LOGIN_THROTTLE_ENABLED=0 关掉。
#
# 为什么需要这个开关：接口测试套件里有若干"故意用错误密码登录"的负向用例
# （错误密码登录失败、多次错误尝试、限流功能本身的验证等）。跑一轮就把失败计数攒满，
# 5 分钟内跑第二轮时所有需要登录的用例全被 429 挡住 —— 实测一轮回归里 84~86 条
# 因此连锁失败，看起来像"代码越改越差"，其实只是限流窗口没过。
#
# ⚠️ 生产环境务必保持开启：这是防在线爆破的第一道减速带。
def _throttle_enabled() -> bool:
    enabled = os.getenv("LOGIN_THROTTLE_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    global _throttle_warned
    if not enabled and not _throttle_warned:
        _throttle_warned = True
        LOGGER.warning(
            "[auth] 登录限流已被 LOGIN_THROTTLE_ENABLED 关闭 —— "
            "暴力破解保护失效，仅应在测试环境这样配置"
        )
    return enabled


_throttle_warned = False


_login_attempts: dict[str, tuple[int, float, float]] = {}  # key -> (fails, window_start, locked_until)
_login_attempts_lock = threading.Lock()


def _login_throttle_key(username: str, ip: str | None) -> str:
    return f"{username}\x00{ip or '-'}"


def _check_login_locked(username: str, ip: str | None) -> int:
    """返回剩余锁定秒数；0 表示未锁定。"""
    if not _throttle_enabled():
        return 0
    key = _login_throttle_key(username, ip)
    now = time.monotonic()
    with _login_attempts_lock:
        entry = _login_attempts.get(key)
        if not entry:
            return 0
        _, _, locked_until = entry
        if locked_until > now:
            return int(locked_until - now) + 1
    return 0


def _register_login_failure(username: str, ip: str | None) -> None:
    if not _throttle_enabled():
        return
    key = _login_throttle_key(username, ip)
    now = time.monotonic()
    with _login_attempts_lock:
        fails, window_start, locked_until = _login_attempts.get(key, (0, now, 0.0))
        # 窗口过期则重新计数
        if now - window_start > LOGIN_FAILURE_WINDOW:
            fails, window_start = 0, now
        fails += 1
        if fails >= LOGIN_MAX_FAILURES:
            locked_until = now + LOGIN_LOCKOUT_SECONDS
            fails, window_start = 0, now
        _login_attempts[key] = (fails, window_start, locked_until)


def _clear_login_failures(username: str, ip: str | None) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(_login_throttle_key(username, ip), None)


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
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


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
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


def _decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=["HS256"])
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


def _revoke_same_client_type_sessions(
    db: DBDep,
    user_id: int,
    client_type: str,
    device_id: str | None,
    reason: str,
) -> int:
    """同一账号、客户端类型和浏览器设备只保留最新会话。"""
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
    revoked = 0
    for row in rows:
        if device_id is not None and row.device_id != device_id:
            continue
        row.revoked_at = now
        row.revoked_reason = reason
        revoked += 1
    return revoked


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
# API Key 鉴权（长效 service token，给 MCP server / CI 等机器调用方）
# ---------------------------------------------------------------------------
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

# API Key 一律摸不到的资源（无论 scope）：认证、密钥管理、用户与角色管理
_API_KEY_DENY_PREFIXES = ("/api/auth", "/api/api-keys", "/api/users", "/api/roles")


def _check_api_key_scope(key: ApiKey, method: str, path: str) -> None:
    """scope 白名单校验：read=全部 GET；非 GET 只放行显式列出的路径。"""
    for deny in _API_KEY_DENY_PREFIXES:
        if path.startswith(deny):
            raise HTTPException(status_code=403, detail="API Key 不允许访问该资源")

    scopes = set(key.scopes or [])
    if method == "GET":
        if API_KEY_SCOPE_READ not in scopes:
            raise HTTPException(status_code=403, detail="该 API Key 缺少 read scope")
        return

    allowed: list[tuple[str, str]] = []
    if API_KEY_SCOPE_EXECUTE in scopes:
        allowed.append(("POST", "/api/run_test"))
    if API_KEY_SCOPE_AI in scopes:
        allowed.append(("POST", "/api/functional_cases/ai_diagnose_report"))
        allowed.append(("POST", "/api/functional_cases/ai_report_fix/apply"))
        # 规则分诊落成诊断：与 ai_diagnose_report 同性质（产出可应用的诊断），只是判定方是规则
        if path.startswith("/api/reports/") and path.endswith("/triage/diagnosis"):
            allowed.append((method, path))
    if (method, path) not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"该 API Key 的 scope 不允许 {method} {path}",
        )


def _authenticate_api_key(db: DBDep, request: Request, raw_key: str) -> User:
    """X-API-Key 鉴权：哈希查表 → 有效性/过期/scope 校验 → 以签发人身份执行。"""
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    key = db.session.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()
    if key is None or not key.is_active:
        raise HTTPException(status_code=401, detail="API Key 无效或已吊销")
    if key.expires_at and key.expires_at < datetime.now():
        raise HTTPException(status_code=401, detail="API Key 已过期")

    _check_api_key_scope(key, request.method, request.url.path)

    user = (
        db.session.query(User)
        .options(selectinload(User.roles))
        .filter(User.id == key.created_by)
        .first()
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="API Key 签发人不存在或已停用")

    key.last_used_at = datetime.now()   # commit 由 get_db 兜底
    request.state.api_key_id = key.id   # 给下游审计用
    return user


def _authenticate_bearer(
    db: DBDep,
    creds: HTTPAuthorizationCredentials | None,
) -> User:
    """只使用 Bearer token 认证，供明确禁止 API Key 的资源复用。"""
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


# ---------------------------------------------------------------------------
# 依赖：从 Bearer Token / X-API-Key 获取当前用户（给其他路由用的）
# ---------------------------------------------------------------------------
def get_current_user_bearer(
    db: DBDep,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """仅允许 Bearer token；OpenAPI 也只声明 HTTPBearer。"""
    return _authenticate_bearer(db, creds)


def get_current_user(
    db: DBDep,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Depends(api_key_scheme),
) -> User:
    if api_key:
        return _authenticate_api_key(db, request, api_key)
    return _authenticate_bearer(db, creds)


def _get_optional_user(
    db: DBDep,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    api_key: str | None = Depends(api_key_scheme),
) -> User | None:
    try:
        return get_current_user(db, request, creds, api_key)
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
    user: dict


class LoginEnvelope(pydantic.BaseModel):
    status: str
    data: LoginResponse


class RefreshResponseData(pydantic.BaseModel):
    access_token: str
    expires_in: int


class RefreshResponse(pydantic.BaseModel):
    status: str
    data: RefreshResponseData


class HTTPErrorResponse(pydantic.BaseModel):
    detail: Any


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.post(
    "/login",
    response_model=LoginEnvelope,
    responses={
        401: {"model": HTTPErrorResponse, "description": "用户名或密码错误"},
        429: {"model": HTTPErrorResponse, "description": "登录失败次数过多"},
    },
)
def login(payload: LoginRequest, request: Request, db: DBDep):
    ip = _client_ip(request)

    locked_for = _check_login_locked(payload.username, ip)
    if locked_for > 0:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请 {locked_for} 秒后再试",
        )

    user = (
        db.session.query(User)
        .options(selectinload(User.roles))
        .filter(User.username == payload.username)
        .first()
    )
    if user is None or not user.is_active:
        _register_login_failure(payload.username, ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.password_hash or not _verify_password(payload.password, user.password_hash):
        _register_login_failure(payload.username, ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    _clear_login_failures(payload.username, ip)
    client = _normalize_client(payload.client)
    _revoke_same_client_type_sessions(
        db,
        user.id,
        client["client_type"] or "web",
        client["device_id"],
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
            "user": user.to_dict(),
        },
    }


@router.get(
    "/me",
    responses={401: {"model": HTTPErrorResponse, "description": "未认证或会话失效"}},
)
def me(current_user: User = Depends(get_current_user_bearer)):
    return {"status": "success", "data": current_user.to_dict()}


@router.get(
    "/sessions",
    responses={401: {"model": HTTPErrorResponse, "description": "未认证或会话失效"}},
)
def list_sessions(
    db: DBDep,
    current_user: User = Depends(get_current_user_bearer),
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


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    responses={401: {"model": HTTPErrorResponse, "description": "refresh token 无效或会话失效"}},
)
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
        },
    }


@router.post(
    "/logout",
    responses={401: {"model": HTTPErrorResponse, "description": "refresh token 无效或已过期"}},
)
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


@router.post(
    "/logout-all",
    responses={401: {"model": HTTPErrorResponse, "description": "未认证或会话失效"}},
)
def logout_all(db: DBDep, current_user: User = Depends(get_current_user_bearer)):
    count = revoke_user_sessions(db, current_user.id, "logout_all")
    return {"status": "success", "data": {"revoked": count}, "message": "已退出全部设备"}


@router.put(
    "/password",
    responses={
        400: {"model": HTTPErrorResponse, "description": "旧密码错误或当前用户未设置密码"},
        401: {"model": HTTPErrorResponse, "description": "未认证或会话失效"},
        403: {"model": HTTPErrorResponse, "description": "内置管理员禁止修改密码"},
    },
)
def change_password(
    payload: ChangePasswordRequest,
    db: DBDep,
    current_user: User = Depends(get_current_user_bearer),
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


# ---------------------------------------------------------------------------
# echo_test —— RSA + AES-ECB 加解密自测靶子（强制加密传输）
# ---------------------------------------------------------------------------
# 契约见 echo_test.openapi.json：入参 username(必填 str) + amount(必填 int)
# + note/tags(可选)，成功返回 {status:"success", message:"hello", data:<回显>}。
# 但**线上只收 REL 密文信封 {key,data}**：先私钥解密→按下述规则校验→响应用公钥
# 加密返回。OpenAPI 描述的是解密后的逻辑契约。
#
# 平台用例侧配 encryption_decryption 走 utils.custom_crypto 的 rel_* handler 即可：
#   on_off: true / custom_request_handler: rel_request_crypto
#   custom_response_handler: rel_response_crypto / custom_crypto_only: true
#
# 无需平台登录态：auth_router 挂在 main.py 的无鉴权路由组，模拟外部被测系统。
def _validate_echo_payload(payload: Any) -> dict[str, Any]:
    """按 openapi 契约校验解密后的明文；不合规抛 422。返回回显用的 echo。"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体解密后必须是 JSON 对象")

    errors: list[str] = []
    username = payload.get("username")
    if "username" not in payload:
        errors.append("username 必填")
    elif not isinstance(username, str):
        errors.append("username 必须是字符串")

    amount = payload.get("amount")
    if "amount" not in payload:
        errors.append("amount 必填")
    elif not isinstance(amount, int) or isinstance(amount, bool):
        errors.append("amount 必须是整数")

    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        errors.append("note 必须是字符串")

    tags = payload.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
    ):
        errors.append("tags 必须是字符串数组")

    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))

    echo: dict[str, Any] = {"username": username, "amount": amount}
    if note is not None:
        echo["note"] = note
    if tags is not None:
        echo["tags"] = tags
    return echo


@router.post("/echo_test")
def echo_test(payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    """RSA+AES-ECB 加密回显靶子：解密请求 → 校验 → 加密返回。"""
    try:
        plain = rel_decrypt_json(payload)
    except Exception as exc:  # noqa: BLE001 —— 靶子对外表现要像真实系统
        LOGGER.warning("echo_test 解密失败: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=400, detail=f"密文解密失败: {exc}") from exc

    echo = _validate_echo_payload(plain)
    reply = {"status": "success", "message": "hello", "data": echo}
    return rel_encrypt(reply)
