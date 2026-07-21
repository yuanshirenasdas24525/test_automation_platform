"""API 参数生命周期与 AI 修复的回归测试。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from database.data_sync import _case_id_from_allure_result
from database.models import TestCase as ORMTestCase
from database.models import TestStep as ORMTestStep
from runners.case_executor import CaseExecutor
from runners.context.auth_cache import (
    RunAuthCache,
    build_auth_request_signature,
    extract_hook_values,
)
from runners.context.execution_context import ExecutionContext
from runners.protocol import StepResult, StepStatus
from server.api.functional_cases import (
    _build_report_dependency_context,
    _normalize_report_diagnosis_item,
    _serialize_api_case_definition,
)
from server.services.ai_fix_service import _apply_fix_to_case, _preflight_one


class _HookDispatcher:
    """只验证 hook 编排，不发送真实 HTTP 请求。"""

    def __init__(self, *, hook_extracts: bool) -> None:
        self.hook_extracts = hook_extracts
        self.main_calls = 0
        self.main_token: Any = None

    def dispatch(self, step: dict, ctx: ExecutionContext) -> StepResult:
        is_hook = int(step.get("step_order") or 0) == -1
        extracted = {}
        if is_hook and self.hook_extracts:
            extracted = {"token": "fresh-token"}
            ctx.set_var("token", "fresh-token")
        if not is_hook:
            self.main_calls += 1
            self.main_token = ctx.get_var("token")
        return StepResult(
            step_id=step.get("id"),
            step_order=int(step.get("step_order") or 0),
            step_name=str(step.get("step_name") or "step"),
            step_type=str(step.get("step_type") or "http_request"),
            status=StepStatus.PASSED,
            extracted=extracted,
        )


class _NestedLoginDispatcher:
    """模拟真实登录响应结构，并统计实际登录请求次数。"""

    def __init__(self) -> None:
        self.hook_calls = 0

    def dispatch(self, step: dict, ctx: ExecutionContext) -> StepResult:
        is_hook = int(step.get("step_order") or 0) == -1
        if is_hook:
            self.hook_calls += 1
            return StepResult(
                step_id=None,
                step_order=-1,
                step_name=str(step.get("step_name") or "pre_hook"),
                step_type="http_request",
                status=StepStatus.PASSED,
                output_data={
                    "status": "success",
                    "data": {"access_token": "fresh-token", "refresh_token": "refresh-token"},
                },
            )
        return StepResult(
            step_id=step.get("id"),
            step_order=int(step.get("step_order") or 0),
            step_name=str(step.get("step_name") or "step"),
            step_type=str(step.get("step_type") or "http_request"),
            status=StepStatus.PASSED,
        )


def _case_with_login_hook() -> dict:
    return {
        "id": 1,
        "name": "需要独立登录的用例",
        "case_type": "api",
        "pre_hook": [{
            "type": "http_request",
            "config": {
                "method": "POST",
                "path": "/api/auth/login",
                "params": {"username": "u", "password": "p"},
                "extract_data": {"token": "$.data.token"},
            },
        }],
        "steps": [{
            "id": 2,
            "step_order": 1,
            "step_name": "查询自己",
            "step_type": "http_request",
            "config": {},
        }],
    }


def test_pre_hook_missing_extract_does_not_fallback_to_stale_token(monkeypatch):
    """登录 hook 未产出 token 时，中断主步骤，禁止复用共享池旧 token。"""
    monkeypatch.setattr(CaseExecutor, "_inject_default_parameters", staticmethod(lambda _ctx: None))
    monkeypatch.setattr(CaseExecutor, "_inject_target_db", staticmethod(lambda _ctx: None))
    dispatcher = _HookDispatcher(hook_extracts=False)
    ctx = ExecutionContext()
    ctx.set_var("_run_shared_vars", {"token": "stale-token"})

    result = CaseExecutor(dispatcher=dispatcher).run(_case_with_login_hook(), ctx)

    assert result.status == StepStatus.ERROR
    assert "未提取到声明变量" in str(result.error_message)
    assert dispatcher.main_calls == 0


def test_pre_hook_fresh_token_overrides_shared_token(monkeypatch):
    """登录 hook 成功后，主步骤只能看到本用例新 token。"""
    monkeypatch.setattr(CaseExecutor, "_inject_default_parameters", staticmethod(lambda _ctx: None))
    monkeypatch.setattr(CaseExecutor, "_inject_target_db", staticmethod(lambda _ctx: None))
    dispatcher = _HookDispatcher(hook_extracts=True)
    ctx = ExecutionContext()
    ctx.set_var("_run_shared_vars", {"token": "stale-token"})

    result = CaseExecutor(dispatcher=dispatcher).run(_case_with_login_hook(), ctx)

    assert result.status == StepStatus.PASSED
    assert dispatcher.main_calls == 1
    assert dispatcher.main_token == "fresh-token"


def test_wrong_login_jsonpath_recovers_unique_nested_token():
    """AI 少写 data 层级时，只在响应里同名字段唯一的情况下自动纠偏。"""
    config = {"extract_data": {"admin_token": "$.access_token"}}

    extracted = extract_hook_values(
        config,
        {"status": "success", "data": {"access_token": "fresh-token"}},
    )

    assert extracted == {"admin_token": "fresh-token"}


def test_same_login_hook_reuses_one_run_cache_across_cases(monkeypatch):
    """同凭据的逐用例登录 hook 在一轮运行中只请求一次，并映射不同变量名。"""
    monkeypatch.setattr(CaseExecutor, "_inject_default_parameters", staticmethod(lambda _ctx: None))
    monkeypatch.setattr(CaseExecutor, "_inject_target_db", staticmethod(lambda _ctx: None))
    dispatcher = _NestedLoginDispatcher()
    cache = RunAuthCache()
    executor = CaseExecutor(dispatcher=dispatcher)

    first = _case_with_login_hook()
    first["pre_hook"][0]["config"]["extract_data"] = {"token_one": "$.access_token"}
    second = _case_with_login_hook()
    second["id"] = 2
    second["pre_hook"][0]["config"]["extract_data"] = {"token_two": "$.access_token"}

    first_ctx = ExecutionContext()
    first_ctx.set_var("_run_auth_cache", cache)
    second_ctx = ExecutionContext()
    second_ctx.set_var("_run_auth_cache", cache)

    first_result = executor.run(first, first_ctx)
    second_result = executor.run(second, second_ctx)

    assert first_result.status == StepStatus.PASSED
    assert second_result.status == StepStatus.PASSED
    assert dispatcher.hook_calls == 1
    assert first_ctx.get_var("token_one") == "fresh-token"
    assert second_ctx.get_var("token_two") == "fresh-token"


def test_auth_cache_invalidation_matches_token_usage_only():
    """登出或 401 只清理请求实际携带的那一组认证缓存。"""
    cache = RunAuthCache()
    first_ctx = ExecutionContext()
    second_ctx = ExecutionContext()
    first_config = {
        "method": "POST",
        "path": "/api/auth/login",
        "params": {"username": "first", "password": "p"},
    }
    second_config = {
        "method": "POST",
        "path": "/api/auth/login",
        "params": {"username": "second", "password": "p"},
    }
    first_signature = build_auth_request_signature(first_config, first_ctx)
    second_signature = build_auth_request_signature(second_config, second_ctx)
    cache.put(first_signature, {"data": {"access_token": "first-token"}})
    cache.put(second_signature, {"data": {"access_token": "second-token"}})

    removed = cache.invalidate_if_used({"headers": {"Authorization": "Bearer first-token"}})

    assert removed == 1
    assert cache.get(first_signature) is None
    assert cache.get(second_signature) == {"data": {"access_token": "second-token"}}


def test_allure_case_id_prefers_label_and_supports_legacy_fixture():
    """报告补缺优先读显式 label，历史结果可从 fixture 参数开头识别 id。"""
    assert _case_id_from_allure_result({
        "labels": [{"name": "case_id", "value": "495"}],
        "parameters": [{"name": "case", "value": "{'id': 1, 'name': '旧值'}"}],
    }) == 495
    assert _case_id_from_allure_result({
        "parameters": [{"name": "case", "value": "{'id': 495, 'name': '0146'}"}],
    }) == 495


def test_ai_fix_updates_runner_effective_extract_and_assertion():
    """AI 修复必须同步快速编辑器字段，否则 Runner 仍会读取旧规则。"""
    case = ORMTestCase(id=10, module_id=1, name="登录", case_type="api")
    step = ORMTestStep(
        id=20,
        case_id=10,
        step_order=1,
        step_name="登录",
        step_type="http_request",
        config={
            "method": "POST",
            "path": "/api/auth/login",
            "extract_data": {"token": "$.old.token", "uid": "$.data.id"},
            "assertion": {"status_code": 201, "$.code": 1},
        },
        extract=[{"name": "token", "from": "response.body", "jsonpath": "$.stale.token"}],
        assertion=[{"type": "equal", "target": "status_code", "expected": 201}],
    )
    case.steps = [step]

    parts = _apply_fix_to_case(
        case,
        {
            "extract": {"token": "$.data.access_token"},
            "assertion": {"status_code": 200, "$.code": 0},
        },
    )

    assert set(parts) == {"extract", "assertion"}
    assert step.config["extract_data"] == {
        "token": "$.data.access_token",
        "uid": "$.data.id",
    }
    assert step.config["assertion"] == {"status_code": 200, "$.code": 0}
    serialized = _serialize_api_case_definition(case)
    assert serialized["extract_data"]["token"] == "$.data.access_token"
    assert serialized["assertion"]["status_code"] == 200


def test_report_diagnosis_keeps_pre_hook_for_apply_stage():
    """模型返回的独立登录 hook 必须穿过诊断规整层，交给应用服务。"""
    hook = [{
        "type": "http_request",
        "config": {
            "method": "POST",
            "path": "/api/auth/login",
            "params": {"username": "${my_account}", "password": "${my_password}"},
            "extract_data": {"token": "$.data.token"},
        },
    }]
    case = ORMTestCase(id=10, module_id=3, name="读取资料", case_type="api")

    normalized = _normalize_report_diagnosis_item(
        {
            "case_id": 10,
            "name": "读取资料",
            "classification": "用例问题",
            "findings": ["共享 token 已失效"],
            "fix": {"pre_hook": hook},
        },
        {10: case},
    )

    assert normalized["fix"]["pre_hook"] == hook


def test_preflight_rejects_ai_generated_hidden_pre_hook():
    """AI 修复不得再自动写入前置步骤或依赖其虚构变量。"""
    case = ORMTestCase(id=10, module_id=3, name="读取资料", case_type="api", sort_order=1)
    step = ORMTestStep(
        id=20,
        case_id=10,
        step_order=1,
        step_name="读取资料",
        step_type="http_request",
        config={"method": "GET", "path": "/api/profile", "headers": {}, "params": {}},
        extract=[],
        assertion=[],
    )
    case.steps = [step]
    hook = [{
        "type": "http_request",
        "config": {
            "method": "POST",
            "path": "/api/auth/login",
            "params": {"username": "${my_account}", "password": "${my_password}"},
            "extract_data": {"token": "$.data.token"},
        },
    }]
    row = SimpleNamespace(
        status="failed",
        step_type="http_request",
        output_data='{"detail":"unauthorized"}',
        status_code=401,
    )

    checked = _preflight_one(
        {
            "case_id": 10,
            "name": "读取资料",
            "classification": "用例问题",
            "fix": {
                "headers": {"Authorization": "Bearer ${token}"},
                "pre_hook": hook,
            },
        },
        {10: case},
        {10: [row]},
        {},
        {10: 0},
    )

    assert checked["eligible"] is False
    assert checked["fix"]["headers"] == {}
    assert checked["fix"]["pre_hook"] == []
    assert any(
        dropped["part"] == "pre_hook" and "禁止 AI 自动写入" in dropped["reason"]
        for dropped in checked["dropped"]
    )


def test_lifecycle_prefers_latest_token_for_same_account():
    """同账号连续登录时，早期 token 引用应指向最后成功产出的 token。"""
    items = [
        {
            "case_id": 1,
            "name": "A 连续登录",
            "def": {"steps": [
                _login_step(11, "token1"),
                _login_step(12, "token2"),
                _login_step(13, "token3"),
            ]},
            "result": [
                _step_result(11, {"token1": "value-1"}),
                _step_result(12, {"token2": "value-2"}),
                _step_result(13, {"token3": "value-3"}),
            ],
        },
        {
            "case_id": 2,
            "name": "B 查询当前用户",
            "def": {"steps": [{
                "step_id": 21,
                "step_name": "查询当前用户",
                "method": "GET",
                "path": "/api/me",
                "headers": {"Authorization": "Bearer ${token1}"},
                "params": {},
                "extract": {},
                "assertion": {"status_code": 200},
            }]},
            "result": [{"step_id": 21, "status_code": 401, "response": {}, "extract_values": {}}],
        },
    ]

    context, _producers, hints = _build_report_dependency_context(items, {})

    assert "${token1}" in context
    assert "${token3}" in context
    assert any("应优先改用 ${token3}" in hint for hint in hints[2])


def test_lifecycle_marks_token_invalid_after_successful_logout():
    """成功登出后，普通下游用例继续引用旧 token 必须得到确定性线索。"""
    items = [
        {
            "case_id": 1,
            "name": "登录",
            "def": {"steps": [_login_step(11, "token")]},
            "result": [_step_result(11, {"token": "value"})],
        },
        {
            "case_id": 2,
            "name": "登出",
            "def": {"steps": [{
                "step_id": 21,
                "step_name": "登出当前会话",
                "method": "POST",
                "path": "/api/auth/logout",
                "headers": {"Authorization": "Bearer ${token}"},
                "params": {},
                "extract": {},
                "assertion": {"status_code": 200},
            }]},
            "result": [{"step_id": 21, "status_code": 200, "response": {"status": "success"}, "extract_values": {}}],
        },
        {
            "case_id": 3,
            "name": "读取资料",
            "def": {"steps": [{
                "step_id": 31,
                "step_name": "读取资料",
                "method": "GET",
                "path": "/api/profile",
                "headers": {"Authorization": "Bearer ${token}"},
                "params": {},
                "extract": {},
                "assertion": {"status_code": 200},
            }]},
            "result": [{"step_id": 31, "status_code": 401, "response": {}, "extract_values": {}}],
        },
        {
            "case_id": 4,
            "name": "【鉴权】登出后 token 失效",
            "def": {"steps": [{
                "step_id": 41,
                "step_name": "验证旧 token 被拒绝",
                "method": "GET",
                "path": "/api/profile",
                "headers": {"Authorization": "Bearer ${token}"},
                "params": {},
                "extract": {},
                "assertion": {"status_code": 401},
            }]},
            "result": [{"step_id": 41, "status_code": 401, "response": {}, "extract_values": {}}],
        },
    ]

    context, _producers, hints = _build_report_dependency_context(items, {})

    assert "作废 ${token}" in context
    assert any("已被" in hint and "可见的登录前置步骤" in hint for hint in hints[3])
    assert 4 not in hints


def _login_step(step_id: int, token_name: str) -> dict:
    return {
        "step_id": step_id,
        "step_name": f"登录并提取{token_name}",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": {},
        "params": {"username": "${my_account}", "password": "${my_password}"},
        "extract": {token_name: "$.data.token"},
        "assertion": {"status_code": 200},
    }


def _step_result(step_id: int, extracted: dict) -> dict:
    return {
        "step_id": step_id,
        "status_code": 200,
        "response": {"status": "success"},
        "extract_values": extracted,
    }
