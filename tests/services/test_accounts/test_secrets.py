import json as _json

from server.services.test_accounts.secrets import (
    TEST_ACCOUNT_SECRET_MASK,
    decode_test_account_secret,
    encode_test_account_secret,
    is_test_account_secret,
    mask_test_account_config,
    prepare_test_account_config_value,
)


def test_encode_decode_roundtrip():
    enc = encode_test_account_secret("s3cret")
    assert enc.startswith("enc:v1:")
    assert decode_test_account_secret(enc) == "s3cret"


def test_is_secret_targets_account_password_keys():
    assert is_test_account_secret("test_accounts", "account_password") is True
    assert is_test_account_secret("test_accounts", "dynamic_script") is False
    assert is_test_account_secret("browser", "base_url") is False


def test_mask_hides_password():
    enc = encode_test_account_secret("p")
    assert mask_test_account_config("test_accounts", "account_password", enc) == TEST_ACCOUNT_SECRET_MASK
    assert mask_test_account_config("test_accounts", "dynamic_script", "x") == "x"


def test_prepare_keeps_existing_on_mask_or_empty():
    assert prepare_test_account_config_value("test_accounts", "account_password", "", existing="old") == "old"
    assert prepare_test_account_config_value("test_accounts", "account_password", TEST_ACCOUNT_SECRET_MASK, existing="old") == "old"
    new = prepare_test_account_config_value("test_accounts", "account_password", "new", existing="old")
    assert decode_test_account_secret(new) == "new"


from server.services.test_accounts.secrets import (
    decode_test_account_secret as _dec,
    mask_test_account_config as _mask,
    prepare_test_account_config_value as _prep,
)


def test_mask_hides_nested_pool_passwords():
    # config_value 是 String 列：入参可为原生 list，返回 JSON 字符串（与全库一致）。
    pool = [{"label": "普通", "username": "u1", "password": "enc:v1:abc", "state": "normal", "enabled": True}]
    masked = _json.loads(_mask("test_accounts", "accounts", pool))
    assert masked[0]["password"] == "••••••••"
    assert masked[0]["username"] == "u1"
    # 原对象不被就地修改
    assert pool[0]["password"] == "enc:v1:abc"


def test_prepare_encrypts_new_pool_passwords():
    incoming = [{"username": "u1", "password": "plain1", "state": "normal"}]
    stored = _json.loads(_prep("test_accounts", "accounts", incoming, existing=None))
    assert stored[0]["password"].startswith("enc:v1:")
    assert _dec(stored[0]["password"]) == "plain1"


def test_prepare_preserves_existing_password_on_mask_or_empty():
    # existing 以 DB 的真实形态传入：JSON 字符串。
    existing = _json.dumps([{"username": "u1", "password": "enc:v1:OLD", "state": "normal"}])
    incoming = [
        {"username": "u1", "password": "••••••••", "state": "normal"},   # 掩码=保留旧值
        {"username": "u2", "password": "", "state": "admin"},             # 空=保留(此处无旧值→空)
    ]
    stored = _json.loads(_prep("test_accounts", "accounts", incoming, existing=existing))
    assert stored[0]["password"] == "enc:v1:OLD"   # 按 username 匹配保留
    assert stored[1]["password"] == ""
