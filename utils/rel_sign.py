"""REL 风格请求签名头：power-timestamp / power-nonce / power-access-key / power-sign。

签名算法（与平台 ParameterEncryption 同构）::

    result = "&".join(f"{k}={v}" for k, v in 业务参数按 key 升序)
    raw    = f"{result}&{timestamp}{nonce}{secret}"
    sign   = md5(raw)                       # 十六进制小写

其中 ``secret`` 只参与哈希、**从不随请求发送**；``timestamp``/``nonce`` 每次请求现生成。
签名对象是**解密后的业务参数**（不是密文信封），这样验签方解密后即可复算。

生成与验签在同一个文件里，两端（客户端 handler + 服务端靶子）共用，保证一致。
密钥/access-key 内置默认值，可被配置覆盖；服务端靶子用同一份默认值验签。
"""
from __future__ import annotations

import hashlib
import hmac
import random
import string
import time
from typing import Any

# 两端共享的默认签名密钥与 access-key（写死；配置可覆盖，但要与靶子一致才验得过）
DEFAULT_SIGN_SECRET = "rel-echo-sign-secret-2026"
DEFAULT_ACCESS_KEY = "REL_ECHO_AK"

# 时间戳容忍窗口（毫秒）：请求 timestamp 与服务端当前时间偏差超过它即判过期
TIMESTAMP_WINDOW_MS = 5 * 60 * 1000

H_TIMESTAMP = "power-timestamp"
H_NONCE = "power-nonce"
H_ACCESS_KEY = "power-access-key"
H_SIGN = "power-sign"


def _canonical(params: Any) -> str:
    """业务参数按 key 升序拼成 k=v&k=v；非 dict 返回空串。"""
    if not isinstance(params, dict):
        return ""
    return "&".join(f"{key}={params[key]}" for key in sorted(params.keys()))


def compute_sign(params: Any, timestamp: str, nonce: str, secret: str) -> str:
    raw = f"{_canonical(params)}&{timestamp}{nonce}{secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def build_sign_headers(
    params: Any,
    *,
    secret: str = DEFAULT_SIGN_SECRET,
    access_key: str = DEFAULT_ACCESS_KEY,
) -> dict[str, str]:
    """为一次请求生成签名头（timestamp/nonce 现生成）。"""
    timestamp = str(int(round(time.time() * 1000)))
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=8))
    return {
        H_TIMESTAMP: timestamp,
        H_NONCE: nonce,
        H_ACCESS_KEY: access_key or DEFAULT_ACCESS_KEY,
        H_SIGN: compute_sign(params, timestamp, nonce, secret or DEFAULT_SIGN_SECRET),
    }


# 简单的进程内 nonce 防重放缓存：nonce -> 过期时刻(ms)
_seen_nonces: dict[str, int] = {}


def _evict_expired(now_ms: int) -> None:
    for nonce in [n for n, exp in _seen_nonces.items() if exp <= now_ms]:
        _seen_nonces.pop(nonce, None)


def verify_sign_headers(
    headers: dict[str, Any],
    params: Any,
    *,
    secret: str = DEFAULT_SIGN_SECRET,
    access_key: str = DEFAULT_ACCESS_KEY,
    window_ms: int = TIMESTAMP_WINDOW_MS,
) -> tuple[bool, str]:
    """服务端验签：校验签名头齐全、access-key、时间戳窗口、nonce 重放、签名值。

    返回 (是否通过, 原因)。``params`` 传解密后的业务参数。
    """
    lower = {str(k).lower(): v for k, v in (headers or {}).items()}
    timestamp = lower.get(H_TIMESTAMP)
    nonce = lower.get(H_NONCE)
    ak = lower.get(H_ACCESS_KEY)
    sign = lower.get(H_SIGN)

    if not (timestamp and nonce and ak and sign):
        return False, "缺少签名头（power-timestamp/nonce/access-key/sign）"
    if (access_key or DEFAULT_ACCESS_KEY) != str(ak):
        return False, "power-access-key 不匹配"
    try:
        ts_ms = int(timestamp)
    except (TypeError, ValueError):
        return False, "power-timestamp 非法"

    now_ms = int(round(time.time() * 1000))
    if abs(now_ms - ts_ms) > window_ms:
        return False, "power-timestamp 已过期"

    _evict_expired(now_ms)
    if str(nonce) in _seen_nonces:
        return False, "power-nonce 重放"

    expected = compute_sign(params, str(timestamp), str(nonce), secret or DEFAULT_SIGN_SECRET)
    if not hmac.compare_digest(expected, str(sign)):
        return False, "power-sign 不匹配"

    _seen_nonces[str(nonce)] = now_ms + window_ms
    return True, "ok"
