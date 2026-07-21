"""单轮测试运行内的认证响应缓存。

同一组账号凭据不应给几十条用例逐条重新登录，否则很快触发接口限流。缓存只存活于
当前 pytest 运行进程，按“登录方法 + 路径 + 已解析请求参数”去重；登出、改密或服务端
返回 401/403 时，由 HTTP Runner 按实际使用过的 token 精准失效对应缓存项。
"""
from __future__ import annotations

import hashlib
import json
import re
from threading import Lock
from typing import Any

from utils.platform_utils import extractor, rep_expr


_LOGIN_PATH_HINTS = ("login", "signin", "sign_in", "/auth/token")
_PATH_LEAF_RE = re.compile(r"([A-Za-z_][\w-]*)$")


def is_login_path(path: str) -> bool:
    """判断请求是否是登录/签发 token 接口。"""
    normalized = str(path or "").lower()
    return any(hint in normalized for hint in _LOGIN_PATH_HINTS) and "register" not in normalized


def build_auth_request_signature(config: dict, ctx: Any) -> str | None:
    """构建不暴露凭据明文的登录请求指纹。"""
    path = str(config.get("path") or config.get("url") or "")
    if not is_login_path(path):
        return None
    pool = getattr(ctx, "vars", {}) if ctx is not None else {}
    payload = {
        "method": str(config.get("method") or "POST").upper(),
        "path": path,
        "params": _render_references(config.get("params") or config.get("body") or {}, pool),
        "headers": _render_references(config.get("headers") or {}, pool),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_hook_values(config: dict, response_body: Any) -> dict[str, Any]:
    """按 hook 规则提取变量；JSONPath 层级写错时按唯一叶子键安全纠偏。"""
    rules = config.get("extract_data") or config.get("extract") or {}
    if not isinstance(rules, dict):
        return {}
    extracted: dict[str, Any] = {}
    for name, raw_path in rules.items():
        path = str(raw_path or "")
        value = extractor(response_body, path) if path else None
        if value is None:
            leaf_match = _PATH_LEAF_RE.search(path)
            leaf = leaf_match.group(1) if leaf_match else _semantic_leaf(str(name))
            candidates = _find_key_values(response_body, leaf)
            if len(candidates) == 1:
                value = candidates[0]
        if value is not None:
            extracted[str(name)] = value
    return extracted


class RunAuthCache:
    """线程安全的单轮认证响应缓存；不持久化、不写日志。"""

    def __init__(self) -> None:
        self._responses: dict[str, Any] = {}
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._responses.clear()

    def get(self, signature: str | None) -> Any:
        if not signature:
            return None
        with self._lock:
            return self._responses.get(signature)

    def put(self, signature: str | None, response_body: Any) -> None:
        if not signature or response_body is None:
            return
        with self._lock:
            self._responses[signature] = response_body

    def invalidate_if_used(self, request_data: Any) -> int:
        """若请求携带了某缓存响应里的 token，删除对应缓存。"""
        request_text = json.dumps(request_data, ensure_ascii=False, default=str)
        removed = 0
        with self._lock:
            for signature, response in list(self._responses.items()):
                tokens = _token_values(response)
                if any(token and token in request_text for token in tokens):
                    self._responses.pop(signature, None)
                    removed += 1
        return removed


def _render_references(value: Any, pool: dict) -> Any:
    if isinstance(value, dict):
        return {str(k): _render_references(v, pool) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_references(item, pool) for item in value]
    if isinstance(value, str):
        return rep_expr(value, pool)
    return value


def _semantic_leaf(name: str) -> str:
    normalized = name.lower()
    if "refresh" in normalized and "token" in normalized:
        return "refresh_token"
    if "token" in normalized:
        return "access_token"
    return name


def _find_key_values(node: Any, target_key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() == target_key.lower() and value is not None:
                found.append(value)
            if isinstance(value, (dict, list)):
                found.extend(_find_key_values(value, target_key))
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                found.extend(_find_key_values(value, target_key))
    return found


def _token_values(node: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if "token" in str(key).lower() and isinstance(value, str) and value:
                values.add(value)
            elif isinstance(value, (dict, list)):
                values |= _token_values(value)
    elif isinstance(node, list):
        for value in node:
            values |= _token_values(value)
    return values
