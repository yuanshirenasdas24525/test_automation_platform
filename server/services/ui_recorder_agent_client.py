"""平台 API 到宿主机 Recorder Agent 的同步 HTTP 客户端。"""
from __future__ import annotations

import os
from typing import Any

import httpx

from database.models import UiRecordingSession


class RecorderAgentError(RuntimeError):
    """Recorder Agent 不可达或拒绝控制命令。"""


def _base_url() -> str:
    return os.getenv("UI_RECORDER_AGENT_URL", "http://127.0.0.1:54352").rstrip("/")


def _headers() -> dict[str, str]:
    secret = os.getenv("UI_RECORDER_AGENT_SECRET", "").strip()
    return {"X-Recorder-Secret": secret} if secret else {}


def _request(method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:
    timeout = httpx.Timeout(connect=3.0, read=75.0, write=10.0, pool=3.0)
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


def control_web_session(session_id: int, action: str) -> dict[str, Any]:
    return _request("POST", f"/sessions/{session_id}/{action}")


def pull_web_events(session_id: int, after_sequence: int, limit: int = 500) -> list[dict[str, Any]]:
    data = _request(
        "GET",
        f"/sessions/{session_id}/events?after_sequence={after_sequence}&limit={limit}",
    )
    return data if isinstance(data, list) else []


def agent_health() -> dict[str, Any]:
    data = _request("GET", "/health")
    return data if isinstance(data, dict) else {"ok": False}
