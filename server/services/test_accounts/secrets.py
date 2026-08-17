from __future__ import annotations

from typing import Any

from utils.crypto import decrypt_secret, encrypt_secret

TEST_ACCOUNT_CONFIG_GROUP = "test_accounts"
TEST_ACCOUNT_SECRET_PREFIX = "enc:v1:"
TEST_ACCOUNT_SECRET_MASK = "••••••••"
# 池内每个账号的密码键；配置写入时按此键加密、读取时掩码。
_SECRET_KEYS = {"account_password"}

# 平台内部标识 Web UI 自动化临时账号的常量（users.py 用于识别/过滤，resolver 用于命名）。
TEST_ACCOUNT_USER_PREFIX = "AUTO_UI_"
TEST_ACCOUNT_FULL_NAME = "Web UI 自动化临时账号"


def is_test_account_secret(group: str | None, key: str | None) -> bool:
    return (
        str(group or "").strip().lower() == TEST_ACCOUNT_CONFIG_GROUP
        and str(key or "").strip().lower() in _SECRET_KEYS
    )


def encode_test_account_secret(value: str) -> str:
    text = str(value or "")
    if text.startswith(TEST_ACCOUNT_SECRET_PREFIX):
        return text
    return TEST_ACCOUNT_SECRET_PREFIX + encrypt_secret(text)


def decode_test_account_secret(value: str | None) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith(TEST_ACCOUNT_SECRET_PREFIX):
        return decrypt_secret(text[len(TEST_ACCOUNT_SECRET_PREFIX):])
    return text


def mask_test_account_config(group: str | None, key: str | None, value: Any) -> Any:
    if is_test_account_secret(group, key) and value:
        return TEST_ACCOUNT_SECRET_MASK
    return value


def prepare_test_account_config_value(
    group: str | None,
    key: str | None,
    value: str | None,
    *,
    existing: str | None = None,
) -> str | None:
    if not is_test_account_secret(group, key):
        return value
    text = str(value or "")
    if text in {"", TEST_ACCOUNT_SECRET_MASK}:
        return existing
    return encode_test_account_secret(text)
