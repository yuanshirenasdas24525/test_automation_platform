"""页面脚本沙箱可调用的受控 REL 加解密/签名 facade。

``utils.script_runtime`` 的沙箱禁止 import ``cryptography`` 等重型库，也没有
``bytes``/``ord`` 之类基础件，因此 crypto_request / crypto_response 页面脚本
本身没法做 RSA+AES-ECB。这里把**已审过的高层原语**挑出来，以名字 ``rel``
注入脚本命名空间，让脚本能以"薄包装"的方式调用——只暴露下面这几个安全函数，
不放开任意 cryptography 能力，沙箱其余限制不变。

脚本里可直接用（无需 import）::

    def handler(headers, body, config, vars=None):
        headers = dict(headers)
        headers.update(rel.sign_headers(body, secret=config.get("sign_secret")))
        return headers, rel.encrypt(body, public_key_pem=config.get("rsa_public_key"))
"""
from __future__ import annotations

from typing import Any

from utils.rel_crypto import public_key_pem as _built_in_public_key_pem
from utils.rel_crypto import rel_decrypt_json, rel_encrypt
from utils.rel_sign import (
    DEFAULT_ACCESS_KEY,
    DEFAULT_SIGN_SECRET,
    build_sign_headers,
    verify_sign_headers,
)


def encrypt(body: Any, public_key_pem: str | None = None) -> dict[str, str]:
    """RSA+AES-ECB 加密，返回 {key, data}。public_key_pem 留空用内置公钥。"""
    return rel_encrypt(body, public_key_pem=public_key_pem or None)


def decrypt_json(payload: Any, private_key_pem: str | None = None) -> Any:
    """解密 {key,data} 信封为明文 dict/list。private_key_pem 留空用内置私钥。"""
    return rel_decrypt_json(payload, private_key_pem=private_key_pem or None)


def sign_headers(
    params: Any,
    secret: str | None = None,
    access_key: str | None = None,
) -> dict[str, str]:
    """对业务参数生成 power-* 签名头。secret/access_key 留空用内置默认值。"""
    return build_sign_headers(
        params if isinstance(params, dict) else {},
        secret=secret or DEFAULT_SIGN_SECRET,
        access_key=access_key or DEFAULT_ACCESS_KEY,
    )


def verify_sign(
    headers: Any,
    params: Any,
    secret: str | None = None,
    access_key: str | None = None,
) -> tuple[bool, str]:
    """验签，返回 (ok, reason)。secret/access_key 留空用内置默认值。"""
    return verify_sign_headers(
        headers or {},
        params if isinstance(params, dict) else {},
        secret=secret or DEFAULT_SIGN_SECRET,
        access_key=access_key or DEFAULT_ACCESS_KEY,
    )


def public_key_pem() -> str:
    """返回内置公钥 PEM。"""
    return _built_in_public_key_pem()
