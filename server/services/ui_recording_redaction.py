"""UI 录制与执行上下文的服务端纵深脱敏。"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|credential|authorization|cookie|token|signature|card|cvv|cvc)",
)
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b",
)


def _sensitive_key(value: str) -> bool:
    return _SENSITIVE_KEY_RE.search(value) is not None


def redact_context_url(value: str) -> str:
    """保留业务 URL 结构，查询参数中的凭据值统一遮盖。"""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        return redact_context_text(value)[:4000]
    query = urlencode([
        (key, "***" if _sensitive_key(key) else item_value)
        for key, item_value in parse_qsl(parts.query, keep_blank_values=True)
    ])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))[:4000]


def redact_context_text(value: str) -> str:
    """遮盖自由文本中的 Bearer/JWT 和常见键值凭据。"""
    redacted = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer ***",
        value,
    )
    redacted = _JWT_RE.sub("***", redacted)
    return re.sub(
        r'(?i)(["\']?(?:password|passwd|secret|token|authorization|cookie|cvv|cvc)["\']?\s*[:=]\s*)["\']?[^"\'\s,&}]+',
        r"\1***",
        redacted,
    )


def redact_context_body(value: str, content_type: str = "") -> str:
    """按 JSON/Form 结构脱敏正文，解析失败时回退到自由文本。"""
    stripped = value.strip()
    if "json" in content_type.lower() or stripped.startswith(("{", "[")):
        try:
            return json.dumps(
                redact_context_payload(json.loads(value)),
                ensure_ascii=False,
            )
        except (TypeError, ValueError):
            pass
    if "x-www-form-urlencoded" in content_type.lower():
        try:
            return urlencode([
                (key, "***" if _sensitive_key(key) else item_value)
                for key, item_value in parse_qsl(value, keep_blank_values=True)
            ])
        except ValueError:
            pass
    return redact_context_text(value)


def redact_context_payload(value: Any, *, key: str = "") -> Any:
    """递归脱敏结构化上下文；可安全用于 JSONB 整体重新赋值。"""
    if key and _sensitive_key(key):
        return "***"
    if isinstance(value, dict):
        headers = value.get("headers")
        content_type = ""
        if isinstance(headers, dict):
            content_type = str(
                headers.get("content-type")
                or headers.get("Content-Type")
                or ""
            )
        element = value.get("element")
        element_attributes = element.get("attributes") if isinstance(element, dict) else None
        sensitive_input = bool(value.get("redacted")) or (
            isinstance(element_attributes, dict)
            and str(element_attributes.get("type") or "").lower() == "password"
        )
        result: dict[str, Any] = {}
        for item_key, item_value in value.items():
            normalized_key = str(item_key)
            lowered = normalized_key.lower()
            if _sensitive_key(normalized_key):
                result[normalized_key] = "***"
            elif lowered in {"url", "source_url", "entry_url"} and isinstance(item_value, str):
                result[normalized_key] = redact_context_url(item_value)
            elif lowered == "headers" and isinstance(item_value, dict):
                result[normalized_key] = {
                    str(header): (
                        "***"
                        if str(header).lower() in _SENSITIVE_HEADERS
                        else redact_context_text(str(header_value))[:2000]
                    )
                    for header, header_value in item_value.items()
                }
            elif lowered == "body" and isinstance(item_value, str):
                result[normalized_key] = redact_context_body(item_value, content_type)
            elif lowered == "value" and sensitive_input:
                result[normalized_key] = "${password}"
            else:
                result[normalized_key] = redact_context_payload(
                    item_value,
                    key=normalized_key,
                )
        return result
    if isinstance(value, list):
        return [redact_context_payload(item) for item in value]
    if isinstance(value, str):
        return redact_context_text(value)[:64_000]
    return value
