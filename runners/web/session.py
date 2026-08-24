"""WebSession：一条 Case 的浏览器生命周期单元。

和 `src/runners/app/session.py` 里的 AppSession 是对称结构 —— 只不过底层不是 Appium
driver，而是我们自己抽象出来的 WebDriverAdapter（Playwright / Selenium 二选一）。

为什么要独立这一层？
  - 一条 web case 会有多个 web_* step，它们要共享**同一个**浏览器 context：保留
    cookie、登录态、tab 状态。每条 step 新起一个浏览器既慢又没意义。
  - step runner 写起来只关心 `session.adapter.click(...)`，engine 切换由 session
    一次性决定。单测里也可以塞 FakeAdapter 做离线验证。
  - 生命周期和 AppSession 一致：懒启动（第一次访问 `adapter` 时才真的开浏览器），
    case 结束统一 `close()`（CaseExecutor 在 finally 里兜一下）。

ctx 约定：
  - WebSession 实例挂在 ctx.vars["_web_session"]；
  - 引擎信息（engine / config）挂在 ctx.vars["_web_engine"]（纯 dict，便于 debug）。
  - close 之后把 ctx key 清掉，防止后续 step 误用已关闭的 session。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Optional

from runners.context.execution_context import ExecutionContext
from runners.web.adapters import (
    WebDriverAdapter,
    build_adapter,
    pick_default_engine,
)


def _env_truthy(name: str) -> Optional[bool]:
    """读布尔型环境变量：未设置 → None；"1"/"true"/"yes"/"on" → True；其它 → False。
    返回 None 是故意的，让调用方能区分"没设置"和"显式关掉"两种语义。
    """
    raw = os.getenv(name)
    if raw is None:
        return None
    val = str(raw).strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return None

logger = logging.getLogger(__name__)


# ============================================================
# 配置中心 - web/browser 可识别的配置项
# ============================================================
# 所有配置项都放在 category="web" / config_group="browser" 下，config_value 是字符串。
# 这张表同时驱动：
#   1) acquire_session_for_case 的类型转换和合并逻辑；
#   2) 前端 ConfigPage 的"推荐配置项"提示面板（/api/config/schema/web 暴露给前端）。
#
# 字段：
#   key            - config_key
#   type           - "str" | "bool" | "int" | "float" | "json"
#   default        - 默认值（仅用于提示，不会写进 config_store）
#   description    - 给前端展示的一句话说明
#   example        - 示例值
#   applies_to     - ["playwright", "selenium"] 中的一个或两个；用于提示
WEB_CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "engine",
        "type": "str",
        "default": "playwright",
        "description": "驱动引擎，playwright / selenium。case.variables 和 WEB_ENGINE 环境变量可覆盖。",
        "example": "playwright",
        "applies_to": ["playwright", "selenium"],
    },
    {
        "key": "browser",
        "type": "str",
        "default": "chromium",
        "description": "浏览器类型。Playwright 支持 chromium/firefox/webkit；Selenium 支持 chrome/firefox/edge/safari。",
        "example": "chromium",
        "applies_to": ["playwright", "selenium"],
    },
    {
        "key": "headless",
        "type": "bool",
        "default": "true",
        "description": "是否无头运行。调试点选逻辑时置为 false 可看到实际浏览器窗口；CI 保持 true。",
        "example": "false",
        "applies_to": ["playwright", "selenium"],
    },
    {
        "key": "slow_mo",
        "type": "int",
        "default": "0",
        "description": "Playwright 每个动作之间的慢放时间（毫秒）。调试时 300~800 比较直观。",
        "example": "500",
        "applies_to": ["playwright"],
    },
    {
        "key": "highlight_actions",
        "type": "bool",
        "default": "true",
        "description": "有头模式下在点击/输入处画一个红色水波纹圆圈，看清「点了哪里」。无头(CI)自动忽略。",
        "example": "true",
        "applies_to": ["playwright"],
    },
    {
        "key": "highlight_pause_ms",
        "type": "int",
        "default": "350",
        "description": "画出水波纹后停顿多少毫秒再执行动作（顺带把过快的执行放慢一点看清）。设 0 则只画不停。",
        "example": "350",
        "applies_to": ["playwright"],
    },
    {
        "key": "default_timeout",
        "type": "float",
        "default": "30",
        "description": "页面/元素默认超时（秒）。适配 goto、wait、click 等底层操作。",
        "example": "30",
        "applies_to": ["playwright", "selenium"],
    },
    {
        "key": "viewport_width",
        "type": "int",
        "default": "1280",
        "description": "浏览器视口宽度（像素）。会和 viewport_height 合并成 viewport={width,height}。",
        "example": "1440",
        "applies_to": ["playwright"],
    },
    {
        "key": "viewport_height",
        "type": "int",
        "default": "800",
        "description": "浏览器视口高度（像素）。",
        "example": "900",
        "applies_to": ["playwright"],
    },
    {
        "key": "base_url",
        "type": "str",
        "default": "",
        "description": "goto 相对路径时会拼接到前面。比如 base_url=http://127.0.0.1:54351/，goto 只写 /login 就够。",
        "example": "http://127.0.0.1:54351/",
        "applies_to": ["playwright"],
    },
    {
        "key": "user_agent",
        "type": "str",
        "default": "",
        "description": "自定义 User-Agent。留空则用浏览器默认。",
        "example": "Mozilla/5.0 ... QA-Bot/1.0",
        "applies_to": ["playwright"],
    },
    {
        "key": "launch_args",
        "type": "json",
        "default": "[]",
        "description": "浏览器启动参数数组（JSON）。比如 Selenium 的 --no-sandbox；Playwright 的 --disable-gpu。",
        "example": '["--no-sandbox", "--disable-gpu"]',
        "applies_to": ["playwright", "selenium"],
    },
    {
        "key": "screenshot_on_failure",
        "type": "bool",
        "default": "true",
        "description": "step 失败时是否自动截图（预留给 CaseExecutor 读取，保存到 data/screenshots/ ）。",
        "example": "true",
        "applies_to": ["playwright", "selenium"],
    },
    {
        "key": "sync_mode",
        "type": "bool",
        "default": "false",
        "description": "同步模式。开启后浏览器在本地桌面弹出，可直接看到操作过程（需本地非容器化运行 + headless=false）。",
        "example": "false",
        "applies_to": ["playwright", "selenium"],
    },
    {
        "key": "window_size",
        "type": "str",
        "default": "",
        "description": "Selenium 窗口尺寸，格式 WIDTH,HEIGHT。Selenium 专用（Playwright 走 viewport）。",
        "example": "1440,900",
        "applies_to": ["selenium"],
    },
    {
        "key": "remote_url",
        "type": "str",
        "default": "",
        "description": "Selenium Grid 的 hub 地址。留空用本地 driver；设了就走远程。",
        "example": "http://selenium-grid:4444/wd/hub",
        "applies_to": ["selenium"],
    },
]

# 方便查找：key -> schema 条目
_WEB_CONFIG_SCHEMA_INDEX: dict[str, dict[str, Any]] = {s["key"]: s for s in WEB_CONFIG_SCHEMA}


def _coerce_web_config_value(key: str, raw: Any) -> Any:
    """把 config_store 里的字符串值按照 WEB_CONFIG_SCHEMA 声明的类型转成实际类型。
    未知 key 原样返回；转换失败记 warning 但不抛（让默认值兜底）。
    """
    schema = _WEB_CONFIG_SCHEMA_INDEX.get(key)
    if schema is None:
        return raw
    if raw is None:
        return None
    t = schema["type"]
    try:
        if t == "bool":
            if isinstance(raw, bool):
                return raw
            val = str(raw).strip().lower()
            return val in ("1", "true", "yes", "on")
        if t == "int":
            return int(str(raw).strip())
        if t == "float":
            return float(str(raw).strip())
        if t == "json":
            if isinstance(raw, (list, dict)):
                return raw
            import json as _json
            return _json.loads(str(raw))
        # "str" 默认
        return str(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("web 配置项 %s=%r 转换失败（期望 %s），忽略：%s", key, raw, t, exc)
        return None


def _load_web_config_from_store(project_id: int | None = None) -> dict[str, Any]:
    """从 config_store (category="web", config_group="browser") 读所有已知键，返回类型已转好的 dict。
    任何异常都吞掉，改成空 dict —— 不能让配置读失败阻塞用例执行。
    """
    try:
        from database.db import DB
        from utils.reload_config import config_center
    except Exception as exc:  # noqa: BLE001
        logger.warning("web 配置无法加载 (import 失败)，改用默认：%s", exc)
        return {}

    db = None
    try:
        db = DB()
        config_center.reload(db.sql, project_id=project_id, category="web")
    except Exception as exc:  # noqa: BLE001
        logger.warning("config_center.reload(category=web) 失败，改用默认：%s", exc)
        try:
            if db is not None:
                db.close()
        except Exception:  # noqa: BLE001
            pass
        return {}

    try:
        raw = config_center.get("browser", project_id=project_id) or {}
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    if not isinstance(raw, dict):
        return {}

    coerced: dict[str, Any] = {}
    for key, value in raw.items():
        v = _coerce_web_config_value(key, value)
        if v is not None and v != "":
            coerced[key] = v
    return coerced


def _merge_viewport(config: dict[str, Any]) -> None:
    """把 viewport_width / viewport_height 合成 viewport={width,height}，并从顶层删掉。
    只要有一个给了就合并；另一个走默认。
    """
    w = config.pop("viewport_width", None)
    h = config.pop("viewport_height", None)
    if w is None and h is None:
        return
    # 已有 viewport 就尊重用户
    if isinstance(config.get("viewport"), dict):
        vp = dict(config["viewport"])
    else:
        vp = {"width": 1280, "height": 800}
    if w is not None:
        try:
            vp["width"] = int(w)
        except Exception:  # noqa: BLE001
            pass
    if h is not None:
        try:
            vp["height"] = int(h)
        except Exception:  # noqa: BLE001
            pass
    config["viewport"] = vp


class WebSession:
    """一条 case 的 Web 浏览器会话：engine + config + adapter（懒启动）。"""

    CTX_KEY = "_web_session"
    CTX_ENGINE_KEY = "_web_engine"

    def __init__(
        self,
        engine: str,
        config: dict | None = None,
        adapter_factory: Callable[[str, dict], WebDriverAdapter] | None = None,
    ):
        """:param engine: "playwright" | "selenium"
        :param config:  对应 adapter 的启动参数（见 adapters.py 的各 docstring）
        :param adapter_factory: 可选注入，用于单测把 build_adapter 换成 FakeAdapter
        """
        self.engine = (engine or "").strip().lower() or pick_default_engine()
        self.config: dict = dict(config or {})
        self._adapter_factory = adapter_factory or build_adapter
        self._adapter: WebDriverAdapter | None = None
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------
    # 工厂 / ctx 绑定
    # ------------------------------------------------------------
    @classmethod
    def bind(cls, ctx: ExecutionContext, session: "WebSession") -> None:
        ctx.set_var(cls.CTX_KEY, session)
        ctx.set_var(cls.CTX_ENGINE_KEY, {
            "engine": session.engine,
            "config": session.config,
        })

    @classmethod
    def from_ctx(cls, ctx: ExecutionContext) -> "WebSession | None":
        return ctx.vars.get(cls.CTX_KEY)

    @classmethod
    def require(cls, ctx: ExecutionContext) -> "WebSession":
        s = cls.from_ctx(ctx)
        if s is None:
            raise RuntimeError(
                "WebSession 未绑定到 ctx：请确认这条 case 的 case_type 是 web/mixed，"
                "或 steps 里含有 web_* 开头的 step_type，让 CaseExecutor 帮你建好 session。"
            )
        return s

    # ------------------------------------------------------------
    # adapter 懒启动
    # ------------------------------------------------------------
    @property
    def adapter(self) -> WebDriverAdapter:
        """第一次访问时才构造并启动 adapter；之后复用同一个。"""
        if self._closed:
            raise RuntimeError("WebSession 已关闭，不能再使用 adapter")
        if self._adapter is None:
            with self._lock:
                if self._adapter is None:
                    logger.info("构造 WebDriverAdapter engine=%s", self.engine)
                    self._adapter = self._adapter_factory(self.engine, self.config)
        return self._adapter

    # ------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------
    def close(self) -> None:
        """优雅关闭 adapter。异常吞掉，确保 CaseExecutor 的 finally 一定能走完。"""
        if self._closed:
            return
        self._closed = True
        if self._adapter is None:
            return
        try:
            self._adapter.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("WebDriverAdapter.close 失败（忽略）：%s", exc)
        finally:
            self._adapter = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# ============================================================
# 便捷入口：给 CaseExecutor 用
# ============================================================
def acquire_session_for_case(
    case_dict: dict,
    adapter_factory: Callable[[str, dict], WebDriverAdapter] | None = None,
) -> WebSession:
    """给一条 case 分配一个 WebSession。

    配置优先级（高 → 低）：
      1) 环境变量 WEB_HEADLESS / WEB_ENGINE / WEB_SLOW_MO —— 临时调试用
      2) case.variables 中 "browser." / "web." 前缀的条目 —— 单用例临时 override
      3) env.browser_config —— 用例携带的环境描述
      4) config_store(category=web, group=browser) —— 全局统一默认，通过"配置中心"维护
      5) adapter 里写死的默认值（headless=True / browser=chromium ...）

    新加第 4 步：配置中心里的 web.browser.* 可以成为团队统一的默认值，
    比如把 headless 设成 false，调试时就不用每次都 export WEB_HEADLESS=false。
    """
    env = case_dict.get("environment") or {}
    browser_cfg_raw = env.get("browser_config") or {}
    # 老 schema 有人把 browser_config 存成 JSON 字符串，容一下
    if isinstance(browser_cfg_raw, str):
        try:
            import json
            browser_cfg_raw = json.loads(browser_cfg_raw) or {}
        except Exception:  # noqa: BLE001
            logger.warning("env.browser_config 不是合法 JSON，按空 dict 处理")
            browser_cfg_raw = {}

    config: dict = {}

    # ---- 层 4：config_store 项目默认 ----
    project_id = case_dict.get("project_id")
    store_cfg = _load_web_config_from_store(project_id=project_id)
    if store_cfg:
        logger.info("[WebSession] 从 config_store 读到 web.browser.*: %s", list(store_cfg.keys()))
        print(f"[WebSession] 配置中心 web.browser.*: {store_cfg}")
        config.update(store_cfg)

    # ---- 层 3：env.browser_config ----
    if isinstance(browser_cfg_raw, dict):
        config.update(browser_cfg_raw)

    # ---- 层 2：case.variables 前缀覆盖 ----
    case_vars = case_dict.get("variables") or {}
    if isinstance(case_vars, dict):
        for k, v in case_vars.items():
            if not isinstance(k, str):
                continue
            for prefix in ("browser.", "web."):
                if k.startswith(prefix):
                    config[k[len(prefix):]] = v
                    break

    # engine 单独抽一下：允许放在 browser_config.engine 或 variables 里
    engine: Optional[str] = None
    if isinstance(browser_cfg_raw, dict):
        engine = browser_cfg_raw.get("engine")
    if not engine and isinstance(case_vars, dict):
        engine = case_vars.get("browser.engine") or case_vars.get("web.engine")
    # config_store 里的 engine 作为最后兜底（在 pick_default_engine 之前）
    if not engine and config.get("engine"):
        engine = config.get("engine")
    if not engine:
        engine = pick_default_engine()

    # engine 字段本身不是 adapter 的启动参数，避免传进去被当成未知 kw
    config.pop("engine", None)

    # viewport_width / viewport_height → viewport={width,height}
    _merge_viewport(config)

    # screenshot_on_failure 是 CaseExecutor 层读的，不是 adapter 的启动参数；
    # 保留在 config 里不会怎样，但顺手剥掉避免将来被当成 kw 传进去
    if "screenshot_on_failure" in config:
        # 保留但不会传给 adapter —— adapter 不认识时通常也不会出错，但我们显式剥离更稳
        config.pop("screenshot_on_failure", None)

    # -------------------------------------------------------------------
    # 环境变量 override：调试时最重要的两个开关
    # -------------------------------------------------------------------
    #   WEB_HEADLESS=false  → 把浏览器显示出来（看得见点哪里、跳哪里）
    #   WEB_HEADLESS=true   → 强制无头（CI 环境默认就是 True，一般不用设）
    #   WEB_ENGINE=selenium → 强制用 Selenium（Playwright 没装 / 想跟老脚本对齐时用）
    #   WEB_SLOW_MO=500     → Playwright 每一步慢放 500ms，能看清楚点击顺序
    env_headless = _env_truthy("WEB_HEADLESS")
    if env_headless is not None:
        config["headless"] = env_headless
        logger.info("WEB_HEADLESS=%s → headless=%s（env override）", os.getenv("WEB_HEADLESS"), env_headless)

    env_engine = os.getenv("WEB_ENGINE")
    if env_engine:
        engine = env_engine.strip().lower()
        logger.info("WEB_ENGINE=%s（env override）", engine)

    env_slow_mo = os.getenv("WEB_SLOW_MO")
    if env_slow_mo:
        try:
            config["slow_mo"] = int(env_slow_mo)
            logger.info("WEB_SLOW_MO=%s ms（env override）", config["slow_mo"])
        except ValueError:
            logger.warning("WEB_SLOW_MO 不是整数，忽略：%r", env_slow_mo)

    logger.info(
        "[WebSession] 初始化 engine=%s headless=%s config_keys=%s",
        engine, config.get("headless", True), list(config.keys()),
    )
    # 同步 print 到 stdout，保证在 celery worker 日志里一眼能看到（LOGGER 有时走不同 handler）
    print(
        f"[WebSession] 初始化 engine={engine} headless={config.get('headless', True)} "
        f"config_keys={list(config.keys())}"
    )

    return WebSession(engine=str(engine), config=config, adapter_factory=adapter_factory)


__all__ = [
    "WebSession",
    "acquire_session_for_case",
    "WEB_CONFIG_SCHEMA",
]
