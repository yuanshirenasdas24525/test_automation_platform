"""自动鉴权补齐（auth_provider）单测。

场景来自真实缺陷：AI 生成的用例引用 ${admin_token} 却没有任何步骤产出它
（前置链自己就要 admin 权限建号 → 鸡生蛋），导致整批用例 401。
配置中心配一次 auth_provider 后，执行器应自动登录补齐该变量。

覆盖：
  1. 悬空鉴权变量 → 自动登录补齐，主步骤拿到真实 token
  2. 变量池里已有该变量 → 不触发登录（不浪费请求、不刷限流）
  3. 用例没引用可补齐的变量 → 不触发登录
  4. 没配 auth_provider → 功能整体不生效（零侵入）
  5. enabled=false → 显式关闭
  6. 登录失败 → 不中断用例，让用例带自己的真实错误失败

跑法：pytest tests/runners/test_auto_auth.py -v
"""
from __future__ import annotations

import json

import pytest
import requests

from runners.case_executor import CaseExecutor
from runners.context.execution_context import ExecutionContext
from runners.dispatcher import StepDispatcher
from runners.protocol import StepStatus
from runners.steps.http_request import HttpRequestStepRunner


class _FakeProcessor:
    def __init__(self, base_url="http://example.com"):
        self.base_header = {}
        self.base_url = {"url": base_url}
        self.extra_pool = {}
        self.encryption_decryption = {}

    def handler_files(self, file_path):  # pragma: no cover
        return None


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise requests.exceptions.JSONDecodeError("no json", "", 0)
        return self._payload


def _build(monkeypatch, responses):
    """按顺序返回 responses 的 dispatcher + 请求记录。"""
    calls = {"i": 0, "recorded": []}

    def fake_request(self, *args, **kwargs):
        calls["recorded"].append(kwargs)
        resp = responses[min(calls["i"], len(responses) - 1)]
        calls["i"] += 1
        return resp

    monkeypatch.setattr(requests.Session, "request", fake_request)
    d = StepDispatcher()
    d.register(HttpRequestStepRunner(processor=_FakeProcessor()))
    return d, calls


def _patch_config(monkeypatch, config: dict | None):
    """替换 config_center.get，只对 auth_provider 组返回给定配置。"""
    from utils import reload_config

    def fake_get(group, key=None, default=None, project_id=None):
        if group == "auth_provider":
            return config or {}
        return {}

    monkeypatch.setattr(reload_config.config_center, "get", fake_get)


AUTH_PROVIDER = {
    "path": "/api/auth/login",
    "params": json.dumps({"username": "${user_admin}", "password": "${password_admin}"}),
    "extract": json.dumps({"admin_token": "$.data.access_token"}),
}

# 主步骤引用 ${admin_token}，但用例内没有任何步骤产出它
CASE_NEEDING_TOKEN = {
    "id": 1,
    "name": "创建测试用户",
    "case_type": "api",
    "project_id": 7,
    "steps": [{
        "id": 11,
        "step_order": 0,
        "step_name": "创建测试用户",
        "step_type": "http_request",
        "config": {
            "method": "POST",
            "path": "/api/users",
            "headers": {"Authorization": "Bearer ${admin_token}"},
            "params": {"username": "u1"},
            "data_type": "json",
        },
        "assertion": [{"type": "equal", "target": "status_code", "expected": 201}],
    }],
}


def _ctx(project_id=7):
    ctx = ExecutionContext()
    ctx.set_var("_project_id", project_id)
    return ctx


def test_dangling_auth_var_is_filled_by_auth_provider(monkeypatch):
    """悬空 ${admin_token} → 自动登录补齐，主步骤带上真实 token。"""
    login_resp = _FakeResponse(200, {"data": {"access_token": "REAL-TOKEN"}})
    created_resp = _FakeResponse(201, {"data": {"id": 9}})
    dispatcher, calls = _build(monkeypatch, [login_resp, created_resp])
    _patch_config(monkeypatch, AUTH_PROVIDER)

    ctx = _ctx()
    result = CaseExecutor(dispatcher).run(dict(CASE_NEEDING_TOKEN), ctx)

    assert result.status == StepStatus.PASSED, result.error_message
    assert ctx.get_var("admin_token") == "REAL-TOKEN"
    # 发了两次请求：先登录，再建用户
    assert calls["i"] == 2
    # 主步骤真的带上了解析后的 token，而不是字面量 ${admin_token}
    main_headers = calls["recorded"][1].get("headers") or {}
    assert main_headers.get("Authorization") == "Bearer REAL-TOKEN"


def test_existing_var_does_not_trigger_login(monkeypatch):
    """变量池里已有 admin_token（如前序用例产出）→ 不再登录，省请求防限流。"""
    created_resp = _FakeResponse(201, {"data": {"id": 9}})
    dispatcher, calls = _build(monkeypatch, [created_resp])
    _patch_config(monkeypatch, AUTH_PROVIDER)

    ctx = _ctx()
    ctx.set_var("admin_token", "EXISTING-TOKEN")
    result = CaseExecutor(dispatcher).run(dict(CASE_NEEDING_TOKEN), ctx)

    assert result.status == StepStatus.PASSED, result.error_message
    assert calls["i"] == 1                      # 只有主步骤，没有登录
    assert ctx.get_var("admin_token") == "EXISTING-TOKEN"


def test_case_without_auth_var_does_not_trigger_login(monkeypatch):
    """用例没引用可补齐的变量 → 不触发登录。"""
    dispatcher, calls = _build(monkeypatch, [_FakeResponse(200, {"ok": True})])
    _patch_config(monkeypatch, AUTH_PROVIDER)

    case = {
        "id": 2, "name": "公开接口", "case_type": "api", "project_id": 7,
        "steps": [{
            "id": 21, "step_order": 0, "step_name": "ping", "step_type": "http_request",
            "config": {"method": "GET", "path": "/api/ping", "headers": {}, "params": {},
                       "data_type": "json"},
            "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
        }],
    }
    result = CaseExecutor(dispatcher).run(case, _ctx())

    assert result.status == StepStatus.PASSED, result.error_message
    assert calls["i"] == 1


@pytest.mark.parametrize("config", [None, {}, {**AUTH_PROVIDER, "enabled": "false"}])
def test_disabled_or_missing_provider_is_noop(monkeypatch, config):
    """没配 / 配空 / enabled=false → 功能不生效，用例按原样跑（这里必然 401 失败）。"""
    dispatcher, calls = _build(monkeypatch, [_FakeResponse(401, {"detail": "token 无效"})])
    _patch_config(monkeypatch, config)

    result = CaseExecutor(dispatcher).run(dict(CASE_NEEDING_TOKEN), _ctx())

    assert result.status == StepStatus.FAILED       # 断言 201 != 401
    assert calls["i"] == 1                          # 没有额外的登录请求


def test_login_failure_does_not_abort_case(monkeypatch):
    """登录失败不中断用例：让用例带着自己的真实错误失败，不被"鉴权补齐失败"盖住。"""
    login_fail = _FakeResponse(500, {"detail": "login service down"})
    main_401 = _FakeResponse(401, {"detail": "token 无效或已过期"})
    dispatcher, calls = _build(monkeypatch, [login_fail, main_401])
    _patch_config(monkeypatch, AUTH_PROVIDER)

    ctx = _ctx()
    result = CaseExecutor(dispatcher).run(dict(CASE_NEEDING_TOKEN), ctx)

    assert result.status == StepStatus.FAILED
    assert ctx.get_var("admin_token") is None
    assert calls["i"] == 2                          # 登录尝试过，主步骤照常执行
    # 用例结论来自主步骤自己的断言失败，而不是鉴权补齐环节
    assert "201" in (result.error_message or "") or "401" in (result.error_message or "")
