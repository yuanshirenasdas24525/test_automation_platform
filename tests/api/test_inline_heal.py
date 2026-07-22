"""逐条即时自愈单测。

需求：163 条用例逐条跑，通过就跑下一条；不通过就当场让 AI 看是哪错了、能不能直接修。
与"跑完整批再统一修"的关键差别是**阻断连锁污染**——上游拿不到变量会让下游全挂，
事后再修还得重跑一整轮（实测批量修 58 条只多通过 9 条）。

本测试锁住"改内存里的 case dict"这一层：修复必须立刻对重试生效，
否则重试跑的还是原来那份配置，自愈等于没做。

跑法：pytest tests/api/test_inline_heal.py -v
"""
from __future__ import annotations

from server.services.inline_heal import _apply_to_case_dict


def _case():
    return {
        "id": 1, "name": "创建用户", "case_type": "api",
        "steps": [
            {"id": 11, "step_order": 0, "step_name": "建用户", "step_type": "http_request",
             "config": {"method": "POST", "path": "/api/users",
                        "assertion": {"status_code": 201}, "extract_data": {}}},
            {"id": 12, "step_order": 1, "step_name": "查用户", "step_type": "http_request",
             "config": {"method": "GET", "path": "/api/users/1"}},
        ],
    }


def test_assertion_fix_applies_to_memory_immediately():
    """断言修复必须落到内存里的 case dict —— 否则重试还是跑旧配置。"""
    case = _case()
    parts = _apply_to_case_dict(case, {"assertion": {"status_code": 200}})
    assert parts == ["assertion"]
    assert case["steps"][0]["config"]["assertion"]["status_code"] == 200


def test_assertion_fix_merges_rather_than_replaces():
    """只改要改的那条断言，其它断言保留。"""
    case = _case()
    case["steps"][0]["config"]["assertion"] = {"status_code": 201, "$.status": "success"}
    _apply_to_case_dict(case, {"assertion": {"status_code": 200}})
    got = case["steps"][0]["config"]["assertion"]
    assert got == {"status_code": 200, "$.status": "success"}


def test_extract_fix_merges_into_extract_data():
    case = _case()
    case["steps"][0]["config"]["extract_data"] = {"uid": "$.id"}
    parts = _apply_to_case_dict(case, {"extract": {"token": "$.data.access_token"}})
    assert parts == ["extract"]
    assert case["steps"][0]["config"]["extract_data"] == {
        "uid": "$.id", "token": "$.data.access_token",
    }


def test_insert_steps_prepends_and_shifts_order():
    """插入的前置步骤必须排在最前面，原步骤整体后移 —— 顺序错了变量就取不到。"""
    case = _case()
    parts = _apply_to_case_dict(case, {"insert_steps": [{
        "step_name": "管理员登录",
        "config": {"method": "POST", "path": "/api/auth/login",
                   "extract_data": {"admin_token": "$.data.access_token"}},
    }]})
    assert parts == ["insert_steps"]
    names = [s["step_name"] for s in case["steps"]]
    assert names == ["管理员登录", "建用户", "查用户"]
    assert [s["step_order"] for s in case["steps"]] == [0, 1, 2]


def test_no_http_step_is_noop():
    case = {"id": 2, "name": "纯 sql", "steps": [
        {"id": 21, "step_order": 0, "step_name": "查库", "step_type": "sql", "config": {}},
    ]}
    assert _apply_to_case_dict(case, {"assertion": {"status_code": 200}}) == []


def test_empty_fix_changes_nothing():
    case = _case()
    before = case["steps"][0]["config"]["assertion"]["status_code"]
    assert _apply_to_case_dict(case, {}) == []
    assert case["steps"][0]["config"]["assertion"]["status_code"] == before
