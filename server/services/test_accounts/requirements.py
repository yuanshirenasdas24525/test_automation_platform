from __future__ import annotations

import re
from typing import Any


_USERNAME_MARKERS = ("username", "user_name", "account", "用户名", "账号")
_PASSWORD_MARKERS = ("password", "passwd", "pwd", "密码")


def _variable_key(variables: dict[str, Any], markers: tuple[str, ...], fallback: str) -> str:
    for key in variables:
        lowered = str(key).lower()
        if any(marker in lowered for marker in markers):
            return str(key)
    return fallback


def infer_account_requirement(
    title: str | None,
    description: str | None = None,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从用例意图推导账号能力，不推导真实凭据。

    这是生成器和历史回填共用的确定性门禁。AI 即使漏写测试数据声明，也不能让
    密码为空的"成功登录"脚本被误判成可执行。
    """
    values = dict(variables or {})
    text = f"{title or ''} {description or ''}".lower()
    username_variable = _variable_key(values, _USERNAME_MARKERS, "username")
    password_variable = _variable_key(values, _PASSWORD_MARKERS, "password")
    base = {
        "version": 1,
        "status": "ready",
        "profile": "none",
        "credential_mode": "none",
        "username_variable": username_variable,
        "password_variable": password_variable,
        "lifecycle": "none",
        "reason": "该场景不需要真实账号",
    }

    if any(marker in text for marker in ("页面加载", "页加载", "页面展示")) or not values:
        return base
    if "用户名和密码均为空" in text or "用户名密码均为空" in text:
        return {**base, "profile": "form_empty", "credential_mode": "both_empty"}
    if "用户名为空" in text:
        return {**base, "profile": "form_empty", "credential_mode": "empty_username"}
    if "密码为空" in text:
        return {**base, "profile": "form_empty", "credential_mode": "empty_password"}
    if "不存在的用户名" in text or "用户不存在" in text:
        return {
            **base,
            "profile": "synthetic_nonexistent",
            "credential_mode": "wrong",
            "reason": "使用每次运行唯一且确定不存在的用户名",
        }

    contract_mismatch = None
    if re.search(r"密码长度\s*5\s*位", text) or "密码长度5位" in text:
        contract_mismatch = "登录接口只要求密码非空；密码最小6位属于创建用户规则"
    elif "用户名包含非法字符" in text:
        contract_mismatch = "登录接口不校验用户名字符集；字符规则属于创建用户接口"
    elif re.search(r"用户名长度\s*65\s*位", text) or "用户名长度65位" in text:
        contract_mismatch = "当前页面没有与接口422错误文案一致的可验证预期"
    if contract_mismatch:
        return {
            **base,
            "status": "contract_mismatch",
            "profile": "unsupported",
            "reason": contract_mismatch,
        }

    if any(marker in text for marker in ("锁定时间过后", "其他来源", "任何来源")):
        return {
            **base,
            "status": "unsupported",
            "profile": "isolated_lock_account",
            "lifecycle": "dynamic",
            "reason": "需要可控时钟或多浏览器来源，不能由当前单浏览器稳定准备",
        }
    if "锁定期间" in text:
        return {
            **base,
            "status": "unsupported",
            "profile": "isolated_lock_account",
            "lifecycle": "dynamic",
            "reason": "需要先在同一来源准备锁定状态，当前脚本缺少可靠前置步骤",
        }
    if "连续5次" in text or "连续 5 次" in text:
        return {
            **base,
            "profile": "isolated_lock_account",
            "credential_mode": "wrong",
            "lifecycle": "dynamic",
            "reason": "每次运行创建独立账号，避免锁定共享账号",
        }
    if "已停用账号" in text or "停用账号" in text:
        return {
            **base,
            "profile": "dynamic_disabled",
            "credential_mode": "correct",
            "lifecycle": "dynamic",
            "reason": "运行前创建并停用专用账号",
        }
    if "密码错误" in text or "登录失败后" in text:
        return {
            **base,
            "profile": "dynamic_active",
            "credential_mode": "wrong",
            "lifecycle": "dynamic",
            "reason": "使用独立普通账号和错误密码，避免锁定共享管理员",
        }
    if "admin" in text and ("成功" in text or "默认密码" in text):
        return {
            **base,
            "profile": "shared_admin",
            "credential_mode": "correct",
            "lifecycle": "shared",
            "reason": "绑定项目级加密共享管理员账号",
        }

    profile = "dynamic_active"
    constraints: dict[str, Any] = {}
    if re.search(r"用户名长度\s*64\s*位", text) or "用户名长度64位" in text:
        profile = "dynamic_boundary"
        constraints["username_length"] = 64
    elif "允许的特殊字符" in text:
        profile = "dynamic_boundary"
        constraints["username_allowed_special"] = True
    elif re.search(r"密码长度\s*6\s*位", text) or "密码长度6位" in text:
        profile = "dynamic_boundary"
        constraints["password_length"] = 6
    elif re.search(r"密码长度\s*128\s*位", text) or "密码长度128位" in text:
        profile = "dynamic_boundary"
        constraints["password_length"] = 128

    return {
        **base,
        "profile": profile,
        "credential_mode": "correct",
        "lifecycle": "dynamic",
        "constraints": constraints,
        "reason": "运行前创建满足条件的独立测试账号",
    }
