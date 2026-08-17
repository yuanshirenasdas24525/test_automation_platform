"""单轮执行的跨用例变量生命周期治理。

跨用例变量池不能把 token 当普通字符串处理：同账号重新登录、登出和改密都会改变
会话有效性。这里根据真实请求/响应中的 JWT ``sub`` / ``sid`` 做最小生命周期跟踪，
避免失败用例已经签发新会话、但旧 token 仍留在共享池里继续造成连锁 401。
"""
from __future__ import annotations

import base64
import json
from typing import Any

from runners.context.auth_cache import is_login_path
from runners.protocol import CaseResult, StepStatus


_SENSITIVE_VARIABLE_MARKERS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "密码",
    "令牌",
    "密钥",
)


def is_sensitive_variable_name(name: Any) -> bool:
    """判断变量名是否承载凭据；用于执行记录和跨用例发布门禁。"""
    lowered = str(name or "").lower()
    return any(marker in lowered for marker in _SENSITIVE_VARIABLE_MARKERS)


def redact_variable_pool(pool: dict[str, Any] | None) -> dict[str, Any]:
    """保留变量键供排障，凭据值统一遮盖。"""
    return {
        str(name): "***" if is_sensitive_variable_name(name) else value
        for name, value in (pool or {}).items()
        if not str(name).startswith("_")
    }


def _jwt_claims(value: Any) -> dict[str, Any] | None:
    """不校验签名，只读取 JWT 的非敏感生命周期字段。"""
    if not isinstance(value, str):
        return None
    token = value.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(claims, dict) or claims.get("sub") is None:
        return None
    return {
        "sub": str(claims.get("sub")),
        "sid": str(claims.get("sid")) if claims.get("sid") is not None else None,
        "type": str(claims.get("type") or "access"),
    }


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_strings(child)


def _request_token_claims(input_data: Any) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for value in _walk_strings(input_data):
        parsed = _jwt_claims(value)
        if parsed is not None:
            claims.append(parsed)
    return claims


def _login_tokens(output_data: Any) -> tuple[Any, Any]:
    """读取常见登录信封中的 access/refresh token；找不到时返回 ``(None, None)``。"""
    if not isinstance(output_data, dict):
        return None, None
    data = output_data.get("data")
    if not isinstance(data, dict):
        data = output_data
    return data.get("access_token") or data.get("token"), data.get("refresh_token")


def _response_succeeded(step) -> bool:
    body = step.output_data
    if isinstance(body, dict):
        if body.get("success") is False:
            return False
        status = str(body.get("status") or "").strip().lower()
        if status in {"error", "failed", "failure"}:
            return False
        if status == "success":
            return True
    return step.status == StepStatus.PASSED


def _same_subject(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    return bool(left and right and left.get("sub") == right.get("sub"))


def _identity(claims: dict[str, Any] | None) -> tuple[str, str | None, str] | None:
    if claims is None:
        return None
    return str(claims["sub"]), claims.get("sid"), str(claims.get("type") or "access")


def update_run_shared_vars(
    shared: dict[str, object],
    result: CaseResult,
    ctx,
) -> None:
    """按真实会话生命周期更新单轮共享变量池。

    - 成功登录即刷新同账号的共享 token，即使用例稍后因其它断言失败；
    - 登出按 ``sid`` 淘汰会话，登出全部/改密按 ``sub`` 淘汰账号全部会话；
    - 普通非 token 变量仍只在整条用例最终通过时发布。

    这样既不会发布失败用例的半成品业务数据，又不会保留已经被后续登录替换的旧
    token。该函数只更新运行期内存，不修改用例定义或数据库。
    """
    pool: dict[str, object] = dict(shared)
    unavailable: set[tuple[str, str | None, str]] = set()
    extracted_names = {
        str(name)
        for step in result.steps or []
        for name in (step.extracted or {})
    }
    input_variable_names = {
        str(name)
        for name in (getattr(ctx, "vars", {}).get("_input_variable_names") or [])
    }

    for step in result.steps or []:
        target = str(step.target or "").lower()

        if is_login_path(target):
            access_token, refresh_token = _login_tokens(step.output_data)
            access_claims = _jwt_claims(access_token)
            if access_claims is not None and _response_succeeded(step):
                # 登录接口未显式 extract 时，也要刷新共享池里同一账号的旧变量。
                # 被替换的旧值加入 unavailable，避免用例最终通过时从 ctx.vars 又写回来。
                for name, current in list(pool.items()):
                    current_claims = _jwt_claims(current)
                    if not _same_subject(current_claims, access_claims):
                        continue
                    old_identity = _identity(current_claims)
                    if old_identity is not None:
                        unavailable.add(old_identity)
                    if current_claims.get("type") == "refresh" and refresh_token:
                        pool[name] = refresh_token
                    else:
                        pool[name] = access_token

                # 显式提取的 token 使用用例声明的变量名发布。断言在提取之后失败时，
                # StepResult 仍保留 extracted，正好可以阻断“新会话已生效、共享池还是旧值”。
                for name, value in (step.extracted or {}).items():
                    if _jwt_claims(value) is not None:
                        pool[str(name)] = value

        mutation_scope = None
        if any(hint in target for hint in ("logout-all", "logout_all", "revoke-all")):
            mutation_scope = "subject"
        elif any(hint in target for hint in ("/password", "change-password", "reset-password")):
            mutation_scope = "subject"
        elif any(hint in target for hint in ("logout", "signout", "sign-out", "revoke")):
            mutation_scope = "session"

        if mutation_scope and _response_succeeded(step):
            used_claims = _request_token_claims(step.input_data)
            for name, current in list(pool.items()):
                current_claims = _jwt_claims(current)
                if current_claims is None:
                    continue
                invalid = any(
                    current_claims.get("sub") == used.get("sub")
                    and (
                        mutation_scope == "subject"
                        or (
                            used.get("sid") is not None
                            and current_claims.get("sid") == used.get("sid")
                        )
                    )
                    for used in used_claims
                )
                if invalid:
                    identity = _identity(current_claims)
                    if identity is not None:
                        unavailable.add(identity)
                    pool.pop(name, None)

    if result.status == StepStatus.PASSED:
        for name, value in (getattr(ctx, "vars", {}) or {}).items():
            if str(name).startswith("_"):
                continue
            if is_sensitive_variable_name(name):
                # JWT 在上面的登录生命周期分支按真实会话发布；普通密码、密钥等
                # 永远不能作为“业务变量”串到下一条用例。
                continue
            if str(name) in input_variable_names and str(name) not in extracted_names:
                # 默认参数、环境变量和当前用例输入是本用例前置，不是输出。旧逻辑
                # 把 username/password 全量发布，后一个账号用例因此被前一个覆盖。
                continue
            claims = _jwt_claims(value)
            if claims is not None and _identity(claims) in unavailable:
                continue
            pool[str(name)] = value

    shared.clear()
    shared.update(pool)
