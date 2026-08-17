from __future__ import annotations

import os
import secrets as _secrets
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from database.models.script_store import SCRIPT_KIND_WORKFLOW
from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.secrets import TEST_ACCOUNT_FULL_NAME
from utils.script_runtime import run_named_script

# profile → 静态池 state
_PROFILE_STATE = {
    "shared_admin": "admin",
    "dynamic_active": "normal",
    "dynamic_disabled": "disabled",
    "dynamic_boundary": "boundary",
    "isolated_lock_account": "locked",
}
_STATE_LABEL = {
    "admin": "管理员", "normal": "普通", "disabled": "停用",
    "locked": "锁定", "boundary": "边界",
}


@dataclass
class ResolvedAccount:
    bindings: dict[str, str] = field(default_factory=dict)
    cleanup_token: dict[str, Any] | None = None


def validate_account_requirement(
    session: Session, project_id: int, requirement: dict[str, Any]
) -> list[str]:
    """运行/提交前校验需求可否绑定；返回错误文案列表（空=可绑定）。"""
    status = str(requirement.get("status") or "ready")
    if status != "ready":
        return [str(requirement.get("reason") or "测试数据前置条件不满足")]
    profile = str(requirement.get("profile") or "none")
    if profile in {"none", "form_empty", "synthetic_nonexistent"}:
        return []
    if profile == "isolated_lock_account" and os.getenv(
        "LOGIN_THROTTLE_ENABLED", "1"
    ).strip().lower() in {"0", "false", "no", "off"}:
        return ["当前环境已关闭登录限流，无法验证连续失败后的账号锁定"]
    from server.services.test_accounts.sources import load_account_sources

    sources = load_account_sources(session, project_id)
    if _pick_pool_account(sources["accounts"], profile) is not None:
        return []
    if sources["dynamic_script"]:
        return []
    state = _PROFILE_STATE.get(profile, "normal")
    return [
        f"项目未声明满足『{_STATE_LABEL.get(state, state)}』的测试账号，"
        f"请在账号池补充或配置 dynamic_script"
    ]


def _pick_pool_account(accounts: list[dict[str, Any]], profile: str) -> dict[str, Any] | None:
    state = _PROFILE_STATE.get(profile, "normal")
    enabled = [a for a in accounts if a.get("enabled", True)]
    for a in enabled:
        if a.get("state") == state:
            return a
    if state == "normal":  # normal 可回落 admin
        for a in enabled:
            if a.get("state") == "admin":
                return a
    return None


def resolve_account(
    requirement: dict[str, Any],
    sources: dict[str, Any],
    *,
    session: Session | None,
    project_id: int,
) -> ResolvedAccount:
    profile = str(requirement.get("profile") or "none")
    mode = str(requirement.get("credential_mode") or "none")
    ukey = str(requirement.get("username_variable") or "username")
    pkey = str(requirement.get("password_variable") or "password")

    if profile == "none":
        return ResolvedAccount()
    if profile == "form_empty":
        username = "" if mode in {"both_empty", "empty_username"} else f"AUTO_FORM_{_secrets.token_hex(4)}"
        password = "" if mode in {"both_empty", "empty_password"} else "Validation#1"
        return ResolvedAccount(bindings={ukey: username, pkey: password})
    if profile == "synthetic_nonexistent":
        return ResolvedAccount(bindings={
            ukey: f"AUTO_MISSING_{_secrets.token_hex(6)}", pkey: "Wrong#1234",
        })

    account = _pick_pool_account(sources["accounts"], profile)
    if account is not None:
        return ResolvedAccount(bindings={ukey: account["username"], pkey: account["password"]})

    script_name = str(sources.get("dynamic_script") or "").strip()
    if script_name:
        return _resolve_via_script(script_name, requirement, project_id, ukey, pkey)

    state = _PROFILE_STATE.get(profile, "normal")
    raise WebTestDataError(
        f"项目未声明满足『{_STATE_LABEL.get(state, state)}』的测试账号，"
        f"请在账号池补充或配置 dynamic_script"
    )


def _resolve_via_script(
    script_name: str, requirement: dict[str, Any], project_id: int, ukey: str, pkey: str
) -> ResolvedAccount:
    username = f"AUTO_UI_{_secrets.token_hex(4)}"
    password = f"Auto#{_secrets.token_hex(4)}"
    found, output = run_named_script(
        script_name,
        kind=SCRIPT_KIND_WORKFLOW,
        project_id=project_id,
        body={
            "action": "create",
            "requirement": requirement,
            "account": {
                "username": username, "password": password,
                "is_active": requirement.get("profile") != "dynamic_disabled",
                "full_name": TEST_ACCOUNT_FULL_NAME,
            },
        },
        config={"project_id": project_id},
        vars={},
        timeout=30,
    )
    if not found:
        raise WebTestDataError(f"未找到启用的 workflow 脚本：{script_name}")
    if not isinstance(output, dict) or output.get("ok") is False:
        reason = output.get("error") or output.get("message") if isinstance(output, dict) else None
        raise WebTestDataError(str(reason or "账号准备脚本返回失败"))
    result = output.get("result") if isinstance(output.get("result"), dict) else {}
    variables = output.get("variables") if isinstance(output.get("variables"), dict) else {}
    resolved_username = str(variables.get("username", result.get("username", username)))
    resolved_password = str(variables.get("password", result.get("password", password)))
    cleanup_payload = output.get("cleanup", result.get("cleanup_token", result))
    return ResolvedAccount(
        bindings={ukey: resolved_username, pkey: resolved_password},
        cleanup_token={"script_name": script_name, "project_id": project_id, "payload": cleanup_payload},
    )
