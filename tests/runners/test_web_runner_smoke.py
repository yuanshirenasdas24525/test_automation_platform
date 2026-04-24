"""端到端冒烟：v2 CaseExecutor + WebStepRunner 家族（Playwright / Selenium 双引擎）。

不连真实浏览器：用 FakeAdapter 把 `WebSession.adapter` 整个替掉，只验证
"engine 选择 → WebSession 绑定 → 派发 → 执行 → adapter 调用 → 资源关闭"这条链路。

覆盖：
  1. web_goto                       → adapter.goto + 回填 title/url
  2. web_click                      → adapter.click + 变量替换
  3. web_input                      → adapter.input + clear_first + ${var}
  4. web_select                     → adapter.select_option（value / label / index 三分支）
  5. web_wait                       → 有 locator 时走 adapter.wait_for；没 locator 时纯 sleep
  6. web_screenshot                 → adapter.screenshot + attachments
  7. web_assert_text                → equals / contains / regex 三分支 + 失败分支 FAILED
  8. web_evaluate                   → adapter.evaluate + save_as 写回 ctx
  9. CaseExecutor 自动拉起 WebSession（命中 step_type=web_*）+ 结束时 close()
 10. api-only case 不触发 WebSession 工厂
 11. WebSession.require(ctx) 未绑定时抛 RuntimeError
 12. acquire_session_for_case：从 env.browser_config / variables("browser.xxx") 收集配置

跑法：

    pytest tests/runners/test_web_runner_smoke.py -v
"""
from __future__ import annotations

from typing import Any

import pytest

from core.context.execution_context import ExecutionContext
from runners.case_executor import CaseExecutor
from runners.dispatcher import StepDispatcher
from runners.protocol import StepStatus
from runners.steps.generic import AssertStepRunner, SleepStepRunner
from runners.steps.web_actions import build_web_runners
from runners.web.adapters import WebDriverAdapter
from runners.web.session import WebSession, acquire_session_for_case


# =============================================================================
# FakeAdapter：实现 WebDriverAdapter 全部方法，记下每次调用的入参
# =============================================================================
class FakeAdapter(WebDriverAdapter):
    engine = "fake"

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.calls: list[tuple[str, tuple, dict]] = []
        self.closed = False
        # 存一份"页面状态"供 get_url / get_title / get_text 返回
        self.fake_url = "about:blank"
        self.fake_title = "Fake"
        self.fake_text_by_locator: dict[str, str] = {}
        self.fake_eval_return: Any = "evaluated"

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))

    def goto(self, url, timeout=30):
        self._record("goto", (url,), {"timeout": timeout})
        self.fake_url = url
        self.fake_title = f"title of {url}"

    def get_url(self):
        return self.fake_url

    def get_title(self):
        return self.fake_title

    def click(self, by, locator, timeout=10):
        self._record("click", (by, locator), {"timeout": timeout})

    def input(self, by, locator, text, clear_first=True, timeout=10):
        self._record("input", (by, locator, text), {"clear_first": clear_first, "timeout": timeout})

    def select_option(self, by, locator, value=None, label=None, index=None, timeout=10):
        self._record("select_option", (by, locator), {
            "value": value, "label": label, "index": index, "timeout": timeout,
        })

    def wait_for(self, by, locator, state="visible", timeout=10):
        self._record("wait_for", (by, locator), {"state": state, "timeout": timeout})

    def get_text(self, by, locator, timeout=10):
        self._record("get_text", (by, locator), {"timeout": timeout})
        return self.fake_text_by_locator.get(locator, "")

    def get_attribute(self, by, locator, name, timeout=10):
        self._record("get_attribute", (by, locator, name), {"timeout": timeout})
        return None

    def screenshot(self, path):
        self._record("screenshot", (path,), {})
        import os
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

    def evaluate(self, script, *args):
        self._record("evaluate", (script, *args), {})
        return self.fake_eval_return

    def close(self):
        self.closed = True


# =============================================================================
# 给 ctx 绑一个用 FakeAdapter 的 WebSession
# =============================================================================
def _bind_fake_session(ctx: ExecutionContext, **cfg) -> tuple[WebSession, FakeAdapter]:
    adapter = FakeAdapter(cfg)
    session = WebSession(engine="fake", config=cfg,
                         adapter_factory=lambda eng, c: adapter)
    # 触发 lazy 启动 → 绑定
    _ = session.adapter
    WebSession.bind(ctx, session)
    return session, adapter


def _dispatch_web_step(ctx, step):
    d = StepDispatcher()
    d.register_all(build_web_runners())
    return d.dispatch(step, ctx)


# =============================================================================
# 1. web_goto
# =============================================================================
def test_web_goto_calls_adapter_and_fills_output():
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    r = _dispatch_web_step(ctx, {
        "id": 1, "step_order": 0, "step_name": "open",
        "step_type": "web_goto",
        "config": {"url": "https://example.com/login", "timeout": 5},
    })
    assert r.status == StepStatus.PASSED, r.error_message
    assert adapter.calls[0][0] == "goto"
    assert adapter.calls[0][1] == ("https://example.com/login",)
    assert r.output_data["url"] == "https://example.com/login"
    assert "title" in r.output_data


# =============================================================================
# 2. web_click 变量替换
# =============================================================================
def test_web_click_substitutes_variables():
    ctx = ExecutionContext()
    ctx.set_var("sel", "#ok_btn")
    _, adapter = _bind_fake_session(ctx)
    r = _dispatch_web_step(ctx, {
        "id": 2, "step_order": 0, "step_name": "click",
        "step_type": "web_click",
        "config": {"by": "css", "locator": "${sel}", "timeout": 2},
    })
    assert r.status == StepStatus.PASSED, r.error_message
    name, args, kwargs = adapter.calls[0]
    assert name == "click"
    assert args == ("css", "#ok_btn")
    assert kwargs == {"timeout": 2}


# =============================================================================
# 3. web_input：${var} + clear_first
# =============================================================================
def test_web_input_forwards_value_and_clear_first():
    ctx = ExecutionContext()
    ctx.set_var("user", "alice")
    _, adapter = _bind_fake_session(ctx)
    r = _dispatch_web_step(ctx, {
        "id": 3, "step_order": 0, "step_name": "input",
        "step_type": "web_input",
        "config": {"by": "id", "locator": "username",
                   "value": "${user}", "clear_first": False, "timeout": 4},
    })
    assert r.status == StepStatus.PASSED, r.error_message
    name, args, kwargs = adapter.calls[0]
    assert name == "input"
    assert args == ("id", "username", "alice")
    assert kwargs == {"clear_first": False, "timeout": 4}


# =============================================================================
# 4. web_select：三种参数互斥分支
# =============================================================================
@pytest.mark.parametrize("payload,expected_kw", [
    ({"value": "BJ"}, {"value": "BJ", "label": None, "index": None}),
    ({"label": "Beijing"}, {"value": None, "label": "Beijing", "index": None}),
    ({"index": 2}, {"value": None, "label": None, "index": 2}),
])
def test_web_select_passes_right_option(payload, expected_kw):
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    step_config = {"by": "css", "locator": "select#city", "timeout": 3}
    step_config.update(payload)
    r = _dispatch_web_step(ctx, {
        "id": 4, "step_order": 0, "step_name": "select",
        "step_type": "web_select", "config": step_config,
    })
    assert r.status == StepStatus.PASSED, r.error_message
    name, args, kwargs = adapter.calls[0]
    assert name == "select_option"
    assert args == ("css", "select#city")
    for k, v in expected_kw.items():
        assert kwargs[k] == v


# =============================================================================
# 5a. web_wait：有 locator → adapter.wait_for
# =============================================================================
def test_web_wait_with_locator_calls_wait_for():
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    r = _dispatch_web_step(ctx, {
        "id": 5, "step_order": 0, "step_name": "wait visible",
        "step_type": "web_wait",
        "config": {"by": "css", "locator": ".loaded", "state": "visible", "timeout": 3},
    })
    assert r.status == StepStatus.PASSED, r.error_message
    assert adapter.calls[0][0] == "wait_for"
    assert adapter.calls[0][2]["state"] == "visible"


# =============================================================================
# 5b. web_wait：无 locator → 纯 sleep（拦截 time.sleep 不真的等）
# =============================================================================
def test_web_wait_without_locator_sleeps_only(monkeypatch):
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    slept = []
    monkeypatch.setattr("src.runners.steps.web_actions.time.sleep",
                        lambda s: slept.append(s))
    r = _dispatch_web_step(ctx, {
        "id": 6, "step_order": 0, "step_name": "sleep",
        "step_type": "web_wait", "config": {"seconds": 0.05},
    })
    assert r.status == StepStatus.PASSED, r.error_message
    # 没有访问 adapter
    assert adapter.calls == []
    assert slept and abs(slept[0] - 0.05) < 1e-6


# =============================================================================
# 6. web_screenshot：文件落盘 + attachments
# =============================================================================
def test_web_screenshot_saves_file_and_attaches(tmp_path):
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    path = tmp_path / "home.png"
    r = _dispatch_web_step(ctx, {
        "id": 7, "step_order": 0, "step_name": "shot",
        "step_type": "web_screenshot",
        "config": {"name": "home.png", "path": str(path)},
    })
    assert r.status == StepStatus.PASSED, r.error_message
    assert adapter.calls[0][0] == "screenshot"
    assert path.exists()
    assert r.attachments and r.attachments[0]["path"] == str(path)


# =============================================================================
# 7a. web_assert_text：equals 通过
# =============================================================================
def test_web_assert_text_equals_pass():
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    adapter.fake_text_by_locator["h1"] = "Welcome"
    r = _dispatch_web_step(ctx, {
        "id": 8, "step_order": 0, "step_name": "assert",
        "step_type": "web_assert_text",
        "config": {"by": "css", "locator": "h1", "equals": "Welcome"},
    })
    assert r.status == StepStatus.PASSED, r.error_message


# =============================================================================
# 7b. web_assert_text：contains 不匹配 → FAILED（业务断言失败，不是 ERROR）
# =============================================================================
def test_web_assert_text_contains_fail():
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    adapter.fake_text_by_locator["h1"] = "Hello"
    r = _dispatch_web_step(ctx, {
        "id": 9, "step_order": 0, "step_name": "assert",
        "step_type": "web_assert_text",
        "config": {"by": "css", "locator": "h1", "contains": "World"},
    })
    assert r.status == StepStatus.FAILED
    assert "World" in (r.error_message or "")


# =============================================================================
# 7c. web_assert_text：regex 通过
# =============================================================================
def test_web_assert_text_regex_pass():
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    adapter.fake_text_by_locator[".msg"] = "order#1234 created"
    r = _dispatch_web_step(ctx, {
        "id": 10, "step_order": 0, "step_name": "assert",
        "step_type": "web_assert_text",
        "config": {"by": "css", "locator": ".msg", "regex": r"order#\d+ created"},
    })
    assert r.status == StepStatus.PASSED, r.error_message


# =============================================================================
# 8. web_evaluate：save_as 把返回值塞回 ctx
# =============================================================================
def test_web_evaluate_saves_return_to_ctx():
    ctx = ExecutionContext()
    _, adapter = _bind_fake_session(ctx)
    adapter.fake_eval_return = 42
    r = _dispatch_web_step(ctx, {
        "id": 11, "step_order": 0, "step_name": "eval",
        "step_type": "web_evaluate",
        "config": {"script": "return 42;", "args": [], "save_as": "answer"},
    })
    assert r.status == StepStatus.PASSED, r.error_message
    assert ctx.get_var("answer") == 42
    assert r.extracted["answer"] == 42


# =============================================================================
# 9. CaseExecutor：识别 web_* step → 用工厂拿 WebSession → 结束 close()
# =============================================================================
def test_case_executor_manages_web_session_lifecycle():
    adapter = FakeAdapter()
    built_sessions: list[WebSession] = []

    def factory(case_dict):
        s = WebSession(
            engine="fake", config={},
            adapter_factory=lambda eng, c: adapter,
        )
        built_sessions.append(s)
        return s

    executor = CaseExecutor(web_session_factory=factory)
    case = {
        "id": 50, "name": "web lifecycle", "case_type": "web",
        "steps": [
            {"id": 501, "step_order": 0, "step_name": "open",
             "step_type": "web_goto", "config": {"url": "https://example.com"}},
            {"id": 502, "step_order": 1, "step_name": "click",
             "step_type": "web_click", "config": {"by": "css", "locator": "button"}},
        ],
    }
    r = executor.run(case)

    assert r.status == StepStatus.PASSED, r.error_message
    assert len(built_sessions) == 1
    assert adapter.closed is True
    # 两条 step 都打到了 adapter 上
    ops = [c[0] for c in adapter.calls]
    assert ops == ["goto", "click"]


# =============================================================================
# 10. api-only case 不触发 WebSession 工厂
# =============================================================================
def test_case_executor_does_not_acquire_web_session_for_api_only():
    called = {"factory": 0}

    def factory(_):
        called["factory"] += 1
        raise AssertionError("web session factory should NOT be invoked for api-only case")

    d = StepDispatcher()
    d.register(SleepStepRunner())
    d.register(AssertStepRunner())
    d.register_all(build_web_runners())

    executor = CaseExecutor(dispatcher=d, web_session_factory=factory)
    case = {
        "id": 60, "name": "api only", "case_type": "api",
        "steps": [{
            "id": 601, "step_order": 0, "step_name": "sleep",
            "step_type": "sleep", "config": {"seconds": 0},
        }],
    }
    r = executor.run(case)
    assert r.status == StepStatus.PASSED, r.error_message
    assert called["factory"] == 0


# =============================================================================
# 11. WebSession.require(ctx) —— 未绑定时抛
# =============================================================================
def test_web_session_require_raises_when_missing():
    ctx = ExecutionContext()
    with pytest.raises(RuntimeError):
        WebSession.require(ctx)


# =============================================================================
# 12. acquire_session_for_case：engine + 前缀变量合并
# =============================================================================
def test_acquire_session_for_case_merges_config():
    case_dict = {
        "environment": {
            "browser_config": {
                "engine": "selenium",
                "browser": "chrome",
                "headless": True,
            },
        },
        "variables": {
            "browser.headless": False,          # case 级 override
            "web.window_size": "800,600",       # 兼容 "web." 前缀
            "unrelated": "ignored",
        },
    }
    # 注入 FakeAdapter 工厂，避免真的去启动 selenium
    captured: dict = {}

    def fake_factory(engine, config):
        captured["engine"] = engine
        captured["config"] = config
        return FakeAdapter(config)

    session = acquire_session_for_case(case_dict, adapter_factory=fake_factory)
    assert session.engine == "selenium"
    _ = session.adapter  # 触发 factory
    assert captured["engine"] == "selenium"
    cfg = captured["config"]
    assert cfg["browser"] == "chrome"
    assert cfg["headless"] is False             # case-level override
    assert cfg["window_size"] == "800,600"      # web. 前缀被剥掉
    assert "engine" not in cfg                  # engine 不应该作为 adapter 参数
    assert "unrelated" not in cfg               # 没 browser./web. 前缀的不进来

    session.close()
