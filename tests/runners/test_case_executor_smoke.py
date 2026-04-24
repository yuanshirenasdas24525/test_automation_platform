"""端到端冒烟：v2 CaseExecutor + HttpRequestStepRunner + AssertStepRunner

不依赖真实服务端，通过 monkeypatch requests.Session.request 模拟 HTTP 响应。
覆盖：
  1. 一条 http_request step：status_code + jsonpath 断言、extract 结果
  2. 多条 step：上一步 extract 的变量在下一步可通过 ${var} 引用
  3. step 失败 + on_failure=stop：后续 step 不执行
  4. step 失败 + on_failure=continue：后续 step 继续执行
  5. 未注册的 step_type：返回 ERROR 但不炸
  6. v1 兼容：case 没有 steps，靠 method/path 合成一条

跑法（需要在项目根目录）：

    pytest tests/runners/test_case_executor_smoke.py -v
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from core.api.request_data_processor import RequestDataProcessor
from runners.case_executor import CaseExecutor
from runners.dispatcher import StepDispatcher
from runners.protocol import StepStatus
from runners.steps.http_request import HttpRequestStepRunner


# ===================================================================
# 工具：一个假的 RequestDataProcessor，避开真实 DB / config 初始化
# ===================================================================
class _FakeProcessor:
    def __init__(self, base_url="http://example.com", extra_pool=None):
        self.base_header = {}
        self.base_url = {"url": base_url}
        self.extra_pool = extra_pool or {}

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


# ===================================================================
# 3. 失败 + on_failure=stop：后续 step 不执行
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


# ===================================================================
# 5. 未注册的 step_type：返回 ERROR，不抛异常
# ===================================================================
def test_unknown_step_type_returns_error(monkeypatch):
    # 不需要 HTTP mock，永远不会走到
    http_runner = HttpRequestStepRunner(processor=_FakeProcessor())
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 5, "name": "unknown-type", "case_type": "mixed",
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
# 6. v1 兼容：case 没 steps，靠 method/path 合成一条 http_request
# ===================================================================
def test_v1_compat_synthesizes_step(monkeypatch):
    resp = _FakeResponse(200, {"v1": True})
    http_runner, _ = _make_runner_with_mocked_http(monkeypatch, [resp])
    dispatcher = _build_dispatcher(http_runner)
    executor = CaseExecutor(dispatcher)

    case = {
        "id": 6, "name": "legacy", "case_type": None,
        "method": "GET", "path": "/legacy",
        "headers": "{}", "data_type": "application/json",
        "params": "{}", "assertion": None,
        # 注意：没有 "steps" 键
    }
    result = executor.run(case)
    # 虽然 case.assertion 为 None，http_request 拿不到任何断言也是过
    assert result.status == StepStatus.PASSED, result.error_message
    assert len(result.steps) == 1
    assert result.steps[0].step_type == "http_request"
