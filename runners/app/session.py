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

from runners.context.execution_context import ExecutionContext

logger = logging.getLogger(__name__)

# W3C Appium Capabilities：必须以 `appium:` 开头；只有少数几个是 "vendor-prefix
# free" 的。Selenium 会校验这一点 —— 如果用户直接给了 "appPackage"，我们要替他
# 加上 `appium:` 前缀，否则 `webdriver.Remote` 会抛 InvalidArgumentError。
_W3C_STANDARD_KEYS = {
    "browserName", "browserVersion", "platformName",
    "acceptInsecureCerts", "pageLoadStrategy", "proxy",
    "setWindowRect", "timeouts", "strictFileInteractability",
    "unhandledPromptBehavior",
}


def _coerce_w3c_caps(caps: dict | None) -> dict:
    """给所有非标准 capabilities 加上 `appium:` 前缀；已经带前缀的不重复加。"""
    result: dict = {}
    for k, v in (caps or {}).items():
        if not isinstance(k, str):
            continue
        if k in _W3C_STANDARD_KEYS or k.startswith("appium:") or ":" in k:
            result[k] = v
        else:
            result[f"appium:{k}"] = v
    return result


def _merge_device_caps(device: dict, user_caps: dict | None) -> dict:
    """把 Device 记录里能映射成 caps 的字段合并进用户 caps。

    优先级：用户/环境/step 明确指定的 > 从 device 自动派生的。这样用户可以在
    env.browser_config 或 step.config 里显式 override 任何字段。

    Device 记录里的这些字段映射到 capabilities：
      - platform            → platformName            (必填，Android/iOS)
      - platform_version    → appium:platformVersion
      - udid                → appium:udid             (多设备时 Appium 用这个选设备)
      - device_name / udid  → appium:deviceName
      - capabilities (JSON) → 逐字段合并（用户在注册设备时填的）

    如果 device.platform 是 Android 且 user 没指定 automationName，默认 UiAutomator2；
    iOS 则默认 XCUITest。
    """
    platform = str(device.get("platform") or "").strip()
    # Appium/W3C 大小写敏感："Android" / "iOS"。这里做一次规范化。
    _canonical = {"android": "Android", "ios": "iOS"}
    platform_norm = _canonical.get(platform.lower(), platform)

    auto: dict = {}
    if platform_norm:
        auto["platformName"] = platform_norm
    if device.get("platform_version"):
        auto["appium:platformVersion"] = str(device["platform_version"])
    if device.get("udid"):
        auto["appium:udid"] = device["udid"]
    # deviceName 对 Android 是"人类可读"标签，对 iOS 是真正用来选模拟器的。
    auto["appium:deviceName"] = device.get("device_name") or device.get("udid") or "device"

    # 默认 noReset=true：会话结束时不 reset/卸载被测 app。
    # 否则默认 noReset=false 会在 driver.quit() 时触发卸载/清数据——(1) 会话收尾的
    # adb 卸载在设备上卡住时，quit 长时间不返回、报告卡在收尾（多条用例累加更久）；
    # (2) 每跑一次就把 app 卸掉、下次又要重装。keep 已装的 app、退出不动它最稳。
    # 需要干净状态的场景，用户可在 env/设备 caps 里显式设 fullReset/noReset override。
    if platform_norm:
        auto.setdefault("appium:noReset", True)

    # automationName 默认值
    if platform_norm.lower() == "android":
        auto.setdefault("appium:automationName", "UiAutomator2")
        # 服务端 setup 超时兜底（毫秒）：UIA2 装/起、adb 命令一旦卡住（如坏掉的
        # UIA2 server），让 Appium 自己也别死等，配合客户端超时双保险。用户可 override。
        auto.setdefault("appium:uiautomator2ServerLaunchTimeout", 60000)
        auto.setdefault("appium:uiautomator2ServerInstallTimeout", 60000)
        auto.setdefault("appium:adbExecTimeout", 40000)
        auto.setdefault("appium:androidInstallTimeout", 90000)
    elif platform_norm.lower() == "ios":
        auto.setdefault("appium:automationName", "XCUITest")
        auto.setdefault("appium:wdaLaunchTimeout", 120000)
        auto.setdefault("appium:wdaConnectionTimeout", 120000)

    # 设备记录里用户在 DevicesPage 注册时填的 capabilities JSON 做底座
    dev_extra = device.get("capabilities") if isinstance(device.get("capabilities"), dict) else {}
    merged: dict = {}
    merged.update(auto)
    merged.update(_coerce_w3c_caps(dev_extra))
    # 最后把调用方（env / case / step）传进来的 caps 放最顶，优先级最高
    merged.update(_coerce_w3c_caps(user_caps))
    return merged


# 默认的 Appium driver 工厂：包一层延迟 import，避免单测拉起 Appium 依赖
def _default_driver_factory(device: dict, caps: dict):
    """device: DB 里挑出来的设备字典；caps: step / case 上指定的 Capabilities。

    注意：caps 传进来时可能已经被 AppLaunchStepRunner 用 step.config override 过，
    但都还没做 W3C 规范化。这里负责：
      1. 从 device 记录自动派生 platformName / udid / deviceName / automationName；
      2. 给非标准 caps 加 `appium:` 前缀；
      3. 按 platform 选 UiAutomator2Options / XCUITestOptions。
    """
    from appium import webdriver

    final_caps = _merge_device_caps(device, caps)
    platform = str(final_caps.get("platformName") or "").lower()

    # 根据平台选 options 类。Appium 2 要求传符合 W3C 的 options 对象。
    if platform == "ios":
        from appium.options.ios import XCUITestOptions
        options = XCUITestOptions().load_capabilities(final_caps)
    else:
        # 默认走 Android（Android 是最常见的场景，iOS 用户会显式改）
        from appium.options.android import UiAutomator2Options
        options = UiAutomator2Options().load_capabilities(final_caps)

    appium_url = _build_appium_url(device)
    logger.info(
        "creating Appium driver @ %s udid=%s platform=%s caps=%s",
        appium_url, device.get("udid"), platform or "?", list(final_caps.keys()),
    )
    # 关键：给 Appium 连接加客户端超时。否则建会话（装/起 UIA2、拉 app）一旦卡住，
    # webdriver.Remote 的 create-session HTTP 调用会**永不返回**——会话还没建成，
    # /sessions 也看不到，任务就无限挂着（曾出现报告卡 running 8 小时）。加了超时后
    # 建会话/命令超过阈值即抛错，由 Runner 兜底成 ERROR，报告不会再卡死。
    # 默认 180s：足够冷启动模拟器 + 装 UIA2 + 起 app，又能挡住无限阻塞。
    import os
    from appium.webdriver.appium_connection import AppiumConnection
    from selenium.webdriver.remote.client_config import ClientConfig

    try:
        client_timeout = float(os.getenv("APPIUM_CLIENT_TIMEOUT", "180"))
    except (TypeError, ValueError):
        client_timeout = 180.0
    client_config = ClientConfig(
        remote_server_addr=appium_url,
        timeout=client_timeout,
        keep_alive=True,
    )
    executor = AppiumConnection(client_config=client_config)
    return webdriver.Remote(command_executor=executor, options=options)


def _build_appium_url(device: dict) -> str:
    """拼 Appium server URL。

    Appium 版本差异：
      - Appium 2（默认命令 `appium`）：base_path = '/'，`/session` 直连。
      - Appium 1 / 显式 `appium --base-path /wd/hub`：走 `/wd/hub/session`。

    解析优先级（高 → 低）：
      1. device.capabilities 里带 `appium:basePath` / `basePath`；
      2. 环境变量 `APPIUM_BASE_PATH`（全局兜底，便于老环境整体切到 /wd/hub）；
      3. 默认 ''（Appium 2 裸根路径）。

    注意：Appium 2 下访问 `/wd/hub/session` 会返回 404
      "The requested resource could not be found" —— 如果看到这个错，
      八成是 base_path 没对上。
    """
    import json
    import os

    host = device.get("agent_host") or "localhost"
    port = device.get("appium_port") or 4723

    # capabilities 字段可能是 dict，也可能是 ORM 里塞的 JSON 字符串
    caps = device.get("capabilities")
    if isinstance(caps, str):
        try:
            caps = json.loads(caps)
        except Exception:  # noqa: BLE001
            caps = None
    base_path = ""
    if isinstance(caps, dict):
        bp = caps.get("appium:basePath") or caps.get("basePath")
        if bp:
            base_path = bp

    if not base_path:
        base_path = os.environ.get("APPIUM_BASE_PATH", "")

    # 规范化：确保以 / 开头但不以 / 结尾（避免拼出 //session）
    if base_path and not base_path.startswith("/"):
        base_path = "/" + base_path
    base_path = base_path.rstrip("/")

    return f"http://{host}:{port}{base_path}"


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
    def started(self) -> bool:
        """driver 是否已经创建。给 step runner 区分『首次启动』vs『已在跑了』用。

        典型场景：app_install 会 lazy 拉起 driver；后面 app_launch 再来时，已经
        start 过了，更新 self.caps 是没用的（W3C session 不可改），需要显式调
        activate_app/terminate_app 才能切换前台 / 启动应用。
        """
        return self._driver is not None

    @property
    def app_action(self):
        """懒构造 AppAction（包 Finder/ActionExecutor/Assertion 的 facade）。"""
        if self._app_action is None:
            from runners.app.action import AppAction
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
    from runners.app.device_pool import DevicePool

    pool = device_pool or DevicePool.default()

    env = case_dict.get("environment") or {}
    requested_pool = env.get("device_pool") or "default"
    platform = _infer_platform(case_dict)
    execution_id = case_dict.get("execution_id")

    # 指定设备：前端让用户在 RunCaseDialog 手选某台；传了就走 acquire_by_id，
    # 忽略 pool/platform 过滤。这里不做"平台一致性校验"—— runs.py 入口已经拒了
    # 非 idle 的设备，而 platform 不一致大概率是调用方错配，让底层 Appium 自己报。
    device_id = case_dict.get("device_id")
    if device_id is not None:
        device = pool.acquire_by_id(int(device_id), execution_id=execution_id)
        if device is None:
            raise RuntimeError(
                f"无法锁定指定设备 device_id={device_id}："
                "设备可能已离线 / 被别的任务占用 / 不存在。"
            )
    else:
        device = pool.acquire(
            pool_name=requested_pool, platform=platform, execution_id=execution_id,
        )
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
    # 注意：case_dict["environment"] 可能显式为 None（用例没关联环境时常见），
    # 不能依赖 dict.get 的 default —— 得手动兜底成 {}，不然 .get('variables') 会炸。
    env = case_dict.get("environment") or {}
    for src in (case_dict.get("variables"), env.get("variables") if isinstance(env, dict) else None):
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
