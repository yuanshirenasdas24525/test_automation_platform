"""AppSession：一条 Case 的 Appium 驱动生命周期。

一条 TestCase 可能包含多条 app_* step，这些 step 要共享**同一个** driver（同一台
设备上的同一个应用会话），而不是每条 step 都 new 一个 driver —— 那样既慢，又会
丢失登录态、导航栈等状态。

因此设计上：
  - 一条 Case 对应**最多一个** AppSession；
  - CaseExecutor 在跑到第一条 app_* step 时才真正创建 driver（懒启动，没有 app
    step 的 case 不会浪费设备）；
  - case 结束（无论成功 / 失败 / 异常）都要 `close()`，保证 driver.quit() + 归还设备。

外部依赖注入点：
  - `driver_factory`：一个可调用对象 (device_info, caps) -> driver；默认用 Appium
    的 webdriver.Remote。单测里可以替换成 FakeDriver 工厂，避免真的去连 Appium。
  - `device_pool`：acquire()/release() 的设备池；默认用 DevicePool.default()（DB 版）。

ctx 约定：
  - AppSession 实例挂在 `ctx.vars["_app_session"]`，各 app_* step runner 通过
    `AppSession.get_or_open(ctx)` 拿到（首次调用负责创建）；
  - 设备信息挂在 `ctx.vars["_device"]`（dict），包含 udid / platform / appium_url；
  - close 之后自动把 ctx 里的 key 清掉，避免后续 step 误用。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from src.core.context.execution_context import ExecutionContext

logger = logging.getLogger(__name__)

# 默认的 Appium driver 工厂：包一层延迟 import，避免单测拉起 Appium 依赖
def _default_driver_factory(device: dict, caps: dict):
    """device: DB 里挑出来的设备字典；caps: step / case 上指定的 Capabilities。"""
    from appium import webdriver
    from appium.options.android import UiAutomator2Options
    options = UiAutomator2Options().load_capabilities(caps or {})
    appium_url = _build_appium_url(device)
    logger.info("creating Appium driver @ %s for udid=%s", appium_url, device.get("udid"))
    return webdriver.Remote(appium_url, options=options)


def _build_appium_url(device: dict) -> str:
    host = device.get("agent_host") or "localhost"
    port = device.get("appium_port") or 4723
    return f"http://{host}:{port}/wd/hub"


class AppSession:
    """一个 case 的 Appium 会话：设备 + driver + AppAction（可选）。"""

    CTX_KEY = "_app_session"
    CTX_DEVICE_KEY = "_device"

    def __init__(
        self,
        device: dict,
        caps: dict | None = None,
        driver_factory: Callable[[dict, dict], Any] | None = None,
        on_release: Callable[[dict], None] | None = None,
    ):
        self.device = device
        self.caps = dict(caps or {})
        self._driver_factory = driver_factory or _default_driver_factory
        self._on_release = on_release
        self._driver = None
        self._app_action = None
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------
    # 工厂 / ctx 绑定
    # ------------------------------------------------------------
    @classmethod
    def bind(cls, ctx: ExecutionContext, session: "AppSession") -> None:
        ctx.set_var(cls.CTX_KEY, session)
        ctx.set_var(cls.CTX_DEVICE_KEY, session.device)

    @classmethod
    def from_ctx(cls, ctx: ExecutionContext) -> "AppSession | None":
        return ctx.vars.get(cls.CTX_KEY)

    @classmethod
    def require(cls, ctx: ExecutionContext) -> "AppSession":
        s = cls.from_ctx(ctx)
        if s is None:
            raise RuntimeError(
                "AppSession 未绑定到 ctx：请确认这条 case 的 case_type 是 app/mixed，"
                "或在执行前调用了 CaseExecutor 的设备获取逻辑。"
            )
        return s

    # ------------------------------------------------------------
    # driver 懒启动
    # ------------------------------------------------------------
    @property
    def driver(self):
        """第一次访问时启动 driver；之后直接复用。"""
        if self._closed:
            raise RuntimeError("AppSession 已关闭，不能再使用 driver")
        if self._driver is None:
            with self._lock:
                if self._driver is None:
                    self._driver = self._driver_factory(self.device, self.caps)
        return self._driver

    @property
    def app_action(self):
        """懒构造 AppAction（包 Finder/ActionExecutor/Assertion 的 facade）。"""
        if self._app_action is None:
            from src.core.mobile.app_action import AppAction
            self._app_action = AppAction(self.driver, db_connection=None)
        return self._app_action

    # ------------------------------------------------------------
    # 关闭 & 归还
    # ------------------------------------------------------------
    def close(self) -> None:
        """优雅关闭：quit driver → on_release 回调 → 清 ctx 引用。异常吞掉。"""
        if self._closed:
            return
        self._closed = True
        try:
            if self._driver is not None:
                try:
                    self._driver.quit()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("driver.quit 失败（忽略）：%s", exc)
        finally:
            if self._on_release is not None:
                try:
                    self._on_release(self.device)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("设备 release 回调失败：%s", exc)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# ------------------------------------------------------------
# 便捷入口：给 CaseExecutor 用
# ------------------------------------------------------------
def acquire_session_for_case(
    case_dict: dict,
    device_pool=None,
    driver_factory: Callable[[dict, dict], Any] | None = None,
) -> AppSession:
    """给一条 case 分配一个 AppSession：
      1. 从 device_pool 挑设备（按 case.environment.device_pool / case_type 过滤）；
      2. 合并 env.capabilities / case.variables 中的 caps；
      3. 返回 AppSession。调用方负责在 finally 里 session.close()。
    """
    from src.runners.app.device_pool import DevicePool

    pool = device_pool or DevicePool.default()

    env = case_dict.get("environment") or {}
    requested_pool = env.get("device_pool") or "default"
    platform = _infer_platform(case_dict)

    device = pool.acquire(pool_name=requested_pool, platform=platform,
                          execution_id=case_dict.get("execution_id"))
    if device is None:
        raise RuntimeError(
            f"未能从池 pool={requested_pool!r} platform={platform!r} 找到可用设备。"
            f"请检查 devices 表里是否有 status=idle 的记录。"
        )

    caps = _collect_caps(case_dict, env)
    return AppSession(
        device=device,
        caps=caps,
        driver_factory=driver_factory,
        on_release=lambda d: pool.release(d.get("id") or d.get("udid")),
    )


def _infer_platform(case_dict: dict) -> str | None:
    """从 case.variables / environment.browser_config 里推断 android / ios，推断不出来就 None。"""
    for src in (case_dict.get("variables"), case_dict.get("environment", {}).get("variables")):
        if isinstance(src, dict):
            p = src.get("platform") or src.get("platformName")
            if p:
                return str(p).lower()
    return None


def _collect_caps(case_dict: dict, env: dict) -> dict:
    """合并 env 里的 capabilities / case 里的 variables 中带前缀 "cap." 的键。"""
    caps: dict = {}
    env_caps = (env or {}).get("browser_config") or {}
    if isinstance(env_caps, dict):
        caps.update(env_caps)

    case_vars = case_dict.get("variables") or {}
    if isinstance(case_vars, dict):
        for k, v in case_vars.items():
            if isinstance(k, str) and k.startswith("cap."):
                caps[k[4:]] = v
    return caps
