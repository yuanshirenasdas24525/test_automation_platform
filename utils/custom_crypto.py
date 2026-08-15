from __future__ import annotations

from typing import Any


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
