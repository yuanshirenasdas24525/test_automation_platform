"""端到端冒烟：v2 CaseExecutor + AppStepRunner 家族。

不连真实 Appium：用 FakeDriver / FakeFinder 把 `AppSession.driver` 和
`AppSession.app_action.finder` 都替换掉，只验证"派发 → 执行 → 断言"链路是否连通。

覆盖：
  1. 单条 app_tap：找到元素 → click()
  2. app_input：clear() + send_keys(${var}) 变量替换
  3. app_swipe：direction=up + ratio=0.5，调用 driver.swipe()
  4. app_wait：纯 sleep（不依赖元素）
  5. app_screenshot：调用 driver.save_screenshot 并写入 attachments
  6. CaseExecutor 自动拉起 AppSession（命中 step_type=app_*）+ 结束时 close()

跑法：

    pytest tests/runners/test_app_runner_smoke.py -v
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from runners.context.execution_context import ExecutionContext
from runners.app.session import AppSession
from runners.case_executor import CaseExecutor
from runners.dispatcher import StepDispatcher
from runners.protocol import StepStatus
from runners.steps.app_actions import build_app_runners
from runners.steps.generic import AssertStepRunner, SleepStepRunner


# =============================================================================
# FakeDriver / FakeElement / FakeFinder：整套 mock 掉 Appium 调用
# =============================================================================
class FakeElement:
    def __init__(self, name: str = "el"):
        self.name = name
        self.clicks = 0
        self.clears = 0
        self.sent_keys: list[str] = []

    def click(self):
        self.clicks += 1

    def clear(self):
        self.clears += 1

    def send_keys(self, text):
        self.sent_keys.append(str(text))


class FakeFinder:
    def __init__(self):
        self.last_find_args: tuple | None = None
        self.find_count = 0
        self._element = FakeElement()

    def find(self, by, locator, timeout=10):
        self.last_find_args = (by, locator, timeout)
        self.find_count += 1
        return self._element

    def swipe_find(self, cfg):
        self.last_find_args = ("swipe_find", cfg.get("locator"), cfg)
        return self._element


class FakeAppAction:
    def __init__(self, driver):
        self.driver = driver
        self.finder = FakeFinder()


class FakeDriver:
    def __init__(self):
        self.quit_called = False
        self.swipes: list[tuple] = []
        self.screenshots: list[str] = []
        self.closed_app = False
        self.terminated_pkg = None
        self.back_called = 0
        self.keycodes: list[int] = []

    def quit(self):
        self.quit_called = True

    def get_window_size(self):
        return {"width": 400, "height": 800}

    def swipe(self, x1, y1, x2, y2, duration):
        self.swipes.append((x1, y1, x2, y2, duration))

    def save_screenshot(self, path):
        self.screenshots.append(path)
        # 模拟真正写一个空文件，避免调用方检查存在性时失败
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")  # 最小 PNG 头
        return True

    def close_app(self):
        self.closed_app = True

    def terminate_app(self, pkg):
        self.terminated_pkg = pkg
        return True

    def back(self):
        self.back_called += 1

    def press_keycode(self, code):
        self.keycodes.append(int(code))


# =============================================================================
# 构造一个"绑好 FakeSession"的 ctx，再用它来跑 step
# =============================================================================
def _make_ctx_with_fake_session():
    ctx = ExecutionContext()
    driver = FakeDriver()
    session = AppSession(
        device={"id": 1, "udid": "fake-udid", "agent_host": "localhost",
                "appium_port": 4723, "platform": "android"},
        caps={"platformName": "Android"},
        driver_factory=lambda dev, caps: driver,
    )
    # 旁路：直接把 driver / app_action 塞进去，跳过懒启动路径（也测得更直接）
    session._driver = driver
    session._app_action = FakeAppAction(driver)
    AppSession.bind(ctx, session)
    return ctx, session, driver


# =============================================================================
# 直接跑 runner 单条 step（不经过 CaseExecutor）
# =============================================================================
def _dispatch_app_step(ctx, step):
    d = StepDispatcher()
    d.register_all(build_app_runners())
    return d.dispatch(step, ctx)


# =============================================================================
# 1. app_tap：找到元素并 click
# =============================================================================
def test_app_tap_clicks_element():
    ctx, session, _driver = _make_ctx_with_fake_session()
    step = {
        "id": 11, "step_order": 0, "step_name": "tap login",
        "step_type": "app_tap",
        "config": {"by": "xpath", "locator": "//Button[@text='登录']", "timeout": 3},
    }
    r = _dispatch_app_step(ctx, step)

    assert r.status == StepStatus.PASSED, r.error_message
    element = session.app_action.finder._element
    assert element.clicks == 1
    assert session.app_action.finder.last_find_args == ("xpath", "//Button[@text='登录']", 3)


# =============================================================================
# 2. app_input：变量替换 + clear + send_keys
# =============================================================================
def test_app_input_sends_keys_with_var_substitution():
    ctx, session, _driver = _make_ctx_with_fake_session()
    ctx.set_var("phone", "13800000000")
    step = {
        "id": 12, "step_order": 0, "step_name": "input phone",
        "step_type": "app_input",
        "config": {"by": "id", "locator": "phone_input",
                   "value": "${phone}", "clear_first": True, "timeout": 5},
    }
    r = _dispatch_app_step(ctx, step)

    assert r.status == StepStatus.PASSED, r.error_message
    element = session.app_action.finder._element
    assert element.clears == 1
    assert element.sent_keys == ["13800000000"]


# =============================================================================
# 3. app_swipe：direction + ratio → 调用 driver.swipe()
# =============================================================================
def test_app_swipe_with_direction_ratio():
    ctx, _session, driver = _make_ctx_with_fake_session()
    step = {
        "id": 13, "step_order": 0, "step_name": "swipe up",
        "step_type": "app_swipe",
        "config": {"direction": "up", "ratio": 0.5, "duration": 400},
    }
    r = _dispatch_app_step(ctx, step)

    assert r.status == StepStatus.PASSED, r.error_message
    assert len(driver.swipes) == 1
    x1, y1, x2, y2, dur = driver.swipes[0]
    # center 200,400; offset = (400*0.5/2, 800*0.5/2) = (100, 200)
    assert (x1, y1, x2, y2, dur) == (200, 600, 200, 200, 400)


# =============================================================================
# 4. app_wait：无 locator 的纯 sleep（seconds 很小，以免拖慢测试）
# =============================================================================
def test_app_wait_sleep_only(monkeypatch):
    ctx, _session, _driver = _make_ctx_with_fake_session()
    slept = []
    monkeypatch.setattr("runners.steps.app_actions.time.sleep",
                        lambda s: slept.append(s))
    step = {
        "id": 14, "step_order": 0, "step_name": "wait",
        "step_type": "app_wait",
        "config": {"seconds": 0.05},
    }
    r = _dispatch_app_step(ctx, step)
    assert r.status == StepStatus.PASSED, r.error_message
    assert slept and abs(slept[0] - 0.05) < 1e-6


# =============================================================================
# 5. app_screenshot：driver.save_screenshot 被调 + attachments 有一条
# =============================================================================
def test_app_screenshot_saves_and_attaches(tmp_path):
    ctx, _session, driver = _make_ctx_with_fake_session()
    target = tmp_path / "shot.png"
    step = {
        "id": 15, "step_order": 0, "step_name": "shot",
        "step_type": "app_screenshot",
        "config": {"name": "login.png", "path": str(target)},
    }
    r = _dispatch_app_step(ctx, step)
    assert r.status == StepStatus.PASSED, r.error_message
    assert driver.screenshots == [str(target)]
    assert target.exists()
    assert r.attachments and r.attachments[0]["path"] == str(target)


# =============================================================================
# 6. CaseExecutor：识别 app_* step → 自动通过工厂拿 AppSession → 结束关闭
# =============================================================================
def test_case_executor_manages_app_session_lifecycle():
    driver = FakeDriver()
    closed_flag = {"called": False}

    def fake_session_factory(case_dict):
        s = AppSession(
            device={"id": 99, "udid": "factory-udid"},
            caps={},
            driver_factory=lambda d, c: driver,
            on_release=lambda dev: closed_flag.__setitem__("called", True),
        )
        # 旁路：直接填进去，避免真的调用 _driver_factory
        s._driver = driver
        s._app_action = FakeAppAction(driver)
        return s

    executor = CaseExecutor(app_session_factory=fake_session_factory)
    case = {
        "id": 20, "name": "app session mgmt", "case_type": "app",
        "steps": [
            {"id": 201, "step_order": 0, "step_name": "tap",
             "step_type": "app_tap",
             "config": {"by": "id", "locator": "ok_btn", "timeout": 1}},
            {"id": 202, "step_order": 1, "step_name": "back",
             "step_type": "app_back", "config": {}},
        ],
    }
    r = executor.run(case)

    assert r.status == StepStatus.PASSED, r.error_message
    assert driver.back_called == 1
    # close 流程：driver.quit + on_release 都被调
    assert driver.quit_called is True
    assert closed_flag["called"] is True


# =============================================================================
# 7. CaseExecutor：case 只有 http_request 时**不会**触发 AppSession 工厂
# =============================================================================
def test_case_executor_does_not_acquire_session_for_api_only():
    called = {"factory": 0}

    def factory(_):
        called["factory"] += 1
        raise AssertionError("app session factory should NOT be invoked for api-only case")

    # 需要一个 sleep step（不需要真 HTTP）—— 避开 http 的 processor 依赖
    d = StepDispatcher()
    d.register(SleepStepRunner())
    d.register(AssertStepRunner())
    d.register_all(build_app_runners())

    executor = CaseExecutor(dispatcher=d, app_session_factory=factory)
    case = {
        "id": 30, "name": "api only", "case_type": "api",
        "steps": [{
            "id": 301, "step_order": 0, "step_name": "sleep",
            "step_type": "sleep", "config": {"seconds": 0},
        }],
    }
    r = executor.run(case)
    assert r.status == StepStatus.PASSED, r.error_message
    assert called["factory"] == 0
