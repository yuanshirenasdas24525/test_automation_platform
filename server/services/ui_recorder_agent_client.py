"""平台 API 到宿主机 Recorder Agent 的同步 HTTP 客户端。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from database.models import AppPackage, Device, UiRecordingSession


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class RecorderAgentError(RuntimeError):
    """Recorder Agent 不可达或拒绝控制命令。"""


def _base_url() -> str:
    return os.getenv("UI_RECORDER_AGENT_URL", "http://127.0.0.1:54352").rstrip("/")


def _headers() -> dict[str, str]:
    secret = os.getenv("UI_RECORDER_AGENT_SECRET", "").strip()
    return {"X-Recorder-Secret": secret} if secret else {}


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    read_timeout: float = 75.0,
) -> Any:
    timeout = httpx.Timeout(connect=3.0, read=read_timeout, write=10.0, pool=3.0)
    try:
        with httpx.Client(base_url=_base_url(), headers=_headers(), timeout=timeout) as client:
            response = client.request(method, path, json=body)
    except httpx.HTTPError as exc:
        raise RecorderAgentError(
            "Recorder Agent 未启动或不可连接，请通过 start-dev.sh 启动宿主机录制服务"
        ) from exc
    if response.is_error:
        try:
            payload = response.json()
            detail = payload.get("detail") or payload.get("message")
        except ValueError:
            detail = response.text
        raise RecorderAgentError(str(detail or f"Recorder Agent HTTP {response.status_code}"))
    payload = response.json()
    if isinstance(payload, dict) and payload.get("status") == "success":
        return payload.get("data")
    return payload


def _request_bytes(method: str, path: str, *, read_timeout: float = 75.0) -> bytes:
    """读取 Agent 二进制制品，同时沿用统一鉴权和错误转换。"""
    timeout = httpx.Timeout(connect=3.0, read=read_timeout, write=10.0, pool=3.0)
    try:
        with httpx.Client(base_url=_base_url(), headers=_headers(), timeout=timeout) as client:
            response = client.request(method, path)
    except httpx.HTTPError as exc:
        raise RecorderAgentError(
            "Recorder Agent 未启动或不可连接，请通过 start-dev.sh 启动宿主机录制服务"
        ) from exc
    if response.is_error:
        try:
            payload = response.json()
            detail = payload.get("detail") or payload.get("message")
        except ValueError:
            detail = response.text
        raise RecorderAgentError(str(detail or f"Recorder Agent HTTP {response.status_code}"))
    return response.content


def start_web_session(session: UiRecordingSession) -> dict[str, Any]:
    """启动可见 Playwright 浏览器；成功返回 Agent 能力。"""
    if not session.source_url:
        raise RecorderAgentError("开始 Web 录制前必须填写目标 URL")
    config = session.capture_config or {}
    return _request(
        "POST",
        "/sessions",
        body={
            "session_id": session.id,
            "target_url": session.source_url,
            "browser": config.get("browser") or "chromium",
            "headless": bool(config.get("headless", False)),
            "slow_mo": int(config.get("slow_mo") or 0),
            "viewport": config.get("viewport") or {"width": 1440, "height": 900},
        },
    )


def _host_appium_url(device: Device) -> str:
    """生成宿主机 Recorder Agent 可访问的 Appium 地址。"""
    from runners.app.session import _build_appium_url

    url = _build_appium_url({
        "agent_host": device.agent_host,
        "appium_port": device.appium_port,
        "capabilities": device.capabilities,
    })
    parts = urlsplit(url)
    hostname = parts.hostname or "localhost"
    if hostname == "host.docker.internal":
        port = f":{parts.port}" if parts.port else ""
        return urlunsplit((parts.scheme, f"127.0.0.1{port}", parts.path, "", ""))
    return url


def start_mobile_session(
    session: UiRecordingSession,
    device: Device,
    app_package: AppPackage | None,
) -> dict[str, Any]:
    """通过宿主机 Appium 连接 Android Emulator 或 iOS Simulator。"""
    app_path: str | None = None
    app_identifier: str | None = None
    if app_package is not None:
        path = Path(app_package.file_path)
        app_path = str(path if path.is_absolute() else (_PROJECT_ROOT / path).resolve())
        app_identifier = (
            app_package.app_package
            if session.platform == "android"
            else app_package.bundle_id
        )
    return _request(
        "POST",
        "/mobile/sessions",
        body={
            "session_id": session.id,
            "platform": session.platform,
            "appium_url": _host_appium_url(device),
            "udid": device.udid,
            "device_name": device.device_name,
            "platform_version": device.platform_version,
            "app_path": app_path,
            "app_identifier": app_identifier,
            "capabilities": device.capabilities or {},
            "restore_scenario": dict((session.capture_config or {}).get("restore_scenario") or {}),
        },
        read_timeout=180.0,
    )


def control_web_session(session_id: int, action: str) -> dict[str, Any]:
    return _request("POST", f"/sessions/{session_id}/{action}")


def control_agent_session(session_id: int, action: str) -> dict[str, Any]:
    """控制 Web 或移动端 Agent 会话。"""
    return _request("POST", f"/sessions/{session_id}/{action}")


def set_web_pick_mode(session_id: int, enabled: bool) -> dict[str, Any]:
    """切换受控页面的非破坏性元素拾取模式。"""
    return _request(
        "POST",
        f"/sessions/{session_id}/pick-mode",
        body={"enabled": enabled},
    )


def set_agent_pick_mode(session_id: int, enabled: bool) -> dict[str, Any]:
    """切换 Web 注入拾取或移动远程画面的非破坏性拾取。"""
    return _request(
        "POST",
        f"/sessions/{session_id}/pick-mode",
        body={"enabled": enabled},
    )


def perform_mobile_action(session_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """将前端远程画面动作转发给持有 Appium Driver 的 Agent。"""
    return _request("POST", f"/sessions/{session_id}/actions", body=payload)


def perform_web_action(session_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """将平台快照上的动作转发给持有 Playwright Page 的 Agent。"""
    return _request("POST", f"/sessions/{session_id}/web-actions", body=payload)


def start_web_replay(
    session_id: int,
    *,
    browser: str,
    headless: bool,
    entry_url: str | None = None,
    page_fingerprint: str | None = None,
    viewport: dict[str, int] | None = None,
    reuse_key: str | None = None,
    freeze_dom: bool = False,
) -> dict[str, Any]:
    """从 Agent 本地归档启动严格离线回放浏览器。"""
    return _request(
        "POST",
        "/replays",
        body={
            "session_id": session_id,
            "browser": browser,
            "headless": headless,
            "entry_url": entry_url,
            "page_fingerprint": page_fingerprint,
            "viewport": viewport or {"width": 1440, "height": 900},
            "reuse_key": reuse_key,
            "freeze_dom": freeze_dom,
        },
    )


def get_web_replay(replay_id: str) -> dict[str, Any]:
    """读取离线回放当前 URL、标题和命中统计。"""
    data = _request("GET", f"/replays/{replay_id}")
    return data if isinstance(data, dict) else {}


def perform_web_replay_action(replay_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """在完成态离线浏览器中执行画布动作。"""
    data = _request("POST", f"/replays/{replay_id}/actions", body=payload)
    return data if isinstance(data, dict) else {}


def get_web_replay_screenshot(replay_id: str) -> bytes:
    """读取完成态离线浏览器的当前画面。"""
    return _request_bytes("GET", f"/replays/{replay_id}/screenshot")


def validate_web_replay_locator(
    replay_id: str,
    strategy: str,
    locator: str,
) -> dict[str, Any]:
    """在离线页面状态中重新验证 Web 定位器。"""
    data = _request(
        "POST",
        f"/replays/{replay_id}/locators:validate",
        body={"strategy": strategy, "locator": locator},
    )
    return data if isinstance(data, dict) else {}


def stop_web_replay(replay_id: str) -> None:
    """关闭并回收一个离线浏览器。"""
    _request("POST", f"/replays/{replay_id}/stop")


def pull_web_events(session_id: int, after_sequence: int, limit: int = 500) -> list[dict[str, Any]]:
    data = _request(
        "GET",
        f"/sessions/{session_id}/events?after_sequence={after_sequence}&limit={limit}",
    )
    return data if isinstance(data, list) else []


def pull_agent_events(session_id: int, after_sequence: int, limit: int = 500) -> list[dict[str, Any]]:
    """拉取 Web 或移动端统一事件。"""
    return pull_web_events(session_id, after_sequence, limit)


def agent_health() -> dict[str, Any]:
    data = _request("GET", "/health")
    return data if isinstance(data, dict) else {"ok": False}


def mobile_preflight() -> dict[str, Any]:
    """读取宿主机模拟器和 Appium 的即时可用状态。"""
    data = _request("GET", "/mobile/preflight")
    return data if isinstance(data, dict) else {"ready": False}
