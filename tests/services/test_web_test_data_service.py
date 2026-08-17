"""Web UI 测试账号需求与密钥处理。"""
from __future__ import annotations

import pytest

from server.services import web_test_data_service as service
from server.services.web_test_data_service import (
    TEST_ACCOUNT_PROVIDER_HTTP,
    TEST_ACCOUNT_PROVIDER_SCRIPT,
    TEST_ACCOUNT_SECRET_MASK,
    _HttpTestAccountClient,
    _render_template,
    _temporary_password,
    _temporary_username,
    cleanup_web_test_accounts,
    decode_test_account_secret,
    infer_account_requirement,
    mask_test_account_config,
    prepare_web_test_data,
    prepare_test_account_config_value,
    validate_account_requirement,
)


@pytest.mark.parametrize(
    ("title", "profile", "mode"),
    [
        ("登录页加载验证", "none", "none"),
        ("用户名和密码均为空", "form_empty", "both_empty"),
        ("用户名为空时提示", "form_empty", "empty_username"),
        ("密码为空时提示", "form_empty", "empty_password"),
        ("输入不存在的用户名", "synthetic_nonexistent", "wrong"),
        ("使用已停用账号登录", "dynamic_disabled", "correct"),
        ("存在的用户名但密码错误", "dynamic_active", "wrong"),
        ("内置admin默认密码登录成功", "shared_admin", "correct"),
        ("同一账号连续5次密码错误即锁定", "isolated_lock_account", "wrong"),
    ],
)
def test_infer_account_profiles(title: str, profile: str, mode: str):
    requirement = infer_account_requirement(title, variables={"username": "", "password": ""})
    assert requirement["profile"] == profile
    assert requirement["credential_mode"] == mode


@pytest.mark.parametrize(
    "title",
    [
        "密码长度5位小于最小登录失败",
        "用户名包含非法字符登录失败",
        "用户名长度65位超长边界验证",
    ],
)
def test_login_contract_mismatch_is_blocked(title: str):
    requirement = infer_account_requirement(title, variables={"username": "", "password": ""})
    assert requirement["status"] == "contract_mismatch"


@pytest.mark.parametrize(
    "title",
    [
        "锁定时间过后失败计数自动重置",
        "锁定仅针对该账号该来源，不影响其他来源",
        "任何来源登录成功后清零失败计数",
        "锁定期间输入正确密码提示剩余秒数",
    ],
)
def test_unstable_lock_precondition_is_blocked(title: str):
    requirement = infer_account_requirement(title, variables={"username": "", "password": ""})
    assert requirement["status"] == "unsupported"


def test_boundary_account_values_have_exact_length():
    assert len(_temporary_username({"username_length": 64})) == 64
    assert len(_temporary_password({"password_length": 6})) == 6
    assert len(_temporary_password({"password_length": 128})) == 128
    assert "." in _temporary_username({"username_allowed_special": True})


def test_test_account_secret_roundtrip_and_mask():
    stored = prepare_test_account_config_value(
        "test_accounts",
        "shared_password",
        "Secret#123",
    )
    assert stored is not None
    assert "Secret#123" not in stored
    assert decode_test_account_secret(stored) == "Secret#123"
    assert mask_test_account_config("test_accounts", "shared_password", stored) == TEST_ACCOUNT_SECRET_MASK


def test_blank_secret_edit_preserves_existing_ciphertext():
    stored = prepare_test_account_config_value(
        "test_accounts",
        "shared_password",
        "Secret#123",
    )
    assert prepare_test_account_config_value(
        "test_accounts",
        "shared_password",
        "",
        existing=stored,
    ) == stored


def test_lock_case_is_blocked_when_login_throttle_is_disabled(monkeypatch):
    monkeypatch.setenv("LOGIN_THROTTLE_ENABLED", "0")
    requirement = infer_account_requirement(
        "同一账号连续5次密码错误即锁定",
        variables={"username": "", "password": ""},
    )
    errors = validate_account_requirement(None, 1, requirement)  # type: ignore[arg-type]
    assert errors == ["当前环境已关闭登录限流，无法验证连续失败后的账号锁定"]


def _http_config() -> dict:
    return {
        "provider": TEST_ACCOUNT_PROVIDER_HTTP,
        "shared_username": "admin",
        "shared_password": "Admin#123",
        "auto_cleanup": True,
        "api_base_url": "http://target.local",
        "login_method": "POST",
        "login_path": "/api/auth/login",
        "login_body": '{"username":"${shared_username}","password":"${shared_password}"}',
        "token_jsonpath": "$.data.access_token",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "create_method": "POST",
        "create_path": "/api/users",
        "create_body": (
            '{"username":"${username}","password":"${password}",'
            '"is_active":"${is_active}","role_codes":["test"]}'
        ),
        "user_id_jsonpath": "$.data.id",
        "cleanup_method": "DELETE",
        "cleanup_path": "/api/users/${user_id}/purge-test-account",
        "timeout_seconds": 10.0,
    }


def _script_config() -> dict:
    return {
        "provider": TEST_ACCOUNT_PROVIDER_SCRIPT,
        "shared_username": "account_admin",
        "shared_password": "Admin#123",
        "auto_cleanup": True,
        "api_base_url": "http://target.local",
        "prepare_script": "project_account_factory",
        "cleanup_script": "project_account_factory",
        "script_config": {"tenant": "qa"},
        "timeout_seconds": 10.0,
    }


def test_http_template_preserves_boolean_value():
    rendered = _render_template(
        {"username": "${username}", "is_active": "${is_active}"},
        {"username": "AUTO_UI_1", "is_active": False},
    )
    assert rendered == {"username": "AUTO_UI_1", "is_active": False}


def test_http_account_client_logs_in_creates_and_cleans(monkeypatch):
    calls: list[dict] = []

    class _Response:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.content = b"{}"
            self.text = ""

        def json(self):
            return self._payload

    def fake_request(**kwargs):
        calls.append(kwargs)
        if kwargs["url"].endswith("/api/auth/login"):
            return _Response(200, {"data": {"access_token": "token-1"}})
        if kwargs["method"] == "POST":
            return _Response(200, {"data": {"id": 88}})
        return _Response(200, {"status": "success"})

    client = _HttpTestAccountClient(_http_config())
    monkeypatch.setattr(client.session, "request", fake_request)
    account = client.create_account(
        {"profile": "dynamic_disabled", "constraints": {}},
        username="AUTO_UI_1",
        password="AutoTest#123",
    )
    client.cleanup_account({"user_id": 88, "username": "AUTO_UI_1"})
    client.close()

    assert account == {"user_id": 88, "username": "AUTO_UI_1"}
    assert calls[0]["json"] == {"username": "admin", "password": "Admin#123"}
    assert calls[1]["json"]["is_active"] is False
    assert calls[1]["headers"] == {"Authorization": "Bearer token-1"}
    assert calls[2]["url"].endswith("/api/users/88/purge-test-account")
    assert len([call for call in calls if call["url"].endswith("/api/auth/login")]) == 1


def test_prepare_web_test_data_uses_http_provider(monkeypatch):
    created: list[tuple[str, str]] = []

    class _Client:
        def __init__(self, config):
            assert config["provider"] == TEST_ACCOUNT_PROVIDER_HTTP

        def create_account(self, requirement, *, username, password):
            created.append((username, password))
            return {"user_id": 91, "username": username}

        def cleanup_account(self, token):  # pragma: no cover - 仅失败回滚时调用
            raise AssertionError(token)

        def close(self):
            return None

    monkeypatch.setattr(service, "load_test_account_config", lambda *_: _http_config())
    monkeypatch.setattr(service, "validate_account_requirement", lambda *_: [])
    monkeypatch.setattr(service, "_HttpTestAccountClient", _Client)
    requirement = {
        "status": "ready",
        "profile": "dynamic_active",
        "credential_mode": "correct",
        "username_variable": "username",
        "password_variable": "password",
        "constraints": {},
    }
    cases = [{
        "name": "普通账号登录成功",
        "variables": {"username": "", "password": ""},
        "generation_metadata": {"test_data_requirement": requirement},
    }]

    tokens = prepare_web_test_data(None, cases, project_id=7)  # type: ignore[arg-type]

    assert created
    assert cases[0]["variables"]["username"].startswith("AUTO_UI_")
    assert cases[0]["variables"]["password"] == "AutoTest#123"
    assert tokens == [{
        "provider": TEST_ACCOUNT_PROVIDER_HTTP,
        "project_id": 7,
        "user_id": 91,
        "username": cases[0]["variables"]["username"],
    }]
    assert cases[0]["_test_data_cleanup_tokens"] == tokens


def test_prepare_web_test_data_uses_isolated_workflow_provider(monkeypatch):
    calls: list[dict] = []

    def fake_run_named_script(name, **kwargs):
        calls.append({"name": name, **kwargs})
        assert kwargs["kind"] == "workflow"
        assert kwargs["body"]["action"] == "create"
        assert kwargs["config"]["tenant"] == "qa"
        return True, {
            "ok": True,
            "result": {
                "user_id": "external-91",
                "username": "SCRIPT_USER",
                "password": "Script#123",
            },
            "cleanup": {"external_id": "external-91"},
        }

    monkeypatch.setattr(service, "load_test_account_config", lambda *_: _script_config())
    monkeypatch.setattr(service, "validate_account_requirement", lambda *_: [])
    monkeypatch.setattr(service, "run_named_script", fake_run_named_script)
    requirement = {
        "status": "ready",
        "profile": "dynamic_active",
        "credential_mode": "correct",
        "username_variable": "username",
        "password_variable": "password",
        "constraints": {},
    }
    cases = [{
        "name": "脚本账号登录成功",
        "variables": {"username": "", "password": ""},
        "generation_metadata": {"test_data_requirement": requirement},
    }]

    tokens = prepare_web_test_data(None, cases, project_id=7)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert cases[0]["variables"] == {
        "username": "SCRIPT_USER",
        "password": "Script#123",
    }
    assert tokens[0]["provider"] == TEST_ACCOUNT_PROVIDER_SCRIPT
    assert tokens[0]["script_name"] == "project_account_factory"
    assert tokens[0]["cleanup_payload"] == {"external_id": "external-91"}
    assert tokens[0]["token_id"]


def test_cleanup_web_test_accounts_calls_http_cleanup(monkeypatch):
    cleaned: list[dict] = []

    class _Client:
        def __init__(self, config):
            assert config["provider"] == TEST_ACCOUNT_PROVIDER_HTTP

        def cleanup_account(self, token):
            cleaned.append(token)

        def close(self):
            return None

    monkeypatch.setattr(service, "load_test_account_config", lambda *_: _http_config())
    monkeypatch.setattr(service, "_HttpTestAccountClient", _Client)
    token = {"provider": "http", "project_id": 7, "user_id": 91, "username": "AUTO_UI_1"}

    count = cleanup_web_test_accounts(None, [token, dict(token)])  # type: ignore[arg-type]

    assert count == 1
    assert cleaned == [token]


def test_cleanup_web_test_accounts_calls_isolated_workflow(monkeypatch):
    calls: list[dict] = []

    def fake_run_named_script(name, **kwargs):
        calls.append({"name": name, **kwargs})
        return True, {"ok": True}

    monkeypatch.setattr(service, "load_test_account_config", lambda *_: _script_config())
    monkeypatch.setattr(service, "run_named_script", fake_run_named_script)
    token = {
        "provider": TEST_ACCOUNT_PROVIDER_SCRIPT,
        "project_id": 7,
        "token_id": "token-91",
        "script_name": "project_account_factory",
        "username": "SCRIPT_USER",
        "user_id": "external-91",
        "cleanup_payload": {"external_id": "external-91"},
    }

    count = cleanup_web_test_accounts(None, [token, dict(token)])  # type: ignore[arg-type]

    assert count == 1
    assert len(calls) == 1
    assert calls[0]["body"] == {
        "action": "cleanup",
        "token": {"external_id": "external-91"},
        "account": {"username": "SCRIPT_USER", "user_id": "external-91"},
    }
