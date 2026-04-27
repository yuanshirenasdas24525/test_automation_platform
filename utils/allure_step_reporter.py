# -*- coding:utf-8 -*-
"""把一条 step 的详细信息塞进 Allure 报告。

老 dispatcher 跑 step 时，Allure 报告里只能看到 pytest 默认拼出来的"测试方法名"
那一层；step 里的 `step_name / action / target / input_data / output_data /
extracted / 截图` 全都丢掉了。这个模块负责：

  - 提供 `with allure_step(name)` 上下文（allure 没装时就 no-op，方便单测）；
  - `attach_step_details(result)` 把 StepResult 里的关键字段以合适的 attachment
    类型（JSON / TEXT / PNG）挂到当前 allure step 下；
  - `capture_failure_screenshot(ctx, step, result)` 在 web/app step 失败时自动
    截图，文件存到 data/screenshots/，同时塞进 result.attachments + allure。

设计原则：所有 allure 调用都在 try/except 里，import 失败 / 在 pytest 之外被
调用都不能阻断 case 执行。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from typing import Any, Iterator

# 不要在模块顶部直接 import allure —— 离线单测环境可能没装。
# 全部走 lazy import + 异常吞掉。
logger = logging.getLogger(__name__)


def _try_import_allure():
    try:
        import allure  # type: ignore  # noqa: WPS433
        return allure
    except Exception:  # noqa: BLE001
        return None


@contextlib.contextmanager
def allure_step(name: str) -> Iterator[None]:
    """allure.step 的容错版包装。allure 不可用时直接 yield 不报错。"""
    allure = _try_import_allure()
    if allure is None:
        yield
        return
    try:
        with allure.step(name):
            yield
    except Exception:
        # 这里不吞用例自己的异常 —— allure.step 把异常 re-raise 出来是预期行为
        raise


def _attach_text(name: str, content: str) -> None:
    allure = _try_import_allure()
    if allure is None or not content:
        return
    try:
        allure.attach(content, name, allure.attachment_type.TEXT)
    except Exception as exc:  # noqa: BLE001
        logger.debug("allure.attach text 失败（忽略）：%s", exc)


def _attach_json(name: str, payload: Any) -> None:
    """payload 不是 str 就 json.dumps；任何序列化失败兜底成 repr。"""
    allure = _try_import_allure()
    if allure is None or payload is None:
        return
    try:
        if isinstance(payload, (dict, list, tuple)):
            body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        elif isinstance(payload, (str, bytes)):
            body = payload if isinstance(payload, str) else payload.decode("utf-8", "replace")
            # 字符串包成 JSON 字符串看起来更整齐，但如果是已序列化 JSON 就直接贴
            if not (body.startswith("{") or body.startswith("[")):
                body = json.dumps({"value": body}, ensure_ascii=False, indent=2)
        else:
            body = json.dumps({"repr": repr(payload)}, ensure_ascii=False, indent=2)
        allure.attach(body, name, allure.attachment_type.JSON)
    except Exception as exc:  # noqa: BLE001
        logger.debug("allure.attach json 失败（忽略）：%s", exc)


def _attach_image_file(name: str, path: str) -> None:
    allure = _try_import_allure()
    if allure is None or not path or not os.path.isfile(path):
        return
    try:
        allure.attach.file(path, name, attachment_type=allure.attachment_type.PNG)
    except Exception as exc:  # noqa: BLE001
        logger.debug("allure.attach image 失败（忽略）：%s", exc)


def attach_step_details(result) -> None:
    """把 StepResult 关键字段挂到当前 allure step。

    在 with allure_step(name) 块里调。字段为空就跳过。
    """
    if result is None:
        return
    # 概要：一条 KV 总览，方便不展开附件就能看出动作、定位器、状态、耗时。
    summary_lines = []
    if result.action:
        summary_lines.append(f"action  : {result.action}")
    if result.target:
        summary_lines.append(f"target  : {result.target}")
    summary_lines.append(f"status  : {result.status.value if hasattr(result.status, 'value') else result.status}")
    if result.duration_ms:
        summary_lines.append(f"duration: {result.duration_ms} ms")
    if summary_lines:
        _attach_text("step_summary", "\n".join(summary_lines))

    # 输入 / 输出 / extract：能 JSON 化就 JSON 化
    if result.input_data not in (None, "", {}, []):
        _attach_json("input_data", result.input_data)
    if result.output_data not in (None, "", {}, []):
        _attach_json("output_data", result.output_data)
    if result.extracted:
        _attach_json("extracted_vars", result.extracted)

    # 错误：失败 / 异常都把 message + traceback 挂上去
    if result.error_message:
        _attach_text("error_message", result.error_message)
    if result.traceback:
        _attach_text("traceback", result.traceback)

    # Runner 自己塞进来的附件（截图等）
    for att in (result.attachments or []):
        if not isinstance(att, dict):
            continue
        path = att.get("path")
        name = att.get("name") or os.path.basename(str(path or "attachment"))
        att_type = (att.get("type") or "").lower()
        if "image" in att_type or (path and str(path).lower().endswith((".png", ".jpg", ".jpeg"))):
            _attach_image_file(name, path)
        elif path and os.path.isfile(path):
            # 非图片附件按文本兜底（防止 allure 报告里啥也看不到）
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    _attach_text(name, fh.read())
            except Exception:  # noqa: BLE001
                pass


def capture_failure_screenshot(ctx, step: dict, result) -> None:
    """web/app step 失败时自动截图，存盘 + 塞进 result.attachments + 挂到 allure。

    策略：
      - 只在 result.status ∈ {FAILED, ERROR} 时截
      - step_type 以 web_ 开头：从 ctx 取 WebSession.adapter，调 adapter.screenshot(path)
      - step_type 以 app_ 开头：从 ctx 取 AppSession.driver，调 driver.save_screenshot(path)
      - 任意失败都吞掉，不能因为截图失败把 case 翻得更红
    """
    try:
        from runners.protocol import StepStatus  # noqa: WPS433
    except Exception:  # noqa: BLE001
        return
    if result is None or result.status not in (StepStatus.FAILED, StepStatus.ERROR):
        return

    step_type = str(step.get("step_type") or "")
    if not (step_type.startswith("web_") or step_type.startswith("app_")):
        return

    # 落盘路径：data/screenshots/{ts}_{step_id}_{step_type}.png
    try:
        screenshots_dir = os.path.abspath(os.path.join(os.getcwd(), "data", "screenshots"))
        os.makedirs(screenshots_dir, exist_ok=True)
        ts = int(time.time() * 1000)
        sid = step.get("id") or step.get("step_order") or "x"
        filename = f"{ts}_step{sid}_{step_type}_failure.png"
        path = os.path.join(screenshots_dir, filename)
    except Exception as exc:  # noqa: BLE001
        logger.debug("screenshots 目录准备失败（跳过截图）：%s", exc)
        return

    saved = False
    try:
        if step_type.startswith("web_"):
            from runners.web.session import WebSession  # noqa: WPS433
            ws = WebSession.from_ctx(ctx)
            if ws is not None and ws._adapter is not None:  # type: ignore[attr-defined]
                ws.adapter.screenshot(path)
                saved = True
        else:
            from runners.app.session import AppSession  # noqa: WPS433
            asess = AppSession.from_ctx(ctx)
            if asess is not None and asess.started:
                asess.driver.save_screenshot(path)
                saved = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("失败截图采集异常（已忽略）：%s", exc)
        return

    if not saved:
        return

    # 塞进 result.attachments 让 pytest_runtest_makereport 那侧也能看到
    try:
        result.attachments.append({"name": "失败截图", "path": path, "type": "image/png"})
    except Exception:  # noqa: BLE001
        pass
    _attach_image_file("失败截图", path)
