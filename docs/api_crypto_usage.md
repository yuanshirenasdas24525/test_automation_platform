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

> 提示：本节是「改文件」的方式（方式二/三）。**推荐优先看后面的[方式一：页面脚本（DB）+ `crypto` 工具箱](#方式一页面脚本db-crypto-工具箱推荐)**——按项目隔离、免发版重启。三种方式对比见[自定义加解密的三种落地方式](#自定义加解密的三种落地方式)。

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

## 自定义加解密的三种落地方式

同样的 `custom_request_handler` / `custom_response_handler` 名字，平台按下面的优先级查找实现：**项目脚本 > 全局脚本 > 文件函数**（见 `runners/api` 调用链与 `utils/encrypt.py::RequestCryptoProcessor`）。

| 方式 | 写在哪 | 隔离粒度 | 要发版/重启 | 适用 |
|---|---|---|---|---|
| 方式一（推荐） | **DB 页面脚本**（脚本管理） | 按项目 / 全局 | 不用（改脚本即时生效） | 项目专属算法、想快、免发版 |
| 方式二 | `utils/<模块>.py`，配 `custom_crypto_module` | 全仓库 | 要 | 逻辑重、要 review/复用 |
| 方式三 | 直接写进 `utils/custom_crypto.py` | 全仓库 | 要 | 真·通用的少数几个 |

> 上一节的“自定义加解密算法”即方式二/三。下面重点讲**方式一**。

## 方式一：页面脚本（DB）+ `crypto` 工具箱（推荐）

在**脚本管理**里为项目（或全局）新建脚本，用例配置里 `custom_request_handler` / `custom_response_handler` 填**脚本名**即可命中；同名脚本会自动覆盖文件函数，因此从方式二/三迁移**不用改配置**。

### 脚本约定

脚本必须定义 `handler`，按 kind 不同签名不同（见 `utils/script_runtime.py`）：

```python
# kind = crypto_request
def handler(headers, body, config, vars=None):
    return headers, body          # 返回 (新请求头, 新请求体)

# kind = crypto_response
def handler(response_body, config, vars=None):
    return response_body          # 返回解密后的 dict/list，进入 extract/assert
```

### 沙箱限制（重要）

页面脚本跑在加固沙箱里（`utils/script_runtime.py`）：

- **只能 import**：`base64 / hashlib / hmac / json / math / random / re / time / uuid`，外加受控的 `crypto`。
- **禁止** import `cryptography` 等重库；内置函数受限（**没有** `bytes / ord / chr / sorted / open` 等）；静态拦截所有 dunder 访问与非白名单 import。
- 因此 **RSA/AES 这类重加密不能在脚本里手写**，必须调下面的 `crypto` 工具箱（它是 repo 里受信代码，把审过的高层原语递进沙箱）。
- 脚本用**分离的 globals/locals** 执行，`handler` **调不到同级定义的其它顶层函数**（会 `NameError`）。逻辑要么全内联进 `handler`，要么用 `handler` 内的嵌套函数。

### `crypto` 工具箱 API

在脚本里直接用 `crypto.xxx`（无需 import），实现见 `utils/crypto_toolkit.py`：

| 函数 | 用途 |
|---|---|
| `crypto.rsa_aes_ecb_encrypt(data, public_key_pem=None)` | RSA(PKCS#1v1.5)+AES-ECB 加密 → `{key,data}` 信封 |
| `crypto.rsa_aes_ecb_decrypt(payload, private_key_pem=None)` | 解密 `{key,data}` 信封 → dict/list |
| `crypto.aes_gcm_encrypt(text, key)` / `aes_gcm_decrypt(token, key)` | AES-256-GCM 通用对称 |
| `crypto.md5(text)` / `sha256(text)` / `hmac_sha256(text, key)` | 摘要 / 签名 |
| `crypto.canonical(params, fields=None)` | 参数拼成 `k=v&k=v`（给 fields 按序，否则 key 升序）；沙箱没 `sorted`，签名靠它 |
| `crypto.b64encode(raw)` / `b64decode(text)` | base64 |
| `crypto.random_hex(n=8)` / `now_ms()` | 随机串 / 毫秒时间戳 |
| `crypto.generate_rsa_keypair(bits=2048)` | 生成 `(私钥PEM, 公钥PEM)` |
| `crypto.should_apply(config, vars, flag="rel_crypto")` | 按作用范围策略判断当前用例是否该加解密（见下节） |
| `crypto.TEST_PUBLIC_KEY_PEM` / `TEST_PRIVATE_KEY_PEM` | 自测靶子内置密钥（仅自测用） |

密钥留空（传 `None`）时 RSA 函数回落内置测试密钥；真实项目在 config 里传自己的密钥。

### 完整示例：RSA+AES-ECB 信封 + power-\* 请求签名

对应自测靶子 `POST /api/auth/echo_test`（见下一节）。

脚本带一个**开关变量 `rel_crypto`**：只有用例变量池里 `rel_crypto` 为真才加密/解密，否则原样放行。这样即使 `encryption_decryption` 是项目级全局配置、对所有 API 用例生效，也只有**标记了 `rel_crypto=1` 的用例**真正走加密，其它接口完全不受影响（见后面「只对部分用例加密」）。

**请求脚本**（名称任意，如 `rel_request_crypto`，kind `crypto_request`）：

```python
def handler(headers, body, config, vars=None):
    # 作用范围策略：全局/指定用例/指定模块，见「加解密作用范围策略」小节
    if not crypto.should_apply(config, vars):
        return headers, body
    headers = dict(headers or {})
    # 可选：对明文业务参数加 power-* 签名头
    if str(config.get("sign_on") or "").strip().lower() in ("1", "true", "on", "yes", "y"):
        params = body if isinstance(body, dict) else {}
        ts = str(crypto.now_ms())
        nonce = crypto.random_hex(6)
        secret = config.get("sign_secret") or "rel-echo-sign-secret-2026"
        raw = crypto.canonical(params) + "&" + ts + nonce + secret
        headers["power-timestamp"] = ts
        headers["power-nonce"] = nonce
        headers["power-access-key"] = config.get("sign_access_key") or "REL_ECHO_AK"
        headers["power-sign"] = crypto.md5(raw)
    # RSA+AES-ECB 加密请求体
    return headers, crypto.rsa_aes_ecb_encrypt(
        body, public_key_pem=config.get("rsa_public_key") or crypto.TEST_PUBLIC_KEY_PEM
    )
```

**响应脚本**（名称 `rel_response_crypto`，kind `crypto_response`）：

```python
def handler(response_body, config, vars=None):
    if not crypto.should_apply(config, vars):
        return response_body
    if isinstance(response_body, dict) and "key" in response_body and "data" in response_body:
        return crypto.rsa_aes_ecb_decrypt(
            response_body,
            private_key_pem=config.get("rsa_private_key") or crypto.TEST_PRIVATE_KEY_PEM,
        )
    return response_body
```

**用例配置**（`encryption_decryption`）：

```text
on_off = true
custom_request_handler = rel_request_crypto
custom_response_handler = rel_response_crypto
custom_crypto_only = true
rsa_public_key =  <被测系统公钥；打自测靶子可留空用内置公钥>
sign_on = true
sign_secret = rel-echo-sign-secret-2026     # 留空用内置默认
sign_access_key = REL_ECHO_AK               # 留空用内置默认
```

用例请求体照常写**明文** dict，断言照常写**解密后**结构——加解密全透明。

### 加解密作用范围策略（全局 / 指定用例 / 指定模块混用）

`encryption_decryption` 是**项目级全局配置**（`config_center.get("encryption_decryption", project_id=...)`），一旦开启会作用到该项目**所有** API 用例；step/case 无法在配置层单独关。因此加密/不加密混用不能靠配置分组，而是由脚本里的 `crypto.should_apply(config, vars)` 按策略判定——它读**全局配置的策略字段** + **用例/模块身份**（引擎已把 `_case_id`/`_case_name`/`_module_name`/`_module_id` 注入变量池）。

判定优先级：**用例显式开关 > 全局策略**。

**① 用例显式开关（最高优先，单用例强制开/关）**

给用例加变量 `rel_crypto = 1`（强制加密）或 `rel_crypto = 0`（强制不加密），作用域仅该用例，覆盖下面的全局策略。

**② 全局策略（`crypto_scope`）**

| 配置 | 效果 | 对应诉求 |
|---|---|---|
| `crypto_scope = all`（默认/留空） | 全项目 API 用例都加密 | **默认全局开启** |
| `crypto_scope = include` + `crypto_cases = ["用例名", 1024]` | 只有名单里的用例加密 | **指定接口开启** |
| `crypto_scope = include` + `crypto_modules = ["支付","结算"]` | 名单模块下的用例都加密 | **指定模块开启** |
| `crypto_scope = include` + `crypto_paths = ["/api/auth/echo_test"]` | 命中该请求路径的才加密 | **指定接口(路径)开启** |
| `crypto_scope = exclude` + `crypto_modules/crypto_cases/crypto_paths` | 名单之外的都加密 | 少数接口除外 |

`crypto_cases` 支持**用例名或用例 id**，`crypto_modules` 是**模块名**，`crypto_paths` 是**请求路径**（精确 `/api/auth/echo_test` 或前缀通配 `/api/auth/*`，按请求 `_request_path`/`_request_url` 匹配——引擎已把它们注入脚本上下文）。三者都支持 JSON 数组或逗号串；`include`/`exclude` 可同时给多个，**命中任一即算命中**。

**配置示例——只加密"支付"模块 + 一个额外用例：**

```text
on_off = true
custom_request_handler = rel_request_crypto
custom_response_handler = rel_response_crypto
custom_crypto_only = true
crypto_scope = include
crypto_modules = ["支付"]
crypto_cases = ["对账单下载加密用例"]
rsa_public_key = <公钥，留空用内置>
```

其它模块/用例：脚本 `should_apply` 返回 False → 明文原样放行、响应原样透传，完全不受影响。想临时给某个不在名单里的用例开一下，加个用例变量 `rel_crypto=1` 即可。

> 变量名默认 `rel_crypto`，可在 `should_apply(config, vars, flag="自定义名")` 里改。

### 自测靶子（服务端 mock，验证链路用）

平台内建两个无鉴权靶子，模拟“外部被测系统”，方便端到端验证加解密：

| 接口 | 说明 |
|---|---|
| `POST /api/auth/echo_test` | 强制加密 + 强制 power-* 验签的回显靶子：解密失败 400 / 验签失败 401 / 字段校验 422（`username`、`amount` 必填）/ 成功回 `{status,message:"hello",data:<回显>}`（加密返回）。见 `server/api/auth.py` |
| `POST /api/crypto_echo/echo` | 纯回显靶子（只加解密、不校验字段）；`GET /api/crypto_echo/public-key` 返回内置公钥 PEM。见 `server/api/crypto_echo.py` |

靶子内置的**私钥写死在服务端**、公钥即 `crypto.TEST_PUBLIC_KEY_PEM`，与前端 REL 解密 HTML 同源。

### 切换与排错

- 改了 `utils/script_runtime.py`（沙箱白名单）或 `utils/crypto_toolkit.py` 后，**必须重启 worker**（用例在 celery worker 里执行）。
- 报 `NameError: name 'crypto' is not defined` → worker 用的是旧代码，重启 worker。
- 报 `handler 不存在` / 走了文件旧逻辑 → 脚本没保存或名字/kind 不对。
- 切换脚本内容与重启之间别跑用例（新旧不匹配的短窗口）。

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
