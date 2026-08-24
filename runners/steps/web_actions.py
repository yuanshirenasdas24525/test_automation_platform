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
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from runners.context.execution_context import ExecutionContext
from runners.protocol import BaseStepRunner, StepResult
from runners.web.session import WebSession
from utils.value_resolver import resolve_value

logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
        timeout = float(config.get("timeout") or 10)

        # 坐标点击：填了 x/y 就按视口坐标点（不认元素，和手点一样落给最上层的东西）。
        x = config.get("x")
        y = config.get("y")
        if x is not None and str(x) != "" and y is not None and str(y) != "":
            cx, cy = float(x), float(y)
            session.adapter.click_at(cx, cy)
            result.action = f"click at ({cx:.0f},{cy:.0f})"
            result.target = f"({cx:.0f},{cy:.0f})"
            return

        by, locator = _require_by_locator(config)
        locator = _resolve_str(locator, ctx)
        force = bool(config.get("force"))
        session.adapter.click(by, str(locator), timeout=timeout, force=force)

        result.action = f"click{' (force)' if force else ''} {by}={locator}"
        result.target = f"{by}={locator}"


# ============================================================
# 2.5 web_press —— 按键盘按键（Escape 关弹层 / Enter 提交 / Tab 切焦点等）
# ============================================================
class WebPressStepRunner(BaseStepRunner):
    step_types = ("web_press",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        key = _resolve_str(config.get("key") or "Escape", ctx)
        by = config.get("by")
        locator = config.get("locator")
        timeout = float(config.get("timeout") or 10)
        if by and locator:
            session.adapter.press(str(key), by=str(by), locator=str(_resolve_str(locator, ctx)), timeout=timeout)
            result.target = f"{by}={locator}"
        else:
            session.adapter.press(str(key))
            result.target = "keyboard"
        result.action = f"press {key}"


# ============================================================
# 2.6 web_drag —— 拖动（进度条/滑块/拖拽排序/滑块验证）
# ============================================================
class WebDragStepRunner(BaseStepRunner):
    step_types = ("web_drag",)

    @staticmethod
    def _num(v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        by, locator = _require_by_locator(config)  # 拖动起点：源元素
        locator = _resolve_str(locator, ctx)
        timeout = float(config.get("timeout") or 10)
        to_by = config.get("to_by") or None
        to_locator = config.get("to_locator")
        to_locator = str(_resolve_str(to_locator, ctx)) if to_locator else None
        session.adapter.drag(
            by, str(locator), timeout=timeout,
            to_by=to_by, to_locator=to_locator,
            dx=self._num(config.get("dx")), dy=self._num(config.get("dy")),
            tx=self._num(config.get("tx")), ty=self._num(config.get("ty")),
            steps=int(config.get("steps") or 10),
        )
        if to_locator:
            dest = f"→ {to_by}={to_locator}"
        elif self._num(config.get("tx")) is not None:
            dest = f"→ 坐标({config.get('tx')},{config.get('ty')})"
        else:
            dest = f"偏移({config.get('dx') or 0},{config.get('dy') or 0})"
        result.action = f"drag {by}={locator} {dest}"
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

        # 按平台配置,输入步骤在报告里直接显示实际填入值(含密码明文)——
        # 这是本平台自身测试账号的调试需求;若需脱敏改回 _is_sensitive_input 即可。
        shown = "" if value is None else str(value)
        result.action = f"input {locator} = {shown}"
        result.target = f"{by}={locator}"
        result.input_data = {"value": value, "redacted": False}


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

        result.action = f"select {locator}"
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
# 8. web_assert_visual —— 与人工确认过的页面快照基线做像素差异比较
# ============================================================
class WebAssertVisualStepRunner(BaseStepRunner):
    step_types = ("web_assert_visual",)

    @staticmethod
    def _artifact_path(raw: Any) -> Path:
        if not raw:
            raise ValueError("web_assert_visual 缺少 baseline_path")
        candidate = Path(str(raw))
        path = candidate.resolve() if candidate.is_absolute() else (_PROJECT_ROOT / candidate).resolve()
        try:
            path.relative_to(_PROJECT_ROOT)
        except ValueError as exc:
            raise ValueError("视觉基线必须位于项目工作区内") from exc
        return path

    @staticmethod
    def _mask(image: Image.Image, masks: list[Any]) -> None:
        draw = ImageDraw.Draw(image)
        for raw in masks:
            if not isinstance(raw, dict):
                continue
            x = max(0, int(raw.get("x") or 0))
            y = max(0, int(raw.get("y") or 0))
            width = max(0, int(raw.get("width") or 0))
            height = max(0, int(raw.get("height") or 0))
            draw.rectangle((x, y, x + width, y + height), fill=(0, 0, 0))

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = WebSession.require(ctx)
        config = _cfg(step)
        baseline_path = self._artifact_path(config.get("baseline_path"))
        if not baseline_path.is_file():
            raise ValueError(f"视觉基线不存在：{baseline_path}")

        output_dir = _PROJECT_ROOT / "data" / "screenshots" / "visual"
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        actual_path = output_dir / f"{stamp}_actual.png"
        diff_path = output_dir / f"{stamp}_diff.png"
        session.adapter.screenshot(str(actual_path))

        with Image.open(baseline_path) as baseline_raw, Image.open(actual_path) as actual_raw:
            baseline = baseline_raw.convert("RGB")
            actual = actual_raw.convert("RGB")
            if baseline.size != actual.size:
                result.attachments.extend([
                    {"name": "visual-baseline.png", "path": str(baseline_path), "type": "image/png"},
                    {"name": "visual-actual.png", "path": str(actual_path), "type": "image/png"},
                ])
                raise AssertionError(
                    f"视觉断言尺寸不一致：baseline={baseline.size} actual={actual.size}；"
                    "请固定浏览器视口后重新确认基线"
                )

            masks = list(config.get("masks") or [])
            self._mask(baseline, masks)
            self._mask(actual, masks)
            difference = ImageChops.difference(baseline, actual)
            tolerance = max(0, min(255, int(config.get("pixel_tolerance") or 24)))
            luminance = difference.convert("L")
            changed = sum(count for value, count in enumerate(luminance.histogram()) if value > tolerance)
            total = max(1, baseline.width * baseline.height)
            ratio = changed / total
            threshold = max(0.0, min(1.0, float(config.get("threshold") or 0.02)))
            if ratio > 0:
                difference.save(diff_path)

        result.action = f"visual diff ratio={ratio:.6f} threshold={threshold:.6f}"
        result.target = str(baseline_path)
        result.output_data = {
            "difference_ratio": ratio,
            "threshold": threshold,
            "changed_pixels": changed,
            "total_pixels": total,
        }
        result.attachments.extend([
            {"name": "visual-baseline.png", "path": str(baseline_path), "type": "image/png"},
            {"name": "visual-actual.png", "path": str(actual_path), "type": "image/png"},
        ])
        if diff_path.exists():
            result.attachments.append({"name": "visual-diff.png", "path": str(diff_path), "type": "image/png"})
        if ratio > threshold:
            raise AssertionError(f"视觉差异 {ratio:.2%} 超过允许阈值 {threshold:.2%}")


# ============================================================
# 9. web_evaluate —— 执行一段 JS 脚本（抓值 / 触发副作用）
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
        WebPressStepRunner(),
        WebDragStepRunner(),
        WebInputStepRunner(),
        WebSelectStepRunner(),
        WebWaitStepRunner(),
        WebScreenshotStepRunner(),
        WebAssertTextStepRunner(),
        WebAssertVisualStepRunner(),
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
    "WebAssertVisualStepRunner",
    "WebEvaluateStepRunner",
    "build_web_runners",
]
