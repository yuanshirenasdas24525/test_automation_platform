"""宿主机 Web Recorder Agent。

该进程必须原生运行在有桌面环境的宿主机上，用 Playwright 持有可见浏览器。
平台 API 只负责控制和持久化，不在 Celery/uvicorn Worker 内长期持有浏览器 Session。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform as host_platform
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from playwright.async_api import Browser, BrowserContext, Page, Playwright, Request, Response
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field, field_validator


logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ARTIFACT_ROOT = _PROJECT_ROOT / "data" / "ui_recordings"
_MAX_EVENT_BUFFER = 10_000
_MAX_BODY_BYTES = 64 * 1024
_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}


_RECORDER_SCRIPT = r"""
(() => {
  if (window.__uiRecorderInstalled) return;
  window.__uiRecorderInstalled = true;

  const escapeCss = (value) => {
    if (window.CSS && CSS.escape) return CSS.escape(String(value));
    return String(value).replace(/[^a-zA-Z0-9_-]/g, (ch) => `\\${ch}`);
  };

  const cssPath = (element) => {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return "";
    if (element.id) return `#${escapeCss(element.id)}`;
    const testId = element.getAttribute("data-testid") || element.getAttribute("data-test");
    if (testId) return `[data-testid="${String(testId).replace(/"/g, '\\"')}"]`;
    const segments = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && segments.length < 6) {
      let segment = current.tagName.toLowerCase();
      if (current.id) {
        segment += `#${escapeCss(current.id)}`;
        segments.unshift(segment);
        break;
      }
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((node) => node.tagName === current.tagName);
        if (siblings.length > 1) segment += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      }
      segments.unshift(segment);
      current = parent;
    }
    return segments.join(" > ");
  };

  const xpath = (element) => {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return "";
    if (element.id) return `//*[@id="${String(element.id).replace(/"/g, '\\"')}"]`;
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const tag = current.tagName.toLowerCase();
      const siblings = current.parentElement
        ? Array.from(current.parentElement.children).filter((node) => node.tagName === current.tagName)
        : [];
      parts.unshift(`${tag}[${Math.max(1, siblings.indexOf(current) + 1)}]`);
      current = current.parentElement;
    }
    return `/${parts.join("/")}`;
  };

  const describe = (target) => {
    const element = target && target.nodeType === Node.ELEMENT_NODE ? target : target?.parentElement;
    if (!element) return null;
    const tag = element.tagName.toLowerCase();
    const type = element.getAttribute("type") || element.getAttribute("role") || tag;
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
    const aria = (element.getAttribute("aria-label") || "").trim();
    const placeholder = (element.getAttribute("placeholder") || "").trim();
    const name = (element.getAttribute("name") || "").trim();
    const testId = (element.getAttribute("data-testid") || element.getAttribute("data-test") || "").trim();
    const semanticName = aria || text || placeholder || name || testId || `${tag} 元素`;
    const locators = [];
    if (element.id) locators.push({ strategy: "id", locator: element.id, score: 98 });
    if (testId) locators.push({ strategy: "css", locator: `[data-testid="${testId.replace(/"/g, '\\"')}"]`, score: 96 });
    if (name) locators.push({ strategy: "name", locator: name, score: 90 });
    const css = cssPath(element);
    if (css && !locators.some((item) => item.strategy === "css" && item.locator === css)) {
      locators.push({ strategy: "css", locator: css, score: element.id ? 94 : 78 });
    }
    if (text && ["button", "a", "label", "option"].includes(tag)) {
      locators.push({ strategy: tag === "a" ? "link" : "text", locator: text.slice(0, 80), score: 82 });
    }
    const xp = xpath(element);
    if (xp) locators.push({ strategy: "xpath", locator: xp, score: element.id ? 76 : 62 });
    const rect = element.getBoundingClientRect();
    return {
      semantic_name: semanticName.slice(0, 200),
      element_type: type.slice(0, 100),
      fingerprint_seed: [tag, element.id || "", testId, name, aria, placeholder, css].join("|"),
      attributes: {
        tag,
        id: element.id || null,
        name: name || null,
        role: element.getAttribute("role"),
        type: element.getAttribute("type"),
        aria_label: aria || null,
        placeholder: placeholder || null,
        test_id: testId || null,
        text: text || null,
        bounds: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          width: Math.round(rect.width), height: Math.round(rect.height),
        },
      },
      locators,
    };
  };

  const emit = (eventType, target, extra = {}) => {
    if (typeof window.__uiRecorderEmit !== "function") return;
    const element = describe(target);
    void window.__uiRecorderEmit({
      event_type: eventType,
      page_title: document.title,
      url: location.href,
      element,
      ...extra,
    });
  };

  document.addEventListener("click", (event) => emit("user.click", event.target, {
    button: event.button,
  }), true);

  const inputTimers = new WeakMap();
  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!target) return;
    const previous = inputTimers.get(target);
    if (previous) clearTimeout(previous);
    const timer = setTimeout(() => {
      const inputType = String(target.getAttribute?.("type") || "").toLowerCase();
      const autocomplete = String(target.getAttribute?.("autocomplete") || "").toLowerCase();
      const sensitive = inputType === "password" || autocomplete.includes("password") || autocomplete === "cc-number";
      emit("user.input", target, {
        value: sensitive ? "${password}" : String(target.value ?? "").slice(0, 2000),
        redacted: sensitive,
      });
    }, 350);
    inputTimers.set(target, timer);
  }, true);

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!target) return;
    const tag = String(target.tagName || "").toLowerCase();
    if (tag === "select" || target.type === "checkbox" || target.type === "radio") {
      emit("user.change", target, {
        value: String(target.value ?? "").slice(0, 1000),
        checked: Boolean(target.checked),
      });
    }
  }, true);

  document.addEventListener("submit", (event) => emit("user.submit", event.target), true);

  let lastScroll = 0;
  window.addEventListener("scroll", () => {
    const now = Date.now();
    if (now - lastScroll < 500) return;
    lastScroll = now;
    emit("user.scroll", document.scrollingElement || document.documentElement, {
      scroll_x: Math.round(window.scrollX),
      scroll_y: Math.round(window.scrollY),
    });
  }, true);
})();
"""


def _page_key(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        path = parsed.path or "/"
        return f"{parsed.netloc}{path}"[:255]
    return (url or "about:blank")[:255]


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "***" if key.lower() in _SENSITIVE_HEADERS else value[:2000]
        for key, value in headers.items()
    }


def _limited_text(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_BODY_BYTES:
        return value, False
    return encoded[:_MAX_BODY_BYTES].decode("utf-8", errors="replace"), True


class RecorderStartRequest(BaseModel):
    session_id: int = Field(..., gt=0)
    target_url: str = Field(..., min_length=1, max_length=4000)
    browser: str = Field("chromium", pattern="^(chromium|firefox|webkit)$")
    headless: bool = False
    slow_mo: int = Field(0, ge=0, le=2000)
    viewport: dict[str, int] = Field(default_factory=lambda: {"width": 1440, "height": 900})

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("target_url 必须是完整的 http/https URL")
        return value.strip()


@dataclass
class RecorderRuntime:
    session_id: int
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    started_monotonic: float = field(default_factory=time.monotonic)
    paused: bool = False
    stopped: bool = False
    sequence_no: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    pages: set[Page] = field(default_factory=set)
    request_keys: dict[Request, str] = field(default_factory=dict)
    request_started_monotonic: dict[Request, float] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def emit(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        *,
        page: Page | None = None,
        severity: str = "info",
    ) -> dict[str, Any] | None:
        if self.paused and source == "user":
            return None
        async with self.lock:
            self.sequence_no += 1
            event_key = uuid.uuid4().hex
            url = page.url if page is not None else str(payload.get("url") or "")
            element = payload.get("element")
            if isinstance(element, dict):
                seed = str(element.pop("fingerprint_seed", ""))
                element["fingerprint"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            event = {
                "event_key": event_key,
                "sequence_no": self.sequence_no,
                "event_type": event_type,
                "source": source,
                "severity": severity,
                "page_key": _page_key(url),
                "occurred_at": datetime.now().isoformat(),
                "monotonic_ms": int((time.monotonic() - self.started_monotonic) * 1000),
                "payload": payload,
            }
            self.events.append(event)
            if len(self.events) > _MAX_EVENT_BUFFER:
                self.events = self.events[-_MAX_EVENT_BUFFER:]
            return event

    async def attach_page(self, page: Page) -> None:
        if page in self.pages:
            return
        self.pages.add(page)

        page.on(
            "console",
            lambda message: asyncio.create_task(self.emit(
                "console.message",
                "console",
                {
                    "type": message.type,
                    "text": message.text[:10_000],
                    "location": message.location,
                    "url": page.url,
                },
                page=page,
                severity="error" if message.type == "error" else "info",
            )),
        )
        page.on(
            "pageerror",
            lambda error: asyncio.create_task(self.emit(
                "console.pageerror",
                "console",
                {"message": str(error)[:10_000], "url": page.url},
                page=page,
                severity="error",
            )),
        )
        page.on(
            "framenavigated",
            lambda frame: frame == page.main_frame and asyncio.create_task(self.emit(
                "page.navigation",
                "browser",
                {"url": frame.url, "title": ""},
                page=page,
            )),
        )
        page.on("request", lambda request: asyncio.create_task(self.capture_request(page, request)))
        page.on("response", lambda response: asyncio.create_task(self.capture_response(page, response)))
        page.on("requestfailed", lambda request: asyncio.create_task(self.capture_request_failed(page, request)))
        page.on(
            "close",
            lambda: asyncio.create_task(self.emit(
                "page.closed", "browser", {"url": page.url}, page=page,
            )),
        )

    async def handle_user_event(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        page = source.get("page")
        if not isinstance(page, Page):
            return
        event_type = str(payload.get("event_type") or "user.unknown")
        clean_payload = dict(payload)
        clean_payload.pop("event_type", None)
        clean_payload["url"] = page.url
        await self.emit(event_type, "user", clean_payload, page=page)
        if event_type in {"user.click", "user.input", "user.change"}:
            asyncio.create_task(self.capture_screenshot(page, event_type))

    async def capture_screenshot(self, page: Page, reason: str) -> None:
        if self.stopped:
            return
        await page.wait_for_timeout(150)
        target_dir = _ARTIFACT_ROOT / f"session_{self.session_id}" / "screenshots"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"
        try:
            await page.screenshot(path=str(path), full_page=False)
            await self.emit(
                "screen.capture",
                "screen",
                {"reason": reason, "path": str(path), "url": page.url},
                page=page,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("截图失败 session=%s: %s", self.session_id, exc)

    async def capture_request(self, page: Page, request: Request) -> None:
        if request.resource_type not in {"xhr", "fetch"}:
            return
        request_key = uuid.uuid4().hex
        self.request_keys[request] = request_key
        self.request_started_monotonic[request] = time.monotonic()
        body, truncated = _limited_text(request.post_data)
        await self.emit(
            "network.request",
            "network",
            {
                "request_key": request_key,
                "resource_type": request.resource_type,
                "method": request.method,
                "url": request.url,
                "headers": _redact_headers(await request.all_headers()),
                "body": body,
                "body_truncated": truncated,
            },
            page=page,
        )

    async def capture_response(self, page: Page, response: Response) -> None:
        request = response.request
        if request.resource_type not in {"xhr", "fetch"}:
            return
        started_at = self.request_started_monotonic.pop(request, None)
        body_text: str | None = None
        body_truncated = False
        content_type = (await response.all_headers()).get("content-type", "")
        if any(kind in content_type for kind in ("json", "text", "javascript", "xml")):
            try:
                raw = await response.body()
                body_text, body_truncated = _limited_text(raw.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                body_text = None
        await self.emit(
            "network.response",
            "network",
            {
                "request_key": self.request_keys.get(request),
                "method": request.method,
                "url": response.url,
                "status": response.status,
                "status_text": response.status_text,
                "duration_ms": (
                    round((time.monotonic() - started_at) * 1000, 2)
                    if started_at is not None
                    else None
                ),
                "headers": _redact_headers(await response.all_headers()),
                "body": body_text,
                "body_truncated": body_truncated,
            },
            page=page,
            severity="error" if response.status >= 400 else "info",
        )

    async def capture_request_failed(self, page: Page, request: Request) -> None:
        if request.resource_type not in {"xhr", "fetch"}:
            return
        started_at = self.request_started_monotonic.pop(request, None)
        await self.emit(
            "network.failed",
            "network",
            {
                "request_key": self.request_keys.get(request),
                "method": request.method,
                "url": request.url,
                "failure": request.failure,
                "duration_ms": (
                    round((time.monotonic() - started_at) * 1000, 2)
                    if started_at is not None
                    else None
                ),
            },
            page=page,
            severity="error",
        )

    async def close(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        try:
            await self.context.close()
        finally:
            try:
                await self.browser.close()
            finally:
                await self.playwright.stop()


_SESSIONS: dict[int, RecorderRuntime] = {}


async def _authorize(x_recorder_secret: str | None = Header(None)) -> None:
    expected = os.getenv("UI_RECORDER_AGENT_SECRET", "").strip()
    if expected and x_recorder_secret != expected:
        raise HTTPException(status_code=401, detail="Recorder Agent secret 无效")


async def _start_runtime(body: RecorderStartRequest) -> RecorderRuntime:
    existing = _SESSIONS.get(body.session_id)
    if existing is not None and not existing.stopped:
        raise HTTPException(status_code=409, detail="该录制会话已经在 Agent 中运行")

    playwright = await async_playwright().start()
    browser_type = getattr(playwright, body.browser)
    try:
        browser = await browser_type.launch(
            headless=body.headless,
            slow_mo=body.slow_mo,
        )
        context = await browser.new_context(viewport=body.viewport)
    except Exception as exc:  # noqa: BLE001
        await playwright.stop()
        raise HTTPException(status_code=503, detail=f"浏览器启动失败：{exc}") from exc

    runtime = RecorderRuntime(
        session_id=body.session_id,
        playwright=playwright,
        browser=browser,
        context=context,
    )
    _SESSIONS[body.session_id] = runtime

    async def emit_binding(source: dict[str, Any], payload: dict[str, Any]) -> None:
        await runtime.handle_user_event(source, payload)

    await context.expose_binding("__uiRecorderEmit", emit_binding)
    await context.add_init_script(script=_RECORDER_SCRIPT)
    context.on("page", lambda page: asyncio.create_task(runtime.attach_page(page)))

    page = await context.new_page()
    await runtime.attach_page(page)
    await runtime.emit(
        "agent.connected",
        "agent",
        {
            "agent_id": f"web-agent-{os.getpid()}",
            "capabilities": {
                "screen": True,
                "console": True,
                "network": True,
                "user_events": True,
                "locators": ["id", "css", "name", "text", "link", "xpath"],
                "browser": body.browser,
                "headless": body.headless,
            },
        },
        page=page,
    )
    try:
        await page.goto(body.target_url, wait_until="domcontentloaded", timeout=60_000)
        await runtime.emit(
            "page.ready",
            "browser",
            {"url": page.url, "title": await page.title()},
            page=page,
        )
        browser_environment = await page.evaluate(
            """() => {
              const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
              return {
                url: location.href,
                user_agent: navigator.userAgent,
                language: navigator.language,
                languages: Array.from(navigator.languages || []),
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                viewport: {
                  width: window.innerWidth,
                  height: window.innerHeight,
                  device_pixel_ratio: window.devicePixelRatio,
                },
                screen: {
                  width: window.screen.width,
                  height: window.screen.height,
                  color_depth: window.screen.colorDepth,
                },
                network: connection ? {
                  effective_type: connection.effectiveType || null,
                  downlink_mbps: connection.downlink ?? null,
                  rtt_ms: connection.rtt ?? null,
                  save_data: connection.saveData ?? null,
                } : {
                  effective_type: null,
                  downlink_mbps: null,
                  rtt_ms: null,
                  save_data: null,
                  unavailable_reason: "当前浏览器未提供 Network Information API",
                },
              };
            }"""
        )
        await runtime.emit(
            "environment.snapshot",
            "environment",
            {
                **browser_environment,
                "browser": {
                    "name": body.browser,
                    "version": browser.version,
                    "headless": body.headless,
                },
                "host_os": {
                    "system": host_platform.system(),
                    "release": host_platform.release(),
                    "machine": host_platform.machine(),
                },
            },
            page=page,
        )
    except Exception as exc:  # noqa: BLE001
        await runtime.emit(
            "agent.error",
            "agent",
            {"message": f"打开目标页面失败：{exc}"},
            page=page,
            severity="error",
        )
        await runtime.close()
        raise HTTPException(status_code=502, detail=f"打开目标页面失败：{exc}") from exc
    return runtime


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    yield
    await asyncio.gather(
        *(runtime.close() for runtime in list(_SESSIONS.values())),
        return_exceptions=True,
    )


app = FastAPI(title="UI Recorder Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "success",
        "data": {
            "ok": True,
            "active_sessions": sum(not runtime.stopped for runtime in _SESSIONS.values()),
        },
    }


@app.post("/sessions", dependencies=[Depends(_authorize)])
async def start_session(body: RecorderStartRequest):
    runtime = await _start_runtime(body)
    return {
        "status": "success",
        "data": {
            "session_id": runtime.session_id,
            "status": "recording",
            "agent_id": f"web-agent-{os.getpid()}",
            "capabilities": {
                "screen": True,
                "console": True,
                "network": True,
                "user_events": True,
            },
        },
    }


@app.get("/sessions/{session_id}", dependencies=[Depends(_authorize)])
async def get_session(session_id: int):
    runtime = _SESSIONS.get(session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent 会话不存在")
    return {
        "status": "success",
        "data": {
            "session_id": session_id,
            "status": "stopped" if runtime.stopped else "paused" if runtime.paused else "recording",
            "event_count": len(runtime.events),
        },
    }


@app.get("/sessions/{session_id}/events", dependencies=[Depends(_authorize)])
async def list_events(
    session_id: int,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
):
    runtime = _SESSIONS.get(session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent 会话不存在")
    events = [
        event for event in runtime.events if int(event["sequence_no"]) > after_sequence
    ][:limit]
    return {"status": "success", "data": events}


@app.post("/sessions/{session_id}/pause", dependencies=[Depends(_authorize)])
async def pause_session(session_id: int):
    runtime = _SESSIONS.get(session_id)
    if runtime is None or runtime.stopped:
        raise HTTPException(status_code=404, detail="Agent 会话不存在或已停止")
    runtime.paused = True
    await runtime.emit("agent.paused", "agent", {})
    return {"status": "success", "data": {"status": "paused"}}


@app.post("/sessions/{session_id}/resume", dependencies=[Depends(_authorize)])
async def resume_session(session_id: int):
    runtime = _SESSIONS.get(session_id)
    if runtime is None or runtime.stopped:
        raise HTTPException(status_code=404, detail="Agent 会话不存在或已停止")
    runtime.paused = False
    await runtime.emit("agent.resumed", "agent", {})
    return {"status": "success", "data": {"status": "recording"}}


@app.post("/sessions/{session_id}/stop", dependencies=[Depends(_authorize)])
async def stop_session(session_id: int):
    runtime = _SESSIONS.get(session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent 会话不存在")
    if not runtime.stopped:
        await runtime.emit("agent.disconnected", "agent", {"reason": "stopped"})
        await runtime.close()
    return {"status": "success", "data": {"status": "stopped"}}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "recorder_agent.main:app",
        host=os.getenv("UI_RECORDER_AGENT_HOST", "127.0.0.1"),
        port=int(os.getenv("UI_RECORDER_AGENT_PORT", "54352")),
        reload=False,
    )
