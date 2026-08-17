"""独立脚本运行时的加密兼容工具。

只依赖第三方 ``cryptography``，不导入平台 ``utils``。旧脚本可继续直接使用
``crypto.xxx``；新脚本也可以自行 import cryptography 实现项目逻辑。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)


TEST_PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDSX079rpDZZrp+
dyGifZnkzjW6NLweyu7JgdUXuY0uGTOuXVK3tCANhdpUOWqAOqZR4556j+8NnR0q
9Ffrjc3endFAyVf0hIIiVQxDq53lvvZ+zIfFBkQq675T2rNGlFdk9u3KSlCfFWSt
ZZcE/5mkW9zb+hZUDlhE1WClkncp99uoHkiCMnXCJTf6nOfoFd08NY0D12IbxZ80
bMnJDoJLlCxiJTOsLu7Tg32k+h5/M+Bk5ssvRLizUnRT88oEZTXaaGaqXotN/ZFG
hN14DkguIRLVtv8t3nvLbZqlfT84+RhDY8oO2e9ps2f0RvdfNIqL1cZYNOcJ7ZBB
b5cLD6FPAgMBAAECggEAJ8Uf1EJ7nLXYfM79u0++V6yKKK0OgU3A7bRPOiB4aaYX
CJgY6qSxSI0s9K40DZDI34NF8wqh2TOCD5xIwL26lSLFq9dEevPP/DiSzHo1q/i5
dcgpxJwGKA0QGp8mNCoYCXzLAGqjKifrtAIYFjhR7en161owfWyG0GB8WGDDiVm7
2ljF6hoJ8dCPBr335J374I1XOW1bCUYFcGyVm7SYCtLpegus6qX5rwa/267GSJ0y
JI6HxrK1FmtVqZbHFcIRRKsIwKtG0NXMe0ezt1Ya8xF8PSnIkSPr7mu1zGon4LT1
dDzlD9kC09169ShAEn3UiKXasMdJ24yvjRpAlMtn0QKBgQDrlDV3fP4jg9YMWEKZ
eY5t8Z9YMVNeEOO2ZUehol85g73gyXLbnGQlyHNds1GJgEYRjtqL3qOVW1qSRP32
DSlLgDLUs+0+TtQak8NObOyi/S28N+IurzkgLkG7nAFvufAlOFGvmpjG8r8eKd7y
7EX3GUFoNKcvCKoXjfwhmJCE3wKBgQDkm7uZ58AvkxFCEU+0Ia/puikctBaY8f47
x/wr+mE52oG6JyRR6w5gWHIuiyX5W01A+K9p2CVU87mapl38H3YJ+MIGtnsQbqJ3
zVW1yQEYft7q06LrcL19iPM1ru40Cybdl2I0zeiFn/PzcuqUeRdnpKhnTM6YwYn9
tfx6ucCBkQKBgEmdG4QGE+gHJ1jeL5mDyYUDjtZhO3rWbkGtrk+MzJLNXwUiDfkg
Co9f7uTlxuHfqoWMDTDN1nIyhL/WPUGo5TGJktiyjLz+pvrTF6GnGd7onGUHVW9f
I8uxiKrWWgCOqsMGsUfdWEY6zovfa6KfQFGxm6WzZlalL3mCzbm10dsjAoGACyfG
ScZTTH8CspShrQqPyPn6k5n+GEyGuWgS2BqJsAcHmYvba9vqga0PNVI48igQZwE7
nhCcEb8q6W8A2xK18dqfrTAuZSjg6LOuYQaD9SwLuK3HH3IK7RtHsvDsUsHQjbOb
aTQ7Cno5r0GGTORzzeztAs1ur2mSUD0XKu3xhOECgYARvDSVPwYcBSIG7PS/oAv3
LY3aeDa6xDtP6wCz3mLWU3crHROKncioqeM5Ukw4H7jL2v4OSKQd1WVKnUUWoIGB
45iWRnZ2rOeWD+ZUitDu3pWMgAv3g5Q/EmJFFOiAtBKNDfa9jTdP7GD4J+V15KAU
CMeNpHmEPg36rmAxFQPWWw==
-----END PRIVATE KEY-----"""


def _private_key(private_key_pem: str | None = None) -> rsa.RSAPrivateKey:
    value = (private_key_pem or TEST_PRIVATE_KEY_PEM).replace("\\n", "\n").encode("utf-8")
    key = load_pem_private_key(value, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("传入的不是 RSA 私钥")
    return key


def _public_key(public_key_pem: str | None = None) -> rsa.RSAPublicKey:
    if public_key_pem:
        key = load_pem_public_key(str(public_key_pem).replace("\\n", "\n").encode("utf-8"))
        if not isinstance(key, rsa.RSAPublicKey):
            raise TypeError("传入的不是 RSA 公钥")
        return key
    return _private_key().public_key()


TEST_PUBLIC_KEY_PEM = _public_key().public_bytes(
    Encoding.PEM,
    PublicFormat.SubjectPublicKeyInfo,
).decode("ascii")


def _to_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def rsa_aes_ecb_encrypt(data: Any, public_key_pem: str | None = None) -> dict[str, str]:
    aes_key = os.urandom(16)
    padder = PKCS7(128).padder()
    padded = padder.update(_to_bytes(data)) + padder.finalize()
    encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    encrypted_key = _public_key(public_key_pem).encrypt(aes_key, padding.PKCS1v15())
    return {
        "key": base64.b64encode(encrypted_key).decode("ascii"),
        "data": base64.b64encode(encrypted).decode("ascii"),
    }


def rsa_aes_ecb_decrypt(payload: Any, private_key_pem: str | None = None) -> Any:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or "key" not in payload or "data" not in payload:
        raise ValueError('密文信封必须包含 "key" 和 "data"')
    aes_key = _private_key(private_key_pem).decrypt(
        base64.b64decode(payload["key"]),
        padding.PKCS1v15(),
    )
    decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()
    padded = decryptor.update(base64.b64decode(payload["data"])) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    raw = unpadder.update(padded) + unpadder.finalize()
    text = raw.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _aes_key(secret: str) -> bytes:
    if not secret:
        raise ValueError("AES key 不能为空")
    return hashlib.sha256(str(secret).encode("utf-8")).digest()


def aes_gcm_encrypt(plaintext: Any, key: str) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(_aes_key(key)).encrypt(nonce, _to_bytes(plaintext), None)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def aes_gcm_decrypt(token: str, key: str) -> str:
    blob = base64.urlsafe_b64decode(str(token).encode("ascii"))
    return AESGCM(_aes_key(key)).decrypt(blob[:12], blob[12:], None).decode("utf-8")


def md5(text: Any) -> str:
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


def sha256(text: Any) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def hmac_sha256(text: Any, key: Any) -> str:
    return hmac.new(str(key).encode("utf-8"), str(text).encode("utf-8"), hashlib.sha256).hexdigest()


def canonical(params: Any, fields: list[str] | None = None) -> str:
    if not isinstance(params, dict):
        return ""
    keys = [key for key in fields or [] if key in params] if fields else sorted(params)
    return "&".join(f"{key}={params[key]}" for key in keys)


def b64encode(raw: Any) -> str:
    return base64.b64encode(_to_bytes(raw)).decode("ascii")


def b64decode(text: str) -> str:
    return base64.b64decode(str(text)).decode("utf-8", errors="replace")


def random_hex(nbytes: int = 8) -> str:
    return os.urandom(int(nbytes)).hex()


def now_ms() -> int:
    return int(time.time() * 1000)


def should_apply(config: Any, vars: Any = None, flag: str = "rel_crypto") -> bool:  # noqa: A002
    config = config or {}
    vars = vars or {}
    explicit = vars.get(flag)
    if explicit not in (None, ""):
        return str(explicit).strip().lower() in {"1", "true", "on", "yes", "y", "启用", "开启"}
    scope = str(config.get("crypto_scope") or "all").strip().lower()
    if scope in {"", "all", "global"}:
        return True
    modules = _as_list(config.get("crypto_modules"))
    cases = _as_list(config.get("crypto_cases"))
    paths = _as_list(config.get("crypto_paths"))
    current_path = str(vars.get("_request_path") or "")
    current_url = str(vars.get("_request_url") or "")
    hit = bool(
        str(vars.get("_module_name") or "") in modules
        or str(vars.get("_case_name") or "") in cases
        or str(vars.get("_case_id") or "") in cases
        or _path_matches(paths, current_path, current_url)
    )
    return hit if scope in {"include", "whitelist", "only"} else not hit


def _as_list(raw: Any) -> list[str]:
    if raw in (None, "", False):
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if text.startswith("["):
        try:
            return [str(item).strip() for item in json.loads(text) if str(item).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
    return [item.strip() for item in text.split(",") if item.strip()]


def _path_matches(patterns: list[str], path: str, url: str) -> bool:
    for pattern in patterns:
        if pattern.endswith("*") and (path.startswith(pattern[:-1]) or url.startswith(pattern[:-1])):
            return True
        if path == pattern or url == pattern:
            return True
    return False


def generate_rsa_keypair(bits: int = 2048) -> tuple[str, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return (
        private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii"),
        private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii"),
    )
