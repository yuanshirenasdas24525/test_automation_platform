"""通用 App step runner：`app_action` 和 `app_assert`。

为什么要这两个 runner？
  - v1 时代积累了大量通过 `ActionRegistry.register(...)` 注册的"杂项动作"（30+
    个：ac_send / get_attribute / handle_alert / start_screen_recording / ...），
    这些大多是平台用户写过定制函数注册进去的，要把每个都包成单独 step type 既臃肿
    也滞后。给个**通用入口** `app_action` —— 让用户自己选 action 名 + 给 element +
    给 value，把已有的注册表能力直接放出来；
  - 同理，`AssertionEngine.assert_value(actual, expected, assert_type)` 内置 11 种
    比较类型（equal / contains / gt / lt / length_* ...），单独包成 step type 太散，
    用 `app_assert` 一个入口 + `assert_type` 选择子类型即可。

设计要点：
  - 这俩 runner 是 v1 ActionRegistry / AssertionEngine 的**薄壳**，不重新实现逻辑；
  - 所有字符串字段（locator / value / expected / attr）都先走 `resolve_value`，
    所以 ${var} / sql: / function: 前缀全部支持；
  - 元素查找复用 `runners.steps.app_actions._find_element`，跟其它 app step 一致；
  - 异常按基类约定：AssertionError → FAILED，其它 Exception → ERROR，由
    BaseStepRunner.execute 兜底。
"""
from __future__ import annotations

import logging
from typing import Any

from runners.context.execution_context import ExecutionContext
from runners.app.session import AppSession
from runners.protocol import BaseStepRunner, StepResult
from runners.steps.app_actions import _find_element
from utils.value_resolver import resolve_value

logger = logging.getLogger(__name__)


# ============================================================
# 1. app_action - 通用动作入口（包 ActionRegistry）
# ============================================================
class AppActionStepRunner(BaseStepRunner):
    """通用 App 动作 runner，按 action 名分发到 ActionRegistry。

    config = {
        "action":       "click",                      # 必填，注册表里的名字
        "by":           "id",                         # 大多数 action 需要元素
        "locator":      "com.example:id/btn",
        "value":        "13800000000",                # 可选，传给 action 的第三个参数
                                                      # 可以是 str / list / dict /
                                                      # ${var} / sql: / function:
        "timeout":      10,
        "skip_element": false,                        # true = 不查元素，element=None；
                                                      # 给 launch_app / get_clipboard 这类
                                                      # 不需要元素的 action 用
        "save_as":      "var_name",                   # 把 action 返回值存进 ctx.vars
    }

    可用 action 列表见 `core/mobile/actions/executor.py`：click / send_keys / clear /
    text / get_attribute / is_enabled / install_app / set_orientation / handle_alert /
    open_notifications / start_screen_recording / get_clipboard / ... 等 30+ 个。
    """

    step_types = ("app_action",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        # 进 ActionExecutor 之前先触发一次 import：保证注册表里有内容
        # （ActionRegistry.register 调用都在 executor.py 模块级，import 即注册）
        from runners.app.actions.registry import ActionRegistry
        from runners.app.actions import executor as _executor_mod  # noqa: F401  确保副作用

        session = AppSession.require(ctx)
        config = step.get("config") or {}

        action = config.get("action")
        if not action:
            raise ValueError("app_action 缺少 config.action（必须是 ActionRegistry 里注册的名字）")

        # 提前查注册表：未知 action 早报错，避免走完元素查找才发现拼错名
        if ActionRegistry.get(action) is None:
            registered = sorted((ActionRegistry._actions or {}).keys())
            raise ValueError(
                f"app_action 未知 action={action!r}。"
                f"可选: {registered}"
            )

        # 元素查找：locator 走 resolve_value，支持 ${var}
        skip_element = bool(config.get("skip_element"))
        by = config.get("by")
        locator_raw = config.get("locator")
        element = None
        if not skip_element and by and locator_raw:
            locator = resolve_value(locator_raw, ctx)
            element = _find_element(session, {**config, "locator": locator})
            target_desc = f"{by}={locator}"
        else:
            target_desc = "(no element)"

        # value 走 resolve_value，支持各种前缀；非字符串原样
        value = resolve_value(config.get("value"), ctx)

        # 复用 session 上的 executor（由 AppAction lazy 构造）
        # mini_step 给 ActionExecutor.execute —— 它内部会读 step["action"] / step["by"] /
        # step["locator"]，后两个用于 StaleElementReferenceException 重试时重新查
        mini_step = {"action": action, "by": by, "locator": locator_raw}
        executor = session.app_action.executor

        result.action = f"app_action {action} target={target_desc} value={value!r}"
        result.target = target_desc
        result.input_data = {"action": action, "value": value}

        ret = executor.execute(mini_step, element, value)

        result.output_data = ret

        # 把返回值存进变量池
        save_as = config.get("save_as")
        if save_as:
            ctx.set_var(str(save_as), ret)
            result.extracted[str(save_as)] = ret


# ============================================================
# 2. app_assert - 通用断言入口（包 AssertionEngine）
# ============================================================
class AppAssertStepRunner(BaseStepRunner):
    """通用 App 断言 runner，按 assert_type 分发到 AssertionEngine。

    config = {
        "assert_type":  "equal",                # 必填，equal / not_equal / gt / lt /
                                                # contains / not_contains / empty /
                                                # not_empty / length_equal / length_gt /
                                                # length_lt
        "expected":     "登录成功",              # 期望值；支持 ${var} / sql: / function:

        # actual 来源 —— 二选一：
        "by":      "id",                        # A) 元素属性。attr 默认 "text"，也可以
        "locator": "com.example:id/title",      #    填 "enabled" / "displayed" / 任意
        "attr":    "text",                      #    Appium 支持的 get_attribute 名
        # 或：
        "value":   "${var}",                    # B) 直接给值（${var}/sql:/function:）

        "timeout": 10,                          # element 查找超时（仅 A 模式）
    }

    与 `app_assert_text` 的区别：app_assert_text 是 equals/contains/not_contains 的
    专用快捷方式（不需要选 assert_type，直接填三个字段之一），适合最常见的文本断言；
    app_assert 是更通用的入口，需要数值比较 (gt/lt) 或长度断言时用。
    """

    step_types = ("app_assert",)

    # 如果 assert_type 拼错 / 不在内置 11 种里，先抛 ValueError 让用户知道有哪些可选
    _SUPPORTED = (
        "equal", "not_equal", "gt", "lt",
        "contains", "not_contains",
        "empty", "not_empty",
        "length_equal", "length_gt", "length_lt",
    )

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        from runners.app.assertions.assertion import AssertionEngine

        config = step.get("config") or {}

        # —— 可见性断言：只给了 by/locator、没给 assert_type 也没给 expected/value ——
        # 语义就是"断言这个元素可见/存在"（生成器的 assert_visible 就是这么产出的）。
        # 不能退化成默认 equal 拿元素 text 去比 None —— open menu 这类无文本控件必挂。
        # 找得到元素本身就证明存在；再用 is_displayed() 确认可见。
        by = config.get("by")
        locator_raw = config.get("locator")
        if (
            by and locator_raw
            and not config.get("assert_type")
            and "expected" not in config
            and "value" not in config
        ):
            session = AppSession.require(ctx)
            locator = resolve_value(locator_raw, ctx)
            element = _find_element(session, {**config, "locator": locator})  # 找不到会抛 → FAILED
            try:
                displayed = element.is_displayed() if hasattr(element, "is_displayed") else True
            except Exception:  # noqa: BLE001
                displayed = True  # 取不到就以"找到即存在"为准
            result.action = f"app_assert visible actual={displayed}"
            result.target = f"{by}={locator}"
            result.output_data = displayed
            if not displayed:
                raise AssertionError(f"元素 {by}={locator} 存在但不可见")
            return

        assert_type = config.get("assert_type") or "equal"
        if assert_type not in self._SUPPORTED:
            raise ValueError(
                f"app_assert 未知 assert_type={assert_type!r}，可选：{list(self._SUPPORTED)}"
            )

        if by and locator_raw:
            session = AppSession.require(ctx)
            locator = resolve_value(locator_raw, ctx)
            element = _find_element(session, {**config, "locator": locator})
            attr = (config.get("attr") or "text").strip()
            actual = self._read_element_attr(element, attr)
            target_desc = f"{by}={locator}.{attr}"
        elif "value" in config:
            actual = resolve_value(config.get("value"), ctx)
            target_desc = "(value)"
        else:
            raise ValueError(
                "app_assert 既没给 by/locator 也没给 value，"
                "无法确定 actual 来源。"
            )

        # expected 走 resolve_value：${var} / sql: / function: 都通；
        # 因为 resolve_value 已经把 sql: 处理掉了，这里再传给 AssertionEngine 时
        # expected 一定不会以 "sql:" 开头，所以 AssertionEngine 内部那段
        # 旧的 sql: 处理（要 self.db）不会触发，相当于绕过了 db 依赖。
        expected = resolve_value(config.get("expected"), ctx)

        engine = AssertionEngine(db_connection=None, device_action=None)

        result.action = f"app_assert {assert_type} actual={actual!r} expected={expected!r}"
        result.target = target_desc
        result.output_data = actual
        result.input_data = {"assert_type": assert_type, "expected": expected}

        # AssertionEngine.assert_value 失败时直接 raise AssertionError —— 由
        # BaseStepRunner.execute 兜底成 status=FAILED
        engine.assert_value(actual, expected, assert_type=assert_type)

    # ------------------------------------------------------------
    @staticmethod
    def _read_element_attr(element, attr: str) -> Any:
        """从 WebElement / MobileElement 读属性。

        优先级：
          - attr == "text" → element.text
          - attr == "enabled" → element.is_enabled()
          - attr == "displayed" → element.is_displayed()
          - 其它 → element.get_attribute(attr)
        """
        if attr == "text":
            return getattr(element, "text", "")
        if attr == "enabled":
            return element.is_enabled() if hasattr(element, "is_enabled") else None
        if attr == "displayed":
            return element.is_displayed() if hasattr(element, "is_displayed") else None
        if hasattr(element, "get_attribute"):
            return element.get_attribute(attr)
        return None


# ============================================================
# 工厂：交给 dispatcher 注册
# ============================================================
def build_app_generic_runners() -> list[BaseStepRunner]:
    return [
        AppActionStepRunner(),
        AppAssertStepRunner(),
    ]
