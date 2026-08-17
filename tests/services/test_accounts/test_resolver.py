import pytest

from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.resolver import resolve_account


def _req(profile, credential_mode="correct", **extra):
    return {
        "status": "ready", "profile": profile, "credential_mode": credential_mode,
        "username_variable": "username", "password_variable": "password", **extra,
    }


def _sources(accounts=None, dynamic_script=""):
    return {"accounts": accounts or [], "dynamic_script": dynamic_script}


def test_none_profile_binds_nothing():
    r = resolve_account(_req("none", "none"), _sources(), session=None, project_id=1)
    assert r.bindings == {}
    assert r.cleanup_token is None


def test_form_empty_both_empty():
    r = resolve_account(_req("form_empty", "both_empty"), _sources(), session=None, project_id=1)
    assert r.bindings == {"username": "", "password": ""}


def test_synthetic_nonexistent_is_unique_and_wrong():
    r = resolve_account(_req("synthetic_nonexistent", "wrong"), _sources(), session=None, project_id=1)
    assert r.bindings["username"].startswith("AUTO_MISSING_")
    assert r.bindings["password"]


def test_static_pool_match_by_state():
    accts = [{"label": "普通", "username": "u1", "password": "p1", "state": "normal", "enabled": True}]
    r = resolve_account(_req("dynamic_active"), _sources(accts), session=None, project_id=1)
    assert r.bindings == {"username": "u1", "password": "p1"}
    assert r.cleanup_token is None


def test_normal_falls_back_to_admin():
    accts = [{"label": "管理员", "username": "admin", "password": "pa", "state": "admin", "enabled": True}]
    r = resolve_account(_req("dynamic_active"), _sources(accts), session=None, project_id=1)
    assert r.bindings["username"] == "admin"


def test_disabled_pool_entry_is_skipped():
    accts = [{"label": "普通", "username": "u1", "password": "p1", "state": "normal", "enabled": False}]
    with pytest.raises(WebTestDataError):
        resolve_account(_req("dynamic_active"), _sources(accts), session=None, project_id=1)


def test_no_match_no_script_raises_actionable():
    with pytest.raises(WebTestDataError) as e:
        resolve_account(_req("dynamic_disabled"), _sources(), session=None, project_id=7)
    assert "disabled" in str(e.value) or "停用" in str(e.value)
