from server.services.test_accounts import binding


def _fake_sources(accounts, dynamic_script):
    return {"accounts": accounts, "dynamic_script": dynamic_script}


def test_prepare_binds_static_pool_account(monkeypatch):
    # prepare_web_test_data calls binding's own module-level `load_account_sources`
    # name directly, while resolver.validate_account_requirement does its own local
    # `from server.services.test_accounts.sources import load_account_sources` inside
    # the function body — a fresh lookup on the `sources` module each call. Patching
    # only one of the two leaves the other hitting the real DB-backed implementation
    # with session=None, so both need patching to the same fake.
    fake = lambda *_: _fake_sources(
        [{"label": "普通", "username": "u1", "password": "p1", "state": "normal", "enabled": True}], ""
    )
    monkeypatch.setattr(binding, "load_account_sources", fake)
    monkeypatch.setattr("server.services.test_accounts.sources.load_account_sources", fake)
    cases = [{
        "name": "登录成功", "variables": {"username": "", "password": ""},
        "generation_metadata": {"test_data_requirement": {
            "status": "ready", "profile": "dynamic_active", "credential_mode": "correct",
            "username_variable": "username", "password_variable": "password",
        }},
    }]
    tokens = binding.prepare_web_test_data(None, cases, project_id=1)
    assert cases[0]["variables"]["username"] == "u1"
    assert cases[0]["variables"]["password"] == "p1"
    assert tokens == []


def test_prepare_collects_cleanup_token_from_script(monkeypatch):
    fake = lambda *_: _fake_sources([], "provision")
    monkeypatch.setattr(binding, "load_account_sources", fake)
    monkeypatch.setattr("server.services.test_accounts.sources.load_account_sources", fake)
    monkeypatch.setattr("server.services.test_accounts.resolver.run_named_script", lambda *a, **k: (True, {
        "ok": True, "result": {"username": "fresh", "password": "pw"},
        "cleanup": {"user_id": 9},
    }))
    cases = [{
        "name": "停用账号", "variables": {"username": "", "password": ""},
        "generation_metadata": {"test_data_requirement": {
            "status": "ready", "profile": "dynamic_disabled", "credential_mode": "correct",
            "username_variable": "username", "password_variable": "password",
        }},
    }]
    tokens = binding.prepare_web_test_data(None, cases, project_id=1)
    assert cases[0]["variables"]["username"] == "fresh"
    assert len(tokens) == 1
    assert tokens[0]["script_name"] == "provision"


def test_cleanup_token_is_attached_to_case_for_celery_transport(monkeypatch):
    # 回归 C1：令牌必须挂到 case 上，否则跨 Celery 进程边界丢失、动态账号泄漏。
    fake = lambda *_: _fake_sources([], "provision")
    monkeypatch.setattr(binding, "load_account_sources", fake)
    monkeypatch.setattr("server.services.test_accounts.sources.load_account_sources", fake)
    monkeypatch.setattr(
        "server.services.test_accounts.resolver.run_named_script",
        lambda *a, **k: (True, {"ok": True, "result": {"username": "fresh", "password": "pw"}, "cleanup": {"user_id": 9}}),
    )
    cases = [{
        "name": "停用账号", "variables": {"username": "", "password": ""},
        "generation_metadata": {"test_data_requirement": {
            "status": "ready", "profile": "dynamic_disabled", "credential_mode": "correct",
            "username_variable": "username", "password_variable": "password",
        }},
    }]
    binding.prepare_web_test_data(None, cases, project_id=1)

    # 1) 令牌挂在 case 上（随 cases 序列化进 Celery 载荷）
    assert cases[0]["_test_data_cleanup_tokens"][0]["script_name"] == "provision"

    # 2) 复现 run_test_task 的重建逻辑，证明令牌真能被收尾清理拿到
    reconstructed = [
        dict(token)
        for case in cases
        if isinstance(case, dict)
        for token in (case.get("_test_data_cleanup_tokens") or [])
        if isinstance(token, dict)
    ]
    assert len(reconstructed) == 1
    assert reconstructed[0]["payload"] == {"user_id": 9}


def test_manually_resolved_case_bypasses_data_gate(monkeypatch):
    # 用户已"完成调整"的 contract_mismatch 用例:不再被数据门禁拦,放行执行(尽力绑账号)。
    fake = lambda *_: _fake_sources(
        [{"label": "管理员", "username": "demo_admin", "password": "pw", "state": "admin", "enabled": True}], ""
    )
    monkeypatch.setattr(binding, "load_account_sources", fake)
    monkeypatch.setattr("server.services.test_accounts.sources.load_account_sources", fake)
    cases = [{
        "name": "0005 用户名长度65位", "variables": {"long_username": "", "password": ""},
        "generation_metadata": {
            "needs_manual_adjustment": False,
            "manual_adjustment_status": "resolved",
            "test_data_requirement": {
                "status": "contract_mismatch", "profile": "unsupported",
                "username_variable": "long_username", "password_variable": "password",
                "reason": "登录不校验用户名长度",
            },
        },
    }]
    tokens = binding.prepare_web_test_data(None, cases, project_id=1)  # 不应抛错
    assert tokens == []
    # 尽力绑上了(池里 admin 兜底)
    assert cases[0]["variables"]["long_username"] == "demo_admin"


def test_unresolved_contract_mismatch_still_blocked(monkeypatch):
    import pytest
    fake = lambda *_: _fake_sources([], "")
    monkeypatch.setattr(binding, "load_account_sources", fake)
    monkeypatch.setattr("server.services.test_accounts.sources.load_account_sources", fake)
    cases = [{
        "name": "0002", "variables": {"username": "", "password": ""},
        "generation_metadata": {
            "needs_manual_adjustment": True, "manual_adjustment_status": "pending",
            "test_data_requirement": {"status": "contract_mismatch", "profile": "unsupported", "reason": "bad"},
        },
    }]
    with pytest.raises(binding.WebTestDataError):
        binding.prepare_web_test_data(None, cases, project_id=1)
