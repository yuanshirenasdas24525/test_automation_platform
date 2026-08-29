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

from runners.context.execution_context import ExecutionContext
from runners.app.session import AppSession
from runners.protocol import BaseStepRunner, StepResult
from utils.value_resolver import resolve_value

logger = logging.getLogger(__name__)


# ============================================================
# 公共工具：把 ctx 的变量池 + sql:/function: 前缀一并解析
# ============================================================
# 说明：老版本只做 ${var} 替换（rep_expr），现在改成走 utils.value_resolver，
# 这样 config.locator / config.value / config.equals 等字段都能写成：
#   - "sql:select name from users where id=${uid}"   → 真正查 target DB
#   - "function:rand_phone()"                        → 调 function_executor 注册的函数
#   - "user_${idx}"                                  → 纯 ${var} 替换（兼容老用法）
# 非字符串输入原样返回。sql: 前缀需要 ctx.vars['_db'] 提前注入 DB 连接，
# 不然会显式抛错（静默失败会让断言错位极难排查）。
def _resolve_str(value: Any, ctx: ExecutionContext) -> Any:
    return resolve_value(value, ctx)


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
    """App 截图。

    config = {
        "name": "shot.png",                 # 文件名（可选，默认 screenshot.png）
        "path": "/Users/x/Downloads/Shots", # 落盘位置（可选）
    }

    `path` 容错处理（用户最常踩的坑）：
      - 不传：默认落到 data/screenshots/<ts>_<name>，一个 case 跑多次不会冲掉；
      - 是目录（已存在的目录、或以 / 结尾、或没有扩展名）：把 name 拼进去；
      - 是文件路径：直接用，缺扩展名时补 .png（避免 driver 写出无扩展名文件
        被相册 / 系统识别失败）。

    历史坑：之前直接 `driver.save_screenshot(config['path'])`，用户传一个
    目录路径时，driver 会尝试把目录名当文件名写入，要么报错要么落了一个空文件，
    用户看到"截图传入指定地址没内容"。
    """
    step_types = ("app_screenshot",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        import os

        raw_name = _resolve_str(config.get("name") or "screenshot.png", ctx)
        # name 也兜底加 .png
        name = str(raw_name)
        if not os.path.splitext(name)[1]:
            name += ".png"

        raw_path = _resolve_str(config.get("path"), ctx)
        path = self._resolve_screenshot_path(raw_path, name)

        # 确保父目录存在；driver.save_screenshot 在父目录不存在时会失败
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)

        driver = session.driver
        ok = driver.save_screenshot(path)
        # 验证文件真的写出来了 + 非 0 字节，否则给出清晰错误
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise RuntimeError(
                f"app_screenshot: 截图文件未写出或为空 -> {path}（driver.save_screenshot 返回 {ok!r}）。"
                f"检查 path 是不是可写、Appium driver 是否健康。"
            )

        result.action = f"screenshot -> {path}"
        result.attachments.append({"name": name, "path": path, "type": "image/png"})

    @staticmethod
    def _resolve_screenshot_path(raw_path: str | None, name: str) -> str:
        """把用户给的 path / 默认值 解析成"指向真实截图文件的路径"。"""
        import os

        if not raw_path:
            # 默认落盘：data/screenshots/<ts>_<name>，文件级唯一
            return f"data/screenshots/{int(time.time()*1000)}_{name}"

        path = str(raw_path).strip()
        # 是目录的几个判断方式：以 / 结尾，或当前真的是目录，或 basename 没扩展名
        is_dir_like = (
            path.endswith(os.sep)
            or path.endswith("/")
            or (os.path.exists(path) and os.path.isdir(path))
            or os.path.splitext(os.path.basename(path))[1] == ""
        )
        if is_dir_like:
            # 把 name 拼上去；为避免覆盖前一次截图，加 ts 前缀
            return os.path.join(path, f"{int(time.time()*1000)}_{name}")
        # 是文件路径：缺扩展名补 .png
        if not os.path.splitext(path)[1]:
            path += ".png"
        return path


# ============================================================
# 6. app_launch / app_close / app_back / app_press
# ============================================================
class AppLaunchStepRunner(BaseStepRunner):
    """启动 / 切到前台一个应用。

    config = {
        "appPackage": "com.example.app",   # Android
        "appActivity": ".MainActivity",     # Android（可选；不填走 launchable activity）
        "bundleId": "com.example.app",     # iOS（与 appPackage 二选一）
        "noReset": False,                  # caps；只在首次启动 driver 时生效
    }

    两种场景：
      1) driver **还没起**：把 config merge 进 session.caps，然后访问 session.driver
         触发懒启动；如果 caps 里有 appPackage/appActivity，Appium 会自动拉起应用。
      2) driver **已经起了**（典型：前面跑过 app_install / app_tap 等）：W3C 的
         session caps 不能再改，单纯更新 session.caps 不生效——必须显式
         `driver.activate_app(appId)` 才会真正把应用切前台 / 启动。
         之前这里只是 `_ = session.driver` 拿一下旧 driver 就返回了，结果用例显示
         passed 但应用根本没启动，是个静默坑。
    """
    step_types = ("app_launch",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        resolved = {k: _resolve_str(v, ctx) for k, v in config.items()}

        # 取 appPackage / bundleId，按"先看 step.config，再看 session.caps，再问 driver"
        # 三级回退。这样兼容三种历史写法：
        #   (a) step.config 显式给 appPackage/bundleId（最常见）
        #   (b) 只在 case-level caps 里给了 appPackage（前面 cold-start 时灌进 session.caps）
        #   (c) 完全没指定，但 driver 已经知道当前是哪个 app（典型：前面 step 操作过同一个 app）
        def _pick_app_id(d: dict) -> str | None:
            for k in ("appPackage", "appium:appPackage", "bundleId", "appium:bundleId"):
                v = d.get(k)
                if v:
                    return str(v)
            return None

        def _pick_app_activity(d: dict) -> str | None:
            for k in ("appActivity", "appium:appActivity"):
                v = d.get(k)
                if v:
                    return str(v)
            return None

        app_id = _pick_app_id(resolved) or _pick_app_id(session.caps)
        app_activity = _pick_app_activity(resolved) or _pick_app_activity(session.caps)

        # 在做任何动作之前先把"是不是冷启动"记下来 —— 进入分支后 session.started
        # 会变 True，再读就分不清了。
        was_cold = not session.started

        if was_cold:
            # —— 场景 1：driver 还没起，首次启动 ——
            # caps 此时改还来得及，会被 _merge_device_caps 拿去喂 webdriver.Remote
            if resolved:
                session.caps.update(resolved)
            _ = session.driver  # 触发懒启动；caps 里有 appPackage 时 Appium 会自动拉起
            result.action = f"launch {app_id or '(default)'} (cold start)"
        else:
            # —— 场景 2：driver 在跑，需要主动 activate ——
            # 如果 step.config 和 session.caps 都没有 app_id，再问一下 driver
            # 当前前台是啥（Android 才有 current_package；iOS 没有同名属性）。
            if not app_id:
                try:
                    cp = getattr(session.driver, "current_package", None)
                    if cp:
                        app_id = str(cp)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("driver.current_package 取不到：%s", exc)

            if not app_id:
                # 真的什么都没有：降级为"驱动级 launch_app"或干脆 no-op，避免误判 passed。
                # Appium 2 的 driver.launch_app() 已 deprecated；这里就 no-op + 记日志，
                # 让用户看 input_data 里 cold_start=False / app_id=null 自己排查。
                logger.warning(
                    "app_launch step 在 driver 已启动状态下被调用，但找不到 appPackage/"
                    "bundleId（step.config 与 session.caps 都为空，driver 也没暴露 "
                    "current_package）。本步退化为 no-op。"
                )
                result.action = "launch (no-op: 缺 app_id)"
            else:
                # —— 关键修复：terminate_app 之后 activate_app 在 Android 上经常失效 ——
                # activate_app 内部对刚被强杀的进程依赖 monkey LAUNCHER intent，部分 ROM /
                # Android 版本上反应不一致；表现就是"调了好像没事，应用却没起来"。
                # 解决方案：Android 优先用 ActivityManager 直接拉 activity（最可靠），
                # 兜底再回到 activate_app；iOS 没这套，直接 activate_app。
                platform = str(session.caps.get("platformName") or "").lower()

                # —— 幂等：一条用例=一个测试点，App 只需首次启动，后续用例复用同一个运行中的 App ——
                # 若 App 已在前台且未要求强制重启，就跳过，避免每条用例都 start_activity 重启 App。
                # 需要每条用例回到干净启动态的场景，在该 step.config 里设 force_relaunch=true。
                _force_relaunch = str(resolved.get("force_relaunch") or "").strip().lower() in ("1", "true", "yes", "on")
                if not _force_relaunch and platform == "android":
                    try:
                        _cur = getattr(session.driver, "current_package", None)
                    except Exception:  # noqa: BLE001
                        _cur = None
                    if _cur and str(_cur) == app_id:
                        result.action = f"already running {app_id} (skip relaunch)"
                        return

                # force_relaunch：先 terminate 杀进程再重启，保证回到干净启动态
                # （仅 start_activity 对 RN 等 App 未必登出/重置；kill 后重启才是干净的登录页）。
                if _force_relaunch and app_id:
                    try:
                        session.driver.terminate_app(app_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("force_relaunch terminate 失败（忽略）：%s", exc)

                launched = False

                if platform == "android" and app_activity:
                    # 首选：start_activity(appPackage, appActivity) —— 直接发 am start，
                    # 不走 monkey；对 terminate 之后的"冷启动应用"最稳。
                    try:
                        session.driver.start_activity(app_id, app_activity)
                        result.action = f"start_activity {app_id}/{app_activity}"
                        launched = True
                    except (AttributeError, NotImplementedError) as exc:
                        # 老 Selenium / Appium python client 没这个方法，再退回 mobile: startActivity
                        logger.debug("driver.start_activity 不可用，尝试 mobile: startActivity：%s", exc)
                        try:
                            session.driver.execute_script(
                                "mobile: startActivity",
                                {"intent": f"{app_id}/{app_activity}"},
                            )
                            result.action = f"mobile:startActivity {app_id}/{app_activity}"
                            launched = True
                        except Exception as exc2:  # noqa: BLE001
                            logger.warning(
                                "mobile: startActivity 也失败，回退 activate_app：%s", exc2
                            )

                if not launched:
                    # 兜底：iOS / 没给 appActivity / start_activity 抛错时
                    session.driver.activate_app(app_id)
                    result.action = f"activate {app_id}"

        result.input_data = {
            "app_id": app_id,
            "cold_start": was_cold,
            **{k: v for k, v in resolved.items()
               if k in ("appPackage", "appActivity", "bundleId", "noReset")},
        }


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
# 7. 扩展 app step runner：install / uninstall / activate / terminate /
#    background / orientation / hide_keyboard
#
# 这些是日常跑 case 时高频用到但老 v1 AppAction 没好好暴露的能力。全部走 Appium
# Python client 的原生方法，尽量**薄**：我们不做平台差异抹平（Android / iOS 行为
# 本来就不一样），把平台差异留给用例作者选对 config。
# ============================================================


def _require_app_id(config: dict, *, for_step: str) -> str:
    """取 appPackage（Android）或 bundleId（iOS）。哪个在就用哪个，都没有就报错。"""
    app_id = (
        config.get("appPackage")
        or config.get("bundleId")
        or config.get("appium:appPackage")
        or config.get("appium:bundleId")
    )
    if not app_id:
        raise ValueError(
            f"{for_step} 缺少 config.appPackage / config.bundleId，"
            "Android 填 appPackage（e.g. com.example.app），iOS 填 bundleId。"
        )
    return str(app_id)


class AppInstallStepRunner(BaseStepRunner):
    """安装 apk / ipa。

    config = {
        "app_path": "/path/to/app.apk" | "http://.../app.apk",   # 必填
    }

    Appium 支持本地路径和 URL；URL 会由 Appium server 下载。

    本地路径处理（v2 加固）：
      - 如果是相对路径 / basename，自动尝试在 ProjectPaths.APP_PACKAGES_DIR
        下查找同名文件；找到就替换成可用的绝对路径。
      - 如果绝对路径不存在，但 basename 在 APP_PACKAGES_DIR 下能找到，
        也走"自愈"替换（兼容老数据：file_path 里多了 server/ 段、或者项目挪了位置）。
      - 都救不了再抛清晰错误，避免把锅甩给 Appium 的"does not exist or
        is not accessible"，让用户看到真正用了哪个路径。

    URL 路径（http:// / https://）不动，交给 Appium server 自己下载。
    """
    step_types = ("app_install",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        app_path = _resolve_str(config.get("app_path") or config.get("app"), ctx)
        if not app_path:
            raise ValueError("app_install 缺少 config.app_path")

        resolved = self._resolve_app_path(str(app_path))
        session.driver.install_app(resolved)
        result.action = f"install_app {resolved}"
        result.input_data = {"app_path": resolved, "raw": app_path}

    @staticmethod
    def _resolve_app_path(app_path: str) -> str:
        """把 config.app_path 解析成 Appium 真正可用的本地绝对路径或 URL。

        分两类：
          1. URL（http/https）：原样返回，由 Appium 自己下载。
          2. 本地路径：尝试 1) 原样存在 → 用；2) 相对 BASE_DIR 解析 → 用；
             3) APP_PACKAGES_DIR 下按 basename 找 → 用；都没 → 抛清晰错误。
        """
        from pathlib import Path

        s = (app_path or "").strip()
        if not s:
            raise ValueError("app_install: app_path 为空")

        low = s.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return s  # 远程 URL 不动，Appium server 自己下载

        # 延迟 import 避免循环依赖（runners 不直连 config 里 ProjectPaths 也行）
        try:
            from config.settings import ProjectPaths
            base_dir = Path(ProjectPaths.BASE_DIR)
            packages_dir = Path(ProjectPaths.APP_PACKAGES_DIR)
        except Exception:
            base_dir = None
            packages_dir = None

        p = Path(s)

        # 1) 原样存在
        if p.exists():
            return str(p.resolve())

        # 2) 相对路径 → 锚 BASE_DIR
        if not p.is_absolute() and base_dir is not None:
            cand = (base_dir / p).resolve()
            if cand.exists():
                return str(cand)

        # 3) basename 自愈：APP_PACKAGES_DIR 下找同名文件
        if packages_dir is not None and packages_dir.exists():
            cand = packages_dir / p.name
            if cand.exists():
                logger.warning(
                    "app_install: 原路径找不到，按文件名 '%s' 自愈到 %s（建议把数据库 file_path 修一下）",
                    p.name, cand,
                )
                return str(cand.resolve())

        # 4) 都救不了：清晰报错
        hint_dir = str(packages_dir) if packages_dir else "data/app_packages"
        raise FileNotFoundError(
            f"app_install: APK/IPA 文件找不到 —— 路径='{s}'。"
            f"已尝试原路径、相对项目根、以及 {hint_dir} 下按文件名查找；"
            f"请确认：(1) 安装包是否还在；(2) FastAPI 与 Appium server 是否能访问到同一份文件。"
        )


class AppUninstallStepRunner(BaseStepRunner):
    """卸载应用：传 appPackage（Android）或 bundleId（iOS）。"""
    step_types = ("app_uninstall",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        app_id = _resolve_str(_require_app_id(config, for_step="app_uninstall"), ctx)
        # Appium 2 统一是 remove_app；老版本也叫 remove_app。
        session.driver.remove_app(str(app_id))
        result.action = f"remove_app {app_id}"
        result.input_data = {"app_id": app_id}


class AppActivateStepRunner(BaseStepRunner):
    """把应用从后台拉到前台（或启动），参数同 uninstall。

    和 app_launch 的区别：activate 是把**已安装**的应用唤到前台，不会重新走完整
    的 session caps 初始化；app_launch 第一次调用会建 session。
    """
    step_types = ("app_activate",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        app_id = _resolve_str(_require_app_id(config, for_step="app_activate"), ctx)
        session.driver.activate_app(str(app_id))
        result.action = f"activate_app {app_id}"
        result.input_data = {"app_id": app_id}


class AppTerminateStepRunner(BaseStepRunner):
    """杀掉应用进程（不卸载）。参数同 uninstall。"""
    step_types = ("app_terminate",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        app_id = _resolve_str(_require_app_id(config, for_step="app_terminate"), ctx)
        session.driver.terminate_app(str(app_id))
        result.action = f"terminate_app {app_id}"
        result.input_data = {"app_id": app_id}


class AppBackgroundStepRunner(BaseStepRunner):
    """把当前应用切后台 N 秒；-1 表示永久（不自动回前台）。

    config = {"seconds": 3}  # 默认 3 秒；Appium 文档建议 >= 1
    """
    step_types = ("app_background",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        seconds = int(config.get("seconds") if config.get("seconds") is not None else 3)
        session.driver.background_app(seconds)
        result.action = f"background_app {seconds}s"
        result.input_data = {"seconds": seconds}


class AppOrientationStepRunner(BaseStepRunner):
    """设置屏幕方向。

    config = {"orientation": "PORTRAIT" | "LANDSCAPE"}

    Appium 规范只接受这两个值（大写）。我们这里不做严格校验，让 Appium 去报；
    但做一次大写规范化避免用户写 "portrait" 就报错。
    """
    step_types = ("app_orientation",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        raw = _resolve_str(config.get("orientation"), ctx)
        if not raw:
            raise ValueError("app_orientation 缺少 config.orientation（PORTRAIT / LANDSCAPE）")
        orientation = str(raw).upper()
        session.driver.orientation = orientation
        result.action = f"set orientation={orientation}"
        result.input_data = {"orientation": orientation}


class AppHideKeyboardStepRunner(BaseStepRunner):
    """收起软键盘。iOS 上 Appium 会抛 "keyboard not present"，我们吞掉这一类异常。

    config 可选：
      {"key_name": "Done"}   # 仅 iOS；点哪个按键收起键盘
    """
    step_types = ("app_hide_keyboard",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        key_name = _resolve_str(config.get("key_name"), ctx)
        try:
            if key_name:
                session.driver.hide_keyboard(key_name=str(key_name))
            else:
                session.driver.hide_keyboard()
            result.action = "hide_keyboard"
        except Exception as exc:  # noqa: BLE001
            # Appium 在键盘没弹出时会抛错。这不算真的失败 —— 我们把它降级成 info
            # 记录进 output_data，让用例继续跑。
            logger.info("hide_keyboard 被忽略（键盘可能本就未弹出）：%s", exc)
            result.action = "hide_keyboard (no-op)"
            result.output_data = f"ignored: {type(exc).__name__}: {exc}"


# ============================================================
# 9. app_assert_text - 文本断言
# ============================================================
class AppAssertTextStepRunner(BaseStepRunner):
    """对某个元素的可见文本做断言。

    config = {
        "by":       "id" | "xpath" | ...,     # 必填，定位方式
        "locator":  "com.example:id/title",   # 必填，定位表达式
        "equals":   "登录成功",                # 与下面三选一（优先级如排列）
        "contains": "成功",
        "not_contains": "错误",
        "timeout":  10,                       # 找元素最大等待秒数（可选）
    }

    判定优先级：equals > contains > not_contains。
    三者都没填等价于"只校验元素找得到 + 文本非空"——这种退化模式比报错对用户更友好，
    实务里用得不多，但避免把"UI 已经出来了但懒得写期望值"的 case 直接判红。
    """

    step_types = ("app_assert_text",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        session = AppSession.require(ctx)
        config = step.get("config") or {}
        locator = _resolve_str(config.get("locator"), ctx)
        el = _find_element(session, {**config, "locator": locator})
        actual = el.text if hasattr(el, "text") else ""

        # 三种模式，按优先级依次判定；只要有其中一个填了就以它为准
        equals = _resolve_str(config.get("equals"), ctx)
        contains = _resolve_str(config.get("contains"), ctx)
        not_contains = _resolve_str(config.get("not_contains"), ctx)

        result.target = f"{config.get('by')}={locator}"
        result.output_data = actual
        result.input_data = {
            "equals": equals,
            "contains": contains,
            "not_contains": not_contains,
        }

        if equals is not None and equals != "":
            result.action = f"assert_text equals {equals!r}"
            assert str(actual) == str(equals), (
                f"文本断言失败：期望 == {equals!r}，实际 = {actual!r}"
            )
            return

        if contains is not None and contains != "":
            result.action = f"assert_text contains {contains!r}"
            assert str(contains) in str(actual), (
                f"文本断言失败：期望包含 {contains!r}，实际 = {actual!r}"
            )
            return

        if not_contains is not None and not_contains != "":
            result.action = f"assert_text not_contains {not_contains!r}"
            assert str(not_contains) not in str(actual), (
                f"文本断言失败：不应包含 {not_contains!r}，实际 = {actual!r}"
            )
            return

        # 都没填：降级为"文本非空"断言
        result.action = "assert_text not_empty (degraded)"
        assert actual not in (None, ""), (
            f"文本断言失败：expected/contains/not_contains 都为空，且元素文本也为空"
        )


# ============================================================
# 工厂：一次性返回所有 app step runner，供 dispatcher 注册
# ============================================================
def build_app_runners() -> list[BaseStepRunner]:
    return [
        # 核心交互
        AppTapStepRunner(),
        AppInputStepRunner(),
        AppSwipeStepRunner(),
        AppWaitStepRunner(),
        AppScreenshotStepRunner(),
        # 生命周期
        AppLaunchStepRunner(),
        AppCloseStepRunner(),
        AppBackStepRunner(),
        AppPressStepRunner(),
        # 扩展能力（安装 / 卸载 / 激活 / 杀进程 / 后台 / 方向 / 键盘）
        AppInstallStepRunner(),
        AppUninstallStepRunner(),
        AppActivateStepRunner(),
        AppTerminateStepRunner(),
        AppBackgroundStepRunner(),
        AppOrientationStepRunner(),
        AppHideKeyboardStepRunner(),
        # 断言
        AppAssertTextStepRunner(),
    ]
