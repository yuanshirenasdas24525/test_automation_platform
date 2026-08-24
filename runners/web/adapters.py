"""WebDriverAdapter：一个薄抽象，把 Selenium 和 Playwright 两种 Web 自动化引擎
在 step runner 能看到的那一小块儿接口上统一起来。

为什么需要这层抽象？
  - step runner（web_click / web_input / ...）本质上只关心「在某个元素上做某件事」，
    它不应该因为底层是 Selenium 还是 Playwright 写两份代码；
  - 团队里有人熟 Selenium、有人熟 Playwright，存量用例也可能是 Selenium 的 By 定位符。
    用统一的 `by + locator` 组合，两边 Adapter 各自翻译到自己引擎的原生调用；
  - Appium 那一侧只有一种引擎（WebDriver），不需要这层抽象，所以 app_actions.py 直接
    拿 `session.driver` 调方法就够了。Web 这边要做"双引擎兼容"，这层就值得加。

接口原则：
  - 所有方法都是「同步」调用：本平台整体是同步的（pytest + allure）。
    Playwright 用 `sync_api`，不用 async。
  - 所有方法都能抛异常；由 runner 里 BaseStepRunner 的 try/except 兜底转成 StepResult。
  - `by` 只接受一组有限的字符串常量（见 BY_TYPES）；未知的 by 一律抛 ValueError。

新增/换绑定方法时请同步改两边实现，避免一边可用一边 NotImplementedError。
"""
from __future__ import annotations

import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 定位方式：统一字符串常量
# ---------------------------------------------------------------------------
BY_TYPES = ("css", "xpath", "id", "name", "class", "text", "link", "role")
ByType = Literal["css", "xpath", "id", "name", "class", "text", "link", "role"]


def _parse_role_locator(locator: str) -> tuple[str, dict[str, str]]:
    """解析 ARIA role 定位器：``role=tab;name=测试`` / ``tab;name=测试`` →
    (role, {name: "测试", ...})。是 Radix/无障碍组件最稳且唯一的定位方式。"""
    val = str(locator or "").strip()
    if val.lower().startswith("role="):
        val = val[5:]
    parts = [p for p in val.split(";") if p.strip()]
    if not parts:
        return "", {}
    role = parts[0].strip()
    attrs: dict[str, str] = {}
    for piece in parts[1:]:
        if "=" in piece:
            key, value = piece.split("=", 1)
            attrs[key.strip().lower()] = value.strip().strip("'\"")
    return role, attrs


def _must_force_headless(
    headless: bool,
    *,
    platform_name: str,
    display: str | None,
    wayland_display: str | None,
) -> bool:
    """只在 Linux 且同时没有 X11/Wayland 显示服务时强制无头。

    macOS 使用 Quartz、Windows 使用桌面会话，两者通常都没有 DISPLAY，
    不能因此将用户显式设置的 headless=false 覆盖掉。
    """
    return (
        not headless
        and platform_name.startswith("linux")
        and not display
        and not wayland_display
    )


def _normalize_by(by: str | None) -> ByType:
    if not by:
        return "css"
    b = str(by).strip().lower()
    # 兼容一些写法：CSS_SELECTOR / css_selector / link_text / tag
    alias = {
        "css_selector": "css",
        "css-selector": "css",
        "classname": "class",
        "class_name": "class",
        "link_text": "link",
        "linktext": "link",
        "partial_link_text": "link",  # 两边在单个 API 下尽量向"link"坍塌
    }
    b = alias.get(b, b)
    if b not in BY_TYPES:
        raise ValueError(
            f"不支持的 by={by!r}；可用值：{BY_TYPES}"
        )
    return b  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Adapter 抽象
# ---------------------------------------------------------------------------
class WebDriverAdapter(ABC):
    """Web 自动化引擎的统一接口。step runner 只认这层。"""

    #: "playwright" | "selenium"
    engine: str = ""

    # ------------------------ 导航 / 页面级 ------------------------
    @abstractmethod
    def goto(self, url: str, timeout: float = 30) -> None: ...

    @abstractmethod
    def get_url(self) -> str: ...

    @abstractmethod
    def get_title(self) -> str: ...

    # ------------------------ 元素操作 ------------------------
    @abstractmethod
    def click(self, by: str, locator: str, timeout: float = 10, force: bool = False) -> None:
        """force=True 跳过可操作性检查（可见/稳定/命中测试），照样点——更接近“手点坐标”。"""
        ...

    @abstractmethod
    def click_at(self, x: float, y: float) -> None:
        """按视口坐标(x,y)点一下：不认元素，落给该坐标最上面的东西（和手点一样）。"""
        ...

    @abstractmethod
    def input(
        self, by: str, locator: str, text: str,
        clear_first: bool = True, timeout: float = 10,
    ) -> None: ...

    @abstractmethod
    def select_option(
        self, by: str, locator: str, value: str | None = None,
        label: str | None = None, index: int | None = None,
        timeout: float = 10,
    ) -> None: ...

    @abstractmethod
    def wait_for(
        self, by: str, locator: str,
        state: Literal["visible", "attached", "hidden", "detached"] = "visible",
        timeout: float = 10,
    ) -> None: ...

    @abstractmethod
    def get_text(self, by: str, locator: str, timeout: float = 10) -> str: ...

    @abstractmethod
    def get_attribute(self, by: str, locator: str, name: str, timeout: float = 10) -> str | None: ...

    @abstractmethod
    def press(
        self, key: str, by: str | None = None, locator: str | None = None,
        timeout: float = 10,
    ) -> None:
        """按键盘按键。指定 by/locator 则先聚焦该元素再按，否则对整页按（如 Escape 关弹层）。"""
        ...

    @abstractmethod
    def drag(
        self, by: str, locator: str, *, timeout: float = 10,
        to_by: str | None = None, to_locator: str | None = None,
        dx: float | None = None, dy: float | None = None,
        tx: float | None = None, ty: float | None = None,
        steps: int = 10,
    ) -> None:
        """从源元素中心按住拖动到：目标元素(to_by/to_locator) / 目标坐标(tx,ty) /
        偏移(dx,dy) 三选一。steps 是中途移动步数（越大越平滑，滑块/轨迹校验更像人手）。
        用于进度条、滑块、拖拽排序、滑块验证等。"""
        ...

    # ------------------------ 其它 ------------------------
    @abstractmethod
    def screenshot(self, path: str) -> None: ...

    @abstractmethod
    def evaluate(self, script: str, *args: Any) -> Any: ...

    @abstractmethod
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Playwright 实现
# ---------------------------------------------------------------------------
class PlaywrightAdapter(WebDriverAdapter):
    """基于 playwright.sync_api 的实现。

    启动参数 config：
        {
            "browser":    "chromium" | "firefox" | "webkit",     # 默认 chromium
            "headless":   True,                                   # 默认 True
            "slow_mo":    0,                                      # 单位 ms
            "viewport":   {"width": 1280, "height": 800},
            "user_agent": "...",
            "extra_http_headers": {...},
            "base_url":   "https://...",                          # goto 相对路径时会拼接
            "launch_args": ["--disable-xss-auditor", ...],        # 传给 browser_type.launch
        }
    """

    engine = "playwright"

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False
        # 有头模式下在点击/输入处画一个水波纹圆圈，便于人眼看清"点了哪里"。
        # 无头(CI)自动关闭：既看不见也不该拖慢。这两个值在 _ensure_started 里按
        # 最终 headless 结果定稿。
        self._highlight = False
        self._highlight_pause = 0.0

    # ---------- 懒启动 ----------
    def _ensure_started(self) -> None:
        if self._started:
            return
        try:
            from playwright.sync_api import sync_playwright  # noqa: WPS433
        except ImportError as e:  # pragma: no cover
            print("[PlaywrightAdapter] ImportError: playwright 未安装。pip install playwright && playwright install")
            raise RuntimeError(
                "使用 Playwright 需要先安装：pip install playwright && playwright install"
            ) from e

        browser_type = str(self.config.get("browser") or "chromium").lower()
        headless = bool(self.config.get("headless", True))
        slow_mo = int(self.config.get("slow_mo") or 0)
        launch_args = list(self.config.get("launch_args") or [])

        # 安全兜底：只有 Linux 容器 / SSH 且没有 X11/Wayland 时强制无头。
        # macOS/Windows 不使用 DISPLAY，必须尊重 headless=false。
        if _must_force_headless(
            headless,
            platform_name=sys.platform,
            display=os.environ.get("DISPLAY"),
            wayland_display=os.environ.get("WAYLAND_DISPLAY"),
        ):
            print(
                "[PlaywrightAdapter] 检测到 Linux 无 X11/Wayland 环境，"
                "自动将 headless=false → true（避免 XServer 报错）"
            )
            logger.warning(
                "PlaywrightAdapter: Linux 无 X11/Wayland，强制 headless=true（原配置为 false）"
            )
            headless = True

        # 点击可视化：仅在有头模式生效，默认开启；可用 highlight_actions=false 关闭。
        # highlight_pause_ms 是圈出后停顿多久（默认 350ms，顺带把"太快"放慢一点）。
        # config 值可能来自配置中心的字符串（"false"/"350"），这里做健壮解析。
        raw_hl = self.config.get("highlight_actions", True)
        highlight_on = raw_hl if isinstance(raw_hl, bool) else str(raw_hl).strip().lower() in ("1", "true", "yes", "on")
        self._highlight = (not headless) and highlight_on
        try:
            self._highlight_pause = max(0.0, float(self.config.get("highlight_pause_ms", 350)))
        except (TypeError, ValueError):
            self._highlight_pause = 350.0

        print(
            f"[PlaywrightAdapter] 启动浏览器 browser={browser_type} "
            f"headless={headless} slow_mo={slow_mo}ms highlight={self._highlight}"
        )

        try:
            self._playwright = sync_playwright().start()
        except Exception as exc:
            print(f"[PlaywrightAdapter] sync_playwright().start() 失败：{exc}")
            raise

        bt = getattr(self._playwright, browser_type, None)
        if bt is None:
            raise ValueError(
                f"Playwright 不支持 browser={browser_type!r}；可选 chromium/firefox/webkit"
            )

        logger.info(
            "启动 Playwright: browser=%s headless=%s slow_mo=%s",
            browser_type, headless, slow_mo,
        )
        try:
            self._browser = bt.launch(headless=headless, slow_mo=slow_mo, args=launch_args)
        except Exception as exc:
            print(
                f"[PlaywrightAdapter] browser.launch 失败：{exc}。"
                f"常见原因：浏览器内核没装（`playwright install chromium` 跑一下）"
            )
            raise

        context_kwargs: dict[str, Any] = {}
        if self.config.get("viewport"):
            context_kwargs["viewport"] = self.config["viewport"]
        if self.config.get("user_agent"):
            context_kwargs["user_agent"] = self.config["user_agent"]
        if self.config.get("extra_http_headers"):
            context_kwargs["extra_http_headers"] = self.config["extra_http_headers"]
        if self.config.get("base_url"):
            context_kwargs["base_url"] = self.config["base_url"]

        self._context = self._browser.new_context(**context_kwargs)

        default_timeout_ms = int(float(self.config.get("default_timeout") or 30) * 1000)
        self._context.set_default_timeout(default_timeout_ms)

        self._page = self._context.new_page()
        self._started = True
        print("[PlaywrightAdapter] 浏览器就绪")

    @property
    def page(self):
        self._ensure_started()
        return self._page

    # ---------- 定位符翻译 ----------
    @staticmethod
    def _selector(by: str, locator: str) -> str:
        by = _normalize_by(by)
        if by == "css":
            return locator
        if by == "xpath":
            return f"xpath={locator}"
        if by == "id":
            return f"#{locator}"
        if by == "name":
            return f"[name={locator!r}]"
        if by == "class":
            return f".{locator}"
        if by == "text":
            # Playwright 的 text 引擎，默认子字符串不区分大小写。用 "= exact match" 的话 runner 层再调整
            return f"text={locator}"
        if by == "link":
            return f"a:has-text({locator!r})"
        if by == "role":
            # ARIA role 引擎：role=tab;name=测试 → role=tab[name="测试"]
            role, attrs = _parse_role_locator(locator)
            sel = f"role={role}"
            name = attrs.get("name")
            if name:
                esc = name.replace("\\", "\\\\").replace('"', '\\"')
                sel += f'[name="{esc}"]'
            for flag in ("exact", "checked", "pressed", "selected", "expanded", "disabled"):
                if flag in attrs:
                    sel += f"[{flag}={attrs[flag]}]"
            return sel
        raise ValueError(f"unsupported by: {by}")

    # ---------- 实现 ----------
    def goto(self, url: str, timeout: float = 30) -> None:
        self.page.goto(url, timeout=timeout * 1000)

    def get_url(self) -> str:
        return self.page.url

    def get_title(self) -> str:
        return self.page.title()

    # 点击/输入前在目标元素中心画一个红色水波纹，纯视觉、不影响断言（pointer-events
    # 关闭、fixed 定位、~0.5s 后自动移除、带唯一 data 标记）。任何异常都吞掉，绝不
    # 因为"画圈"失败而让真实操作失败。
    _RIPPLE_JS = """(pt) => {
      const d = document.createElement('div');
      d.setAttribute('data-tap-ripple','1');
      d.style.cssText = 'position:fixed;left:'+pt.x+'px;top:'+pt.y+'px;width:14px;height:14px;'
        + 'margin:-7px 0 0 -7px;border:3px solid rgba(239,68,68,.95);border-radius:50%;'
        + 'background:rgba(239,68,68,.18);pointer-events:none;z-index:2147483647;'
        + 'box-shadow:0 0 0 3px rgba(239,68,68,.25);opacity:1;'
        + 'transition:transform .45s ease-out, opacity .45s ease-out;';
      document.documentElement.appendChild(d);
      requestAnimationFrame(() => { d.style.transform='scale(3.2)'; d.style.opacity='0'; });
      setTimeout(() => d.remove(), 520);
    }"""

    # 断言/等待用绿色方框圈出被检查的元素（区别于点击的红色水波纹），让人看清
    # "在断言哪里"。同样纯视觉、不影响断言本身。
    _BOX_JS = """(r) => {
      const d = document.createElement('div');
      d.setAttribute('data-tap-ripple','1');
      d.style.cssText = 'position:fixed;left:'+r.x+'px;top:'+r.y+'px;width:'+r.w+'px;height:'+r.h+'px;'
        + 'box-sizing:border-box;border:3px solid rgba(34,197,94,.95);border-radius:6px;'
        + 'background:rgba(34,197,94,.12);pointer-events:none;z-index:2147483647;'
        + 'box-shadow:0 0 0 3px rgba(34,197,94,.22);opacity:1;transition:opacity .5s ease-out .35s;';
      document.documentElement.appendChild(d);
      requestAnimationFrame(() => { d.style.opacity='0'; });
      setTimeout(() => d.remove(), 900);
    }"""

    def _highlight_action(self, sel: str, timeout: float) -> None:
        if not self._highlight:
            return
        try:
            loc = self.page.locator(sel).first
            box = loc.bounding_box(timeout=timeout * 1000)
            if not box:
                return
            center = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
            self.page.evaluate(self._RIPPLE_JS, center)
            if self._highlight_pause > 0:
                self.page.wait_for_timeout(self._highlight_pause)
        except Exception as exc:  # noqa: BLE001
            logger.debug("点击可视化失败(忽略)：%s", exc)

    def _highlight_assert(self, sel: str, timeout: float) -> None:
        """断言/可见性等待命中元素后，用绿框圈出它。元素须已存在（调用方保证）。"""
        if not self._highlight:
            return
        try:
            box = self.page.locator(sel).first.bounding_box(timeout=max(0.5, timeout) * 1000)
            if not box:
                return
            self.page.evaluate(
                self._BOX_JS,
                {"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]},
            )
            if self._highlight_pause > 0:
                self.page.wait_for_timeout(self._highlight_pause)
        except Exception as exc:  # noqa: BLE001
            logger.debug("断言可视化失败(忽略)：%s", exc)

    def click(self, by: str, locator: str, timeout: float = 10, force: bool = False) -> None:
        sel = self._selector(by, locator)
        self._highlight_action(sel, timeout)
        # force=True：跳过 visible/stable/命中测试直接点（用于被遮挡但确实要点的元素）。
        self.page.click(sel, timeout=timeout * 1000, force=force)

    def click_at(self, x: float, y: float) -> None:
        self.page.mouse.click(float(x), float(y))

    def drag(
        self, by: str, locator: str, *, timeout: float = 10,
        to_by: str | None = None, to_locator: str | None = None,
        dx: float | None = None, dy: float | None = None,
        tx: float | None = None, ty: float | None = None,
        steps: int = 10,
    ) -> None:
        src = self.page.locator(self._selector(by, locator)).first
        box = src.bounding_box(timeout=timeout * 1000)
        if not box:
            raise ValueError(f"拖动源元素不可见/无边界：{by}={locator}")
        sx = box["x"] + box["width"] / 2
        sy = box["y"] + box["height"] / 2
        if to_by and to_locator:
            tb = self.page.locator(self._selector(to_by, to_locator)).first.bounding_box(timeout=timeout * 1000)
            if not tb:
                raise ValueError(f"拖动目标元素不可见/无边界：{to_by}={to_locator}")
            ex, ey = tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2
        elif tx is not None and ty is not None:
            ex, ey = float(tx), float(ty)
        elif dx is not None or dy is not None:
            ex, ey = sx + float(dx or 0), sy + float(dy or 0)
        else:
            raise ValueError("拖动需指定 目标元素 / 目标坐标(tx,ty) / 偏移(dx,dy) 之一")
        self.page.mouse.move(sx, sy)
        self.page.mouse.down()
        self.page.mouse.move(ex, ey, steps=max(1, int(steps or 1)))
        self.page.mouse.up()

    def input(
        self, by: str, locator: str, text: str,
        clear_first: bool = True, timeout: float = 10,
    ) -> None:
        sel = self._selector(by, locator)
        self._highlight_action(sel, timeout)
        if clear_first:
            # fill 本身就会 clear；但有些富文本需要先 click() 让 focus 落下
            self.page.fill(sel, text, timeout=timeout * 1000)
        else:
            # 不清空 → type 追加
            self.page.type(sel, text, timeout=timeout * 1000)

    def select_option(
        self, by: str, locator: str, value: str | None = None,
        label: str | None = None, index: int | None = None,
        timeout: float = 10,
    ) -> None:
        sel = self._selector(by, locator)
        self._highlight_action(sel, timeout)
        if value is not None:
            self.page.select_option(sel, value=value, timeout=timeout * 1000)
        elif label is not None:
            self.page.select_option(sel, label=label, timeout=timeout * 1000)
        elif index is not None:
            self.page.select_option(sel, index=index, timeout=timeout * 1000)
        else:
            raise ValueError("select_option 必须至少指定 value / label / index 之一")

    def wait_for(
        self, by: str, locator: str,
        state: Literal["visible", "attached", "hidden", "detached"] = "visible",
        timeout: float = 10,
    ) -> None:
        # 收敛到 .first：定位器命中多个元素时（如页面上叠了多个 toast），
        # Playwright 的 strict 模式会直接抛 "resolved to N elements"。等待/断言
        # 可见性只需要有一个匹配即可，与 get_text/get_attribute 的取值口径保持一致。
        sel = self._selector(by, locator)
        self.page.locator(sel).first.wait_for(state=state, timeout=timeout * 1000)
        # 命中后圈出被断言/等待的元素（hidden/detached 时元素已不在，跳过）。
        if state in ("visible", "attached"):
            self._highlight_assert(sel, timeout)

    def get_text(self, by: str, locator: str, timeout: float = 10) -> str:
        sel = self._selector(by, locator)
        loc = self.page.locator(sel).first
        loc.wait_for(state="visible", timeout=timeout * 1000)
        self._highlight_assert(sel, timeout)
        # 表单控件的“文本”是它的值，不是 innerText —— <input>/<textarea> 的
        # inner_text() 恒为空，直接读会让所有“输入框文本断言”永远失败。
        tag = str(loc.evaluate("el => el.tagName") or "").lower()
        if tag in ("input", "textarea"):
            return loc.input_value()
        if tag == "select":
            return str(loc.evaluate("el => el.value") or "") or loc.inner_text()
        return loc.inner_text()

    def get_attribute(self, by: str, locator: str, name: str, timeout: float = 10) -> str | None:
        loc = self.page.locator(self._selector(by, locator)).first
        loc.wait_for(state="attached", timeout=timeout * 1000)
        return loc.get_attribute(name)

    def press(self, key: str, by: str | None = None, locator: str | None = None, timeout: float = 10) -> None:
        if by and locator:
            self.page.locator(self._selector(by, locator)).first.press(key, timeout=timeout * 1000)
        else:
            self.page.keyboard.press(key)

    def screenshot(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.page.screenshot(path=path, full_page=True)

    def evaluate(self, script: str, *args: Any) -> Any:
        # Playwright 的 evaluate 接受单个 arg 参数，多参打包成 list 传
        if not args:
            return self.page.evaluate(script)
        if len(args) == 1:
            return self.page.evaluate(script, args[0])
        return self.page.evaluate(script, list(args))

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PW context.close 失败（忽略）：%s", exc)
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PW browser.close 失败（忽略）：%s", exc)
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PW playwright.stop 失败（忽略）：%s", exc)
        self._started = False


# ---------------------------------------------------------------------------
# Selenium 实现
# ---------------------------------------------------------------------------
class SeleniumAdapter(WebDriverAdapter):
    """基于 selenium.webdriver 的实现。

    启动参数 config：
        {
            "browser":    "chrome" | "firefox" | "edge" | "safari",  # 默认 chrome
            "headless":   True,
            "remote_url": "http://selenium-grid:4444/wd/hub",         # 可选：走 Selenium Grid
            "arguments":  ["--disable-gpu", "--no-sandbox"],
            "binary":     "/usr/bin/google-chrome",                    # 可选
            "window_size": "1280,800",
        }
    """

    engine = "selenium"

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self._driver = None
        self._started = False

    def _ensure_started(self) -> None:
        if self._started:
            return

        from selenium import webdriver  # noqa: WPS433

        browser = str(self.config.get("browser") or "chrome").lower()
        headless = bool(self.config.get("headless", True))
        remote_url = self.config.get("remote_url")
        arguments = list(self.config.get("arguments") or [])
        binary = self.config.get("binary")
        window_size = self.config.get("window_size")

        # 与 Playwright 保持一致：仅 Linux 无显示服务时强制无头。
        if _must_force_headless(
            headless,
            platform_name=sys.platform,
            display=os.environ.get("DISPLAY"),
            wayland_display=os.environ.get("WAYLAND_DISPLAY"),
        ):
            print(
                "[SeleniumAdapter] 检测到 Linux 无 X11/Wayland 环境，"
                "自动将 headless=false → true（避免 XServer 报错）"
            )
            logger.warning(
                "SeleniumAdapter: Linux 无 X11/Wayland，强制 headless=true（原配置为 false）"
            )
            headless = True

        def _add_common_args(opts):
            if headless:
                # chromium / firefox 都认这个开关（selenium 4.8+）
                opts.add_argument("--headless=new" if browser in ("chrome", "edge") else "-headless")
            if window_size:
                opts.add_argument(f"--window-size={window_size}")
            for a in arguments:
                opts.add_argument(a)
            if binary and hasattr(opts, "binary_location"):
                opts.binary_location = binary
            return opts

        if browser == "chrome":
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            opts = _add_common_args(ChromeOptions())
        elif browser == "firefox":
            from selenium.webdriver.firefox.options import Options as FirefoxOptions
            opts = _add_common_args(FirefoxOptions())
        elif browser == "edge":
            from selenium.webdriver.edge.options import Options as EdgeOptions
            opts = _add_common_args(EdgeOptions())
        elif browser == "safari":
            from selenium.webdriver.safari.options import Options as SafariOptions
            opts = SafariOptions()  # safari 不支持 headless
        else:
            raise ValueError(
                f"Selenium 不支持 browser={browser!r}；可选 chrome/firefox/edge/safari"
            )

        logger.info(
            "启动 Selenium: browser=%s headless=%s remote=%s",
            browser, headless, bool(remote_url),
        )

        if remote_url:
            self._driver = webdriver.Remote(command_executor=remote_url, options=opts)
        else:
            if browser == "chrome":
                self._driver = webdriver.Chrome(options=opts)
            elif browser == "firefox":
                self._driver = webdriver.Firefox(options=opts)
            elif browser == "edge":
                self._driver = webdriver.Edge(options=opts)
            else:
                self._driver = webdriver.Safari(options=opts)

        default_timeout = float(self.config.get("default_timeout") or 30)
        self._driver.implicitly_wait(default_timeout)
        self._started = True

    @property
    def driver(self):
        self._ensure_started()
        return self._driver

    # ---------- By 翻译 ----------
    @staticmethod
    def _by_pair(by: str, locator: str):
        from selenium.webdriver.common.by import By
        b = _normalize_by(by)
        if b == "css":
            return (By.CSS_SELECTOR, locator)
        if b == "xpath":
            return (By.XPATH, locator)
        if b == "id":
            return (By.ID, locator)
        if b == "name":
            return (By.NAME, locator)
        if b == "class":
            return (By.CLASS_NAME, locator)
        if b == "text":
            # Selenium 没有原生的 "text=" 引擎；用 XPath 的 contains 兜底
            # 同时支持用户传 exact "=foo" 做精确匹配（一个简单约定）
            if locator.startswith("="):
                return (By.XPATH, f"//*[normalize-space(text())={locator[1:]!r}]")
            return (By.XPATH, f"//*[contains(normalize-space(text()), {locator!r})]")
        if b == "link":
            return (By.LINK_TEXT, locator)
        if b == "role":
            # Selenium 无原生 ARIA 引擎，用 xpath 近似：匹配 role 属性 + 可见文本。
            # （无障碍名不总等于文本，属尽力而为；Web 主引擎是 Playwright，精确匹配走那边。）
            role, attrs = _parse_role_locator(locator)
            name = attrs.get("name")
            if name:
                return (By.XPATH, f"//*[@role={role!r} and normalize-space()={name!r}]")
            return (By.XPATH, f"//*[@role={role!r}]")
        raise ValueError(f"unsupported by: {b}")

    def _find(self, by: str, locator: str, timeout: float = 10,
              condition: str = "presence"):
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        pair = self._by_pair(by, locator)
        wait = WebDriverWait(self.driver, timeout)
        cond_map = {
            "presence": EC.presence_of_element_located,
            "visible": EC.visibility_of_element_located,
            "clickable": EC.element_to_be_clickable,
        }
        ec = cond_map.get(condition, EC.presence_of_element_located)
        return wait.until(ec(pair))

    # ---------- 实现 ----------
    def goto(self, url: str, timeout: float = 30) -> None:
        self.driver.set_page_load_timeout(timeout)
        self.driver.get(url)

    def get_url(self) -> str:
        return self.driver.current_url

    def get_title(self) -> str:
        return self.driver.title

    def click(self, by: str, locator: str, timeout: float = 10, force: bool = False) -> None:
        if force:
            # 跳过原生可点检查：用 JS 直接触发点击（元素存在即可）。
            el = self._find(by, locator, timeout=timeout, condition="presence")
            self.driver.execute_script("arguments[0].click()", el)
            return
        el = self._find(by, locator, timeout=timeout, condition="clickable")
        el.click()

    def click_at(self, x: float, y: float) -> None:
        from selenium.webdriver.common.action_chains import ActionChains
        # Selenium 只能相对元素/当前位置移动；这里以 <body> 左上角为基准偏移到 (x,y)。
        body = self.driver.find_element("tag name", "body")
        ActionChains(self.driver).move_to_element_with_offset(body, int(x), int(y)).click().perform()

    def drag(
        self, by: str, locator: str, *, timeout: float = 10,
        to_by: str | None = None, to_locator: str | None = None,
        dx: float | None = None, dy: float | None = None,
        tx: float | None = None, ty: float | None = None,
        steps: int = 10,
    ) -> None:
        from selenium.webdriver.common.action_chains import ActionChains
        src = self._find(by, locator, timeout=timeout, condition="visible")
        ac = ActionChains(self.driver)
        if to_by and to_locator:
            tgt = self._find(to_by, to_locator, timeout=timeout, condition="visible")
            ac.drag_and_drop(src, tgt).perform()
        elif dx is not None or dy is not None:
            ac.click_and_hold(src).move_by_offset(int(dx or 0), int(dy or 0)).release().perform()
        elif tx is not None and ty is not None:
            body = self.driver.find_element("tag name", "body")
            ac.click_and_hold(src).move_to_element_with_offset(body, int(tx), int(ty)).release().perform()
        else:
            raise ValueError("拖动需指定 目标元素 / 目标坐标(tx,ty) / 偏移(dx,dy) 之一")

    def input(
        self, by: str, locator: str, text: str,
        clear_first: bool = True, timeout: float = 10,
    ) -> None:
        el = self._find(by, locator, timeout=timeout, condition="visible")
        if clear_first:
            try:
                el.clear()
            except Exception as exc:  # noqa: BLE001
                logger.debug("clear 失败（忽略）：%s", exc)
        el.send_keys(text)

    def select_option(
        self, by: str, locator: str, value: str | None = None,
        label: str | None = None, index: int | None = None,
        timeout: float = 10,
    ) -> None:
        from selenium.webdriver.support.ui import Select
        el = self._find(by, locator, timeout=timeout, condition="visible")
        sel = Select(el)
        if value is not None:
            sel.select_by_value(value)
        elif label is not None:
            sel.select_by_visible_text(label)
        elif index is not None:
            sel.select_by_index(index)
        else:
            raise ValueError("select_option 必须至少指定 value / label / index 之一")

    def wait_for(
        self, by: str, locator: str,
        state: Literal["visible", "attached", "hidden", "detached"] = "visible",
        timeout: float = 10,
    ) -> None:
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        pair = self._by_pair(by, locator)
        wait = WebDriverWait(self.driver, timeout)
        if state in ("visible",):
            wait.until(EC.visibility_of_element_located(pair))
        elif state == "attached":
            wait.until(EC.presence_of_element_located(pair))
        elif state == "hidden":
            wait.until(EC.invisibility_of_element_located(pair))
        elif state == "detached":
            # selenium 没直接对应的 EC，用 staleness_of 也不合适（需要已拿到引用）
            # 简化做法：轮询直到 find 拿不到
            end = time.time() + timeout
            while time.time() < end:
                try:
                    self.driver.find_element(*pair)
                    time.sleep(0.3)
                except Exception:
                    return
            raise TimeoutError(f"等待 detached 超时：{by}={locator}")
        else:
            raise ValueError(f"未知 state: {state}")

    def get_text(self, by: str, locator: str, timeout: float = 10) -> str:
        el = self._find(by, locator, timeout=timeout, condition="visible")
        # 与 Playwright 口径一致：表单控件读 value，普通元素读文本。
        tag = str(el.tag_name or "").lower()
        if tag in ("input", "textarea", "select"):
            return el.get_attribute("value") or ""
        return el.text

    def get_attribute(self, by: str, locator: str, name: str, timeout: float = 10) -> str | None:
        el = self._find(by, locator, timeout=timeout)
        return el.get_attribute(name)

    def press(self, key: str, by: str | None = None, locator: str | None = None, timeout: float = 10) -> None:
        from selenium.webdriver.common.keys import Keys
        # "Escape"→Keys.ESCAPE / "Enter"→Keys.ENTER / "Tab"→Keys.TAB；单字符原样发送
        k = getattr(Keys, str(key).upper(), str(key))
        if by and locator:
            self._find(by, locator, timeout=timeout).send_keys(k)
        else:
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.driver).send_keys(k).perform()

    def screenshot(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.driver.save_screenshot(path)

    def evaluate(self, script: str, *args: Any) -> Any:
        return self.driver.execute_script(script, *args)

    def close(self) -> None:
        try:
            if self._driver is not None:
                self._driver.quit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Selenium driver.quit 失败（忽略）：%s", exc)
        self._started = False


# ---------------------------------------------------------------------------
# 工厂：给 session 层用
# ---------------------------------------------------------------------------
def build_adapter(engine: str, config: Optional[dict] = None) -> WebDriverAdapter:
    """按 engine 字符串构造 adapter。

    engine = "playwright" | "selenium"；不区分大小写。
    config 是对应 adapter 的启动参数（见各类 docstring）。
    """
    e = (engine or "").strip().lower()
    if e in ("playwright", "pw"):
        return PlaywrightAdapter(config)
    if e in ("selenium", "webdriver"):
        return SeleniumAdapter(config)
    raise ValueError(f"未知 web engine={engine!r}；支持 playwright / selenium")


def pick_default_engine() -> str:
    """如果没指定 engine：优先挑 playwright（新 & 快），装不到就降级到 selenium。"""
    try:
        import playwright  # noqa: F401
        return "playwright"
    except ImportError:
        return "selenium"
