"""参数修复后的变量回写推导工具。

当 AI 把请求里的 ``${变量}`` 改成一个新值时，后续步骤仍可能继续引用原变量名。
这里根据“修改前模板”和“修改后模板”确定性推导需要回写的提取参数，避免让模型
再次猜变量名和值。
"""
from __future__ import annotations

import re
from typing import Any

_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][\w.-]*)\}")


def is_response_jsonpath(value: Any) -> bool:
    """判断提取值是否是响应 JSONPath，而不是 ``${变量}`` 赋值表达式。"""
    return (
        isinstance(value, str)
        and value.strip().startswith("$")
        and not value.strip().startswith("${")
    )


def extract_rule(name: str, expression: Any) -> dict[str, Any]:
    """把简写提取值转换成结构化规则。"""
    if is_response_jsonpath(expression):
        return {
            "name": str(name),
            "from": "response.body",
            "jsonpath": str(expression),
        }
    return {
        "name": str(name),
        "from": "value",
        "value": expression,
    }


def infer_rebound_extracts(original: Any, repaired: Any) -> dict[str, Any]:
    """推导 AI 修改请求后应回写的原变量。

    示例：

    - ``"${password_admin}" -> "NewTest@123"``
      得到 ``{"password_admin": "NewTest@123"}``
    - ``"Bearer ${token}" -> "Bearer ${new_token}"``
      得到 ``{"token": "${new_token}"}``

    只比较相同字段位置；字段未变化、被删除、含多个变量或前后缀不一致时不推导。
    """
    candidates: dict[str, list[Any]] = {}
    _collect_rebounds(original, repaired, candidates)

    out: dict[str, Any] = {}
    for name, values in candidates.items():
        if not values:
            continue
        first = values[0]
        if all(value == first for value in values[1:]):
            out[name] = first
    return out


def infer_state_transition_extracts(params: Any) -> dict[str, Any]:
    """从“旧值字段 + 新值字段”推导成功请求造成的变量状态变化。

    目前只处理有明确字段对的密码修改，避免仅凭字段名模糊猜测普通参数：
    ``old_password/current_password`` 引用的变量会被 ``new_password`` 覆盖。
    """
    if not isinstance(params, dict):
        return {}
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(key).lower()): value
        for key, value in params.items()
    }
    new_value = normalized.get("newpassword")
    if new_value in (None, ""):
        return {}

    out: dict[str, Any] = {}
    for old_key in ("oldpassword", "currentpassword"):
        old_value = normalized.get(old_key)
        if not isinstance(old_value, str):
            continue
        match = _VAR_REF_RE.fullmatch(old_value.strip())
        if match:
            out[match.group(1).split(".")[0]] = new_value
    return out


def merge_rebound_extracts(*items: dict[str, Any]) -> dict[str, Any]:
    """合并多处回写推导；同一变量出现冲突值时保守丢弃。"""
    merged: dict[str, Any] = {}
    conflicts: set[str] = set()
    for item in items:
        for name, value in (item or {}).items():
            if name in conflicts:
                continue
            if name in merged and merged[name] != value:
                merged.pop(name, None)
                conflicts.add(name)
                continue
            merged[name] = value
    return merged


def _collect_rebounds(
    original: Any,
    repaired: Any,
    candidates: dict[str, list[Any]],
) -> None:
    if isinstance(original, dict) and isinstance(repaired, dict):
        for key in original.keys() & repaired.keys():
            _collect_rebounds(original[key], repaired[key], candidates)
        return

    if isinstance(original, list) and isinstance(repaired, list):
        for before, after in zip(original, repaired):
            _collect_rebounds(before, after, candidates)
        return

    if not isinstance(original, str) or original == repaired or repaired is None:
        return

    refs = list(_VAR_REF_RE.finditer(original))
    if len(refs) != 1:
        return

    match = refs[0]
    name = match.group(1).split(".")[0]
    prefix = original[:match.start()]
    suffix = original[match.end():]

    if not prefix and not suffix:
        value = repaired
    elif isinstance(repaired, str) and repaired.startswith(prefix) and repaired.endswith(suffix):
        end = len(repaired) - len(suffix) if suffix else len(repaired)
        value = repaired[len(prefix):end]
        if value == "":
            return
    else:
        return

    candidates.setdefault(name, []).append(value)
