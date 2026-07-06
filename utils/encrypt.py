from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import random
import string
import time
from copy import deepcopy
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from utils.logger import LOGGER


def _as_bool(value: Any) -> bool:
    """把配置中心里的字符串布尔值转成真正 bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes", "y", "启用", "开启"}
    return bool(value)


def _jsonish(value: Any, default: Any = None) -> Any:
    """配置中心 JSON 字段会以字符串保存，这里统一还原。"""
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return value


def _as_list(value: Any) -> list[str]:
    """把 JSON 数组、逗号字符串、单个字符串统一成字段列表。"""
    value = _jsonish(value, default=[])
    if value in (None, "", False):
        return []
    if value is True:
        return ["*"]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(value)]


def _compact_json(value: Any) -> str:
    """按固定格式序列化，避免整体加密/签名时空格差异影响结果。"""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _derive_aes_key(secret: str) -> bytes:
    """业务加密密钥统一派生成 32 字节 AES key。"""
    if not secret:
        raise ValueError("encryption_decryption.key 不能为空")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_text(plaintext: Any, key: str) -> str:
    """AES-256-GCM 加密任意值，返回 urlsafe base64(nonce + ciphertext)。"""
    nonce = os.urandom(12)
    raw = _compact_json(plaintext).encode("utf-8")
    cipher = AESGCM(_derive_aes_key(key)).encrypt(nonce, raw, associated_data=None)
    return base64.urlsafe_b64encode(nonce + cipher).decode("ascii")


def decrypt_text(token: str, key: str) -> str:
    """解密 encrypt_text 的产物。"""
    if not token:
        raise ValueError("待解密内容不能为空")
    blob = base64.urlsafe_b64decode(str(token).encode("ascii"))
    if len(blob) <= 12:
        raise ValueError("待解密内容长度异常")
    nonce, cipher = blob[:12], blob[12:]
    plain = AESGCM(_derive_aes_key(key)).decrypt(nonce, cipher, associated_data=None)
    return plain.decode("utf-8")


def _maybe_json(value: str) -> Any:
    """解密后的字符串如果是 JSON，就还原成结构化对象。"""
    text = value.strip()
    if not text or text[0] not in "{[\"-0123456789tfn":
        return value
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return value


def _get_path(obj: Any, path: str) -> Any:
    """支持 a.b.0.c 形式读取嵌套字段。"""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _set_path(obj: Any, path: str, value: Any) -> bool:
    """支持 a.b.0.c 形式写入嵌套字段。"""
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return False
        if cur is None:
            return False
    last = parts[-1]
    if isinstance(cur, dict) and last in cur:
        cur[last] = value
        return True
    if isinstance(cur, list) and last.isdigit():
        idx = int(last)
        if 0 <= idx < len(cur):
            cur[idx] = value
            return True
    return False


def _ordered_items(data: Any, order: list[str] | None = None) -> list[tuple[str, Any]]:
    """按配置顺序取值；未配置顺序时对 dict key 升序，兼容旧签名。"""
    if not isinstance(data, dict):
        return []
    if order and order != ["*"]:
        return [(key, data[key]) for key in order if key in data]
    keys = sorted(data.keys())
    return [(key, data[key]) for key in keys]


class ParameterEncryption:
    """生成 power-* 签名头。

    旧版只支持按请求体 dict key 升序签名；现在允许传入 order，满足指定顺序签名。
    """

    def __init__(
        self,
        data: dict,
        power_access_key: str = "sJVgS8RdIBIBKbUD3dscfdez0iSrUhX1",
        order: list[str] | None = None,
    ):
        self.millis = int(round(time.time() * 1000))
        LOGGER.info("Millis: %d", self.millis)
        self.random_string = "".join(random.choices(string.ascii_letters + string.digits, k=6))
        LOGGER.info("Random string: %s", self.random_string)
        self.power_access_key = power_access_key
        LOGGER.info("Power access key: %s", self.power_access_key)
        self.result = "&".join([f"{key}={value}" for key, value in _ordered_items(data, order)])
        LOGGER.info("Result: %s", self.result)

    def power_sign(self) -> str:
        raw = f"{self.result}&{self.millis}{self.random_string}{self.power_access_key}"
        LOGGER.info("Power sign source: %s", raw)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def ed_header(self) -> dict[str, str]:
        return {
            "power-timestamp": str(self.millis),
            "power-nonce": self.random_string,
            "power-access-key": "d8BCEqCaaGC9FasF",
            "power-sign": self.power_sign(),
        }


class RequestCryptoProcessor:
    """按配置处理 HTTP 请求/响应加解密。"""

    def __init__(self, config: dict[str, Any] | None, vars: dict[str, Any] | None = None):  # noqa: A002
        self.config = config or {}
        self.vars = vars or {}

    @property
    def enabled(self) -> bool:
        return _as_bool(self.config.get("on_off"))

    @property
    def key(self) -> str:
        return str(self.config.get("key") or "")

    def apply_request(self, headers: dict, body: Any) -> tuple[dict, Any, dict[str, Any]]:
        """请求发送前处理请求头签名、请求体签名和请求体加密。"""
        if not self.enabled:
            return headers, body, {}

        headers_out = deepcopy(headers or {})
        body_out = deepcopy(body)
        meta: dict[str, Any] = {"enabled": True, "request": []}

        custom_request = self._custom_request(headers_out, body_out)
        if custom_request is not None:
            headers_out, body_out, custom_meta = custom_request
            meta["request"].append(custom_meta)
            if _as_bool(self.config.get("custom_crypto_only")):
                return headers_out, body_out, meta

        header_order = _as_list(
            self.config.get("request_header_order_encrypt")
            or self.config.get("header_order_encrypt")
        )
        if header_order:
            headers_out.update(self._signature_headers(headers_out, header_order))
            meta["request"].append({"type": "header_order_sign", "fields": header_order})

        body_order = _as_list(
            self.config.get("request_body_order_encrypt")
            or self.config.get("body_order_encrypt")
        )
        legacy_body_sign = not header_order and not body_order and isinstance(body_out, dict)
        if body_order or legacy_body_sign:
            sign_order = body_order if body_order else None
            headers_out.update(self._signature_headers(body_out, sign_order))
            meta["request"].append({
                "type": "body_order_sign",
                "fields": body_order or sorted(body_out.keys()),
                "legacy": legacy_body_sign,
            })

        header_encrypt_fields = _as_list(self.config.get("request_header_encrypt"))
        if header_encrypt_fields:
            self._encrypt_fields(headers_out, header_encrypt_fields)
            meta["request"].append({"type": "header_field_encrypt", "fields": header_encrypt_fields})

        body_encrypt_fields = _as_list(self.config.get("request_body_encrypt") or self.config.get("encrypt"))
        if body_encrypt_fields and isinstance(body_out, (dict, list)):
            self._encrypt_fields(body_out, body_encrypt_fields)
            meta["request"].append({"type": "body_field_encrypt", "fields": body_encrypt_fields})

        if _as_bool(self.config.get("request_body_whole_encrypt") or self.config.get("body_whole_encrypt")):
            target_field = str(
                self.config.get("request_body_whole_encrypt_field")
                or self.config.get("body_whole_encrypt_field")
                or "data"
            )
            cipher = encrypt_text(body_out, self.key)
            body_out = cipher if target_field in {"", "raw", "__raw__"} else {target_field: cipher}
            meta["request"].append({"type": "body_whole_encrypt", "target_field": target_field or "raw"})

        return headers_out, body_out, meta

    def apply_response(self, response_body: Any) -> tuple[Any, dict[str, Any]]:
        """响应进入 extract/assert 前先解密。"""
        if not self.enabled:
            return response_body, {}

        body_out = deepcopy(response_body)
        meta: dict[str, Any] = {"enabled": True, "response": []}

        custom_response = self._custom_response(body_out)
        if custom_response is not None:
            body_out, custom_meta = custom_response
            meta["response"].append(custom_meta)
            if _as_bool(self.config.get("custom_crypto_only")):
                return body_out, meta

        if _as_bool(
            self.config.get("response_body_whole_decrypt")
            or self.config.get("response_body_whole_encrypt_decrypt")
        ):
            source_field = str(self.config.get("response_body_whole_decrypt_field") or "")
            encrypted_value = _get_path(body_out, source_field) if source_field else body_out
            plain = decrypt_text(encrypted_value, self.key)
            decoded = _maybe_json(plain)
            if source_field and isinstance(body_out, (dict, list)):
                _set_path(body_out, source_field, decoded)
            else:
                body_out = decoded
            meta["response"].append({"type": "body_whole_decrypt", "source_field": source_field or "raw"})

        response_decrypt_fields = _as_list(self.config.get("response_body_decrypt") or self.config.get("decrypt"))
        if response_decrypt_fields and isinstance(body_out, (dict, list)):
            self._decrypt_fields(body_out, response_decrypt_fields)
            meta["response"].append({"type": "body_field_decrypt", "fields": response_decrypt_fields})

        return body_out, meta

    def _signature_headers(self, data: Any, order: list[str] | None) -> dict[str, str]:
        if not isinstance(data, dict):
            return {}
        signer = ParameterEncryption(data=data, power_access_key=self.key, order=order)
        return signer.ed_header()

    def _encrypt_fields(self, obj: Any, fields: list[str]) -> None:
        if fields == ["*"] and isinstance(obj, dict):
            fields = list(obj.keys())
        for field in fields:
            value = _get_path(obj, field)
            if value is None:
                continue
            _set_path(obj, field, encrypt_text(value, self.key))

    def _decrypt_fields(self, obj: Any, fields: list[str]) -> None:
        if fields == ["*"] and isinstance(obj, dict):
            fields = list(obj.keys())
        for field in fields:
            value = _get_path(obj, field)
            if value is None:
                continue
            _set_path(obj, field, _maybe_json(decrypt_text(str(value), self.key)))

    def _custom_request(self, headers: dict, body: Any) -> tuple[dict, Any, dict[str, Any]] | None:
        handler_name = str(self.config.get("custom_request_handler") or "").strip()
        if not handler_name:
            return None
        dynamic_result = self._run_dynamic_handler(
            handler_name,
            kind="crypto_request",
            headers=headers,
            body=body,
        )
        if dynamic_result is not None:
            result = dynamic_result
            if isinstance(result, tuple) and len(result) == 2:
                new_headers, new_body = result
            elif isinstance(result, dict) and ("headers" in result or "body" in result):
                new_headers = result.get("headers", headers)
                new_body = result.get("body", body)
            else:
                raise TypeError(
                    "custom_request_handler 必须返回 (headers, body) 或 "
                    "{'headers': ..., 'body': ...}"
                )
            return dict(new_headers or {}), new_body, {
                "type": "dynamic_custom_request_handler",
                "handler": handler_name,
            }
        func = self._load_custom_handler(handler_name)
        result = func(deepcopy(headers), deepcopy(body), dict(self.config))
        if isinstance(result, tuple) and len(result) == 2:
            new_headers, new_body = result
        elif isinstance(result, dict) and ("headers" in result or "body" in result):
            new_headers = result.get("headers", headers)
            new_body = result.get("body", body)
        else:
            raise TypeError(
                "custom_request_handler 必须返回 (headers, body) 或 "
                "{'headers': ..., 'body': ...}"
            )
        return dict(new_headers or {}), new_body, {
            "type": "custom_request_handler",
            "module": self._custom_module_name(),
            "handler": handler_name,
        }

    def _custom_response(self, response_body: Any) -> tuple[Any, dict[str, Any]] | None:
        handler_name = str(self.config.get("custom_response_handler") or "").strip()
        if not handler_name:
            return None
        dynamic_result = self._run_dynamic_handler(
            handler_name,
            kind="crypto_response",
            body=response_body,
        )
        if dynamic_result is not None:
            return dynamic_result, {
                "type": "dynamic_custom_response_handler",
                "handler": handler_name,
            }
        func = self._load_custom_handler(handler_name)
        result = func(deepcopy(response_body), dict(self.config))
        return result, {
            "type": "custom_response_handler",
            "module": self._custom_module_name(),
            "handler": handler_name,
        }

    def _load_custom_handler(self, handler_name: str):
        module = importlib.import_module(self._custom_module_name())
        func = getattr(module, handler_name, None)
        if not callable(func):
            raise ValueError(f"自定义加解密函数不存在或不可调用：{self._custom_module_name()}.{handler_name}")
        return func

    def _custom_module_name(self) -> str:
        return str(self.config.get("custom_crypto_module") or "utils.custom_crypto").strip()

    def _run_dynamic_handler(self, handler_name: str, kind: str, **kwargs: Any) -> Any | None:
        try:
            from utils.script_runtime import run_named_script
            found, result = run_named_script(
                handler_name,
                kind=kind,
                project_id=self._project_id(),
                config=dict(self.config),
                vars=dict(self.vars),
                **kwargs,
            )
        except Exception:
            raise
        if not found:
            return None
        return result

    def _project_id(self) -> int | None:
        raw = self.vars.get("_project_id") or self.config.get("project_id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None
