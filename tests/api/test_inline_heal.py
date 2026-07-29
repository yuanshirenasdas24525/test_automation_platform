"""逐条即时自愈单测。

需求：163 条用例逐条跑，通过就跑下一条；不通过就当场让 AI 看是哪错了、能不能直接修。
与"跑完整批再统一修"的关键差别是**阻断连锁污染**——上游拿不到变量会让下游全挂，
事后再修还得重跑一整轮（实测批量修 58 条只多通过 9 条）。

本测试锁住"改内存里的 case dict"这一层：修复必须立刻对重试生效，
否则重试跑的还是原来那份配置，自愈等于没做。

跑法：pytest tests/api/test_inline_heal.py -v
"""
from __future__ import annotations

from types import SimpleNamespace

from server.services.inline_heal import (
    _apply_to_case_dict,
    _redact,
    _validate_model_decision,
    _with_rebound_extracts,
)


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


def test_fix_targets_failed_http_step_instead_of_first_step():
    """多请求用例必须精准修改失败步骤，不能把第二步修复误打到第一步。"""
    case = _case()
    parts = _apply_to_case_dict(
        case,
        {"params": {"user_id": "${uid}"}},
        target_step_id=12,
    )
    assert parts == ["params"]
    assert "params" not in case["steps"][0]["config"]
    assert case["steps"][1]["config"]["params"] == {"user_id": "${uid}"}


def test_request_fix_merges_and_deletes_explicit_null_field():
    case = _case()
    case["steps"][0]["config"]["params"] = {
        "account": "${username}",
        "password": "${password}",
    }
    _apply_to_case_dict(
        case,
        {"params": {"account": None, "username": "${username}"}},
    )
    assert case["steps"][0]["config"]["params"] == {
        "username": "${username}",
        "password": "${password}",
    }


def test_request_fix_rebinds_replaced_password_variable():
    """自愈替换旧密码变量时，应自动生成同名提取赋值。"""
    step = {
        "config": {
            "params": {
                "username": "${username}",
                "password": "${password_admin}",
            },
        },
    }
    fix = _with_rebound_extracts(
        step,
        {"params": {"password": "NewTest@123"}},
    )

    assert fix["extract"] == {"password_admin": "NewTest@123"}


def test_request_fix_rebinds_embedded_token_without_bearer_prefix():
    """请求头里的 token 回写时不能把 Bearer 前缀一起写进变量池。"""
    step = {
        "config": {
            "headers": {"Authorization": "Bearer ${token}"},
        },
    }
    fix = _with_rebound_extracts(
        step,
        {"headers": {"Authorization": "Bearer ${new_token}"}},
    )

    assert fix["extract"] == {"token": "${new_token}"}


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


def test_model_fix_without_requirement_evidence_is_rejected():
    view = SimpleNamespace(status_code=200, output_data={"code": 0})
    fix, reason = _validate_model_decision({
        "classification": "用例问题",
        "intent_supported": True,
        "requirement_evidence": [],
        "confidence": 0.99,
        "fix": {"assertion": {"status_code": 200}},
    }, view)
    assert fix is None
    assert "依据" in str(reason)


def test_requirement_guarded_extract_fix_must_exist_in_real_response():
    view = SimpleNamespace(
        status_code=200,
        output_data={"data": {"access_token": "secret"}},
    )
    fix, reason = _validate_model_decision({
        "classification": "用例问题",
        "intent_supported": True,
        "requirement_evidence": ["用例描述要求登录成功后提取访问令牌"],
        "confidence": 0.95,
        "fix": {
            "extract": {
                "token": "$.data.access_token",
                "missing": "$.data.not_exists",
            },
        },
    }, view)
    assert reason is None
    assert fix == {"extract": {"token": "$.data.access_token"}}


def test_ai_prompt_payload_redacts_real_secrets_but_keeps_variable_refs():
    value = _redact({
        "password": "plain-secret",
        "Authorization": "Bearer real-token",
        "template_password": "${password_admin}",
    })
    assert value["password"] == "<redacted>"
    assert value["Authorization"] == "<redacted>"
    assert value["template_password"] == "${password_admin}"
