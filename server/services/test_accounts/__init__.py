"""Web UI 测试账号/数据准备（通用化）。"""
from server.services.test_accounts.binding import (
    cleanup_web_test_accounts,
    prepare_web_test_data,
)
from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.requirements import infer_account_requirement
from server.services.test_accounts.resolver import validate_account_requirement
from server.services.test_accounts.secrets import (
    TEST_ACCOUNT_CONFIG_GROUP,
    TEST_ACCOUNT_FULL_NAME,
    TEST_ACCOUNT_USER_PREFIX,
    decode_test_account_secret,
    encode_test_account_secret,
    is_test_account_secret,
    mask_test_account_config,
    prepare_test_account_config_value,
)

__all__ = [
    "cleanup_web_test_accounts", "prepare_web_test_data", "WebTestDataError",
    "infer_account_requirement", "validate_account_requirement",
    "TEST_ACCOUNT_CONFIG_GROUP", "TEST_ACCOUNT_FULL_NAME", "TEST_ACCOUNT_USER_PREFIX",
    "decode_test_account_secret",
    "encode_test_account_secret", "is_test_account_secret",
    "mask_test_account_config", "prepare_test_account_config_value",
]
