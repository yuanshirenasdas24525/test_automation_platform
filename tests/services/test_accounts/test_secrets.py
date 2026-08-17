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
