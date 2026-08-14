"""/api/crypto_echo/* —— RSA + AES-ECB 加解密自测靶子。

模拟一个"外部被测系统"：请求体必须是 REL 风格的密文信封 ``{"key", "data"}``，
服务端用**私钥**解密，把内容原样回显，再用**公钥**加密成同样的信封返回。

用途：端到端验证平台的自定义加解密链路（``encryption_decryption`` +
``utils.custom_crypto`` 里的 ``rel_*`` handler）是否打通，不依赖任何真实外部系统。
接口无需平台登录态（挂在 main.py 的无鉴权路由组里），因为它模拟的是外部服务。

返回的信封可直接粘进前端 REL 解密 HTML 验证——两者密钥同源。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from utils.logger import LOGGER
from utils.rel_crypto import public_key_pem, rel_decrypt_json, rel_encrypt

router = APIRouter(prefix="/crypto_echo", tags=["crypto-echo"])


@router.get("/public-key")
def get_public_key() -> dict[str, Any]:
    """返回内置公钥 PEM，方便调用方（或脚本）自行加密请求。"""
    return {"status": "success", "data": {"public_key_pem": public_key_pem()}}


@router.post("/echo")
def echo(payload: dict[str, Any] = Body(...)):
    """解密请求信封 → 回显 → 重新加密成信封返回。

    入参（明文对应关系）::

        {"key": "<base64 RSA(aes_key)>", "data": "<base64 AES-ECB(明文)>"}

    出参（同样是密文信封，明文形如）::

        {"echo": <你发的明文>, "received_type": "dict", "message": "ok"}
    """
    try:
        plain = rel_decrypt_json(payload)
    except Exception as exc:  # noqa: BLE001 —— 靶子对外表现要像真实系统：解密失败给 400
        LOGGER.warning("crypto_echo 解密失败: %s: %s", type(exc).__name__, exc)
        return JSONResponse(
            status_code=400,
            content={"error": "decrypt_failed", "message": str(exc)},
        )

    reply = {
        "echo": plain,
        "received_type": type(plain).__name__,
        "message": "ok",
    }
    return rel_encrypt(reply)
