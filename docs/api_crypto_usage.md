# API 请求/响应加解密配置使用指南

本文说明接口自动化用例执行时，如何通过配置中心启用请求签名、请求体加密、响应体解密，以及如何编写自定义加解密算法。

## 生效位置

配置入口：

```text
全局模板/项目配置 -> API -> encryption_decryption
```

HTTP 用例执行链路中，加解密发生在 `http_request` step 内：

1. 变量替换、基础请求头合并完成后
2. 发出 HTTP 请求前，对请求头/请求体执行签名或加密
3. 收到 HTTP 响应后，先对响应体解密
4. 再执行 extract 和 assertion

因此断言和提取规则应写“解密后的响应结构”。

## 基础开关

至少需要打开开关：

```text
on_off = true
key = your-secret-key
```

`key` 会传给内置算法，也会作为 `config["key"]` 传给自定义算法函数。

## 请求头参数顺序签名

配置：

```json
request_header_order_encrypt = ["X-App-Id", "X-Timestamp", "Authorization"]
```

平台会按数组顺序从最终请求头中取值，拼接：

```text
X-App-Id=xxx&X-Timestamp=xxx&Authorization=xxx
```

然后生成并追加这些请求头：

```text
power-timestamp
power-nonce
power-access-key
power-sign
```

当前内置签名兼容旧逻辑：`power-sign = md5("{joined}&{timestamp}{nonce}{key}")`。

## 请求体参数顺序签名

配置：

```json
request_body_order_encrypt = ["username", "password", "nonce"]
```

平台会按数组顺序从请求体中取值并生成 `power-sign`。如果只配置了 `on_off=true`，但没有配置 `request_header_order_encrypt` / `request_body_order_encrypt`，则兼容旧行为：对请求体顶层 key 升序后签名。

## 请求体字段加密

旧字段 `encrypt` 仍然可用，表示对请求体中的指定字段逐个加密：

```json
encrypt = ["password", "id_card", "user.phone"]
```

也可以使用更明确的新字段：

```json
request_body_encrypt = ["password", "id_card", "user.phone"]
```

字段路径支持点号，例如 `user.phone`。

## 请求体整体加密

配置：

```text
request_body_whole_encrypt = true
request_body_whole_encrypt_field = data
```

原始请求体：

```json
{
  "username": "u",
  "password": "p"
}
```

实际发送：

```json
{
  "data": "密文"
}
```

如果希望整个请求体直接变成密文字符串：

```text
request_body_whole_encrypt_field = raw
```

## 响应体字段解密

旧字段 `decrypt` 仍然可用，表示对响应体中的指定字段逐个解密：

```json
decrypt = ["data.password", "data.id_card"]
```

也可以使用更明确的新字段：

```json
response_body_decrypt = ["data.password", "data.id_card"]
```

## 响应体整体解密

如果服务端返回：

```json
{
  "data": "密文"
}
```

配置：

```text
response_body_whole_decrypt = true
response_body_whole_decrypt_field = data
```

平台会先把 `data` 解密，再执行断言和提取。断言示例：

```json
{
  "type": "jsonpath",
  "target": "$.data.token",
  "expected": "not_empty"
}
```

如果整个响应就是密文字符串：

```text
response_body_whole_decrypt = true
response_body_whole_decrypt_field =
```

## 自定义加解密算法

当业务算法不是平台内置签名/AES 时，直接修改：

```text
utils/custom_crypto.py
```

默认模块路径：

```text
custom_crypto_module = utils.custom_crypto
```

### 自定义请求处理函数

函数签名：

```python
def custom_request_crypto(headers, body, config):
    return headers, body
```

入参说明：

| 参数 | 含义 |
|---|---|
| `headers` | 已完成变量替换、基础 header 合并后的请求头 |
| `body` | 已完成变量替换后的请求体 |
| `config` | `encryption_decryption` 整组配置 |

返回值支持两种格式：

```python
return headers, body
```

或：

```python
return {"headers": headers, "body": body}
```

示例：

```python
from __future__ import annotations

import hashlib
import json
from typing import Any


def custom_request_crypto(
    headers: dict[str, Any],
    body: Any,
    config: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    secret = str(config.get("key") or "")
    raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    sign = hashlib.sha256(f"{raw_body}{secret}".encode("utf-8")).hexdigest()

    headers["X-Sign"] = sign
    return headers, {"payload": raw_body}
```

配置：

```text
on_off = true
custom_request_handler = custom_request_crypto
custom_crypto_only = true
```

### 自定义响应处理函数

函数签名：

```python
def custom_response_crypto(response_body, config):
    return response_body
```

返回值会直接进入后续 extract/assert。示例：

```python
from __future__ import annotations

import json
from typing import Any


def custom_response_crypto(response_body: Any, config: dict[str, Any]) -> Any:
    if isinstance(response_body, dict) and "payload" in response_body:
        return json.loads(response_body["payload"])
    return response_body
```

配置：

```text
on_off = true
custom_response_handler = custom_response_crypto
custom_crypto_only = true
```

### 自定义函数和内置配置的执行顺序

配置：

```text
custom_crypto_only = true
```

表示只执行自定义函数，不再执行内置签名/AES 配置。

配置：

```text
custom_crypto_only = false
```

表示先执行自定义函数，然后继续执行内置签名/AES 配置。适用于“先补业务字段，再走平台统一签名”的场景。

## 常见配置组合

### 只用自定义算法

```text
on_off = true
key = your-secret
custom_crypto_module = utils.custom_crypto
custom_request_handler = custom_request_crypto
custom_response_handler = custom_response_crypto
custom_crypto_only = true
```

### 请求体整体加密，响应字段整体解密

```text
on_off = true
key = your-secret
request_body_whole_encrypt = true
request_body_whole_encrypt_field = data
response_body_whole_decrypt = true
response_body_whole_decrypt_field = data
```

### 请求头顺序签名 + 请求体字段加密

```text
on_off = true
key = your-secret
request_header_order_encrypt = ["X-App-Id", "X-Timestamp"]
request_body_encrypt = ["password"]
```

## 排错

1. 自定义函数不存在：检查 `custom_crypto_module` 和函数名是否一致。
2. 解密后断言失败：确认断言路径写的是解密后的结构。
3. 请求体格式不符合服务端要求：检查 `request_body_whole_encrypt_field`，服务端可能要求 `data`、`payload`、`cipherText` 等不同字段名。
4. 同时配置了自定义函数和内置算法但结果不符合预期：将 `custom_crypto_only` 改为 `true`，先单独验证自定义算法。
