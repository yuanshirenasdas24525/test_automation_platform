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
