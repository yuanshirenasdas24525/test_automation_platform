"""App (Appium) step runners：app_tap / app_input / app_swipe / app_wait / ... 等。

一条 app_* step 的标准 config 结构：

    step.config = {
        "by":      "xpath",           # id / xpath / accessibility_id / android_uiautomator ...
        "locator": "//Button[@text='登录']",
        "value":   "13800000000",     # app_input 用
        "timeout": 10,                # 找元素最大等待秒数
        "sliding_location": "vertical", # 找不到时滑动寻找（可选）
    }

    # app_swipe 用的坐标/方向参数
    step.config = {
        "direction": "up",            # up / down / left / right
        "duration":  500,             # ms
        "ratio":     0.5              # 相对屏幕的位移比例
    }

    # app_launch 用
    step.config = {
        "appPackage": "com.example.app",
        "appActivity": ".MainActivity"
    }

所有 runner 都通过 `AppSession.require(ctx)` 拿设备会话；第一次访问 driver 时才真正
连 Appium（懒启动）。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from src.core.context.execution_context import ExecutionContext
from src.runners.app.session import AppSession
from src.runners.protocol import BaseStepRunner, StepResult
from src.utils.platform_utils import rep_expr

logger = logging.getLogger(__name__)


# ============================================================
# 公共工具：把 ctx 的变量池用于 ${var} 替换
# ============================================================
def _resolve_str(value: Any, ctx: ExecutionContext) -> Any:
    if isinstance(value, str):
        return rep_expr(value, ctx.vars or {})
    return value


def _find_element(session: AppSession, config: dict):
    """复用 src/core/mobile/finder/finder.py 的查找逻辑。"""
    by = config.get("by")
    locator = config.get("locator")
    if not by or not locator:
        raise ValueError("app_* step 缺少 config.by 或 config.locator")
    timeout = int(config.get("timeout") or 10)

    app_action = session.app_action
    if config.get("sliding_location"):
        return app_action.finder.swipe_find({
            "by": by, "locator": locator,
            "sliding_location": config["sliding_location"],
        })
    return app_action.finder.find(by, locator, timeout=timeout)


# ============================================================
# 1. app_tap - 点击
# ============================================================
class AppTapStepRunner(BaseStepRunner):
    step_types = ("app_tap",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        locator = _resolve_str(config.get("locator"), ctx)
        config = {**config, "locator": locator}

        el = _find_element(session, config)
        el.click()

        result.action = f"tap {config.get('by')}={locator}"
        result.target = f"{config.get('by')}={locator}"


# ============================================================
# 2. app_input - 输入文本
# ============================================================
class AppInputStepRunner(BaseStepRunner):
    step_types = ("app_input",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        locator = _resolve_str(config.get("locator"), ctx)
        value = _resolve_str(config.get("value"), ctx)
        clear_first = bool(config.get("clear_first", True))

        el = _find_element(session, {**config, "locator": locator})
        if clear_first:
            try:
                el.clear()
            except Exception as exc:  # noqa: BLE001
                logger.debug("clear 失败（忽略）：%s", exc)
        el.send_keys(str(value) if value is not None else "")

        result.action = f"input {locator} = {value!r}"
        result.target = f"{config.get('by')}={locator}"
        result.input_data = {"value": value}


# ============================================================
# 3. app_swipe - 滑动
# ============================================================
class AppSwipeStepRunner(BaseStepRunner):
    step_types = ("app_swipe",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        driver = session.driver

        # 两种模式：显式 x1/y1/x2/y2 或方向 + ratio
        duration = int(config.get("duration") or 500)
        if all(k in config for k in ("x1", "y1", "x2", "y2")):
            x1, y1, x2, y2 = (int(config[k]) for k in ("x1", "y1", "x2", "y2"))
        else:
            direction = str(config.get("direction") or "up").lower()
            ratio = float(config.get("ratio") or 0.5)
            size = driver.get_window_size()
            w, h = size["width"], size["height"]
            cx, cy = w // 2, h // 2
            offx = int(w * ratio / 2)
            offy = int(h * ratio / 2)
            if direction == "up":
                x1, y1, x2, y2 = cx, cy + offy, cx, cy - offy
            elif direction == "down":
                x1, y1, x2, y2 = cx, cy - offy, cx, cy + offy
            elif direction == "left":
                x1, y1, x2, y2 = cx + offx, cy, cx - offx, cy
            elif direction == "right":
                x1, y1, x2, y2 = cx - offx, cy, cx + offx, cy
            else:
                raise ValueError(f"无效 direction: {direction!r}")

        driver.swipe(x1, y1, x2, y2, duration)
        result.action = f"swipe ({x1},{y1})->({x2},{y2}) dur={duration}"
        result.input_data = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration}


# ============================================================
# 4. app_wait - 显式等待
# ============================================================
class AppWaitStepRunner(BaseStepRunner):
    step_types = ("app_wait",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        config = step.get("config") or {}
        seconds = float(config.get("seconds") or 0)
        ms = float(config.get("ms") or 0)
        total = seconds + ms / 1000.0
        if "by" in config and "locator" in config:
            session = AppSession.require(ctx)
            locator = _resolve_str(config["locator"], ctx)
            _find_element(session, {**config, "locator": locator,
                                     "timeout": int(total) or 10})
            result.action = f"wait for {config['by']}={locator}"
        else:
            if total <= 0:
                total = 1.0
            time.sleep(total)
            result.action = f"sleep {total:.3f}s"


# ============================================================
# 5. app_screenshot - 截图并作为附件
# ============================================================
class AppScreenshotStepRunner(BaseStepRunner):
    step_types = ("app_screenshot",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        name = _resolve_str(config.get("name") or "screenshot.png", ctx)
        path = config.get("path") or f"data/screenshots/{int(time.time()*1000)}_{name}"

        driver = session.driver
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        driver.save_screenshot(path)
        result.action = f"screenshot -> {path}"
        result.attachments.append({"name": name, "path": path, "type": "image/png"})


# ============================================================
# 6. app_launch / app_close / app_back / app_press
# ============================================================
class AppLaunchStepRunner(BaseStepRunner):
    step_types = ("app_launch",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        # 允许通过 config 动态 override caps
        if config:
            session.caps.update({k: _resolve_str(v, ctx) for k, v in config.items()})
        # 触发 driver 启动（懒）
        _ = session.driver
        result.action = f"launch {config.get('appPackage', '(default)')}"


class AppCloseStepRunner(BaseStepRunner):
    step_types = ("app_close",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        driver = session.driver
        try:
            driver.close_app()
        except Exception:  # noqa: BLE001
            # 有些 driver 没 close_app，改调 terminate_app
            pkg = (step.get("config") or {}).get("appPackage")
            if pkg and hasattr(driver, "terminate_app"):
                driver.terminate_app(pkg)
        result.action = "close app"


class AppBackStepRunner(BaseStepRunner):
    step_types = ("app_back",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        session.driver.back()
        result.action = "press back"


class AppPressStepRunner(BaseStepRunner):
    step_types = ("app_press",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        keycode = config.get("keycode")
        if keycode is None:
            raise ValueError("app_press 缺少 config.keycode")
        session.driver.press_keycode(int(keycode))
        result.action = f"press keycode={keycode}"


# ============================================================
# 工厂：一次性返回所有 app step runner，供 dispatcher 注册
# ============================================================
def build_app_runners() -> list[BaseStepRunner]:
    return [
        AppTapStepRunner(),
        AppInputStepRunner(),
        AppSwipeStepRunner(),
        AppWaitStepRunner(),
        AppScreenshotStepRunner(),
        AppLaunchStepRunner(),
        AppCloseStepRunner(),
        AppBackStepRunner(),
        AppPressStepRunner(),
    ]
