"""REL 风格的 RSA + AES-ECB 混合加解密（"数字信封"）。

对应前端 node-forge 工具（`RSAES-PKCS1-V1_5` + `AES-ECB`）的等价 Python 实现，
密文信封统一为::

    {"key": "<base64>", "data": "<base64>"}

- ``key``  —— 一次性随机 AES 密钥，用 RSA 公钥（PKCS#1 v1.5）加密后 base64
- ``data`` —— 业务明文用该 AES 密钥做 AES-ECB(PKCS#7) 加密后 base64

方向约定（和前端 HTML 一致）：**公钥加密、私钥解密**。因此：

- 平台作为"客户端"发请求时：用公钥 :func:`rel_encrypt` 加密请求体；
  收到响应后用私钥 :func:`rel_decrypt` 解密。
- ``/api/crypto_echo/echo`` 自测靶子作为"服务端"：用私钥解请求、用公钥加响应。

密钥固定内置（和前端 HTML 里那把同源），纯粹是自测用途，**不要**当成真实
业务密钥使用。
"""
from __future__ import annotations

import base64
import json
import os
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

# ---------------------------------------------------------------------------
# 固定内置密钥（与前端 REL 解密 HTML 同源，仅供自测靶子使用）
# ---------------------------------------------------------------------------
REL_PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
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

# AES 密钥长度（字节）：16=AES-128 / 24=AES-192 / 32=AES-256。
# REL 这类历史方案通常用 AES-128，这里默认 16。
DEFAULT_AES_KEY_SIZE = 16
_AES_BLOCK_BITS = 128  # AES 分组固定 128 bit


@lru_cache(maxsize=1)
def _private_key() -> rsa.RSAPrivateKey:
    key = load_pem_private_key(REL_PRIVATE_KEY_PEM.encode("utf-8"), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("REL_PRIVATE_KEY_PEM 不是 RSA 私钥")
    return key


@lru_cache(maxsize=1)
def _public_key() -> rsa.RSAPublicKey:
    return _private_key().public_key()


def _wrap_pem(b64_body: str, label: str) -> str:
    body = "".join(b64_body.split())
    lines = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----"


def _normalize_pem(text: str, label: str) -> str:
    """把配置里的密钥统一成合法 PEM。

    兼容三种存法：
      1. 完整多行 PEM（带 BEGIN/END）
      2. 单行 PEM——换行被存成字面量 ``\\n``
      3. 纯 base64 主体（无头尾）——按 ``label`` 补上 PEM 头尾（私钥按 PKCS#8）
    """
    t = str(text).strip()
    if "-----BEGIN" in t:
        return t.replace("\\n", "\n")
    return _wrap_pem(t, label)


def _load_public(public_key_pem: str | None) -> rsa.RSAPublicKey:
    if not public_key_pem or not str(public_key_pem).strip():
        return _public_key()
    key = load_pem_public_key(_normalize_pem(public_key_pem, "PUBLIC KEY").encode("utf-8"))
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError("传入的不是 RSA 公钥")
    return key


def _load_private(private_key_pem: str | None) -> rsa.RSAPrivateKey:
    if not private_key_pem or not str(private_key_pem).strip():
        return _private_key()
    key = load_pem_private_key(
        _normalize_pem(private_key_pem, "PRIVATE KEY").encode("utf-8"), password=None
    )
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("传入的不是 RSA 私钥")
    return key


def generate_keypair(key_size: int = 2048) -> tuple[str, str]:
    """生成一对 RSA 密钥，返回 (private_pem_pkcs8, public_pem)。仅便于生成/自测。"""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    from cryptography.hazmat.primitives.serialization import (
        NoEncryption,
        PrivateFormat,
    )

    private_pem = priv.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode("ascii")
    public_pem = priv.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    return private_pem, public_pem


def public_key_pem() -> str:
    """返回内置私钥派生出的公钥 PEM（发请求方加密时用）。"""
    return _public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def _to_bytes(plaintext: Any) -> bytes:
    """str 原样 utf-8；dict/list 走紧凑 JSON；其余转字符串。"""
    if isinstance(plaintext, bytes):
        return plaintext
    if isinstance(plaintext, str):
        return plaintext.encode("utf-8")
    return json.dumps(plaintext, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def rel_encrypt(
    plaintext: Any,
    *,
    public_key_pem: str | None = None,
    aes_key_size: int = DEFAULT_AES_KEY_SIZE,
) -> dict[str, str]:
    """公钥加密，返回 ``{"key": ..., "data": ...}`` 信封。"""
    if aes_key_size not in (16, 24, 32):
        raise ValueError("aes_key_size 只能是 16 / 24 / 32")
    pub = _load_public(public_key_pem)

    aes_key = os.urandom(aes_key_size)
    padder = PKCS7(_AES_BLOCK_BITS).padder()
    padded = padder.update(_to_bytes(plaintext)) + padder.finalize()
    encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()  # noqa: S305 - 与被测方案对齐
    data = encryptor.update(padded) + encryptor.finalize()

    enc_key = pub.encrypt(aes_key, padding.PKCS1v15())
    return {
        "key": base64.b64encode(enc_key).decode("ascii"),
        "data": base64.b64encode(data).decode("ascii"),
    }


def rel_decrypt(payload: Any, *, private_key_pem: str | None = None) -> str:
    """私钥解密 ``{"key", "data"}`` 信封，返回明文字符串。"""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or "key" not in payload or "data" not in payload:
        raise ValueError('密文信封必须是包含 "key" 和 "data" 的对象')

    priv = _load_private(private_key_pem)

    aes_key = priv.decrypt(base64.b64decode(payload["key"]), padding.PKCS1v15())
    decryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).decryptor()  # noqa: S305
    padded = decryptor.update(base64.b64decode(payload["data"])) + decryptor.finalize()
    unpadder = PKCS7(_AES_BLOCK_BITS).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode("utf-8")


def rel_decrypt_json(payload: Any, *, private_key_pem: str | None = None) -> Any:
    """解密后若是 JSON 就还原成 dict/list，否则原样返回字符串。"""
    text = rel_decrypt(payload, private_key_pem=private_key_pem)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
