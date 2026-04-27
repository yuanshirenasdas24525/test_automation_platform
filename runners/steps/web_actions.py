"""Web (Selenium / Playwright) step runners：web_goto / web_click / web_input /
web_select / web_wait / web_screenshot / web_assert_text / web_evaluate。

一条 web_* step 的 config 约定（通用部分）：

    step.config = {
        "by":      "css",              # css / xpath / id / name / class / text / link
        "locator": "button.login",
        "value":   "hello",            # web_input 用
        "timeout": 10,                 # 找元素/跳转的最大等待秒数
    }

    # web_goto
    step.config = {
        "url": "https://example.com/login",
        "timeout": 30,
    }

    # web_select
    step.config = {
        "by": "css", "locator": "select#city",
        "value": "BJ"     # 或 label / index
    }

    # web_wait
    step.config = {
        "by": "css", "locator": "...",
        "state": "visible"        # visible / attached / hidden / detached
        "timeout": 10,
        "seconds": 1               # 没 by/locator 就是纯 sleep
    }

    # web_assert_text
    step.config = {
        "by": "css", "locator": "h1",
        "equals": "Welcome"        # 或 contains / regex
    }

    # web_evaluate
    step.config = {
        "script": "return document.title;",
        "args":   []
    }

所有 runner 都通过 `WebSession.require(ctx)` 拿浏览器会话；第一次访问 `session.adapter`
时才真正启动浏览器（懒启动）。
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from core.context.execution_context import ExecutionContext
from runners.protocol import BaseStepRunner, StepResult
from runners.web.session import WebSession
from utils.value_resolver import resolve_value

logger = logging.getLogger(__name__)


# ============================================================
# 公共工具
# ============================================================
def _resolve_str(value: Any, ctx: ExecutionContext) -> Any:
    """统一走 utils.value_resolver.resolve_value，支持三种语义：
       - ${var} 变量替换（含配置中心 default_parameters）
       - function:foo / function:foo(arg1, arg2) 调用注册函数
       - sql:select ... 查 ctx.vars['_db'] 注入的目标 DB
       老版本只做 rep_expr，把 function: / sql: 当字面量原样落进 selenium，
       表现是用户写 function:generate_phone 输入框里就真的输入了那串字符。
    """
    return resolve_value(value, ctx)


def _cfg(step: dict) -> dict:
    return step.get("config") or {}


def _require_by_locator(config: dict) -> tuple[str, str]:
    by = config.get("by")
    locator = config.get("locator")
    if not by or not locator:
        raise ValueError("web_* step 缺少 config.by 或 config.locator")
    return str(by), str(locator)


# ============================================================
# 1. web_goto
# ============================================================
class WebGotoStepRunner(BaseStepRunner):
    step_types = ("web_goto",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        url = _resolve_str(config.get("url"), ctx)
        if not url:
            raise ValueError("web_goto 缺少 config.url")
        timeout = float(config.get("timeout") or 30)

        session.adapter.goto(str(url), timeout=timeout)

        result.action = f"goto {url}"
        result.target = str(url)
        result.output_data = {
            "url": session.adapter.get_url(),
            "title": session.adapter.get_title(),
        }


# ============================================================
# 2. web_click
# ============================================================
class WebClickStepRunner(BaseStepRunner):
    step_types = ("web_click",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        by, locator = _require_by_locator(config)
        locator = _resolve_str(locator, ctx)
        timeout = float(config.get("timeout") or 10)

        session.adapter.click(by, str(locator), timeout=timeout)

        result.action = f"click {by}={locator}"
        result.target = f"{by}={locator}"


# ============================================================
# 3. web_input
# ============================================================
class WebInputStepRunner(BaseStepRunner):
    step_types = ("web_input",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        by, locator = _require_by_locator(config)
        locator = _resolve_str(locator, ctx)
        value = _resolve_str(config.get("value"), ctx)
        clear_first = bool(config.get("clear_first", True))
        timeout = float(config.get("timeout") or 10)

        session.adapter.input(
            by, str(locator), str(value) if value is not None else "",
            clear_first=clear_first, timeout=timeout,
        )

        result.action = f"input {locator} = {value!r}"
        result.target = f"{by}={locator}"
        result.input_data = {"value": value}


# ============================================================
# 4. web_select
# ============================================================
class WebSelectStepRunner(BaseStepRunner):
    step_types = ("web_select",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        by, locator = _require_by_locator(config)
        locator = _resolve_str(locator, ctx)
        value = _resolve_str(config.get("value"), ctx)
        label = _resolve_str(config.get("label"), ctx)
        index = config.get("index")
        timeout = float(config.get("timeout") or 10)

        session.adapter.select_option(
            by, str(locator),
            value=None if value is None else str(value),
            label=None if label is None else str(label),
            index=None if index is None else int(index),
            timeout=timeout,
        )

        result.action = f"select {locator} value={value} label={label} index={index}"
        result.target = f"{by}={locator}"
        result.input_data = {"value": value, "label": label, "index": index}


# ============================================================
# 5. web_wait
# ============================================================
class WebWaitStepRunner(BaseStepRunner):
    step_types = ("web_wait",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        config = _cfg(step)
        timeout = float(config.get("timeout") or 10)

        if config.get("by") and config.get("locator"):
            session = WebSession.require(ctx)
            by, locator = _require_by_locator(config)
            locator = _resolve_str(locator, ctx)
            state = str(config.get("state") or "visible").lower()
            session.adapter.wait_for(by, str(locator), state=state, timeout=timeout)  # type: ignore[arg-type]
            result.action = f"wait {state} {by}={locator}"
            result.target = f"{by}={locator}"
            return

        # 纯 sleep
        seconds = float(config.get("seconds") or 0)
        ms = float(config.get("ms") or 0)
        total = seconds + ms / 1000.0
        if total <= 0:
            total = 1.0
        time.sleep(total)
        result.action = f"sleep {total:.3f}s"


# ============================================================
# 6. web_screenshot
# ============================================================
class WebScreenshotStepRunner(BaseStepRunner):
    step_types = ("web_screenshot",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        name = _resolve_str(config.get("name") or "screenshot.png", ctx)
        path = config.get("path") or f"data/screenshots/{int(time.time() * 1000)}_{name}"

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        session.adapter.screenshot(path)
        result.action = f"screenshot -> {path}"
        result.attachments.append({"name": str(name), "path": path, "type": "image/png"})


# ============================================================
# 7. web_assert_text —— 对元素文本做等值/包含/正则断言
# ============================================================
class WebAssertTextStepRunner(BaseStepRunner):
    step_types = ("web_assert_text",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        by, locator = _require_by_locator(config)
        locator = _resolve_str(locator, ctx)
        timeout = float(config.get("timeout") or 10)

        actual = session.adapter.get_text(by, str(locator), timeout=timeout)
        result.target = f"{by}={locator}"
        result.output_data = {"text": actual}

        equals = _resolve_str(config.get("equals"), ctx)
        contains = _resolve_str(config.get("contains"), ctx)
        regex = _resolve_str(config.get("regex"), ctx)

        if equals is not None:
            if actual != str(equals):
                raise AssertionError(
                    f"assert text equals 失败：expected={equals!r} actual={actual!r}"
                )
            result.action = f"assert text == {equals!r}"
        elif contains is not None:
            if str(contains) not in actual:
                raise AssertionError(
                    f"assert text contains 失败：{contains!r} not in {actual!r}"
                )
            result.action = f"assert text contains {contains!r}"
        elif regex is not None:
            if not re.search(str(regex), actual):
                raise AssertionError(
                    f"assert text regex 失败：pattern={regex!r} actual={actual!r}"
                )
            result.action = f"assert text matches {regex!r}"
        else:
            raise ValueError(
                "web_assert_text 必须指定 equals / contains / regex 中的一个"
            )


# ============================================================
# 8. web_evaluate —— 执行一段 JS 脚本（抓值 / 触发副作用）
# ============================================================
class WebEvaluateStepRunner(BaseStepRunner):
    step_types = ("web_evaluate",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        script = _resolve_str(config.get("script"), ctx)
        if not script:
            raise ValueError("web_evaluate 缺少 config.script")
        raw_args = config.get("args") or []
        if not isinstance(raw_args, list):
            raw_args = [raw_args]
        args = [_resolve_str(a, ctx) for a in raw_args]

        value = session.adapter.evaluate(str(script), *args)

        result.action = "evaluate"
        result.input_data = {"script": script, "args": args}
        result.output_data = {"return": value}

        # 支持把结果写到 ctx 的变量里：config.save_as = "x"
        save_as = config.get("save_as")
        if save_as:
            ctx.set_var(str(save_as), value)
            result.extracted[str(save_as)] = value


# ============================================================
# 工厂：一次性返回所有 web step runner，供 dispatcher 注册
# ============================================================
def build_web_runners() -> list[BaseStepRunner]:
    return [
        WebGotoStepRunner(),
        WebClickStepRunner(),
        WebInputStepRunner(),
        WebSelectStepRunner(),
        WebWaitStepRunner(),
        WebScreenshotStepRunner(),
        WebAssertTextStepRunner(),
        WebEvaluateStepRunner(),
    ]


__all__ = [
    "WebGotoStepRunner",
    "WebClickStepRunner",
    "WebInputStepRunner",
    "WebSelectStepRunner",
    "WebWaitStepRunner",
    "WebScreenshotStepRunner",
    "WebAssertTextStepRunner",
    "WebEvaluateStepRunner",
    "build_web_runners",
]
