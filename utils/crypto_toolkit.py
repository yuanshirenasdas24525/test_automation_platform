"""注入页面脚本沙箱的**通用加密工具箱**（在脚本里以名字 ``crypto`` 使用）。

沙箱禁止 import ``cryptography``、也缺 ``bytes``/``ord`` 等基础件，所以重加密算法
必须待在受信 repo 代码里。这里把已审过的高层原语挑成一个**通用工具箱**暴露给沙箱：

    - 项目专属的加解密/签名**组合逻辑全部写成 DB 脚本**，调用 ``crypto.*``；
    - repo 侧**只保留这一个工具箱文件**，以后加任何项目的加密方案都不用再新增 .py。

只暴露下面这些安全函数/常量，不放开任意 cryptography 能力，沙箱其余限制不变。

脚本里可直接用（无需 import），例如::

    def handler(headers, body, config, vars=None):
        return headers, crypto.rsa_aes_ecb_encrypt(body, public_key_pem=config.get("rsa_public_key"))
"""
from __future__ import annotations

import base64 as _base64
import hashlib as _hashlib
import hmac as _hmac
import os as _os
import time as _time
from typing import Any

from utils.encrypt import decrypt_text as _aes_gcm_decrypt
from utils.encrypt import encrypt_text as _aes_gcm_encrypt
from utils.rel_crypto import REL_PRIVATE_KEY_PEM as _TEST_PRIVATE_KEY_PEM
from utils.rel_crypto import generate_keypair as _generate_keypair
from utils.rel_crypto import public_key_pem as _builtin_public_key_pem
from utils.rel_crypto import rel_decrypt_json as _rsa_aes_decrypt
from utils.rel_crypto import rel_encrypt as _rsa_aes_encrypt

# ---------------------------------------------------------------------------
# RSA(PKCS#1 v1.5) + AES-ECB 数字信封 {key, data}
# ---------------------------------------------------------------------------
def rsa_aes_ecb_encrypt(data: Any, public_key_pem: str | None = None) -> dict[str, str]:
    """公钥加密，返回 {key, data} 信封。public_key_pem 留空用内置测试公钥。"""
    return _rsa_aes_encrypt(data, public_key_pem=public_key_pem or None)


def rsa_aes_ecb_decrypt(payload: Any, private_key_pem: str | None = None) -> Any:
    """私钥解密 {key,data} 信封为明文 dict/list。private_key_pem 留空用内置测试私钥。"""
    return _rsa_aes_decrypt(payload, private_key_pem=private_key_pem or None)


# ---------------------------------------------------------------------------
# AES-256-GCM 通用对称加解密（承载 base64(nonce+cipher)）
# ---------------------------------------------------------------------------
def aes_gcm_encrypt(plaintext: Any, key: str) -> str:
    return _aes_gcm_encrypt(plaintext, key)


def aes_gcm_decrypt(token: str, key: str) -> str:
    return _aes_gcm_decrypt(token, key)


# ---------------------------------------------------------------------------
# 摘要 / 签名基元
# ---------------------------------------------------------------------------
def md5(text: Any) -> str:
    return _hashlib.md5(str(text).encode("utf-8")).hexdigest()


def sha256(text: Any) -> str:
    return _hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def hmac_sha256(text: Any, key: Any) -> str:
    return _hmac.new(str(key).encode("utf-8"), str(text).encode("utf-8"), _hashlib.sha256).hexdigest()


def canonical(params: Any, fields: list[str] | None = None) -> str:
    """业务参数拼成 ``k=v&k=v``：给了 fields 就按其顺序，否则 key 升序。

    沙箱没有 sorted()，签名要用有序拼接就调这个。
    """
    if not isinstance(params, dict):
        return ""
    keys = [k for k in fields if k in params] if fields else sorted(params.keys())
    return "&".join(f"{k}={params[k]}" for k in keys)


# ---------------------------------------------------------------------------
# 编解码 / 杂项工具
# ---------------------------------------------------------------------------
def b64encode(raw: Any) -> str:
    data = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode("utf-8")
    return _base64.b64encode(data).decode("ascii")


def b64decode(text: str) -> str:
    return _base64.b64decode(str(text)).decode("utf-8", errors="replace")


def random_hex(nbytes: int = 8) -> str:
    return _os.urandom(int(nbytes)).hex()


def now_ms() -> int:
    return int(round(_time.time() * 1000))


_TRUE = {"1", "true", "on", "yes", "y", "启用", "开启"}


def _as_list(raw: Any) -> list[str]:
    """JSON 数组字符串 / 逗号串 / 列表，统一成字符串列表。"""
    if raw in (None, "", False):
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if text.startswith("["):
        import json as _json

        try:
            arr = _json.loads(text)
            return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:  # noqa: BLE001
            pass
    return [p.strip() for p in text.split(",") if p.strip()]


def _path_match(patterns: list[str], path: str, url: str) -> bool:
    """请求路径/URL 是否命中名单。支持精确匹配与 ``尾部*`` 前缀通配。

    例：``/api/auth/echo_test`` 精确；``/api/auth/*`` 前缀。
    """
    path = path or ""
    url = url or ""
    for pat in patterns:
        pat = str(pat).strip()
        if not pat:
            continue
        if pat.endswith("*"):
            pre = pat[:-1]
            if path.startswith(pre) or url.startswith(pre):
                return True
        elif path == pat or url == pat:
            return True
    return False


def should_apply(config: Any, vars: Any = None, flag: str = "rel_crypto") -> bool:  # noqa: A002
    """按"全局/指定用例/指定模块"策略判断当前用例是否该加解密。

    优先级：用例显式开关 > 全局策略。config/vars 均可缺省。

    - 用例显式开关：用例变量 ``flag``（默认 ``rel_crypto``）= 1/true/on → 强制开；
      = 0/false/off → 强制关。未设则看下面的全局策略。
    - 全局策略 ``config['crypto_scope']``：
        ``all`` / 缺省  —— 全项目开启（模式1）
        ``include``     —— 仅命中 ``crypto_modules`` / ``crypto_cases`` 名单的开启（模式2/3）
        ``exclude``     —— 名单之外的开启
    - ``crypto_modules``：模块名列表；``crypto_cases``：用例名或用例 id 列表；
      ``crypto_paths``：请求路径列表（精确 ``/api/auth/echo_test`` 或前缀 ``/api/auth/*``），
      按当前请求的 ``_request_path`` / ``_request_url`` 匹配。
      三者命中任一即算命中；均支持 JSON 数组或逗号串。
    """
    vars = vars or {}
    config = config or {}

    explicit = vars.get(flag)
    if explicit is not None and str(explicit).strip() != "":
        return str(explicit).strip().lower() in _TRUE

    scope = str(config.get("crypto_scope") or "all").strip().lower()
    if scope in ("all", "global", ""):
        return True

    modules = _as_list(config.get("crypto_modules"))
    cases = _as_list(config.get("crypto_cases"))
    paths = _as_list(config.get("crypto_paths"))
    cur_module = str(vars.get("_module_name") or "")
    cur_case = str(vars.get("_case_name") or "")
    cur_case_id = str(vars.get("_case_id") or "")
    cur_path = str(vars.get("_request_path") or "")
    cur_url = str(vars.get("_request_url") or "")
    hit = bool(
        (cur_module and cur_module in modules)
        or (cur_case and cur_case in cases)
        or (cur_case_id and cur_case_id in cases)
        or _path_match(paths, cur_path, cur_url)
    )

    if scope in ("include", "whitelist", "only"):
        return hit
    if scope in ("exclude", "blacklist"):
        return not hit
    return True


def generate_rsa_keypair(bits: int = 2048) -> tuple[str, str]:
    """返回 (private_pem_pkcs8, public_pem)，仅便于生成密钥。"""
    return _generate_keypair(bits)


# ---------------------------------------------------------------------------
# 自测靶子内置密钥（仅自测方便；真实项目请用 config 里自己的密钥）
# ---------------------------------------------------------------------------
TEST_PUBLIC_KEY_PEM: str = _builtin_public_key_pem()
TEST_PRIVATE_KEY_PEM: str = _TEST_PRIVATE_KEY_PEM
