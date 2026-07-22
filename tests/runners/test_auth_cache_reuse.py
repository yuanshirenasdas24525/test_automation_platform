"""登录响应复用开关的行为锁定。

背景（平台正确性缺陷）：pre_hook 原本会拿单轮缓存里的登录响应**顶替真实请求**——
同一账号在多条用例里登录，只有第一次真发，后面直接复用。后果：
  · 报告在撒谎：步骤显示"登录"、挂着一份响应，实际什么都没发出去，断言全是空的
  · 同账号重复登录拿到同一个 token，"多会话 / 多设备"语义在平台里直接失真

它当初是为了压住被测系统的登录限流，但那是用污染测试结果的方式解决对方的问题。
现在默认关闭，只保留 AUTH_RESPONSE_CACHE=1 作为逃生通道。

跑法：pytest tests/runners/test_auth_cache_reuse.py -v
"""
from __future__ import annotations

import json

import pytest
import requests

from runners.case_executor import CaseExecutor, _auth_cache_reuse_enabled
from runners.context.auth_cache import RunAuthCache
from runners.context.execution_context import ExecutionContext
from runners.dispatcher import StepDispatcher
from runners.protocol import StepStatus
from runners.steps.http_request import HttpRequestStepRunner


class _FakeProcessor:
    def __init__(self):
        self.base_header = {}
        self.base_url = {"url": "http://example.com"}
        self.extra_pool = {}
        self.encryption_decryption = {}

    def handler_files(self, file_path):  # pragma: no cover
        return None


class _Resp:
    def __init__(self, payload, code=200):
        self.status_code = code
        self._p = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


def _dispatcher(monkeypatch, tokens):
    """每次登录返回不同 token，用于观察是否真的发了请求。"""
    calls = {"n": 0}

    def fake_request(self, *a, **kw):
        i = calls["n"]
        calls["n"] += 1
        return _Resp({"data": {"access_token": tokens[min(i, len(tokens) - 1)]}})

    monkeypatch.setattr(requests.Session, "request", fake_request)
    d = StepDispatcher()
    d.register(HttpRequestStepRunner(processor=_FakeProcessor()))
    return d, calls


_HOOK = {
    "type": "http_request",
    "config": {
        "method": "POST", "path": "/api/auth/login",
        "params": {"username": "admin", "password": "x"},
        "extract_data": {"tok": "$.data.access_token"},
    },
}


def _case(i):
    return {
        "id": None, "name": f"dup{i}", "case_type": "api", "pre_hook": [_HOOK],
        "steps": [{
            "id": None, "step_order": 0, "step_name": "noop", "step_type": "http_request",
            "config": {"method": "GET", "path": "/ping", "params": {},
                       "data_type": "json", "headers": {}},
            "assertion": [{"type": "equal", "target": "status_code", "expected": 200}],
        }],
    }


def _run_three(monkeypatch):
    dispatcher, calls = _dispatcher(monkeypatch, ["T1", "T2", "T3", "T4", "T5", "T6"])
    cache = RunAuthCache()
    toks = []
    for i in range(3):
        ctx = ExecutionContext()
        ctx.set_var("_run_auth_cache", cache)
        r = CaseExecutor(dispatcher).run(_case(i), ctx)
        assert r.status == StepStatus.PASSED, r.error_message
        toks.append(ctx.get_var("tok"))
    return toks, calls


def test_default_每次登录都真实执行(monkeypatch):
    """默认（未设 AUTH_RESPONSE_CACHE）：三条用例各自真登录，拿到三个不同 token。"""
    monkeypatch.delenv("AUTH_RESPONSE_CACHE", raising=False)
    toks, _ = _run_three(monkeypatch)
    assert len(set(toks)) == 3, f"重复登录应产生不同 token，实际：{toks}"


def test_default_is_disabled(monkeypatch):
    """安全默认值：不复用。"""
    monkeypatch.delenv("AUTH_RESPONSE_CACHE", raising=False)
    assert _auth_cache_reuse_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
def test_escape_hatch_can_reenable(monkeypatch, val):
    """逃生通道：显式打开后恢复旧行为（后两条复用第一条的响应）。"""
    monkeypatch.setenv("AUTH_RESPONSE_CACHE", val)
    assert _auth_cache_reuse_enabled() is True
    toks, _ = _run_three(monkeypatch)
    assert len(set(toks)) == 1, f"开启复用后三条应拿到同一 token，实际：{toks}"
