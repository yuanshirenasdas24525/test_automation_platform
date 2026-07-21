"""_harden_generated_cases 的变量闭合校验（按步骤顺序）。

场景来自真实缺陷复盘：项目 1 的 163 条 api 用例里 107 条存在悬空变量，
其运行通过率恒为 0，占全部失败的 84%。最坑的一类是"鸡生蛋"——
用例名叫「创建超级用户并获取 admin_token」，第一步却先要 admin_token 才能建号。

旧实现按**整条用例**粒度算变量产出（把第 3 步才 extract 的变量也当第 1 步可用），
所以这类用例能过校验、运行时必然 401。本测试锁住"按步骤顺序累积"的语义。

跑法：pytest tests/api/test_harden_variable_order.py -v
"""
from __future__ import annotations

import pytest

from server.api.functional_cases import _harden_generated_cases


def _req(name, path, method="POST", headers=None, body=None, extract=None):
    return {
        "step_name": name, "path": path, "method": method,
        "headers": headers or {"Content-Type": "application/json"},
        "body": body or {},
        "extract": extract or {},
        "assertion": {"status_code": 200},
    }


def _warns(case, var_pool=(), carried=()):
    out = _harden_generated_cases([case], set(var_pool), set(carried))
    return out[0].get("warnings") or []


def _var_warns(warnings):
    return [w for w in warnings if "变量" in w or "引用" in w]


def test_chicken_and_egg_is_flagged():
    """第一步就引用无人产出的变量（case 353 的真实形态）→ 必须报变量找不到来源。"""
    case = {
        "name": "0004 【前置链】创建超级用户并登录获取admin_token",
        "requests": [
            _req("创建超级用户", "/api/users",
                 headers={"Authorization": "Bearer ${admin_token_pre353}"}),
            _req("登录超级用户", "/api/auth/login", extract={}),
        ],
    }
    hits = _var_warns(_warns(case))
    assert hits, "鸡生蛋用例必须被标记"
    assert any("admin_token_pre353" in w and "找不到来源" in w for w in hits), hits


def test_reference_before_produced_is_flagged():
    """step1 引用、step2 才产出 —— 旧实现会漏掉，必须报「要到后面步骤才产出」。"""
    case = {
        "name": "0051 【正常】使用有效token修改密码成功",
        "requests": [
            _req("改密码", "/api/auth/password", method="PUT",
                 headers={"Authorization": "Bearer ${own_token}"}),
            _req("登录拿 token", "/api/auth/login", extract={"own_token": "$.data.access_token"}),
        ],
    }
    hits = _var_warns(_warns(case))
    assert any("own_token" in w for w in hits), hits
    # 必须给出可操作的原因：顺序问题，而不是笼统的"找不到来源"
    assert any("后面的步骤" in w or "调到前面" in w for w in hits), hits


def test_correct_order_passes():
    """先登录产出、后引用 —— 合法，不应报变量问题。"""
    case = {
        "name": "0062 【边界】new_password长度等于最小值修改密码成功",
        "requests": [
            _req("管理员登录", "/api/auth/login", extract={"admin_token": "$.data.access_token"}),
            _req("创建测试用户", "/api/users",
                 headers={"Authorization": "Bearer ${admin_token}"},
                 extract={"test_username": "$.data.username"}),
            _req("登录测试用户", "/api/auth/login",
                 body={"username": "${test_username}", "password": "Test@123"},
                 extract={"user_token": "$.data.access_token"}),
            _req("修改密码", "/api/auth/password", method="PUT",
                 headers={"Authorization": "Bearer ${user_token}"}),
        ],
    }
    assert _var_warns(_warns(case)) == []


def test_var_pool_and_carried_vars_satisfy_references():
    """变量池 / 前序用例带过来的变量都算已产出。"""
    case = {
        "name": "0005 【正常】合法账号密码登录成功",
        "requests": [_req("登录", "/api/auth/login",
                          body={"username": "${user_admin}", "password": "${password_admin}"})],
    }
    assert _var_warns(_warns(case, var_pool={"user_admin", "password_admin"})) == []

    case2 = {
        "name": "0023 【正常】使用有效token获取当前用户信息成功",
        "requests": [_req("查我", "/api/auth/me", method="GET",
                          headers={"Authorization": "Bearer ${token}"})],
    }
    assert _var_warns(_warns(case2, carried={"token"})) == []


def test_pre_hook_vars_satisfy_references():
    """pre_hook 提取的变量在本用例开始时即可用（运行时确实先跑 pre_hook）。"""
    case = {
        "name": "0117 【边界】用户列表分页page为0返回验证错误",
        "pre_hook": [{"config": {"extract_data": {"admin_token_pre": "$.data.access_token"}}}],
        "requests": [_req("列用户", "/api/users", method="GET",
                          headers={"Authorization": "Bearer ${admin_token_pre}"})],
    }
    assert _var_warns(_warns(case)) == []


def test_cross_case_accumulation_is_order_aware():
    """跨用例：后一条能用前一条产出的变量；反过来不行。"""
    producer = {
        "name": "0001 【前置链】登录拿 token",
        "requests": [_req("登录", "/api/auth/login", extract={"token": "$.data.access_token"})],
    }
    consumer = {
        "name": "0023 使用 token",
        "requests": [_req("查我", "/api/auth/me", method="GET",
                          headers={"Authorization": "Bearer ${token}"})],
    }
    # 正序：consumer 排在 producer 后面 → 合法
    out = _harden_generated_cases([dict(producer), dict(consumer)], set(), set())
    assert _var_warns(out[1].get("warnings") or []) == []

    # 逆序：consumer 排在前面 → 必须报
    out = _harden_generated_cases([dict(consumer), dict(producer)], set(), set())
    assert _var_warns(out[0].get("warnings") or []), "顺序颠倒时必须标记"


@pytest.mark.parametrize("base,dotted", [("user", "${user.id}"), ("login", "${login.token}")])
def test_dotted_reference_uses_base_name(base, dotted):
    """${a.b} 形式按根名 a 判定来源：根名已产出就不该报警。"""
    case = {
        "name": "点号引用",
        "requests": [
            _req("产出根对象", "/api/login", extract={base: "$.data"}),
            _req("用点号引用", "/api/users/" + dotted, method="GET"),
        ],
    }
    hits = [w for w in _var_warns(_warns(case)) if base in w]
    assert hits == [], f"根名 {base} 已由前一步产出，不该报警：{hits}"


def test_dotted_reference_without_producer_is_flagged():
    """根名无人产出时，点号引用照样要报。"""
    case = {
        "name": "点号引用但无产出",
        "requests": [_req("用点号引用", "/api/users/${data.token}", method="GET")],
    }
    assert any("data.token" in w for w in _var_warns(_warns(case)))


# ===================================================================
# needs_fix：执行必挂 → 评审页默认不勾选
# ===================================================================
def _harden_one(case, var_pool=(), carried=()):
    return _harden_generated_cases([case], set(var_pool), set(carried))[0]


def test_dangling_variable_sets_needs_fix():
    """变量悬空是执行必挂类（实测通过率 0）→ 必须置 needs_fix。"""
    case = {
        "name": "0004 【前置链】创建超级用户并登录获取admin_token",
        "requests": [_req("创建超级用户", "/api/users",
                          headers={"Authorization": "Bearer ${admin_token_pre353}"})],
    }
    out = _harden_one(case)
    assert out.get("needs_fix") is True
    assert any("admin_token_pre353" in w for w in out.get("blocking_warnings") or [])


def test_empty_case_sets_needs_fix():
    """空壳用例（没有可执行请求）也是执行必挂。"""
    out = _harden_one({"name": "0999 【场景】1000 并发下单", "requests": []})
    assert out.get("needs_fix") is True


def test_missing_assertion_alone_does_not_set_needs_fix():
    """缺断言是质量问题、不是执行必挂 —— 只提醒，不影响默认勾选。"""
    case = {
        "name": "0005 【正常】合法账号密码登录成功",
        "requests": [{
            "step_name": "登录", "path": "/api/auth/login", "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": {"username": "${user_admin}", "password": "${password_admin}"},
            "extract": {}, "assertion": {},          # 故意没断言
        }],
    }
    out = _harden_one(case, var_pool={"user_admin", "password_admin"})
    assert out.get("warnings"), "缺断言应当有提醒"
    assert not out.get("needs_fix"), "缺断言不应拦住入库"


def test_clean_case_has_no_needs_fix():
    """完全合格的用例不带任何标记。"""
    case = {
        "name": "0005 【正常】合法账号密码登录成功",
        "requests": [_req("登录", "/api/auth/login",
                          body={"username": "${user_admin}", "password": "${password_admin}"},
                          extract={"token": "$.data.access_token"})],
    }
    out = _harden_one(case, var_pool={"user_admin", "password_admin"})
    assert not out.get("warnings")
    assert not out.get("needs_fix")
