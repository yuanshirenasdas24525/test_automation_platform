"""端到端冒烟：v2 CaseExecutor + HttpRequestStepRunner + AssertStepRunner

不依赖真实服务端，通过 monkeypatch requests.Session.request 模拟 HTTP 响应。
覆盖：
  1. 一条 http_request step：status_code + jsonpath 断言、extract 结果
  2. 多条 step：上一步 extract 的变量在下一步可通过 ${var} 引用
  3. 前序 case 共享变量在下一条 case 可通过 ${var} 引用
  4. HTTP 断言 expected 支持从变量池取值
  5. step 失败 + on_failure=stop：后续 step 不执行
  6. step 失败 + on_failure=continue：后续 step 继续执行
  7. 未注册的 step_type：返回 ERROR 但不炸
  8. v2 不变量：case 没有 steps 直接失败

跑法（需要在项目根目录）：

    pytest tests/runners/test_case_executor_smoke.py -v
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from runners.api.request_data_processor import RequestDataProcessor
from runners.case_executor import CaseExecutor
from runners.context.execution_context import ExecutionContext
from runners.dispatcher import StepDispatcher
from runners.protocol import StepResult, StepStatus
from runners.steps.http_request import HttpRequestStepRunner
from utils.encrypt import decrypt_text, encrypt_text


# ===================================================================
# 工具：一个假的 RequestDataProcessor，避开真实 DB / config 初始化
# ===================================================================
class _FakeProcessor:
    def __init__(self, base_url="http://example.com", extra_pool=None):
        self.base_header = {}
        self.base_url = {"url": base_url}
        self.extra_pool = extra_pool or {}
        self.encryption_decryption = {}

    def handler_files(self, file_path):  # pragma: no cover - 冒烟没用到
        return None


# ===================================================================
# 工具：mock 出 requests 响应
# ===================================================================
class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self._text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise requests.exceptions.JSONDecodeError("no json", "", 0)
        return self._payload

    @property
    def text(self):
        return self._text


def _make_runner_with_mocked_http(monkeypatch, responses):
    """返回一个 HttpRequestStepRunner，其底层 requests 按 responses 列表顺序返回。"""
    calls = {"i": 0, "recorded": []}

    def fake_request(self, *args, **kwargs):
        calls["recorded"].append(kwargs)
        resp = responses[calls["i"] % len(responses)]
        calls["i"] += 1
        return resp

    monkeypatch.setattr(requests.Session, "request", fake_request)

    return HttpRequestStepRunner(processor=_FakeProcessor()), calls


def _build_dispatcher(http_runner):
    d = StepDispatcher()
    d.register(http_runner)
    # 也注册 sleep/assert，方便跨 step 测试
    from runners.steps.generic import SleepStepRunner, AssertStepRunner
    d.register(SleepStepRunner())
    d.register(AssertStepRunner())
    return d


def test_dispatcher_marks_failed_result_in_allure_context(monkeypatch):
    """Runner 返回失败结果时，Dispatcher 必须让 Allure 上下文观察到异常。"""
    observed = []

    @contextmanager
    def fake_allure_step(name):
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            observed.append((name, type(exc).__name__, str(exc)))
            raise

    class _FailedRunner:
        step_types = ("fake_failed",)

        @staticmethod
        def execute(step, ctx):
            return StepResult(
                step_id=step.get("id"),
                step_order=0,
                step_name="提取 token",
                step_type="fake_failed",
                status=StepStatus.FAILED,
                error_message="参数提取失败：token ($.data.token)",
            )

    monkeypatch.setattr("runners.dispatcher.allure_step", fake_allure_step)
    dispatcher = StepDispatcher()
    dispatcher.register(_FailedRunner())

    result = dispatcher.dispatch({
        "id": 1,
        "step_order": 0,
        "step_name": "提取 token",
        "step_type": "fake_failed",
    }, ExecutionContext())

    assert result.status == StepStatus.FAILED
    assert observed == [(
        "[0] 提取 token (fake_failed)",
        "_AllureReportedFailure",
        "参数提取失败：token ($.data.token)",
    )]


# ===================================================================
# 1. 单 step：200 + jsonpath 断言通过 + extract 成功
# ===================================================================
def test_single_http_step_passes(monkeypatch):
    resp = _FakeResponse(200, {"code": 0, "data": {"token": "abc123"}})
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [resp])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 1,
        "name": "login ok",
        "case_type": "api",
        "steps": [{
            "id": 101,
            "step_order": 0,
            "step_name": "POST /login",
            "step_type": "http_request",
            "config": {
                "method": "POST",
                "path": "/login",
                "headers": {"X-Trace": "t1"},
                "data_type": "json",
                "params": {"username": "u", "password": "p"},
            },
            "extract": [
                {"name": "token", "from": "response.body", "jsonpath": "$.data.token"},
            ],
            "assertion": [
                {"type": "equal", "target": "status_code", "expected": 200},
                {"type": "jsonpath", "target": "$.code", "expected": 0},
            ],
        }],
    }

    result = executor.run(case)
    assert result.status == StepStatus.PASSED, result.error_message
    assert len(result.steps) == 1
    assert result.steps[0].status == StepStatus.PASSED
    assert result.steps[0].extracted == {"token": "abc123"}


# ===================================================================
# 2. 跨 step 变量传递：step1 extract 的 token，step2 通过 ${token} 引用
# ===================================================================
def test_variable_propagation_across_steps(monkeypatch):
    resp1 = _FakeResponse(200, {"data": {"token": "xyz"}})
    resp2 = _FakeResponse(200, {"ok": True})
    http_runner, calls = _make_runner_with_mocked_http(monkeypatch, [resp1, resp2])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 2,
        "name": "chained",
        "case_type": "api",
        "steps": [
            {
                "id": 201, "step_order": 0, "step_name": "login",
                "step_type": "http_request",
                "config": {"method": "POST", "path": "/login",
                           "headers": {}, "data_type": "json", "params": {}},
                "extract": [{"name": "token", "from": "response.body",
                             "jsonpath": "$.data.token"}],
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
            },
            {
                "id": 202, "step_order": 1, "step_name": "whoami",
                "step_type": "http_request",
                "config": {
                    "method": "GET", "path": "/whoami",
                    "headers": {"Authorization": "Bearer ${token}"},
                    "data_type": "json", "params": {},
                },
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
            },
        ],
    }

    result = executor.run(case)
    assert result.status == StepStatus.PASSED, result.error_message
    # 第二次请求的 Authorization 里应当是替换后的值
    second_headers = calls["recorded"][1]["headers"]
    assert second_headers.get("Authorization") == "Bearer xyz"


def test_literal_extract_assignment_updates_parameter_pool_after_success(monkeypatch):
    """AI 修复写入的固定值提取只在成功后覆盖原变量。"""
    responses = [
        _FakeResponse(200, {"status": "success"}),
        _FakeResponse(200, {"status": "success"}),
    ]
    http_runner, calls = _make_runner_with_mocked_http(monkeypatch, responses)
    executor = CaseExecutor(_build_dispatcher(http_runner))
    ctx = ExecutionContext()
    ctx.set_var("password_admin", "OldTest@123")
    case = {
        "id": 22,
        "name": "修复密码后继续登录",
        "case_type": "api",
        "steps": [
            {
                "id": 2201,
                "step_order": 0,
                "step_name": "使用修复后的密码",
                "step_type": "http_request",
                "config": {
                    "method": "POST",
                    "path": "/api/auth/login",
                    "params": {"password": "NewTest@123"},
                    "assertion": {"status_code": 200},
                },
                "extract": [{
                    "name": "password_admin",
                    "from": "value",
                    "value": "NewTest@123",
                }],
            },
            {
                "id": 2202,
                "step_order": 1,
                "step_name": "后续请求复用原变量名",
                "step_type": "http_request",
                "config": {
                    "method": "POST",
                    "path": "/api/auth/login",
                    "params": {"password": "${password_admin}"},
                    "assertion": {"status_code": 200},
                },
            },
        ],
    }

    result = executor.run(case, ctx)

    assert result.status == StepStatus.PASSED, result.error_message
    assert ctx.get_var("password_admin") == "NewTest@123"
    assert calls["recorded"][1]["json"]["password"] == "NewTest@123"
    assert result.steps[0].extracted == {"password_admin": "NewTest@123"}


def test_literal_extract_assignment_is_skipped_for_expected_error(monkeypatch):
    """负向响应即使断言通过，也不能用修复值污染参数池。"""
    response = _FakeResponse(400, {"detail": "密码错误"})
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [response])
    executor = CaseExecutor(_build_dispatcher(http_runner))
    ctx = ExecutionContext()
    ctx.set_var("password_admin", "OldTest@123")
    case = {
        "id": 23,
        "name": "错误密码登录",
        "case_type": "api",
        "steps": [{
            "id": 2301,
            "step_order": 0,
            "step_name": "错误密码",
            "step_type": "http_request",
            "config": {
                "method": "POST",
                "path": "/api/auth/login",
                "params": {"password": "WrongTest@123"},
                "assertion": {"status_code": 400},
            },
            "extract": [{
                "name": "password_admin",
                "from": "value",
                "value": "WrongTest@123",
            }],
        }],
    }

    result = executor.run(case, ctx)

    assert result.status == StepStatus.PASSED, result.error_message
    assert ctx.get_var("password_admin") == "OldTest@123"
    assert result.steps[0].extracted == {}


def test_failed_extract_does_not_overwrite_existing_variable(monkeypatch):
    resp = _FakeResponse(401, {"detail": "用户名或密码错误"})
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [resp])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)
    ctx = ExecutionContext()
    ctx.set_var("token", "old-token")

    case = {
        "id": 20,
        "name": "login failed should keep old token",
        "case_type": "api",
        "steps": [{
            "id": 2001,
            "step_order": 0,
            "step_name": "login failed",
            "step_type": "http_request",
            "config": {
                "method": "POST",
                "path": "/login",
                "headers": {},
                "data_type": "json",
                "params": {"username": "admin", "password": "bad"},
            },
            "extract": [
                {"name": "token", "from": "response.body", "jsonpath": "$.data.token"},
            ],
            "assertion": [
                {"type": "equal", "target": "status_code", "expected": 401},
            ],
        }],
    }

    result = executor.run(case, ctx)
    assert result.status == StepStatus.PASSED, result.error_message
    assert result.steps[0].extracted == {}
    assert ctx.get_var("token") == "old-token"
    assert http_runner.processor.extra_pool.get("token") is None
    assert ctx.records["extract_errors"] == [{
        "变量名": "token",
        "来源": "response.body",
        "表达式": "$.data.token",
        "必需": False,
        "原因": "未匹配到值（JSONPath 无效、响应结构变化或接口返回失败）",
    }]


def test_ai_heal_mode_stops_immediately_on_extract_failure(monkeypatch):
    """自愈模式把普通请求的提取异常提升为失败，避免污染后续变量链。"""
    resp = _FakeResponse(200, {"data": {"access_token": "abc"}})
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [resp])
    executor = CaseExecutor(_build_dispatcher(http_runner))
    ctx = ExecutionContext()
    ctx.set_var("_ai_heal_enabled", True)
    case = {
        "id": 201,
        "name": "登录成功并提取 token",
        "case_type": "api",
        "steps": [{
            "id": 2011,
            "step_order": 0,
            "step_name": "登录",
            "step_type": "http_request",
            "config": {
                "method": "POST",
                "path": "/login",
                "params": {},
                "extract_data": {"token": "$.data.token"},
                "assertion": {"status_code": 200},
            },
        }],
    }

    result = executor.run(case, ctx)

    assert result.status == StepStatus.FAILED
    assert "参数提取失败" in str(result.error_message)


def test_pre_hook_extract_failure_contains_actionable_error(monkeypatch):
    """前置参数提取失败必须带变量、路径、状态码和接口错误。"""
    resp = _FakeResponse(401, {"detail": "会话已失效"})
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [resp])
    ctx = ExecutionContext()
    step = {
        "id": None,
        "step_order": -1,
        "step_name": "pre_hook#1",
        "step_type": "http_request",
        "_is_hook": True,
        "config": {
            "method": "POST",
            "path": "/api/users",
            "extract_data": {"test_username": "$.username"},
        },
    }

    result = _build_dispatcher(http_runner).dispatch(step, ctx)

    assert result.status == StepStatus.FAILED
    assert "test_username ($.username)" in str(result.error_message)
    assert "HTTP 401" in str(result.error_message)
    assert "接口返回：会话已失效" in str(result.error_message)
    assert ctx.records["extract_errors"][0]["变量名"] == "test_username"


def test_http_step_signs_headers_by_configured_order(monkeypatch):
    resp = _FakeResponse(200, {"ok": True})
    http_runner, calls = _make_runner_with_mocked_http(monkeypatch, [resp])
    http_runner.processor.encryption_decryption = {
        "on_off": "true",
        "key": "secret-key",
        "request_header_order_encrypt": '["B", "A"]',
    }
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 21,
        "name": "header order sign",
        "case_type": "api",
        "steps": [{
            "id": 2101,
            "step_order": 0,
            "step_name": "signed headers",
            "step_type": "http_request",
            "config": {
                "method": "POST",
                "path": "/signed",
                "headers": {"A": "1", "B": "2"},
                "data_type": "json",
                "params": {},
            },
            "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
        }],
    }

    result = executor.run(case)
    assert result.status == StepStatus.PASSED, result.error_message
    headers = calls["recorded"][0]["headers"]
    raw = f"B=2&A=1&{headers['power-timestamp']}{headers['power-nonce']}secret-key"
    import hashlib
    assert headers["power-sign"] == hashlib.md5(raw.encode("utf-8")).hexdigest()


def test_http_step_encrypts_whole_request_and_decrypts_response_field(monkeypatch):
    calls = {"recorded": []}

    def fake_request(self, *args, **kwargs):
        calls["recorded"].append(kwargs)
        encrypted_request = kwargs["json"]["payload"]
        assert json.loads(decrypt_text(encrypted_request, "secret-key")) == {
            "username": "u",
            "password": "p",
        }
        return _FakeResponse(200, {"payload": encrypt_text({"ok": True}, "secret-key")})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    http_runner = HttpRequestStepRunner(processor=_FakeProcessor())
    http_runner.processor.encryption_decryption = {
        "on_off": "true",
        "key": "secret-key",
        "request_body_whole_encrypt": "true",
        "request_body_whole_encrypt_field": "payload",
        "response_body_whole_decrypt": "true",
        "response_body_whole_decrypt_field": "payload",
    }
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 22,
        "name": "whole body crypto",
        "case_type": "api",
        "steps": [{
            "id": 2201,
            "step_order": 0,
            "step_name": "encrypted payload",
            "step_type": "http_request",
            "config": {
                "method": "POST",
                "path": "/encrypted",
                "headers": {},
                "data_type": "json",
                "params": {"username": "u", "password": "p"},
            },
            "assertion": [
                {"type": "equal", "target": "status_code", "expected": 200},
                {"type": "jsonpath", "target": "$.payload.ok", "expected": True},
            ],
        }],
    }

    result = executor.run(case)
    assert result.status == StepStatus.PASSED, result.error_message
    assert isinstance(calls["recorded"][0]["json"]["payload"], str)


def test_http_step_uses_custom_crypto_handlers(monkeypatch):
    calls = {"recorded": []}

    def fake_request(self, *args, **kwargs):
        calls["recorded"].append(kwargs)
        assert kwargs["headers"]["X-Custom-Crypto"] == "demo"
        assert kwargs["json"] == {"wrapped": {"name": "alice"}}
        return _FakeResponse(200, {"wrapped_response": {"ok": True}})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    http_runner = HttpRequestStepRunner(processor=_FakeProcessor())
    http_runner.processor.encryption_decryption = {
        "on_off": "true",
        "custom_request_handler": "demo_request_crypto",
        "custom_response_handler": "demo_response_crypto",
        "custom_crypto_only": "true",
        "custom_demo_header": "demo",
    }
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 23,
        "name": "custom crypto",
        "case_type": "api",
        "steps": [{
            "id": 2301,
            "step_order": 0,
            "step_name": "custom handlers",
            "step_type": "http_request",
            "config": {
                "method": "POST",
                "path": "/custom",
                "headers": {},
                "data_type": "json",
                "params": {"name": "alice"},
            },
            "assertion": [
                {"type": "equal", "target": "status_code", "expected": 200},
                {"type": "jsonpath", "target": "$.ok", "expected": True},
            ],
        }],
    }

    result = executor.run(case)
    assert result.status == StepStatus.PASSED, result.error_message


# ===================================================================
# 3. 跨 case 变量传递：上一条 case extract 的 token 可供下一条 case 使用
# ===================================================================
def test_run_shared_variables_propagate_to_next_case(monkeypatch):
    resp = _FakeResponse(200, {"ok": True})
    http_runner, calls = _make_runner_with_mocked_http(monkeypatch, [resp])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)
    ctx = ExecutionContext()
    ctx.set_var("_run_shared_vars", {"token": "from-login-case"})

    case = {
        "id": 3,
        "name": "use shared token",
        "case_type": "api",
        "steps": [{
            "id": 301,
            "step_order": 0,
            "step_name": "GET /profile",
            "step_type": "http_request",
            "config": {
                "method": "GET",
                "path": "/profile",
                "headers": {"Authorization": "Bearer ${token}"},
                "data_type": "json",
                "params": {},
            },
            "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
        }],
    }

    result = executor.run(case, ctx)
    assert result.status == StepStatus.PASSED, result.error_message
    assert calls["recorded"][0]["headers"].get("Authorization") == "Bearer from-login-case"


# ===================================================================
# 4. HTTP 断言 expected 支持从变量池取值
# ===================================================================
def test_http_assertion_expected_resolves_variables(monkeypatch):
    resp = _FakeResponse(200, {"username": "admin"})
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [resp])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 4,
        "name": "assert expected from vars",
        "case_type": "api",
        "variables": {"my_account": "admin"},
        "steps": [{
            "id": 401,
            "step_order": 0,
            "step_name": "GET /me",
            "step_type": "http_request",
            "config": {
                "method": "GET",
                "path": "/me",
                "headers": {},
                "data_type": "json",
                "params": {},
            },
            "assertion": [
                {"type": "equal", "target": "status_code", "expected": 200},
                {"type": "jsonpath", "target": "$.username", "expected": "${my_account}"},
            ],
        }],
    }

    result = executor.run(case)
    assert result.status == StepStatus.PASSED, result.error_message


# ===================================================================
# 5. 失败 + on_failure=stop：后续 step 不执行
# ===================================================================
def test_step_failure_stops_subsequent(monkeypatch):
    resp1 = _FakeResponse(500, {"err": "boom"})
    resp2 = _FakeResponse(200, {"ok": True})
    http_runner, calls = _make_runner_with_mocked_http(monkeypatch, [resp1, resp2])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 3, "name": "fail-stop", "case_type": "api",
        "steps": [
            {
                "id": 301, "step_order": 0, "step_type": "http_request",
                "step_name": "s1",
                "config": {"method": "GET", "path": "/a"},
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
                "on_failure": "stop",
            },
            {
                "id": 302, "step_order": 1, "step_type": "http_request",
                "step_name": "s2",
                "config": {"method": "GET", "path": "/b"},
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
            },
        ],
    }

    result = executor.run(case)
    assert result.status == StepStatus.FAILED
    assert len(result.steps) == 1           # 只跑了 s1
    assert calls["i"] == 1                  # 底层请求也只发了 1 次


# ===================================================================
# 4. 失败 + on_failure=continue：后续 step 继续执行
# ===================================================================
def test_step_failure_continues_when_allowed(monkeypatch):
    resp1 = _FakeResponse(500, {"err": "boom"})
    resp2 = _FakeResponse(200, {"ok": True})
    http_runner, calls = _make_runner_with_mocked_http(monkeypatch, [resp1, resp2])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 4, "name": "fail-continue", "case_type": "api",
        "steps": [
            {
                "id": 401, "step_order": 0, "step_type": "http_request",
                "step_name": "s1",
                "config": {"method": "GET", "path": "/a"},
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
                "on_failure": "continue",
            },
            {
                "id": 402, "step_order": 1, "step_type": "http_request",
                "step_name": "s2",
                "config": {"method": "GET", "path": "/b"},
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
            },
        ],
    }

    result = executor.run(case)
    # case 状态仍然是 FAILED（任一 step 失败就 FAIL）
    assert result.status == StepStatus.FAILED
    assert len(result.steps) == 2
    assert result.steps[0].status == StepStatus.FAILED
    assert result.steps[1].status == StepStatus.PASSED
    assert calls["i"] == 2


def test_ai_heal_mode_interrupts_continue_before_next_request(monkeypatch):
    """AI 自愈模式必须先处理当前失败，不能让 continue 把错误带到下一请求。"""
    resp1 = _FakeResponse(500, {"err": "boom"})
    resp2 = _FakeResponse(200, {"ok": True})
    http_runner, calls = _make_runner_with_mocked_http(monkeypatch, [resp1, resp2])
    executor = CaseExecutor(_build_dispatcher(http_runner))
    ctx = ExecutionContext()
    ctx.set_var("_ai_heal_enabled", True)
    case = {
        "id": 41,
        "name": "失败后立即自愈",
        "case_type": "api",
        "steps": [
            {
                "id": 4101,
                "step_order": 0,
                "step_type": "http_request",
                "step_name": "失败请求",
                "config": {"method": "GET", "path": "/a"},
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
                "on_failure": "continue",
            },
            {
                "id": 4102,
                "step_order": 1,
                "step_type": "http_request",
                "step_name": "不应提前执行",
                "config": {"method": "GET", "path": "/b"},
                "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
            },
        ],
    }

    result = executor.run(case, ctx)

    assert result.status == StepStatus.FAILED
    assert len(result.steps) == 1
    assert calls["i"] == 1


# ===================================================================
# 5. 未注册的 step_type：返回 ERROR，不抛异常
# ===================================================================
def test_unknown_step_type_returns_error(monkeypatch):
    # 不需要 HTTP mock，永远不会走到
    http_runner = HttpRequestStepRunner(processor=_FakeProcessor())
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 5, "name": "unknown-type", "case_type": "api",
        "steps": [{
            "id": 501, "step_order": 0, "step_name": "x",
            "step_type": "totally_made_up",
            "config": {},
        }],
    }
    result = executor.run(case)
    assert result.status == StepStatus.ERROR
    assert "totally_made_up" in (result.error_message or "")


# ===================================================================
# 6. v2 不变量：case 没 steps 直接失败，不再从老字段合成
# ===================================================================
def test_missing_steps_returns_error(monkeypatch):
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 6,
        "name": "missing-steps",
        "case_type": "api",
    }
    result = executor.run(case)
    assert result.status == StepStatus.ERROR
    assert "没有 steps" in (result.error_message or "")
