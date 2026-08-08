"""正式 v2 执行链路的旁路技术上下文采集器。"""
from __future__ import annotations

import json
import platform as host_platform
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from runners.context.execution_context import ExecutionContext
from runners.protocol import StepResult


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SENSITIVE_RE = re.compile(
    r"(?i)(password|passwd|secret|authorization|cookie|access_token|refresh_token|cvv|cvc)",
)


def _redact(value: Any, key: str = "") -> Any:
    if key and _SENSITIVE_RE.search(key):
        return "***"
    if isinstance(value, dict):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:500]]
    if isinstance(value, str):
        return re.sub(r"(?i)\bBearer\s+\S+", "Bearer ***", value)[:64_000]
    return value


class UIContextCollector:
    """只旁路观察，不改变 Runner 返回状态；所有异常都降级为 limitation。"""

    def __init__(self, ctx: ExecutionContext) -> None:
        self.ctx = ctx
        self.started_monotonic = time.monotonic()
        self.sequence_no = 0
        self.events: list[dict[str, Any]] = []
        self.limitations: list[str] = []
        self._step_state: dict[int, dict[str, Any]] = {}
        self._environment_emitted: set[str] = set()
        report_id = str(ctx.get_var("_report_id") or "unbound")
        self.root = _PROJECT_ROOT / "data" / "ui_recordings" / "execution" / f"report_{report_id}"

    def _emit(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        *,
        step_id: int | None,
        severity: str = "info",
    ) -> dict[str, Any]:
        self.sequence_no += 1
        event = {
            "event_key": uuid.uuid4().hex,
            "local_sequence_no": self.sequence_no,
            "event_type": event_type,
            "source": source,
            "severity": severity,
            "step_id": step_id,
            "occurred_at": datetime.now().isoformat(),
            "monotonic_ms": int((time.monotonic() - self.started_monotonic) * 1000),
            "payload": _redact(payload),
        }
        self.events.append(event)
        return event

    def _capture_screenshot(self, step: dict[str, Any], phase: str) -> str | None:
        step_order = int(step.get("step_order") or 0)
        target = self.root / f"step_{step_order}_{phase}_{uuid.uuid4().hex[:8]}.png"
        try:
            web_session = self.ctx.get_var("_web_session")
            adapter = getattr(web_session, "_adapter", None) if web_session is not None else None
            if adapter is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                adapter.screenshot(str(target))
                return str(target.relative_to(_PROJECT_ROOT))
            app_session = self.ctx.get_var("_app_session")
            driver = getattr(app_session, "_driver", None) if app_session is not None else None
            if driver is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not driver.save_screenshot(str(target)):
                    raise RuntimeError("Appium save_screenshot 返回失败")
                return str(target.relative_to(_PROJECT_ROOT))
        except Exception as exc:  # noqa: BLE001
            self.limitations.append(f"step#{step_order} {phase} 截图降级：{exc}")
        return None

    def _capture_environment(self, step_id: int | None) -> None:
        """按执行端、浏览器和移动设备分别采集一次环境快照。"""
        if "host" not in self._environment_emitted:
            self._emit(
                "environment.snapshot",
                "environment",
                {
                    "runtime": {
                        "os": host_platform.system(),
                        "os_release": host_platform.release(),
                        "machine": host_platform.machine(),
                        "python": sys.version.split()[0],
                    },
                    "network": {
                        "measurement": "browser_network_information_or_recorded_exchange",
                        "limitation": "非浏览器执行无法直接测得客户端网络速度",
                    },
                },
                step_id=step_id,
            )
            self._environment_emitted.add("host")

        web_session = self.ctx.get_var("_web_session")
        adapter = getattr(web_session, "_adapter", None) if web_session is not None else None
        adapter_started = bool(getattr(adapter, "_started", False)) if adapter is not None else False
        if adapter is not None and adapter_started and "browser" not in self._environment_emitted:
            try:
                page = getattr(adapter, "_page", None)
                driver = getattr(adapter, "_driver", None)
                browser_payload: dict[str, Any] = {
                    "engine": getattr(adapter, "engine", None),
                    "configured_browser": (getattr(adapter, "config", {}) or {}).get("browser"),
                    "url": adapter.get_url(),
                    "title": adapter.get_title(),
                }
                if page is not None:
                    browser = getattr(adapter, "_browser", None)
                    browser_payload.update(
                        {
                            "browser_version": getattr(browser, "version", None),
                            "viewport": getattr(page, "viewport_size", None),
                            "user_agent": page.evaluate("() => navigator.userAgent"),
                            "network": page.evaluate(
                                """() => {
                                  const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
                                  if (!c) return {supported: false};
                                  return {
                                    supported: true,
                                    effective_type: c.effectiveType || null,
                                    downlink_mbps: c.downlink ?? null,
                                    rtt_ms: c.rtt ?? null,
                                    save_data: Boolean(c.saveData),
                                  };
                                }""",
                            ),
                        }
                    )
                elif driver is not None:
                    capabilities = dict(getattr(driver, "capabilities", {}) or {})
                    browser_payload.update(
                        {
                            "browser_name": capabilities.get("browserName"),
                            "browser_version": capabilities.get("browserVersion")
                            or capabilities.get("version"),
                            "platform_name": capabilities.get("platformName")
                            or capabilities.get("platform"),
                            "viewport": driver.get_window_size(),
                            "user_agent": driver.execute_script("return navigator.userAgent"),
                            "network": driver.execute_script(
                                """const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
                                return c ? {supported:true, effective_type:c.effectiveType || null,
                                  downlink_mbps:c.downlink ?? null, rtt_ms:c.rtt ?? null,
                                  save_data:Boolean(c.saveData)} : {supported:false};"""
                            ),
                        }
                    )
                self._emit(
                    "environment.browser",
                    "environment",
                    browser_payload,
                    step_id=step_id,
                )
                self._environment_emitted.add("browser")
            except Exception as exc:  # noqa: BLE001
                self.limitations.append(f"浏览器环境采集降级：{exc}")

        app_session = self.ctx.get_var("_app_session")
        app_driver = getattr(app_session, "_driver", None) if app_session is not None else None
        if app_driver is not None and "device" not in self._environment_emitted:
            try:
                capabilities = dict(getattr(app_driver, "capabilities", {}) or {})
                safe_capabilities = {
                    key: value
                    for key, value in capabilities.items()
                    if not _SENSITIVE_RE.search(str(key))
                    and isinstance(value, (str, int, float, bool, type(None)))
                }
                device_payload: dict[str, Any] = {
                    "platform": capabilities.get("platformName"),
                    "platform_version": capabilities.get("platformVersion"),
                    "device_name": capabilities.get("deviceName"),
                    "automation_name": capabilities.get("automationName"),
                    "viewport": app_driver.get_window_size(),
                    "capabilities": safe_capabilities,
                }
                for attribute in ("current_activity", "current_package", "current_context"):
                    try:
                        device_payload[attribute] = getattr(app_driver, attribute)
                    except Exception:  # noqa: BLE001
                        continue
                self._emit(
                    "environment.device",
                    "environment",
                    device_payload,
                    step_id=step_id,
                )
                self._environment_emitted.add("device")
            except Exception as exc:  # noqa: BLE001
                self.limitations.append(f"移动设备环境采集降级：{exc}")

    def step_started(self, step: dict[str, Any]) -> None:
        """标记步骤边界与基线画面；失败只记录降级。"""
        try:
            step_id = step.get("id")
            log_from = len(self.ctx.logs)
            event = self._emit(
                "step.started",
                "runner",
                {
                    "step_order": step.get("step_order"),
                    "step_name": step.get("step_name"),
                    "step_type": step.get("step_type"),
                },
                step_id=step_id,
            )
            before = self._capture_screenshot(step, "before")
            self._step_state[id(step)] = {
                "event_from": event["local_sequence_no"],
                "event_index": len(self.events) - 1,
                "log_from": log_from,
                "screenshot_before": before,
                "started_monotonic": time.monotonic(),
            }
        except Exception as exc:  # noqa: BLE001
            self.limitations.append(f"步骤开始上下文降级：{exc}")

    def step_finished(self, step: dict[str, Any], result: StepResult) -> dict[str, Any]:
        """补充步骤结果、网络/日志摘要和动作后画面。"""
        state = self._step_state.pop(id(step), {})
        try:
            step_id = step.get("id")
            self._capture_environment(step_id)
            if result.step_type == "http_request":
                self._emit(
                    "network.exchange",
                    "network",
                    {
                        "action": result.action,
                        "target": result.target,
                        "status_code": self.ctx.records.get("status_code"),
                        "input": result.input_data,
                        "output": result.output_data,
                    },
                    step_id=step_id,
                    severity="error" if result.status.value in {"failed", "error"} else "info",
                )
            for message in self.ctx.logs[int(state.get("log_from") or 0):]:
                self._emit(
                    "runner.log",
                    "console",
                    {"message": str(message)},
                    step_id=step_id,
                )
            after = self._capture_screenshot(step, "after")
            finished = self._emit(
                "step.finished",
                "runner",
                {
                    "step_order": result.step_order,
                    "step_name": result.step_name,
                    "step_type": result.step_type,
                    "status": result.status.value,
                    "duration_ms": result.duration_ms,
                    "error": result.error_message,
                },
                step_id=step_id,
                severity="error" if result.status.value in {"failed", "error"} else "info",
            )
            event_index = int(state.get("event_index") or 0)
            step_events = self.events[event_index:]
            return {
                "events": json.loads(json.dumps(step_events, ensure_ascii=False, default=str)),
                "event_from_local": state.get("event_from"),
                "event_to_local": finished["local_sequence_no"],
                "screenshot_before": state.get("screenshot_before"),
                "screenshot_after": after,
                "limitations": list(dict.fromkeys(self.limitations))[-20:],
            }
        except Exception as exc:  # noqa: BLE001
            self.limitations.append(f"步骤结束上下文降级：{exc}")
            return {
                "events": [],
                "event_from_local": state.get("event_from"),
                "event_to_local": state.get("event_from"),
                "screenshot_before": state.get("screenshot_before"),
                "screenshot_after": None,
                "limitations": list(dict.fromkeys(self.limitations))[-20:],
            }


def collector_for(ctx: ExecutionContext) -> UIContextCollector:
    """一个 ExecutionContext 只创建一个采集器。"""
    existing = ctx.get_var("_ui_context_collector")
    if isinstance(existing, UIContextCollector):
        return existing
    collector = UIContextCollector(ctx)
    ctx.set_var("_ui_context_collector", collector)
    return collector
