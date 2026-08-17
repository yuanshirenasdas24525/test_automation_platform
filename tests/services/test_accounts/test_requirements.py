from server.services.test_accounts.requirements import infer_account_requirement


def test_page_load_needs_no_account():
    r = infer_account_requirement("首页页面加载展示", None, {"username": ""})
    assert r["profile"] == "none"


def test_both_empty_form():
    r = infer_account_requirement("用户名和密码均为空登录", None, {"username": "", "password": ""})
    assert r["profile"] == "form_empty"
    assert r["credential_mode"] == "both_empty"


def test_shared_admin():
    r = infer_account_requirement("admin 使用默认密码成功登录", None, {"username": "admin", "password": "x"})
    assert r["profile"] == "shared_admin"
    assert r["lifecycle"] == "shared"


def test_disabled_account_is_dynamic():
    r = infer_account_requirement("已停用账号登录被拒", None, {"username": "u", "password": "p"})
    assert r["profile"] == "dynamic_disabled"
    assert r["lifecycle"] == "dynamic"
