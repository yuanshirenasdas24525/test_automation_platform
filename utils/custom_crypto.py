from __future__ import annotations

from typing import Any

from utils.rel_crypto import rel_decrypt_json, rel_encrypt


def custom_request_crypto(
    headers: dict[str, Any],
    body: Any,
    config: dict[str, Any],
) -> tuple[dict[str, Any], Any] | dict[str, Any]:
    """用户自定义请求加密入口。

    入参：
      - headers：变量替换和基础 header 合并后的请求头
      - body：变量替换后的请求体
      - config：配置中心 encryption_decryption 整组配置

    返回值支持两种：
      1. (new_headers, new_body)
      2. {"headers": new_headers, "body": new_body}

    示例：
        headers["X-Sign"] = "your sign"
        body = {"payload": "your encrypted text"}
        return headers, body
    """
    return headers, body


def custom_response_crypto(response_body: Any, config: dict[str, Any]) -> Any:
    """用户自定义响应解密入口。

    这里返回的对象会进入后续 extract/assert，所以建议返回解密后的 dict/list。

    示例：
        encrypted = response_body["data"]
        return {"data": your_decrypt(encrypted)}
    """
    return response_body


def demo_request_crypto(
    headers: dict[str, Any],
    body: Any,
    config: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    """冒烟测试示例：给请求打标并包一层 body。"""
    headers["X-Custom-Crypto"] = str(config.get("custom_demo_header") or "1")
    return headers, {"wrapped": body}


def demo_response_crypto(response_body: Any, config: dict[str, Any]) -> Any:
    """冒烟测试示例：把 wrapped_response 解开。"""
    if isinstance(response_body, dict) and "wrapped_response" in response_body:
        return response_body["wrapped_response"]
    return response_body


# ---------------------------------------------------------------------------
# REL 风格 RSA + AES-ECB：配合 /api/crypto_echo/echo 自测靶子
# ---------------------------------------------------------------------------
# 用例里这样配 encryption_decryption：
#   on_off: true
#   custom_request_handler: rel_request_crypto
#   custom_response_handler: rel_response_crypto
#   custom_crypto_only: true          # 跳过平台原生 GCM 签名/加密
#   rsa_public_key: <被测系统的公钥>   # 加密请求用；留空回落内置公钥
#
# 非对称约定（与外界一致）：
#   - 公钥加密请求 —— 由调用方（平台）持有，可配置 rsa_public_key
#   - 私钥解密     —— 只写死在服务端（测试接口）里，客户端不配置，走内置私钥
# rsa_public_key 支持完整 PEM、单行 \n 转义 PEM、或纯 base64 主体三种存法。
#
# 签名头（可选，配 sign_on: true 开启）：对**明文业务参数**生成
#   power-timestamp / power-nonce / power-access-key / power-sign
#   sign_secret / sign_access_key 可配置，留空用内置默认值（需与靶子一致才验得过）。


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes", "y", "启用", "开启"}
    return bool(value)


def rel_request_crypto(
    headers: dict[str, Any],
    body: Any,
    config: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    """加密请求体成 REL 信封 ``{"key", "data"}``；按需追加 power-* 签名头。"""
    headers = dict(headers or {})
    if _as_bool(config.get("sign_on")):
        from utils.rel_sign import DEFAULT_ACCESS_KEY, DEFAULT_SIGN_SECRET, build_sign_headers

        headers.update(
            build_sign_headers(
                body if isinstance(body, dict) else {},
                secret=config.get("sign_secret") or DEFAULT_SIGN_SECRET,
                access_key=config.get("sign_access_key") or DEFAULT_ACCESS_KEY,
            )
        )
    envelope = rel_encrypt(body, public_key_pem=config.get("rsa_public_key") or None)
    return headers, envelope


def rel_response_crypto(response_body: Any, config: dict[str, Any]) -> Any:
    """解密 REL 信封响应，返回明文 dict/list 供 extract/assert。

    私钥写死在代码里（不配置），这里固定用内置私钥解密。
    """
    if isinstance(response_body, dict) and "key" in response_body and "data" in response_body:
        return rel_decrypt_json(response_body)
    # 非信封（比如靶子返回的 400 错误体）原样透传，方便断言错误场景
    return response_body
