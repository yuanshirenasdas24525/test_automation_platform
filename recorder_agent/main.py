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
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse
from urllib.request import urlopen

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response as FastAPIResponse
from playwright.async_api import Browser, BrowserContext, Page, Playwright, Request, Response
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field, field_validator


logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ARTIFACT_ROOT = _PROJECT_ROOT / "data" / "ui_recordings"
_MAX_EVENT_BUFFER = 10_000
_MAX_BODY_BYTES = 64 * 1024
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_ARCHIVE_RESOURCE_TYPES = {"document", "stylesheet", "script", "image", "font"}
_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}
_SENSITIVE_KEY_PARTS = {
    "access_token",
    "authorization",
    "card",
    "client_secret",
    "cookie",
    "credential",
    "cvv",
    "password",
    "refresh_token",
    "secret",
    "session",
    "signature",
    "token",
}
_SENSITIVE_SELECTOR = (
    'input[type="password"], input[autocomplete*="password"], '
    'input[autocomplete^="cc-"], input[name*="token" i], '
    '[data-sensitive="true"], [data-private="true"]'
)


_RECORDER_SCRIPT = r"""
(() => {
  if (window.__uiRecorderInstalled) return;
  window.__uiRecorderInstalled = true;

  const INTERACTIVE_SELECTOR = [
    "a[href]", "button", "input", "select", "textarea", "summary",
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="tab"]', '[role="menuitem"]', '[contenteditable="true"]', "[data-testid]",
  ].join(",");
  const TEXT_SELECTOR = [
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "label", "legend", "caption",
    "th", "td", "dt", "dd", "li", "div", "span", "strong", "small", "code", "pre",
    '[role="heading"]', '[role="cell"]', '[role="columnheader"]', '[role="rowheader"]',
    '[role="status"]', '[role="alert"]',
  ].join(",");
  const LOCATABLE_TEXT_SELECTOR = `${INTERACTIVE_SELECTOR},${TEXT_SELECTOR}`;

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
    const rawElement = target && target.nodeType === Node.ELEMENT_NODE ? target : target?.parentElement;
    const interactiveAncestor = rawElement?.closest?.(INTERACTIVE_SELECTOR);
    const element = interactiveAncestor || rawElement;
    if (!element) return null;
    const tag = element.tagName.toLowerCase();
    const type = element.getAttribute("type") || element.getAttribute("role") || tag;
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120);
    const aria = (element.getAttribute("aria-label") || "").trim();
    const placeholder = (element.getAttribute("placeholder") || "").trim();
    const name = (element.getAttribute("name") || "").trim();
    const testId = (element.getAttribute("data-testid") || element.getAttribute("data-test") || "").trim();
    const explicitRole = (element.getAttribute("role") || "").trim();
    const implicitRole = tag === "button" ? "button"
      : tag === "a" && element.hasAttribute("href") ? "link"
      : tag === "select" ? "combobox"
      : tag === "textarea" ? "textbox"
      : tag === "input" && ["checkbox", "radio"].includes(String(element.type || "").toLowerCase())
        ? String(element.type).toLowerCase()
        : tag === "input" ? "textbox"
        : /^h[1-6]$/.test(tag) ? "heading" : "";
    const role = explicitRole || implicitRole;
    const semanticName = aria || text || placeholder || name || testId || `${tag} 元素`;
    const locators = [];
    if (element.id) locators.push({ strategy: "id", locator: element.id, score: 98 });
    if (testId) locators.push({ strategy: "css", locator: `[data-testid="${testId.replace(/"/g, '\\"')}"]`, score: 96 });
    if (role && semanticName) {
      locators.push({ strategy: "role", locator: `role=${role};name=${semanticName.slice(0, 120)}`, score: 94 });
    }
    if (name) locators.push({ strategy: "name", locator: name, score: 90 });
    const css = cssPath(element);
    if (css && !locators.some((item) => item.strategy === "css" && item.locator === css)) {
      locators.push({ strategy: "css", locator: css, score: element.id ? 94 : 78 });
    }
    if (text && (element.matches(TEXT_SELECTOR) || ["button", "a", "label", "option"].includes(tag))) {
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

  const normalizedText = (element) => String(element?.innerText || element?.textContent || "")
    .replace(/\s+/g, " ").trim();
  const locatorMatchCount = (item) => {
    try {
      if (item.strategy === "id") return document.querySelectorAll(`#${escapeCss(item.locator)}`).length;
      if (item.strategy === "css") return document.querySelectorAll(item.locator).length;
      if (item.strategy === "name") return document.querySelectorAll(`[name="${String(item.locator).replace(/"/g, '\\"')}"]`).length;
      if (item.strategy === "link") return Array.from(document.querySelectorAll("a[href]"))
        .filter((element) => normalizedText(element) === item.locator).length;
      if (item.strategy === "text") return Array.from(document.querySelectorAll(LOCATABLE_TEXT_SELECTOR))
        .filter((element) => normalizedText(element) === item.locator).length;
      if (item.strategy === "xpath") return document.evaluate(
        item.locator, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null,
      ).snapshotLength;
      if (item.strategy === "role") {
        const match = /^role=([^;]+);name=(.*)$/.exec(item.locator);
        if (!match) return 0;
        const [, role, accessibleName] = match;
        const implicit = {
          button: "button",
          link: "a[href]",
          textbox: 'input:not([type="checkbox"]):not([type="radio"]),textarea',
          combobox: "select",
          checkbox: 'input[type="checkbox"]',
          radio: 'input[type="radio"]',
          heading: "h1,h2,h3,h4,h5,h6",
        }[role] || "[data-ui-recorder-no-match]";
        return Array.from(document.querySelectorAll(`[role="${escapeCss(role)}"],${implicit}`))
          .filter((element) => {
            const name = (element.getAttribute("aria-label") || normalizedText(element) || element.getAttribute("placeholder") || element.getAttribute("name") || element.getAttribute("data-testid") || "").trim();
            return name === accessibleName;
          }).length;
      }
    } catch (_error) {
      return 0;
    }
    return 0;
  };
  const validateDescription = (description) => {
    if (!description) return null;
    description.locators = description.locators.map((item) => {
      const matchCount = locatorMatchCount(item);
      return {
        ...item,
        match_count: matchCount,
        is_unique: matchCount === 1,
        score: Math.max(0, item.score - (matchCount === 1 ? 0 : matchCount === 0 ? 35 : 20)),
      };
    });
    return description;
  };

  const emit = (eventType, target, extra = {}) => {
    if (typeof window.__uiRecorderEmit !== "function") return;
    const element = validateDescription(describe(target));
    void window.__uiRecorderEmit({
      event_type: eventType,
      page_title: document.title,
      url: location.href,
      element,
      ...extra,
    });
  };

  let pickMode = false;
  window.__uiRecorderSetPickMode = (enabled) => {
    pickMode = Boolean(enabled);
    document.documentElement.dataset.uiRecorderPickMode = pickMode ? "true" : "false";
  };

  document.addEventListener("click", (event) => {
    if (pickMode) {
      event.preventDefault();
      event.stopImmediatePropagation();
      emit("user.pick", event.target, { button: event.button });
      return;
    }
    emit("user.click", event.target, { button: event.button });
  }, true);

  const inputTimers = new WeakMap();
  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!target) return;
    const previous = inputTimers.get(target);
    if (previous) clearTimeout(previous);
    const timer = setTimeout(() => {
      const inputType = String(target.getAttribute?.("type") || "").toLowerCase();
      const autocomplete = String(target.getAttribute?.("autocomplete") || "").toLowerCase();
      const fieldName = String(target.getAttribute?.("name") || target.getAttribute?.("id") || "").toLowerCase();
      const sensitive = inputType === "password"
        || autocomplete.includes("password")
        || autocomplete.startsWith("cc-")
        || ["cvv", "cvc", "secret", "token"].some((part) => fieldName.includes(part));
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

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (typeof window.__uiRecorderEmit !== "function") return;
      void window.__uiRecorderEmit({
        event_type: "environment.resize",
        page_title: document.title,
        url: location.href,
        viewport: {
          width: window.innerWidth,
          height: window.innerHeight,
          device_pixel_ratio: window.devicePixelRatio,
        },
      });
    }, 350);
  }, true);

  const isVisible = (element) => {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && Number(style.opacity || 1) > 0
      && rect.width > 2
      && rect.height > 2
      && rect.bottom >= 0
      && rect.right >= 0
      && rect.top <= window.innerHeight
      && rect.left <= window.innerWidth;
  };

  const visibleModal = () => Array.from(document.querySelectorAll(
    'dialog[open], [role="dialog"][aria-modal="true"], [role="dialog"][data-state="open"], [aria-modal="true"]',
  )).find(isVisible) || null;

  const isTextCandidate = (element) => {
    if (!element.matches(TEXT_SELECTOR)) return false;
    const interactiveAncestor = element.closest(INTERACTIVE_SELECTOR);
    if (interactiveAncestor && interactiveAncestor !== element) return false;
    const text = normalizedText(element);
    if (!text || text.length > 300) return false;
    const duplicateChild = Array.from(element.children).some(
      (child) => child.matches(TEXT_SELECTOR) && normalizedText(child) === text,
    );
    if (duplicateChild) return false;
    return Array.from(element.childNodes).some(
      (node) => node.nodeType === Node.TEXT_NODE && String(node.textContent || "").trim(),
    ) || element.children.length === 0;
  };

  window.__uiRecorderCollectElements = () => {
    const modal = visibleModal();
    const root = modal || document;
    return Array.from(root.querySelectorAll(LOCATABLE_TEXT_SELECTOR))
      .filter(isVisible)
      .filter((element) => element.matches(INTERACTIVE_SELECTOR) || isTextCandidate(element))
      .slice(0, 800)
      .map((element) => validateDescription(describe(element)))
      .filter(Boolean);
  };

  window.__uiRecorderDescribeAt = (x, y) => validateDescription(describe(document.elementFromPoint(x, y)));
  window.__uiRecorderPageMeta = () => {
    const modal = visibleModal();
    const headingSelectors = [
      "[data-page-title]", "main h1", "main h2", "[role=main] h1", "[role=main] h2", "h1",
    ];
    const pageHeading = headingSelectors
      .map((selector) => document.querySelector(selector))
      .find(isVisible);
    const modalHeading = modal
      ? Array.from(modal.querySelectorAll('[data-dialog-title], [role="heading"], h1, h2, h3'))
        .find(isVisible)
      : null;
    const pageName = (pageHeading?.innerText || pageHeading?.textContent || document.title || location.pathname)
      .replace(/\s+/g, " ").trim().slice(0, 200);
    const modalName = (modalHeading?.innerText || modalHeading?.textContent || modal?.getAttribute("aria-label") || "弹窗")
      .replace(/\s+/g, " ").trim().slice(0, 100);
    return {
      page_name: pageName,
      state_name: modal ? `弹窗：${modalName}` : "默认页面",
      modal_open: Boolean(modal),
    };
  };
})();
"""


def _page_key(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        path = parsed.path or "/"
        ignored_keys = {
            "_", "cache", "cachebuster", "nonce", "password", "signature", "timestamp",
            "token", "ts", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
        }
        query_items = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in ignored_keys
            and not any(part in key.lower() for part in ("secret", "token", "password", "signature"))
        ]
        query = urlencode(sorted(query_items))
        identity = f"{parsed.netloc}{path}{f'?{query}' if query else ''}"
        if len(identity) <= 255:
            return identity
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return f"{identity[:238]}#{suffix}"
    return (url or "about:blank")[:255]


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "***" if key.lower() in _SENSITIVE_HEADERS else value[:2000]
        for key, value in headers.items()
    }


def _is_sensitive_key(value: str) -> bool:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    normalized = re.sub(r"[^a-z0-9]+", "_", expanded.lower()).strip("_")
    if normalized in _SENSITIVE_KEY_PARTS:
        return True
    return any(
        part in normalized
        for part in (
            "password", "passwd", "secret", "credential", "authorization",
            "access_token", "refresh_token", "auth_token", "session_token",
            "signature", "card_number", "credit_card", "cvv", "cvc",
        )
    )


def _redact_value(value: Any, *, key: str = "") -> Any:
    """递归脱敏 JSON/Form 数据，保留结构供离线 Mock 和调试使用。"""
    if key and _is_sensitive_key(key):
        return "***"
    if isinstance(value, dict):
        return {str(item_key): _redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value[:1000]]
    if isinstance(value, str):
        text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", value)
        text = re.sub(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b", "***", text)
        return text
    return value


def _redact_storage_value(key: str, value: str) -> str:
    """脱敏浏览器存储值，同时保留前端恢复运行所需的数据结构。"""
    if _is_sensitive_key(key):
        return "***"
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return str(_redact_value(value, key=key))[:20_000]
    return json.dumps(
        _redact_value(parsed, key=key),
        ensure_ascii=False,
        separators=(",", ":"),
    )[:20_000]


def _redact_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return value[:4000]
    query = urlencode([
        (key, "***" if _is_sensitive_key(key) else item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
    ])
    return parsed._replace(query=query, fragment="").geturl()[:4000]


def _redact_text(value: str | None, content_type: str = "") -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if "json" in content_type.lower() or stripped.startswith(("{", "[")):
        try:
            return json.dumps(_redact_value(json.loads(value)), ensure_ascii=False)
        except (TypeError, ValueError):
            pass
    if "x-www-form-urlencoded" in content_type.lower() or "=" in value:
        try:
            pairs = parse_qsl(value, keep_blank_values=True)
            if pairs:
                return urlencode([
                    (key, "***" if _is_sensitive_key(key) else item_value)
                    for key, item_value in pairs
                ])
        except ValueError:
            pass
    return _redact_free_text(value)


def _redact_free_text(value: str) -> str:
    """脱敏任意文本，但不把含等号的 HTML/JS 误判为 Form。"""
    redacted = re.sub(
        r'(?i)(["\']?(?:password|passwd|secret|token|authorization|cookie|cvv|cvc)["\']?\s*[:=]\s*)["\']?[^"\'\s,&}]+',
        r'\1***',
        value,
    )
    return str(_redact_value(redacted))


def _redact_html(value: str) -> str:
    """移除 DOM 归档中密码/卡号控件的 value，并脱敏内联 JSON 常见字段。"""
    def redact_input(match: re.Match[str]) -> str:
        tag = match.group(0)
        if not re.search(r'(?i)(type\s*=\s*["\']?password|autocomplete\s*=\s*["\']?(?:[^"\']*password|cc-))', tag):
            return tag
        if re.search(r'(?i)\svalue\s*=', tag):
            return re.sub(r'(?i)(\svalue\s*=\s*)(["\']).*?\2', r'\1"***"', tag)
        return tag[:-1] + ' value="***">'

    html = re.sub(r"(?is)<input\b[^>]*>", redact_input, value)
    return _redact_free_text(html)


def _normalized_replay_url(value: str) -> str:
    """离线匹配忽略查询顺序、缓存戳和敏感值，但保留业务筛选参数。"""
    parsed = urlparse(value)
    ignored = {"_", "cache", "cachebuster", "nonce", "timestamp", "ts"}
    query = urlencode(sorted(
        (
            key,
            "***" if _is_sensitive_key(key) else item_value,
        )
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in ignored and not key.lower().startswith("utm_")
    ))
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=query,
        fragment="",
    ).geturl()


def _request_body_signature(value: str | None, content_type: str = "") -> str:
    redacted = _redact_text(value or "", content_type) or ""
    try:
        normalized = json.dumps(json.loads(redacted), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        normalized = redacted
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def _wait_replay_stable(page: Page, *, timeout_ms: int = 5_000) -> None:
    """等待离线 SPA 从启动骨架渲染到可操作状态，并确认 DOM 连续稳定。"""
    deadline = time.monotonic() + timeout_ms / 1000
    previous: str | None = None
    stable = 0
    while time.monotonic() < deadline and not page.is_closed():
        state = await page.evaluate(
            r"""() => ({
              text: (document.body?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200),
              nodes: document.body?.getElementsByTagName('*').length || 0,
              ready: document.readyState,
            })""",
        )
        signature = json.dumps(state, ensure_ascii=False, sort_keys=True)
        text = str(state.get("text") or "")
        is_loading_shell = text in {"加载中...", "加载中…", "Loading...", "Loading…"}
        if signature == previous and not is_loading_shell and int(state.get("nodes") or 0) > 2:
            stable += 1
            if stable >= 2:
                return
        else:
            stable = 0
            previous = signature
        await page.wait_for_timeout(200)


def _limited_text(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_BODY_BYTES:
        return value, False
    return encoded[:_MAX_BODY_BYTES].decode("utf-8", errors="replace"), True


def _structure_similarity(left: list[str], right: list[str]) -> float:
    """用带节点数量的 Jaccard 比较页面结构，避免动态文本制造快照噪声。"""
    if not left or not right:
        return 0.0
    left_counts = Counter(left)
    right_counts = Counter(right)
    keys = set(left_counts) | set(right_counts)
    intersection = sum(min(left_counts[key], right_counts[key]) for key in keys)
    union = sum(max(left_counts[key], right_counts[key]) for key in keys)
    return intersection / union if union else 1.0


def _safe_replay_headers(headers: dict[str, str]) -> dict[str, str]:
    """移除离线 fulfill 时会失效或造成安全干扰的响应头。"""
    blocked = {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "set-cookie",
        "content-security-policy",
        "content-security-policy-report-only",
        "strict-transport-security",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in blocked
    }


_REPLAY_SCRIPT_TAG_RE = re.compile(
    r"<script\b[^>]*>.*?</script\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)


_REPLAY_INTERACTION_SCRIPT = r"""(() => {
  if (window.__uiRecorderReplayBridgeInstalled) return;
  window.__uiRecorderReplayBridgeInstalled = true;

  const visible = (element) => {
    if (!element || !element.isConnected) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden"
      && rect.width > 1 && rect.height > 1;
  };
  const topDialog = () => Array.from(document.querySelectorAll(
    'dialog[open], [role="dialog"][aria-modal="true"], [role="dialog"][data-state="open"], [aria-modal="true"]',
  )).filter(visible).at(-1) || null;
  const appRoot = () => document.querySelector('#root, #app, [data-reactroot]');
  const normalizedText = (element) => String(
    element?.getAttribute?.("aria-label")
      || element?.getAttribute?.("title")
      || element?.innerText
      || element?.textContent
      || "",
  ).replace(/\s+/g, " ").trim().toLowerCase();
  const dismissPattern = /^(关闭|取消|返回|close|cancel|dismiss|back|×|✕|x)$/i;

  const cleanupStaticDialog = (dialog) => {
    if (!dialog?.isConnected) return false;
    const root = appRoot();
    // React 正常创建的 Portal 会自行响应；只在动作后仍残留时兜底清理。
    const bodyChildren = Array.from(document.body.children);
    const related = bodyChildren.filter((element) => {
      if (element === root || element.contains(root)) return false;
      if (element === dialog || element.contains(dialog)) return true;
      if (element.getAttribute("data-state") !== "open") return false;
      const style = getComputedStyle(element);
      return style.position === "fixed" || Number(style.zIndex || 0) >= 40;
    });
    for (const element of related) element.remove();
    if (dialog.isConnected) dialog.remove();
    document.body.style.removeProperty("pointer-events");
    document.body.style.removeProperty("overflow");
    document.body.removeAttribute("data-scroll-locked");
    for (const element of document.querySelectorAll('[aria-hidden="true"], [inert]')) {
      if (element === root || element.contains(root)) {
        element.removeAttribute("aria-hidden");
        element.removeAttribute("inert");
      }
    }
    return true;
  };

  const scheduleFallback = (dialog) => {
    window.setTimeout(() => {
      if (dialog?.isConnected && visible(dialog)) cleanupStaticDialog(dialog);
    }, 120);
  };

  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const dialog = target?.closest?.(
      'dialog[open], [role="dialog"][aria-modal="true"], [role="dialog"][data-state="open"], [aria-modal="true"]',
    );
    if (!dialog) return;
    const action = target.closest('button, [role="button"], [data-radix-dialog-close]');
    if (!action) return;
    if (action.hasAttribute("data-radix-dialog-close") || dismissPattern.test(normalizedText(action))) {
      scheduleFallback(dialog);
    }
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const dialog = topDialog();
    if (dialog) scheduleFallback(dialog);
  }, true);
})();"""


def _freeze_replay_document(body: bytes) -> bytes:
    """移除快照文档脚本，防止框架重新挂载后丢失截图时的瞬时 UI 状态。"""
    html = body.decode("utf-8", errors="replace")
    return _REPLAY_SCRIPT_TAG_RE.sub("", html).encode("utf-8")


def _package_artifact_path(session_root: Path, value: Any) -> Path:
    """解析离线制品相对路径，并禁止逃逸当前 Session 目录。"""
    root = session_root.resolve()
    path = (root / str(value or "")).resolve()
    if path != root and root not in path.parents:
        raise ValueError("离线制品路径逃逸 Session 目录")
    return path


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


class PickModeRequest(BaseModel):
    enabled: bool


class ReplayStartRequest(BaseModel):
    session_id: int = Field(..., gt=0)
    source_session_ids: list[int] = Field(default_factory=list, max_length=50)
    browser: str = Field("chromium", pattern="^(chromium|firefox|webkit)$")
    headless: bool = False
    entry_url: str | None = Field(None, max_length=4000)
    page_fingerprint: str | None = Field(None, max_length=64)
    page_source_session_id: int | None = Field(None, gt=0)
    viewport: dict[str, int] = Field(default_factory=lambda: {"width": 1440, "height": 900})
    reuse_key: str | None = Field(None, min_length=1, max_length=200)
    freeze_dom: bool = False


class WebActionRequest(BaseModel):
    """平台预览画面转发到受控 Playwright 页面的动作。"""

    action: str = Field(..., pattern="^(click|pick|input|scroll|back|refresh)$")
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
    text: str | None = Field(None, max_length=4000)
    delta_x: int = Field(0, ge=-10000, le=10000)
    delta_y: int = Field(0, ge=-10000, le=10000)


class AiExplorationRequest(BaseModel):
    """Web 安全探索的有界参数。"""

    max_pages: int = Field(40, ge=1, le=200)
    max_depth: int = Field(4, ge=0, le=10)
    max_actions_per_page: int = Field(6, ge=0, le=20)
    timeout_seconds: int = Field(600, ge=30, le=3600)
    login_wait_seconds: int = Field(300, ge=0, le=1800)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=20)
    seed_urls: list[str] = Field(default_factory=list, max_length=200)


@dataclass
class AiExplorationState:
    """Recorder Agent 内存中的 AI 探索进度。"""

    status: str = "idle"
    message: str = "等待启动"
    current_url: str = ""
    discovered_urls: int = 0
    visited_urls: int = 0
    captured_states: int = 0
    executed_actions: int = 0
    skipped_actions: int = 0
    failed_actions: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False
    task: asyncio.Task[None] | None = None

    def serialize(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "current_url": self.current_url,
            "discovered_urls": self.discovered_urls,
            "visited_urls": self.visited_urls,
            "captured_states": self.captured_states,
            "executed_actions": self.executed_actions,
            "skipped_actions": self.skipped_actions,
            "failed_actions": self.failed_actions,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "config": self.config,
        }


def _live_browser_launch_options(body: RecorderStartRequest) -> dict[str, Any]:
    """生成可调整大小的有头浏览器启动参数。"""
    options: dict[str, Any] = {
        "headless": body.headless,
        "slow_mo": body.slow_mo,
    }
    if not body.headless and body.browser == "chromium":
        width = max(640, min(3840, int(body.viewport.get("width") or 1440)))
        height = max(480, min(2160, int(body.viewport.get("height") or 900)))
        options["args"] = [f"--window-size={width},{height}"]
    return options


def _live_browser_context_options(body: RecorderStartRequest) -> dict[str, Any]:
    """有头模式关闭固定 viewport，使内容区跟随真实窗口缩放。"""
    return {"viewport": body.viewport} if body.headless else {"no_viewport": True}


class LocatorValidationRequest(BaseModel):
    strategy: str = Field(..., min_length=1, max_length=40)
    locator: str = Field(..., min_length=1, max_length=4000)


class MobileRecorderStartRequest(BaseModel):
    """启动 Android Emulator / iOS Simulator Appium 录制。"""

    session_id: int = Field(..., gt=0)
    platform: str = Field(..., pattern="^(android|ios)$")
    appium_url: str = Field(..., min_length=1, max_length=1000)
    udid: str = Field(..., min_length=1, max_length=128)
    device_name: str | None = Field(None, max_length=128)
    platform_version: str | None = Field(None, max_length=32)
    app_path: str | None = Field(None, max_length=2000)
    app_identifier: str | None = Field(None, max_length=255)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    restore_scenario: dict[str, Any] = Field(default_factory=dict)

    @field_validator("appium_url")
    @classmethod
    def validate_appium_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("appium_url 必须是完整的 http/https URL")
        return value.rstrip("/")


class MobileActionRequest(BaseModel):
    """平台远程画面转发给 Appium 的用户动作。"""

    action: str = Field(..., pattern="^(tap|input|swipe|back|refresh)$")
    x: int | None = Field(None, ge=0)
    y: int | None = Field(None, ge=0)
    end_x: int | None = Field(None, ge=0)
    end_y: int | None = Field(None, ge=0)
    duration_ms: int = Field(400, ge=100, le=5000)
    text: str | None = Field(None, max_length=4000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return value if value is None else value[:4000]


@dataclass
class RecorderRuntime:
    session_id: int
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    started_monotonic: float = field(default_factory=time.monotonic)
    paused: bool = False
    pick_mode: bool = False
    stopped: bool = False
    sequence_no: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    pages: set[Page] = field(default_factory=set)
    request_keys: dict[Request, str] = field(default_factory=dict)
    request_started_monotonic: dict[Request, float] = field(default_factory=dict)
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    page_records: list[dict[str, Any]] = field(default_factory=list)
    snapshot_fingerprints: set[str] = field(default_factory=set)
    snapshot_tasks: dict[Page, asyncio.Task[None]] = field(default_factory=dict)
    archive_bytes: int = 0
    archive_skipped: int = 0
    offline_package: dict[str, Any] | None = None
    exploration: AiExplorationState = field(default_factory=AiExplorationState)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def session_root(self) -> Path:
        return _ARTIFACT_ROOT / f"session_{self.session_id}"

    async def emit(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        *,
        page: Page | None = None,
        severity: str = "info",
    ) -> dict[str, Any] | None:
        if self.paused and source != "agent" and event_type not in {"user.pick", "page.snapshot"}:
            return None
        async with self.lock:
            self.sequence_no += 1
            event_key = uuid.uuid4().hex
            url = page.url if page is not None else str(payload.get("url") or "")
            element = payload.get("element")
            if isinstance(element, dict):
                seed = str(element.pop("fingerprint_seed", ""))
                if not element.get("fingerprint"):
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
                    "text": (_redact_text(message.text) or "")[:10_000],
                    "location": message.location,
                    "url": _redact_url(page.url),
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
                {
                    "message": (_redact_text(str(error)) or "")[:10_000],
                    "url": _redact_url(page.url),
                },
                page=page,
                severity="error",
            )),
        )
        page.on(
            "framenavigated",
            lambda frame: frame == page.main_frame
            and asyncio.create_task(self.handle_navigation(page, frame.url)),
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
        page.on(
            "domcontentloaded",
            lambda: asyncio.create_task(self.handle_dom_content_loaded(page)),
        )

    async def handle_dom_content_loaded(self, page: Page) -> None:
        """导航完成后恢复拾取状态，并等待页面稳定后保存业务页面。"""
        if self.pick_mode:
            try:
                await page.evaluate("window.__uiRecorderSetPickMode?.(true)")
            except Exception:  # noqa: BLE001
                pass
        if self.paused or self.stopped:
            return
        self.schedule_page_snapshot(page, "domcontentloaded", delay_ms=350)

    async def handle_navigation(self, page: Page, url: str) -> None:
        """同时覆盖整页导航与 React Router 等 History API 路由变化。"""
        await self.emit(
            "page.navigation",
            "browser",
            {"url": url, "title": ""},
            page=page,
        )
        if self.paused or self.stopped:
            return
        self.schedule_page_snapshot(page, "navigation", delay_ms=350)

    def schedule_page_snapshot(self, page: Page, reason: str, *, delay_ms: int = 350) -> None:
        """合并短时间内的路由/交互信号，只归档最终稳定状态。"""
        if self.paused or self.stopped or page.is_closed():
            return
        previous = self.snapshot_tasks.get(page)
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(
            self.capture_stable_page_document(page, reason, delay_ms=delay_ms),
        )
        self.snapshot_tasks[page] = task

        def remove_finished(finished: asyncio.Task[None]) -> None:
            if self.snapshot_tasks.get(page) is finished:
                self.snapshot_tasks.pop(page, None)

        task.add_done_callback(remove_finished)

    async def capture_stable_page_document(
        self,
        page: Page,
        reason: str,
        *,
        delay_ms: int = 350,
    ) -> None:
        """在有界等待内确认 SPA 已渲染，避免把加载骨架当成页面快照。"""
        try:
            if delay_ms:
                await page.wait_for_timeout(delay_ms)
            await self.wait_for_page_stability(page)
            await self.capture_page_document(page, reason=reason)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "稳定态快照失败 session=%s reason=%s: %s",
                self.session_id,
                reason,
                exc,
            )

    async def capture_latest_page_document(
        self,
        page: Page,
        *,
        reason: str,
    ) -> dict[str, Any] | None:
        """取消同一页面的延迟快照，只归档调用方刚确认过的最新稳定状态。"""
        pending = self.snapshot_tasks.pop(page, None)
        if pending is not None and pending is not asyncio.current_task() and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        return await self.capture_page_document(page, reason=reason)

    async def wait_for_page_stability(self, page: Page) -> None:
        """等待连续三个 DOM 采样一致；有界快速采集，不被长期轮询请求卡住。"""
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1_000)
        except Exception:  # noqa: BLE001
            pass

        previous: str | None = None
        stable_samples = 0
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not page.is_closed():
            signature = await page.evaluate(
                """() => JSON.stringify({
                  url: location.href,
                  ready: document.readyState,
                  htmlLength: document.documentElement?.outerHTML.length || 0,
                  textLength: document.body?.innerText.length || 0,
                  childCount: document.body?.getElementsByTagName("*").length || 0,
                  scrollWidth: document.documentElement?.scrollWidth || 0,
                  scrollHeight: document.documentElement?.scrollHeight || 0,
                  resourceCount: performance.getEntriesByType("resource").length,
                })""",
            )
            if signature == previous:
                stable_samples += 1
                if stable_samples >= 2:
                    return
            else:
                previous = signature
                stable_samples = 0
            await page.wait_for_timeout(200)

    async def handle_user_event(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        page = source.get("page")
        if not isinstance(page, Page):
            return
        event_type = str(payload.get("event_type") or "user.unknown")
        clean_payload = dict(payload)
        clean_payload.pop("event_type", None)
        clean_payload["url"] = page.url
        emitted = await self.emit(event_type, "user", clean_payload, page=page)
        if emitted and event_type in {"user.click", "user.pick", "user.input", "user.change"}:
            asyncio.create_task(self.capture_screenshot(page, event_type))
        if emitted and event_type in {"user.click", "user.input", "user.change", "user.submit"}:
            self.schedule_page_snapshot(page, event_type, delay_ms=300)
        if emitted and event_type == "environment.resize":
            self.schedule_page_snapshot(page, event_type, delay_ms=500)

    async def perform_web_action(self, body: WebActionRequest) -> None:
        """把平台画面动作转发给当前受控页面，并同步生成新状态快照。"""
        page = next(
            (candidate for candidate in reversed(self.context.pages) if not candidate.is_closed()),
            None,
        )
        if page is None:
            raise ValueError("当前没有可操作的 Web 页面")
        if body.action in {"click", "pick"} and (body.x is None or body.y is None):
            raise ValueError(f"{body.action} 动作必须提供 x/y 坐标")
        if body.action == "input" and body.text is None:
            raise ValueError("input 动作必须提供 text")

        if body.action == "pick":
            element = await page.evaluate(
                "({x, y}) => window.__uiRecorderDescribeAt?.(x, y) || null",
                {"x": body.x, "y": body.y},
            )
            if not isinstance(element, dict):
                raise ValueError("坐标位置没有可拾取元素")
            await self.emit(
                "user.pick",
                "user",
                {
                    "url": page.url,
                    "page_title": await page.title(),
                    "element": element,
                },
                page=page,
            )
            return
        if body.action == "click":
            await page.mouse.click(body.x or 0, body.y or 0)
        elif body.action == "input":
            if body.x is not None and body.y is not None:
                await page.mouse.click(body.x, body.y)
            await page.keyboard.press("ControlOrMeta+A")
            await page.keyboard.insert_text(body.text or "")
        elif body.action == "scroll":
            await page.mouse.wheel(body.delta_x, body.delta_y)
        elif body.action == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=10_000)
        elif body.action == "refresh":
            await page.reload(wait_until="domcontentloaded", timeout=30_000)

        pending = self.snapshot_tasks.pop(page, None)
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await self.capture_stable_page_document(page, f"web.{body.action}", delay_ms=250)

    async def set_pick_mode(self, enabled: bool) -> None:
        """在所有受控页面中切换非破坏性拾取。"""
        self.pick_mode = enabled
        await asyncio.gather(
            *(
                page.evaluate(
                    "enabled => window.__uiRecorderSetPickMode?.(enabled)",
                    enabled,
                )
                for page in self.pages
                if not page.is_closed()
            ),
            return_exceptions=True,
        )
        await self.emit(
            "agent.pick_mode",
            "agent",
            {"enabled": enabled},
        )

    async def capture_screenshot(self, page: Page, reason: str) -> None:
        if self.stopped:
            return
        await page.wait_for_timeout(150)
        target_dir = _ARTIFACT_ROOT / f"session_{self.session_id}" / "screenshots"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"
        try:
            await page.screenshot(
                path=str(path),
                full_page=False,
                mask=[page.locator(_SENSITIVE_SELECTOR)],
                mask_color="#64748b",
            )
            await self.emit(
                "screen.capture",
                "screen",
                {"reason": reason, "path": str(path), "url": page.url},
                page=page,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("截图失败 session=%s: %s", self.session_id, exc)

    async def capture_request(self, page: Page, request: Request) -> None:
        if self.paused or request.resource_type not in {"xhr", "fetch"}:
            return
        request_key = uuid.uuid4().hex
        self.request_keys[request] = request_key
        self.request_started_monotonic[request] = time.monotonic()
        request_headers = await request.all_headers()
        content_type = request_headers.get("content-type", "")
        body, truncated = _limited_text(_redact_text(request.post_data, content_type))
        await self.emit(
            "network.request",
            "network",
            {
                "request_key": request_key,
                "resource_type": request.resource_type,
                "method": request.method,
                "url": _redact_url(request.url),
                "headers": _redact_headers(request_headers),
                "body": body,
                "body_truncated": truncated,
            },
            page=page,
        )

    async def capture_response(self, page: Page, response: Response) -> None:
        if self.paused:
            return
        request = response.request
        if request.resource_type in _ARCHIVE_RESOURCE_TYPES:
            await self.capture_resource(response)
        if request.resource_type in {"document", "stylesheet", "script"} and not self.stopped:
            self.schedule_page_snapshot(page, f"resource.{request.resource_type}", delay_ms=350)
        if request.resource_type not in {"xhr", "fetch"}:
            return
        started_at = self.request_started_monotonic.pop(request, None)
        body_text: str | None = None
        body_truncated = False
        content_type = (await response.all_headers()).get("content-type", "")
        if any(kind in content_type for kind in ("json", "text", "javascript", "xml")):
            try:
                raw = await response.body()
                body_text, body_truncated = _limited_text(
                    _redact_text(raw.decode("utf-8", errors="replace"), content_type),
                )
            except Exception:  # noqa: BLE001
                body_text = None
        await self.emit(
            "network.response",
            "network",
            {
                "request_key": self.request_keys.get(request),
                "method": request.method,
                "url": _redact_url(response.url),
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
        if not self.stopped:
            # 首屏数据或弹框内容可能在导航后才返回；响应完成后补采最终 DOM。
            self.schedule_page_snapshot(page, "network.response", delay_ms=250)

    async def capture_resource(self, response: Response) -> None:
        """归档离线重放需要的 HTML、JS、CSS、字体和图片。"""
        url = response.url
        if url in self.resources:
            return
        try:
            body = await response.body()
            headers = await response.all_headers()
        except Exception as exc:  # noqa: BLE001
            self.archive_skipped += 1
            logger.debug("资源归档失败 session=%s url=%s: %s", self.session_id, url, exc)
            return
        if self.archive_bytes + len(body) > _MAX_ARCHIVE_BYTES:
            self.archive_skipped += 1
            return
        resource_dir = self.session_root / "resources"
        resource_dir.mkdir(parents=True, exist_ok=True)
        resource_name = f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.bin"
        resource_path = resource_dir / resource_name
        content_type = headers.get("content-type", "")
        safe_body = body
        if response.request.resource_type == "document" and "html" in content_type:
            safe_body = _redact_html(body.decode("utf-8", errors="replace")).encode("utf-8")
        resource_path.write_bytes(safe_body)
        self.archive_bytes += len(safe_body)
        self.resources[url] = {
            "url": url,
            "path": str(resource_path.relative_to(self.session_root)),
            "status": response.status,
            "headers": _safe_replay_headers(headers),
            "resource_type": response.request.resource_type,
            "size": len(safe_body),
            "sha256": hashlib.sha256(safe_body).hexdigest(),
        }

    async def capture_request_failed(self, page: Page, request: Request) -> None:
        if self.paused or request.resource_type not in {"xhr", "fetch"}:
            return
        started_at = self.request_started_monotonic.pop(request, None)
        await self.emit(
            "network.failed",
            "network",
            {
                "request_key": self.request_keys.get(request),
                "method": request.method,
                "url": _redact_url(request.url),
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

    async def capture_page_document(
        self,
        page: Page,
        *,
        reason: str = "manual",
    ) -> dict[str, Any] | None:
        """保存一个可追踪的页面 DOM 状态和全页截图。"""
        if page.is_closed():
            return None
        url = page.url
        title = await page.title()
        html = _redact_html(await page.content())
        page_meta = await page.evaluate(
            "() => window.__uiRecorderPageMeta?.() || ({ page_name: document.title, state_name: '默认页面' })",
        )
        if not isinstance(page_meta, dict):
            page_meta = {"page_name": title, "state_name": "默认页面"}
        visible_elements = await page.evaluate(
            "() => window.__uiRecorderCollectElements?.() || []",
        )
        viewport = await page.evaluate(
            "() => ({width: innerWidth, height: innerHeight, device_pixel_ratio: devicePixelRatio})",
        )
        raw_storage = await page.evaluate(
            r"""() => ({
              origin: location.origin,
              local_storage: Object.fromEntries(
                Array.from({length: localStorage.length}, (_, index) => {
                  const key = localStorage.key(index);
                  return key == null ? null : [key, localStorage.getItem(key) || ''];
                }).filter(Boolean),
              ),
              session_storage: Object.fromEntries(
                Array.from({length: sessionStorage.length}, (_, index) => {
                  const key = sessionStorage.key(index);
                  return key == null ? null : [key, sessionStorage.getItem(key) || ''];
                }).filter(Boolean),
              ),
            })""",
        )
        if not isinstance(raw_storage, dict):
            raw_storage = {}
        storage_state = {
            "origin": str(raw_storage.get("origin") or ""),
            "local_storage": {
                str(key)[:500]: _redact_storage_value(str(key), str(value))
                for key, value in dict(raw_storage.get("local_storage") or {}).items()
            },
            "session_storage": {
                str(key)[:500]: _redact_storage_value(str(key), str(value))
                for key, value in dict(raw_storage.get("session_storage") or {}).items()
            },
        }
        structure_tokens = await page.evaluate(
            r"""() => Array.from(document.querySelectorAll('*')).slice(0, 5000).map((element) => {
              const style = getComputedStyle(element);
              const visible = style.display !== 'none' && style.visibility !== 'hidden';
              const stableId = /\d{4,}/.test(element.id || '') ? '' : (element.id || '');
              return [
                element.tagName.toLowerCase(),
                element.getAttribute('role') || '',
                stableId,
                element.getAttribute('data-testid') || element.getAttribute('data-test') || '',
                element.getAttribute('type') || '',
                element.hasAttribute('open') ? 'open' : '',
                element.getAttribute('aria-expanded') || '',
                visible ? 'visible' : 'hidden',
              ].join('|');
            })""",
        )
        if not isinstance(structure_tokens, list):
            structure_tokens = []
        structure_tokens = [str(item)[:300] for item in structure_tokens[:5000]]
        normalized_elements: list[dict[str, Any]] = []
        for raw_element in visible_elements if isinstance(visible_elements, list) else []:
            if not isinstance(raw_element, dict):
                continue
            item = dict(raw_element)
            seed = str(item.pop("fingerprint_seed", ""))
            if not seed:
                continue
            item["fingerprint"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            normalized_elements.append(item)
        page_key = _page_key(url)
        state_name = str(page_meta.get("state_name") or "默认页面")
        exact_fingerprint = hashlib.sha256(f"{url}\n{html}".encode("utf-8")).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "page_key": page_key,
                    "state_name": state_name,
                    "structure": structure_tokens,
                    "viewport": viewport,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint in self.snapshot_fingerprints:
            return next(
                (item for item in self.page_records if item["fingerprint"] == fingerprint),
                None,
            )
        similar = next(
            (
                item
                for item in reversed(self.page_records)
                if item.get("page_key") == page_key
                and item.get("state_name") == state_name
                and item.get("viewport") == viewport
                and _structure_similarity(
                    list(item.get("structure_tokens") or []),
                    structure_tokens,
                ) >= 0.95
            ),
            None,
        )
        if similar is not None:
            return similar
        index = len(self.page_records) + 1
        document_dir = self.session_root / "documents"
        screenshot_dir = self.session_root / "screenshots"
        document_dir.mkdir(parents=True, exist_ok=True)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        document_path = document_dir / f"page_{index}_{fingerprint[:12]}.html"
        screenshot_path = screenshot_dir / f"page_{index}_{fingerprint[:12]}.png"
        document_path.write_text(html, encoding="utf-8")
        await page.screenshot(
            path=str(screenshot_path),
            full_page=False,
            mask=[page.locator(_SENSITIVE_SELECTOR)],
            mask_color="#64748b",
        )
        screenshot_bytes = screenshot_path.read_bytes()
        page_record = {
            "url": url,
            "title": title,
            "page_name": str(page_meta.get("page_name") or title),
            "state_name": state_name,
            "modal_open": bool(page_meta.get("modal_open")),
            "page_key": page_key,
            "fingerprint": fingerprint,
            "exact_fingerprint": exact_fingerprint,
            "structure_tokens": structure_tokens,
            "document_path": str(document_path.relative_to(self.session_root)),
            "screenshot_path": str(screenshot_path.relative_to(self.session_root)),
            "document_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "screenshot_sha256": hashlib.sha256(screenshot_bytes).hexdigest(),
            "capture_reason": reason,
            "viewport": viewport if isinstance(viewport, dict) else {},
            "storage_state": storage_state,
            "visible_elements": normalized_elements,
            "visible_element_fingerprints": [
                item["fingerprint"] for item in normalized_elements
            ],
        }
        self.snapshot_fingerprints.add(fingerprint)
        self.page_records.append(page_record)
        await self.emit(
            "page.snapshot",
            "browser",
            {key: value for key, value in page_record.items() if key != "structure_tokens"},
            page=page,
        )
        return page_record

    async def build_offline_package(self) -> dict[str, Any]:
        """停止前保存页面 DOM，并生成严格离线回放清单。"""
        offline_dir = self.session_root / "offline"
        offline_dir.mkdir(parents=True, exist_ok=True)

        for page in self.pages:
            if page.is_closed():
                continue
            try:
                pending = self.snapshot_tasks.pop(page, None)
                if pending is not None and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await self.wait_for_page_stability(page)
                await self.capture_page_document(page, reason="session.stop")
            except Exception as exc:  # noqa: BLE001
                logger.warning("页面快照失败 session=%s: %s", self.session_id, exc)
        pages = [
            {key: value for key, value in item.items() if key != "structure_tokens"}
            for item in self.page_records
        ]

        requests_by_key = {
            str(event["payload"].get("request_key")): event
            for event in self.events
            if event["event_type"] == "network.request"
            and event["payload"].get("request_key")
        }
        mocks: list[dict[str, Any]] = []
        for event in self.events:
            if event["event_type"] != "network.response":
                continue
            response_payload = event["payload"]
            request_key = str(response_payload.get("request_key") or "")
            request_event = requests_by_key.get(request_key)
            if request_event is None:
                continue
            request_payload = request_event["payload"]
            mocks.append({
                "exchange_key": event["event_key"],
                "sequence_no": event["sequence_no"],
                "request_key": request_key,
                "method": request_payload.get("method") or "GET",
                "url": request_payload.get("url") or response_payload.get("url"),
                "request": request_payload,
                "response": response_payload,
                "match_rule": {
                    "method": str(request_payload.get("method") or "GET").upper(),
                    "normalized_url": _normalized_replay_url(
                        str(request_payload.get("url") or response_payload.get("url") or ""),
                    ),
                    "body_sha256": _request_body_signature(
                        str(request_payload.get("body") or ""),
                        str((request_payload.get("headers") or {}).get("content-type") or ""),
                    ),
                },
            })

        limitations = [
            "WebSocket、流式响应和 Service Worker 缓存暂不进入离线包",
            "只有录制期间实际加载的静态资源和 XHR/Fetch 响应可离线回放",
        ]
        if self.archive_skipped:
            limitations.append(f"{self.archive_skipped} 个资源因读取失败或容量上限未归档")
        manifest = {
            "version": 1,
            "session_id": self.session_id,
            "entry_url": pages[0]["url"] if pages else None,
            "created_at": datetime.now().isoformat(),
            "pages": pages,
            "resources": list(self.resources.values()),
            "mocks": mocks,
            "archive_bytes": self.archive_bytes,
            "limitations": limitations,
            "offline_enforced": True,
            "integrity": "sha256",
        }
        manifest_path = offline_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        package = {
            "manifest_path": str(manifest_path),
            "entry_url": manifest["entry_url"],
            "page_count": len(pages),
            "resource_count": len(self.resources),
            "mock_count": len(mocks),
            "archive_bytes": self.archive_bytes,
            "limitations": limitations,
            "ready": bool(pages),
            "integrity_verified": True,
        }
        self.offline_package = package
        await self.emit("offline.package", "agent", package)
        return package

    async def close(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        exploration_task = self.exploration.task
        if (
            exploration_task is not None
            and exploration_task is not asyncio.current_task()
            and not exploration_task.done()
        ):
            self.exploration.cancel_requested = True
            exploration_task.cancel()
        pending_tasks = list(self.snapshot_tasks.values())
        self.snapshot_tasks.clear()
        for task in pending_tasks:
            if not task.done():
                task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        try:
            await self.context.close()
        finally:
            try:
                await self.browser.close()
            finally:
                await self.playwright.stop()


_EXPLORATION_DANGER_PATTERN = re.compile(
    r"删除|移除|停用|禁用|注销|退出登录|登出|运行|执行|发布|提交|保存|新建|创建|"
    r"编辑|修改|上传|下载|支付|购买|付款|重置|清空|确认|同意|授权|"
    r"delete|remove|disable|logout|sign\s*out|run|execute|publish|submit|save|"
    r"create|new|edit|upload|download|pay|purchase|reset|clear|confirm|approve",
    re.IGNORECASE,
)
_EXPLORATION_SAFE_BUTTON_PATTERN = re.compile(
    r"工作台|首页|项目|需求|用例|记录|报告|设备|脚本|管理|列表|详情|查看|"
    r"菜单|导航|标签|展开|收起|下一页|上一页|返回|关闭|取消|"
    r"home|workspace|project|requirement|case|record|report|device|script|"
    r"manage|list|detail|view|menu|navigation|tab|expand|collapse|next|previous|back|close|cancel",
    re.IGNORECASE,
)
_EXPLORATION_CANDIDATES_SCRIPT = r"""() => {
  const selector = [
    'a[href]', 'button', '[role="button"]', '[role="link"]', '[role="tab"]',
    '[role="menuitem"]', 'summary', '[aria-expanded]'
  ].join(',');
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
      && Number(style.opacity || 1) > 0 && rect.width > 2 && rect.height > 2
      && rect.bottom >= 0 && rect.right >= 0
      && rect.top <= innerHeight && rect.left <= innerWidth;
  };
  const escapeCss = (value) => window.CSS?.escape
    ? window.CSS.escape(value)
    : String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  const cssPath = (element) => {
    if (element.id) return `#${escapeCss(element.id)}`;
    const testAttribute = element.hasAttribute('data-testid') ? 'data-testid' : 'data-test';
    const testId = element.getAttribute(testAttribute);
    if (testId) return `[${testAttribute}="${String(testId).replace(/"/g, '\\"')}"]`;
    const parts = [];
    let current = element;
    while (current && current !== document.body && parts.length < 7) {
      const tag = current.tagName.toLowerCase();
      const siblings = Array.from(current.parentElement?.children || []).filter(
        (item) => item.tagName === current.tagName,
      );
      parts.unshift(`${tag}:nth-of-type(${Math.max(1, siblings.indexOf(current) + 1)})`);
      current = current.parentElement;
    }
    return `body > ${parts.join(' > ')}`;
  };
  return Array.from(document.querySelectorAll(selector)).filter(visible).slice(0, 300).map((element) => {
    const text = String(
      element.getAttribute('aria-label')
        || element.getAttribute('title')
        || element.innerText
        || element.textContent
        || '',
    ).replace(/\s+/g, ' ').trim().slice(0, 180);
    const form = element.closest('form');
    return {
      selector: cssPath(element),
      tag: element.tagName.toLowerCase(),
      role: element.getAttribute('role') || '',
      text,
      href: element.href || '',
      target: element.getAttribute('target') || '',
      aria_expanded: element.getAttribute('aria-expanded'),
      disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
      input_type: String(element.getAttribute('type') || '').toLowerCase(),
      in_form: Boolean(form),
      form_method: String(form?.method || '').toUpperCase(),
    };
  });
}"""


def _exploration_url_key(value: str) -> str:
    """探索图的 URL 去重键，忽略片段和常见缓存参数。"""
    return _normalized_replay_url(value)


def _safe_exploration_candidate(
    candidate: dict[str, Any],
    *,
    allowed_hosts: set[str],
) -> tuple[bool, str]:
    """对候选动作做确定性安全门禁，AI 语义排序不得绕过此规则。"""
    text = str(candidate.get("text") or "").strip()
    href = str(candidate.get("href") or "").strip()
    if candidate.get("disabled"):
        return False, "控件不可用"
    if candidate.get("in_form") or candidate.get("input_type") == "submit":
        return False, "表单提交动作"
    if _EXPLORATION_DANGER_PATTERN.search(text):
        return False, "危险动作关键词"
    if href:
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            return False, "非网页链接"
        if parsed.netloc.lower() not in allowed_hosts:
            return False, "超出允许域名"
        route_identity = unquote(f"{parsed.path}?{parsed.query}")
        if _EXPLORATION_DANGER_PATTERN.search(route_identity):
            return False, "危险链接地址"
        return True, "同域页面链接"
    role = str(candidate.get("role") or "").lower()
    tag = str(candidate.get("tag") or "").lower()
    if role in {"tab", "menuitem", "link"} or tag == "summary":
        return True, "导航型控件"
    if candidate.get("aria_expanded") in {"true", "false"}:
        return True, "展开型控件"
    if text and _EXPLORATION_SAFE_BUTTON_PATTERN.search(text):
        return True, "安全语义控件"
    return False, "未知按钮默认跳过"


async def _page_waits_for_manual_login(page: Page) -> bool:
    """判断当前是否停留在需要人工输入凭据的登录页。"""
    return bool(await page.evaluate(
        r"""() => Array.from(document.querySelectorAll(
          'input[type="password"], input[autocomplete*="password"]'
        )).some((element) => {
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden'
            && rect.width > 2 && rect.height > 2;
        })""",
    ))


async def _emit_exploration_progress(runtime: RecorderRuntime) -> None:
    """把进度同时写入录制时间线，便于平台断线后恢复摘要。"""
    page = next((item for item in reversed(list(runtime.pages)) if not item.is_closed()), None)
    await runtime.emit(
        "ai.exploration.progress",
        "ai",
        runtime.exploration.serialize(),
        page=page,
    )


async def _run_ai_exploration(
    runtime: RecorderRuntime,
    body: AiExplorationRequest,
) -> None:
    """在现有已登录录制上下文中执行有界、同域、非破坏性的页面探索。"""
    state = runtime.exploration
    state.status = "running"
    state.message = "正在分析当前页面"
    state.started_at = datetime.now().isoformat()
    state.config = body.model_dump()
    deadline = time.monotonic() + body.timeout_seconds
    initial_record_count = len(runtime.page_records)
    try:
        page = next(
            (candidate for candidate in reversed(list(runtime.pages)) if not candidate.is_closed()),
            None,
        )
        if page is None:
            raise ValueError("当前没有可探索的 Web 页面")
        start_host = urlparse(page.url).netloc.lower()
        allowed_hosts = {
            item.strip().lower()
            for item in body.allowed_hosts
            if item.strip()
        } or {start_host}
        if start_host not in allowed_hosts:
            allowed_hosts.add(start_host)
        state.config = {**state.config, "allowed_hosts": sorted(allowed_hosts)}

        if await _page_waits_for_manual_login(page):
            state.status = "waiting_for_login"
            state.message = "请在受控浏览器完成登录，登录成功后会自动继续"
            await _emit_exploration_progress(runtime)
            login_deadline = min(deadline, time.monotonic() + body.login_wait_seconds)
            while time.monotonic() < login_deadline and not state.cancel_requested:
                await page.wait_for_timeout(800)
                if not await _page_waits_for_manual_login(page):
                    break
            if await _page_waits_for_manual_login(page):
                if state.cancel_requested:
                    state.status = "cancelled"
                    state.message = "AI 探索已取消"
                    return
                raise TimeoutError("等待人工登录超时")
            state.status = "running"
            state.message = "登录完成，开始安全探索"

        queue: list[tuple[str, int]] = []
        queued: set[str] = set()
        for raw_seed in [page.url, *body.seed_urls]:
            seed_url = urljoin(page.url, str(raw_seed).strip())
            seed_key = _exploration_url_key(seed_url)
            if not seed_key or seed_key in queued:
                continue
            queue.append((seed_url, 0))
            queued.add(seed_key)
        state.discovered_urls = len(queued)
        visited: set[str] = set()
        while queue and len(visited) < body.max_pages:
            if state.cancel_requested:
                state.status = "cancelled"
                state.message = "AI 探索已取消"
                break
            if time.monotonic() >= deadline:
                state.status = "completed"
                state.message = "已达到最长探索时间"
                break
            target_url, depth = queue.pop(0)
            target_key = _exploration_url_key(target_url)
            if target_key in visited or depth > body.max_depth:
                continue
            parsed_target = urlparse(target_url)
            if parsed_target.netloc.lower() not in allowed_hosts:
                state.skipped_actions += 1
                continue
            if _exploration_url_key(page.url) != target_key:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
            await runtime.wait_for_page_stability(page)
            if await _page_waits_for_manual_login(page):
                state.skipped_actions += 1
                continue

            state.current_url = page.url
            state.message = f"正在探索第 {len(visited) + 1} 个页面"
            visited.add(target_key)
            await runtime.capture_latest_page_document(
                page,
                reason="ai.exploration.page",
            )
            state.visited_urls = len(visited)
            state.captured_states = max(
                0,
                len(runtime.page_records) - initial_record_count,
            )

            raw_candidates = await page.evaluate(_EXPLORATION_CANDIDATES_SCRIPT)
            candidates = [
                item for item in raw_candidates
                if isinstance(item, dict)
            ] if isinstance(raw_candidates, list) else []
            safe_buttons: list[dict[str, Any]] = []
            for candidate in candidates:
                safe, _reason = _safe_exploration_candidate(
                    candidate,
                    allowed_hosts=allowed_hosts,
                )
                if not safe:
                    state.skipped_actions += 1
                    continue
                href = str(candidate.get("href") or "").strip()
                if href:
                    next_url = urljoin(page.url, href)
                    next_key = _exploration_url_key(next_url)
                    if (
                        depth < body.max_depth
                        and next_key not in visited
                        and next_key not in queued
                    ):
                        queue.append((next_url, depth + 1))
                        queued.add(next_key)
                else:
                    safe_buttons.append(candidate)

            for candidate in safe_buttons[:body.max_actions_per_page]:
                if state.cancel_requested or time.monotonic() >= deadline:
                    break
                parent_url = page.url
                try:
                    locator = page.locator(str(candidate.get("selector") or "")).first
                    await locator.click(timeout=3_000)
                    state.executed_actions += 1
                    await runtime.wait_for_page_stability(page)
                    await runtime.capture_latest_page_document(
                        page,
                        reason="ai.exploration.action",
                    )
                    state.captured_states = max(
                        0,
                        len(runtime.page_records) - initial_record_count,
                    )
                    if page.url != parent_url:
                        next_key = _exploration_url_key(page.url)
                        if (
                            depth < body.max_depth
                            and next_key not in visited
                            and next_key not in queued
                            and urlparse(page.url).netloc.lower() in allowed_hosts
                        ):
                            queue.append((page.url, depth + 1))
                            queued.add(next_key)
                    if not page.is_closed():
                        await page.goto(parent_url, wait_until="domcontentloaded", timeout=30_000)
                        await runtime.wait_for_page_stability(page)
                except Exception as exc:  # noqa: BLE001
                    state.failed_actions += 1
                    logger.debug(
                        "AI 探索动作失败 session=%s selector=%s: %s",
                        runtime.session_id,
                        candidate.get("selector"),
                        exc,
                    )

            state.discovered_urls = len(queued)
            await _emit_exploration_progress(runtime)

        if state.status in {"running", "waiting_for_login"}:
            state.status = "completed"
            state.message = "AI 安全探索已完成"
    except Exception as exc:  # noqa: BLE001
        state.status = "failed"
        state.message = "AI 探索失败"
        state.error = str(exc)
        logger.exception("AI 探索失败 session=%s", runtime.session_id)
    finally:
        state.current_url = next(
            (item.url for item in reversed(list(runtime.pages)) if not item.is_closed()),
            state.current_url,
        )
        state.captured_states = max(0, len(runtime.page_records) - initial_record_count)
        state.finished_at = datetime.now().isoformat()
        await _emit_exploration_progress(runtime)
        if not runtime.stopped:
            try:
                await runtime.build_offline_package()
                await runtime.emit(
                    "agent.disconnected",
                    "agent",
                    {"reason": "ai_exploration_finished"},
                )
            finally:
                await runtime.close()


_ANDROID_BOUNDS_RE = re.compile(r"\[(?P<x1>-?\d+),(?P<y1>-?\d+)\]\[(?P<x2>-?\d+),(?P<y2>-?\d+)\]")


def _mobile_node_bounds(attributes: dict[str, str]) -> dict[str, int] | None:
    """兼容 Android bounds 与 iOS x/y/width/height。"""
    match = _ANDROID_BOUNDS_RE.fullmatch(attributes.get("bounds") or "")
    if match:
        x1 = int(match.group("x1"))
        y1 = int(match.group("y1"))
        x2 = int(match.group("x2"))
        y2 = int(match.group("y2"))
        return {
            "x": x1,
            "y": y1,
            "width": max(0, x2 - x1),
            "height": max(0, y2 - y1),
        }
    try:
        return {
            "x": round(float(attributes["x"])),
            "y": round(float(attributes["y"])),
            "width": max(0, round(float(attributes["width"]))),
            "height": max(0, round(float(attributes["height"]))),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _mobile_element_from_source(
    source: str,
    x: int,
    y: int,
    platform: str,
) -> dict[str, Any] | None:
    """从当前 UI Tree 中解析坐标命中的最小元素，并生成移动定位器。"""
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return None

    candidates: list[tuple[int, ET.Element, str, dict[str, int]]] = []

    def walk_with_path(node: ET.Element, path: str) -> None:
        attributes = {str(key): str(value) for key, value in node.attrib.items()}
        bounds = _mobile_node_bounds(attributes)
        if bounds:
            right = bounds["x"] + bounds["width"]
            bottom = bounds["y"] + bounds["height"]
            if bounds["x"] <= x <= right and bounds["y"] <= y <= bottom:
                candidates.append((bounds["width"] * bounds["height"], node, path, bounds))
        tag_counts: dict[str, int] = {}
        for child in list(node):
            child_tag = str(child.tag).split("}")[-1]
            tag_counts[child_tag] = tag_counts.get(child_tag, 0) + 1
            walk_with_path(child, f"{path}/{child_tag}[{tag_counts[child_tag]}]")

    root_tag = str(root.tag).split("}")[-1]
    walk_with_path(root, f"/{root_tag}[1]")
    if not candidates:
        return None
    _area, node, xpath, bounds = min(candidates, key=lambda item: item[0])
    attrs = {str(key): str(value) for key, value in node.attrib.items()}
    element_type = attrs.get("class") or attrs.get("type") or str(node.tag).split("}")[-1]
    resource_id = attrs.get("resource-id") or attrs.get("resourceId") or ""
    accessibility = attrs.get("content-desc") or attrs.get("name") or attrs.get("label") or ""
    text = attrs.get("text") or attrs.get("label") or attrs.get("value") or ""
    semantic_name = accessibility or text or resource_id.rsplit("/", 1)[-1] or element_type
    locators: list[dict[str, Any]] = []
    if platform == "android":
        if resource_id:
            locators.append({"strategy": "id", "locator": resource_id, "score": 98})
        if accessibility:
            locators.append({"strategy": "accessibility_id", "locator": accessibility, "score": 96})
        if text:
            escaped = text.replace('"', '\\"')
            locators.append({
                "strategy": "android_uiautomator",
                "locator": f'new UiSelector().text("{escaped}")',
                "score": 82,
            })
    else:
        if accessibility:
            locators.append({"strategy": "accessibility_id", "locator": accessibility, "score": 96})
            predicate_value = accessibility.replace("'", "\\'")
            locators.append({
                "strategy": "ios_predicate",
                "locator": f"name == '{predicate_value}'",
                "score": 90,
            })
            locators.append({
                "strategy": "ios_class_chain",
                "locator": f"**/{element_type}[`name == '{predicate_value}'`]",
                "score": 86,
            })
    if text and not any(item["locator"] == text for item in locators):
        locators.append({"strategy": "text", "locator": text, "score": 76})
    locators.append({"strategy": "xpath", "locator": xpath, "score": 62})
    return {
        "semantic_name": semantic_name[:200],
        "element_type": element_type[:100],
        "fingerprint_seed": "|".join(
            [platform, element_type, resource_id, accessibility, text, xpath]
        ),
        "attributes": {
            **attrs,
            "bounds": bounds,
        },
        "locators": locators,
    }


def _mobile_options(body: MobileRecorderStartRequest):
    """构建严格 W3C 的 Appium Options。"""
    caps: dict[str, Any] = {
        "platformName": "Android" if body.platform == "android" else "iOS",
        "appium:automationName": "UiAutomator2" if body.platform == "android" else "XCUITest",
        "appium:udid": body.udid,
        "appium:deviceName": body.device_name or body.udid,
        "appium:noReset": True,
        "appium:newCommandTimeout": 1800,
    }
    if body.platform_version:
        caps["appium:platformVersion"] = body.platform_version
    if body.app_path:
        app_path = Path(body.app_path).expanduser().resolve()
        if not app_path.is_file():
            raise ValueError(f"应用包文件不存在：{app_path}")
        caps["appium:app"] = str(app_path)
    if body.app_identifier:
        key = "appium:appPackage" if body.platform == "android" else "appium:bundleId"
        caps[key] = body.app_identifier
    for key, value in body.capabilities.items():
        if key in {"is_simulator", "device_type", "basePath", "appium:basePath"}:
            continue
        normalized = key if key == "platformName" or ":" in key else f"appium:{key}"
        caps[normalized] = value
    if body.platform == "android":
        from appium.options.android import UiAutomator2Options

        return UiAutomator2Options().load_capabilities(caps)
    from appium.options.ios import XCUITestOptions

    return XCUITestOptions().load_capabilities(caps)


@dataclass
class MobileRecorderRuntime:
    """一个由 Appium 持有的模拟器录制会话。"""

    session_id: int
    driver: Any
    platform: str
    udid: str
    app_identifier: str | None
    started_monotonic: float = field(default_factory=time.monotonic)
    paused: bool = False
    pick_mode: bool = False
    stopped: bool = False
    sequence_no: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    snapshot_fingerprints: set[str] = field(default_factory=set)
    last_element: dict[str, Any] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    driver_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def session_root(self) -> Path:
        return _ARTIFACT_ROOT / f"session_{self.session_id}"

    async def emit(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any],
        *,
        severity: str = "info",
    ) -> dict[str, Any] | None:
        if self.paused and source != "agent" and event_type not in {"user.pick", "page.snapshot"}:
            return None
        async with self.lock:
            self.sequence_no += 1
            element = payload.get("element")
            if isinstance(element, dict):
                seed = str(element.pop("fingerprint_seed", ""))
                if not element.get("fingerprint"):
                    element["fingerprint"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            event = {
                "event_key": uuid.uuid4().hex,
                "sequence_no": self.sequence_no,
                "event_type": event_type,
                "source": source,
                "severity": severity,
                "page_key": str(payload.get("page_key") or f"{self.platform}:{self.udid}")[:255],
                "occurred_at": datetime.now().isoformat(),
                "monotonic_ms": int((time.monotonic() - self.started_monotonic) * 1000),
                "payload": payload,
            }
            self.events.append(event)
            if len(self.events) > _MAX_EVENT_BUFFER:
                self.events = self.events[-_MAX_EVENT_BUFFER:]
            return event

    def _read_state_sync(self) -> dict[str, Any]:
        screenshot = self.driver.get_screenshot_as_png()
        source = self.driver.page_source
        rect = self.driver.get_window_rect()
        capabilities = dict(getattr(self.driver, "capabilities", {}) or {})
        current_context = str(getattr(self.driver, "current_context", "NATIVE_APP"))
        contexts = [str(item) for item in (getattr(self.driver, "contexts", []) or [])]
        page_name = str(
            capabilities.get("appium:bundleId")
            or capabilities.get("bundleId")
            or self.app_identifier
            or capabilities.get("appium:appPackage")
            or capabilities.get("appPackage")
            or current_context
        )
        if self.platform == "android":
            try:
                package = str(self.driver.current_package)
                activity = str(self.driver.current_activity)
                page_name = activity or package or page_name
            except Exception:  # noqa: BLE001
                package = self.app_identifier
                activity = None
            page_key = f"{package or 'android'}:{activity or current_context}"
        else:
            package = self.app_identifier
            activity = None
            page_key = f"{package or 'ios'}:{current_context}"
        return {
            "screenshot": screenshot,
            "source": source,
            "rect": rect,
            "context": current_context,
            "contexts": contexts,
            "page_name": page_name,
            "page_key": page_key[:255],
            "app_identifier": package,
            "activity": activity,
            "capabilities": capabilities,
        }

    async def capture_snapshot(self, reason: str) -> dict[str, Any] | None:
        """保存模拟器截图与 UI Tree；连续相同状态只保留一个版本。"""
        if self.stopped:
            return None
        async with self.driver_lock:
            state = await asyncio.to_thread(self._read_state_sync)
        screenshot = state["screenshot"]
        source = str(state["source"])
        fingerprint = hashlib.sha256(
            str(state["page_key"]).encode("utf-8") + b"\n" + source.encode("utf-8")
        ).hexdigest()
        if fingerprint in self.snapshot_fingerprints:
            return None
        snapshot_index = len(self.snapshot_fingerprints) + 1
        document_dir = self.session_root / "documents"
        screenshot_dir = self.session_root / "screenshots"
        document_dir.mkdir(parents=True, exist_ok=True)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        document_path = document_dir / f"mobile_{snapshot_index}_{fingerprint[:12]}.xml"
        screenshot_path = screenshot_dir / f"mobile_{snapshot_index}_{fingerprint[:12]}.png"
        document_path.write_text(source, encoding="utf-8")
        screenshot_path.write_bytes(screenshot)
        self.snapshot_fingerprints.add(fingerprint)
        payload = {
            "url": f"appium://{self.udid}/{state['context']}",
            "title": state["page_name"],
            "page_title": state["page_name"],
            "page_key": state["page_key"],
            "state_name": reason,
            "fingerprint": fingerprint,
            "document_path": str(document_path.relative_to(self.session_root)),
            "screenshot_path": str(screenshot_path.relative_to(self.session_root)),
            "app_identifier": state["app_identifier"],
            "activity": state["activity"],
            "context": state["context"],
            "contexts": state["contexts"],
            "viewport": state["rect"],
        }
        await self.emit("page.snapshot", "device", payload)
        return payload

    async def set_pick_mode(self, enabled: bool) -> None:
        self.pick_mode = enabled
        await self.emit("agent.pick_mode", "agent", {"enabled": enabled})

    async def perform_action(self, body: MobileActionRequest) -> None:
        """执行平台远程动作，并把动作与命中元素、前后画面关联起来。"""
        if self.stopped:
            raise RuntimeError("移动录制会话已停止")
        if body.action in {"tap", "swipe"} and (body.x is None or body.y is None):
            raise ValueError(f"{body.action} 动作必须包含 x/y")
        if body.action == "swipe" and (body.end_x is None or body.end_y is None):
            raise ValueError("swipe 动作必须包含 end_x/end_y")
        if body.action == "input" and body.text is None:
            raise ValueError("input 动作必须包含 text")

        async with self.driver_lock:
            source = await asyncio.to_thread(lambda: self.driver.page_source)
            state = await asyncio.to_thread(self._read_state_sync)
            element = None
            if body.x is not None and body.y is not None:
                element = _mobile_element_from_source(source, body.x, body.y, self.platform)
                if element is not None:
                    self.last_element = element
            elif body.action == "input":
                element = self.last_element
            event_type = f"user.{body.action}"
            payload: dict[str, Any] = {
                "page_key": state["page_key"],
                "page_title": state["page_name"],
                "url": f"appium://{self.udid}/{state['context']}",
                "element": element,
                "x": body.x,
                "y": body.y,
            }
            if body.action == "tap" and self.pick_mode:
                event_type = "user.pick"
            elif body.action == "tap":
                await asyncio.to_thread(self._tap_sync, int(body.x or 0), int(body.y or 0))
            elif body.action == "input":
                sensitive = bool(
                    element
                    and str((element.get("attributes") or {}).get("password") or "").lower() == "true"
                )
                await asyncio.to_thread(self._input_sync, body.text or "")
                payload.update({
                    "value": "${password}" if sensitive else body.text,
                    "redacted": sensitive,
                })
            elif body.action == "swipe":
                await asyncio.to_thread(
                    self.driver.swipe,
                    int(body.x or 0),
                    int(body.y or 0),
                    int(body.end_x or 0),
                    int(body.end_y or 0),
                    body.duration_ms,
                )
                payload.update({
                    "end_x": body.end_x,
                    "end_y": body.end_y,
                    "duration_ms": body.duration_ms,
                })
            elif body.action == "back":
                await asyncio.to_thread(self.driver.back)
            elif body.action == "refresh":
                event_type = "user.refresh"

        await self.emit(event_type, "user", payload)
        if event_type != "user.pick":
            await asyncio.sleep(0.35)
        await self.capture_snapshot(event_type)

    def _tap_sync(self, x: int, y: int) -> None:
        if self.platform == "android":
            try:
                self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
                return
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                self.driver.execute_script("mobile: tap", {"x": x, "y": y})
                return
            except Exception:  # noqa: BLE001
                pass
        self.driver.tap([(x, y)], 100)

    def _input_sync(self, value: str) -> None:
        active = self.driver.switch_to.active_element
        active.send_keys(value)

    async def capture_device_logs(self) -> None:
        """尽力读取设备日志；驱动不支持时显式降级，不影响录制。"""
        log_type = "logcat" if self.platform == "android" else "syslog"
        try:
            async with self.driver_lock:
                entries = await asyncio.to_thread(self.driver.get_log, log_type)
        except Exception as exc:  # noqa: BLE001
            await self.emit(
                "device.log_unavailable",
                "device",
                {
                    "page_key": f"{self.platform}:{self.udid}",
                    "log_type": log_type,
                    "reason": str(exc)[:1000],
                },
            )
            return
        filtered_entries = []
        for entry in list(entries or []):
            message = str(entry.get("message") or "")
            if "channel read:" in message or "AppiumResponse:" in message:
                continue
            filtered_entries.append(entry)
        for entry in filtered_entries[-50:]:
            await self.emit(
                "device.log",
                "device",
                {
                    "page_key": f"{self.platform}:{self.udid}",
                    "log_type": log_type,
                    "level": entry.get("level"),
                    "message": str(entry.get("message") or "")[:4000],
                    "timestamp": entry.get("timestamp"),
                },
                severity="error" if str(entry.get("level") or "").upper() == "SEVERE" else "info",
            )

    async def close(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        async with self.driver_lock:
            await asyncio.to_thread(self.driver.quit)


@dataclass
class OfflineReplayRuntime:
    """一个严格断网的离线回放浏览器。"""

    replay_id: str
    session_id: int
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    stats: dict[str, int]
    reuse_key: str | None = None
    freeze_dom: bool = False
    source_session_ids: tuple[int, ...] = ()
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_activity_monotonic: float = field(default_factory=time.monotonic)

    async def active_element(self) -> dict[str, Any] | None:
        """返回当前真实焦点元素，供截图画布叠加可输入控件。"""
        raw = await self.page.evaluate(
            r"""() => {
              const element = document.activeElement;
              if (!element || element === document.body || element === document.documentElement) return null;
              const tag = element.tagName.toLowerCase();
              const inputType = tag === 'input'
                ? String(element.getAttribute('type') || 'text').toLowerCase()
                : tag;
              const blockedInputTypes = new Set([
                'button', 'checkbox', 'color', 'file', 'hidden', 'image',
                'radio', 'range', 'reset', 'submit',
              ]);
              const editable = !element.disabled && !element.readOnly && (
                tag === 'textarea'
                || (tag === 'input' && !blockedInputTypes.has(inputType))
                || element.isContentEditable
              );
              const rect = element.getBoundingClientRect();
              return {
                editable,
                tag,
                input_type: inputType,
                id: element.id || null,
                name: element.getAttribute('name') || null,
                placeholder: element.getAttribute('placeholder') || null,
                aria_label: element.getAttribute('aria-label') || null,
                bounds: {
                  x: Math.max(0, rect.x),
                  y: Math.max(0, rect.y),
                  width: Math.max(0, rect.width),
                  height: Math.max(0, rect.height),
                },
              };
            }""",
        )
        return raw if isinstance(raw, dict) else None

    async def perform_action(self, body: WebActionRequest) -> dict[str, Any]:
        """在离线浏览器中执行远程动作，并返回更新后的页面状态。"""
        if self.page.is_closed():
            raise ValueError("离线回放页面已经关闭")
        if self.freeze_dom and body.action != "pick":
            raise ValueError("冻结快照只允许只读拾取")
        if body.action in {"click", "pick"} and (body.x is None or body.y is None):
            raise ValueError(f"{body.action} 动作必须提供 x/y 坐标")
        if body.action == "input" and body.text is None:
            raise ValueError("input 动作必须提供 text")

        async with self.lock:
            element: dict[str, Any] | None = None
            if body.action == "pick":
                raw = await self.page.evaluate(
                    "({x, y}) => window.__uiRecorderDescribeAt?.(x, y) || null",
                    {"x": body.x, "y": body.y},
                )
                element = raw if isinstance(raw, dict) else None
                if element is not None:
                    seed = str(element.pop("fingerprint_seed", ""))
                    if seed:
                        element["fingerprint"] = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            elif body.action == "click":
                await self.page.mouse.click(body.x or 0, body.y or 0)
            elif body.action == "input":
                if body.x is not None and body.y is not None:
                    await self.page.mouse.click(body.x, body.y)
                await self.page.keyboard.press("ControlOrMeta+A")
                await self.page.keyboard.insert_text(body.text or "")
            elif body.action == "scroll":
                await self.page.mouse.wheel(body.delta_x, body.delta_y)
            elif body.action == "back":
                await self.page.go_back(wait_until="domcontentloaded", timeout=10_000)
            elif body.action == "refresh":
                await self.page.reload(wait_until="domcontentloaded", timeout=30_000)

            if body.action != "pick":
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=1_500)
                except Exception:  # noqa: BLE001
                    pass
                await _wait_replay_stable(self.page, timeout_ms=5_000)
            self.last_activity_monotonic = time.monotonic()
            return {
                "replay_id": self.replay_id,
                "session_id": self.session_id,
                "url": self.page.url,
                "title": await self.page.title(),
                "stats": dict(self.stats),
                "element": element,
                "active_element": await self.active_element(),
            }

    async def screenshot(self) -> bytes:
        """返回当前离线页面可视区域，供元素库画布实时刷新。"""
        async with self.lock:
            self.last_activity_monotonic = time.monotonic()
            return await self.page.screenshot(
                full_page=False,
                type="png",
                mask=[self.page.locator(_SENSITIVE_SELECTOR)],
                mask_color="#64748b",
            )

    async def validate_locator(self, strategy: str, locator: str) -> dict[str, Any]:
        """在选定离线页面状态中验证定位器匹配数和可见性。"""
        async with self.lock:
            result = await self.page.evaluate(
                r"""({strategy, locator}) => {
                  const visible = (element) => {
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                  };
                  let nodes = [];
                  try {
                    if (strategy === 'css') nodes = Array.from(document.querySelectorAll(locator));
                    else if (strategy === 'id') nodes = Array.from(document.querySelectorAll(`[id="${CSS.escape(locator)}"]`));
                    else if (strategy === 'name') nodes = Array.from(document.querySelectorAll(`[name="${CSS.escape(locator)}"]`));
                    else if (strategy === 'xpath') {
                      const snapshot = document.evaluate(locator, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                      nodes = Array.from({length: snapshot.snapshotLength}, (_, index) => snapshot.snapshotItem(index)).filter(Boolean);
                    } else if (strategy === 'link') nodes = Array.from(document.querySelectorAll('a')).filter((node) => (node.innerText || '').trim() === locator);
                    else if (strategy === 'text') nodes = Array.from(document.querySelectorAll('button,a,label,option,h1,h2,h3,h4,h5,h6,p,legend,caption,th,td,dt,dd,li,span,strong,small,code,pre,[role]')).filter((node) => (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim() === locator);
                    else if (strategy === 'role') {
                      const match = locator.match(/^role=([^;]+);name=(.*)$/);
                      if (match) {
                        const implicit = {
                          button: 'button', link: 'a[href]',
                          textbox: 'input:not([type="checkbox"]):not([type="radio"]),textarea',
                          combobox: 'select', checkbox: 'input[type="checkbox"]',
                          radio: 'input[type="radio"]', heading: 'h1,h2,h3,h4,h5,h6',
                        }[match[1]] || '[data-ui-recorder-no-match]';
                        nodes = Array.from(document.querySelectorAll(`[role="${CSS.escape(match[1])}"],${implicit}`)).filter((node) => (node.getAttribute('aria-label') || node.innerText || node.textContent || node.getAttribute('placeholder') || node.getAttribute('name') || node.getAttribute('data-testid') || '').replace(/\s+/g, ' ').trim() === match[2]);
                      }
                    }
                    return {match_count: nodes.length, visible_count: nodes.filter(visible).length, error: null};
                  } catch (error) {
                    return {match_count: 0, visible_count: 0, error: String(error)};
                  }
                }""",
                {"strategy": strategy.lower(), "locator": locator},
            )
            match_count = int(result.get("match_count") or 0)
            return {
                "strategy": strategy.lower(),
                "locator": locator,
                "match_count": match_count,
                "visible_count": int(result.get("visible_count") or 0),
                "is_unique": match_count == 1,
                "error": result.get("error"),
            }

    async def close(self) -> None:
        async with self.lock:
            try:
                await self.context.close()
            finally:
                try:
                    await self.browser.close()
                finally:
                    await self.playwright.stop()


_SESSIONS: dict[int, RecorderRuntime | MobileRecorderRuntime] = {}
_REPLAYS: dict[str, OfflineReplayRuntime] = {}
_REPLAY_REUSE_LOCKS: dict[str, asyncio.Lock] = {}
_CACHED_REPLAY_IDLE_SECONDS = 120
_MAX_CACHED_REPLAYS = 6


async def _cleanup_cached_replays() -> None:
    """回收只读拾取使用的空闲回放，避免长期占用浏览器进程。"""
    now = time.monotonic()
    cached = sorted(
        (
            runtime
            for runtime in _REPLAYS.values()
            if runtime.reuse_key is not None
        ),
        key=lambda runtime: runtime.last_activity_monotonic,
        reverse=True,
    )
    stale_ids = {
        runtime.replay_id
        for index, runtime in enumerate(cached)
        if index >= _MAX_CACHED_REPLAYS
        or now - runtime.last_activity_monotonic >= _CACHED_REPLAY_IDLE_SECONDS
    }
    for replay_id in stale_ids:
        runtime = _REPLAYS.pop(replay_id, None)
        if runtime is not None:
            await runtime.close()


async def _cached_replay_janitor() -> None:
    """定时清理完成态只读拾取产生的临时回放。"""
    while True:
        await asyncio.sleep(30)
        await _cleanup_cached_replays()


def _legacy_replay_storage(manifest: dict[str, Any], entry_url: str) -> dict[str, Any]:
    """为没有存储快照的旧离线包，从成功登录响应恢复最小登录态。"""
    if urlparse(entry_url).path.rstrip("/") == "/login":
        return {}
    for exchange in manifest.get("mocks") or []:
        response = exchange.get("response") or {}
        if (
            str(exchange.get("method") or "GET").upper() != "POST"
            or not urlparse(str(exchange.get("url") or "")).path.endswith("/auth/login")
            or not 200 <= int(response.get("status") or 0) < 300
        ):
            continue
        try:
            payload = json.loads(str(response.get("body") or "{}"))
        except (TypeError, ValueError):
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            continue
        roles = user.get("role_codes") if isinstance(user.get("role_codes"), list) else []
        current_user = {
            "user": user,
            "activeRole": roles[0] if roles else None,
        }
        return {
            "origin": f"{urlparse(entry_url).scheme}://{urlparse(entry_url).netloc}",
            "local_storage": {
                "pm.accessToken": "offline-replay-token",
                "pm.refreshToken": "offline-replay-refresh-token",
                "pm.currentUser": json.dumps(current_user, ensure_ascii=False, separators=(",", ":")),
            },
            "session_storage": {},
        }
    return {}


def _replay_storage_script(storage_state: dict[str, Any]) -> str:
    """生成限定同源执行的浏览器存储恢复脚本。"""
    payload = json.dumps(storage_state, ensure_ascii=False).replace("</", "<\\/")
    return f"""(() => {{
      const state = {payload};
      if (!state.origin || location.origin !== state.origin) return;
      for (const [key, value] of Object.entries(state.local_storage || {{}})) {{
        localStorage.setItem(key, String(value));
      }}
      for (const [key, value] of Object.entries(state.session_storage || {{}})) {{
        sessionStorage.setItem(key, String(value));
      }}
    }})();"""


async def _start_offline_replay(
    body: ReplayStartRequest,
) -> tuple[OfflineReplayRuntime, dict[str, Any], bool]:
    source_session_ids = tuple(dict.fromkeys([
        body.session_id,
        *(item for item in body.source_session_ids if item > 0),
    ]))
    packages: list[tuple[int, Path, dict[str, Any]]] = []
    for source_session_id in source_session_ids:
        source_root = _ARTIFACT_ROOT / f"session_{source_session_id}"
        manifest_path = source_root / "offline" / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"录制会话 #{source_session_id} 的离线回放包不存在",
            )
        try:
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"录制会话 #{source_session_id} 的离线回放清单损坏：{exc}",
            ) from exc
        packages.append((source_session_id, source_root, source_manifest))

    primary_manifest = packages[0][2]
    # 当前所选页面的原始会话优先提供页面、资源和同名 Mock，其余已合并会话补缺。
    preferred_source_session_id = body.page_source_session_id or body.session_id
    replay_packages = sorted(
        packages,
        key=lambda item: 0 if item[0] == preferred_source_session_id else 1,
    )
    pages: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    mocks: list[dict[str, Any]] = []
    limitations: list[str] = []
    for source_session_id, source_root, source_manifest in replay_packages:
        pages.extend([
            {
                **item,
                "_source_session_id": source_session_id,
                "_source_root": source_root,
            }
            for item in source_manifest.get("pages") or []
        ])
        resources.extend([
            {
                **item,
                "_source_session_id": source_session_id,
                "_source_root": source_root,
            }
            for item in source_manifest.get("resources") or []
        ])
        mocks.extend([
            {**item, "_source_session_id": source_session_id}
            for item in source_manifest.get("mocks") or []
        ])
        for limitation in source_manifest.get("limitations") or []:
            if limitation not in limitations:
                limitations.append(limitation)
    manifest = {
        **primary_manifest,
        "pages": pages,
        "resources": resources,
        "mocks": mocks,
        "limitations": limitations,
        "source_session_ids": list(source_session_ids),
    }
    entry_url = str(body.entry_url or primary_manifest.get("entry_url") or "")
    if not entry_url:
        raise HTTPException(status_code=422, detail="离线回放包没有入口页面")

    if body.reuse_key:
        await _cleanup_cached_replays()
        reusable = next(
            (
                runtime
                for runtime in _REPLAYS.values()
                if runtime.session_id == body.session_id
                and runtime.reuse_key == body.reuse_key
                and runtime.freeze_dom == body.freeze_dom
                and runtime.source_session_ids == source_session_ids
                and not runtime.page.is_closed()
            ),
            None,
        )
        if reusable is not None:
            reusable.last_activity_monotonic = time.monotonic()
            return reusable, manifest, True

    try:
        for source_session_id, source_root, source_manifest in packages:
            integrity_items = [
                (item["document_path"], item["document_sha256"])
                for item in source_manifest.get("pages") or []
            ] + [
                (item["screenshot_path"], item["screenshot_sha256"])
                for item in source_manifest.get("pages") or []
            ] + [
                (item["path"], item["sha256"])
                for item in source_manifest.get("resources") or []
            ]
            for relative_path, expected_hash in integrity_items:
                artifact = _package_artifact_path(source_root, relative_path)
                actual_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if actual_hash != expected_hash:
                    raise ValueError(
                        f"会话 #{source_session_id} 制品哈希不匹配：{relative_path}"
                    )
    except (KeyError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"离线回放包完整性校验失败：{exc}") from exc

    page_groups: dict[str, list[dict[str, Any]]] = {}
    for item in manifest.get("pages") or []:
        page_groups.setdefault(_normalized_replay_url(str(item["url"])), []).append(item)
    page_indexes: dict[str, int] = {}
    entry_key = _normalized_replay_url(entry_url)
    if body.entry_url and entry_key not in page_groups:
        raise HTTPException(status_code=422, detail="指定入口页面不属于当前离线回放包")
    if body.page_fingerprint:
        candidates = page_groups.get(entry_key) or []
        selected_index = next(
            (
                index
                for index, item in enumerate(candidates)
                if item.get("fingerprint") == body.page_fingerprint
                and (
                    body.page_source_session_id is None
                    or item.get("_source_session_id") == body.page_source_session_id
                )
            ),
            None,
        )
        if selected_index is None:
            raise HTTPException(status_code=422, detail="指定页面状态不属于当前离线回放包")
        page_indexes[entry_key] = selected_index
    entry_candidates = page_groups.get(entry_key) or []
    entry_index = min(page_indexes.get(entry_key, 0), max(0, len(entry_candidates) - 1))
    entry_page_record = entry_candidates[entry_index] if entry_candidates else {}
    replay_storage = entry_page_record.get("storage_state") or _legacy_replay_storage(
        manifest,
        entry_url,
    )
    resources_by_url: dict[str, dict[str, Any]] = {}
    for item in manifest.get("resources") or []:
        resources_by_url.setdefault(_normalized_replay_url(str(item["url"])), item)
    mock_groups: dict[str, list[dict[str, Any]]] = {}
    for exchange in manifest.get("mocks") or []:
        rule = dict(exchange.get("match_rule") or {})
        normalized_url = str(
            rule.get("normalized_url")
            or _normalized_replay_url(str(exchange.get("url") or ""))
        )
        key = f"{str(exchange.get('method') or 'GET').upper()} {normalized_url}"
        mock_groups.setdefault(key, []).append(exchange)
    mock_indexes: dict[str, int] = {}
    stats = {"requests": 0, "page_hits": 0, "resource_hits": 0, "mock_hits": 0, "misses": 0}

    playwright = await async_playwright().start()
    browser_type = getattr(playwright, body.browser)
    try:
        browser = await browser_type.launch(headless=body.headless)
        context = await browser.new_context(
            service_workers="block",
            viewport={
                "width": max(320, min(3840, int(body.viewport.get("width") or 1440))),
                "height": max(320, min(2160, int(body.viewport.get("height") or 900))),
            },
        )
    except Exception as exc:  # noqa: BLE001
        await playwright.stop()
        raise HTTPException(status_code=503, detail=f"离线浏览器启动失败：{exc}") from exc

    async def route_offline(route) -> None:
        request = route.request
        stats["requests"] += 1
        url = request.url
        method = request.method.upper()
        normalized_url = _normalized_replay_url(url)
        if body.freeze_dom and request.resource_type == "script":
            stats["resource_hits"] += 1
            await route.fulfill(
                status=200,
                content_type="application/javascript; charset=utf-8",
                body="",
            )
            return
        if request.resource_type in {"xhr", "fetch"}:
            key = f"{method} {normalized_url}"
            candidates = mock_groups.get(key) or []
            if candidates:
                stats["mock_hits"] += 1
                request_headers = await request.all_headers()
                incoming_body_hash = _request_body_signature(
                    request.post_data,
                    request_headers.get("content-type", ""),
                )
                matching_indexes = [
                    index
                    for index, candidate in enumerate(candidates)
                    if not (candidate.get("match_rule") or {}).get("body_sha256")
                    or (candidate.get("match_rule") or {}).get("body_sha256") == incoming_body_hash
                ]
                preferred = mock_indexes.get(key, 0)
                index = next(
                    (item for item in matching_indexes if item >= preferred),
                    matching_indexes[-1] if matching_indexes else min(preferred, len(candidates) - 1),
                )
                mock_indexes[key] = index + 1
                response = candidates[index].get("response") or {}
                await route.fulfill(
                    status=int(response.get("status") or 200),
                    headers=_safe_replay_headers(response.get("headers") or {}),
                    body=str(response.get("body") or ""),
                )
                return
            stats["misses"] += 1
            await route.fulfill(
                status=599,
                content_type="application/json; charset=utf-8",
                body=json.dumps({
                    "offline_error": "未命中录制期 Mock，已阻止访问原服务",
                    "method": method,
                    "url": url,
                }, ensure_ascii=False),
            )
            return

        page_candidates = page_groups.get(normalized_url) or []
        if page_candidates:
            stats["page_hits"] += 1
            index = min(page_indexes.get(normalized_url, 0), len(page_candidates) - 1)
            page_indexes[normalized_url] = index + 1
            page_record = page_candidates[index]
            try:
                path = _package_artifact_path(
                    Path(page_record["_source_root"]),
                    page_record["document_path"],
                )
            except ValueError as exc:
                await route.fulfill(status=599, body=str(exc))
                return
            document_body = path.read_bytes()
            if body.freeze_dom:
                document_body = _freeze_replay_document(document_body)
            await route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=document_body,
            )
            return
        resource = resources_by_url.get(normalized_url)
        if resource:
            stats["resource_hits"] += 1
            try:
                path = _package_artifact_path(
                    Path(resource["_source_root"]),
                    resource["path"],
                )
            except ValueError as exc:
                await route.fulfill(status=599, body=str(exc))
                return
            await route.fulfill(
                status=int(resource.get("status") or 200),
                headers=_safe_replay_headers(resource.get("headers") or {}),
                body=path.read_bytes(),
            )
            return
        stats["misses"] += 1
        await route.fulfill(
            status=599,
            content_type="text/plain; charset=utf-8",
            body=f"Offline resource miss: {url}",
        )

    try:
        await context.route("**/*", route_offline)
        if replay_storage:
            await context.add_init_script(script=_replay_storage_script(replay_storage))
        await context.add_init_script(script=_REPLAY_INTERACTION_SCRIPT)
        await context.add_init_script(script=_RECORDER_SCRIPT)
        page = await context.new_page()
        await page.goto(entry_url, wait_until="domcontentloaded", timeout=60_000)
        await _wait_replay_stable(page)
    except Exception as exc:  # noqa: BLE001
        await context.close()
        await browser.close()
        await playwright.stop()
        raise HTTPException(status_code=502, detail=f"打开离线页面失败：{exc}") from exc

    replay_id = uuid.uuid4().hex
    runtime = OfflineReplayRuntime(
        replay_id=replay_id,
        session_id=body.session_id,
        playwright=playwright,
        browser=browser,
        context=context,
        page=page,
        stats=stats,
        reuse_key=body.reuse_key,
        freeze_dom=body.freeze_dom,
        source_session_ids=source_session_ids,
    )
    _REPLAYS[replay_id] = runtime
    return runtime, manifest, False


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
        browser = await browser_type.launch(**_live_browser_launch_options(body))
        context = await browser.new_context(**_live_browser_context_options(body))
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


async def _start_mobile_runtime(body: MobileRecorderStartRequest) -> MobileRecorderRuntime:
    """连接 Appium，并采集模拟器初始画面、UI Tree 与环境。"""
    existing = _SESSIONS.get(body.session_id)
    if existing is not None and not existing.stopped:
        raise HTTPException(status_code=409, detail="该录制会话已经在 Agent 中运行")
    restore_result = await asyncio.to_thread(_restore_mobile_scenario_sync, body)
    if body.restore_scenario and not restore_result.get("restored"):
        raise HTTPException(
            status_code=422,
            detail=f"模拟器场景恢复失败：{restore_result.get('reason') or '未知原因'}",
        )
    try:
        from appium import webdriver

        options = _mobile_options(body)
        driver = await asyncio.to_thread(
            webdriver.Remote,
            command_executor=body.appium_url,
            options=options,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"Appium 模拟器会话启动失败：{exc}",
        ) from exc

    runtime = MobileRecorderRuntime(
        session_id=body.session_id,
        driver=driver,
        platform=body.platform,
        udid=body.udid,
        app_identifier=body.app_identifier,
    )
    _SESSIONS[body.session_id] = runtime
    try:
        async with runtime.driver_lock:
            state = await asyncio.to_thread(runtime._read_state_sync)
        await runtime.emit(
            "agent.connected",
            "agent",
            {
                "page_key": state["page_key"],
                "agent_id": f"mobile-agent-{os.getpid()}",
                "capabilities": {
                    "screen": True,
                    "ui_tree": True,
                    "user_events": True,
                    "device_logs": "best_effort",
                    "native_network": False,
                    "locators": [
                        "id",
                        "accessibility_id",
                        "android_uiautomator",
                        "ios_predicate",
                        "ios_class_chain",
                        "xpath",
                    ],
                    "platform": body.platform,
                    "scenario_restore": restore_result,
                },
            },
        )
        await runtime.emit(
            "environment.snapshot",
            "environment",
            {
                "page_key": state["page_key"],
                "url": f"appium://{body.udid}/{state['context']}",
                "platform": body.platform,
                "udid": body.udid,
                "device_name": body.device_name,
                "platform_version": body.platform_version,
                "app_identifier": body.app_identifier,
                "viewport": state["rect"],
                "contexts": state["contexts"],
                "network": {
                    "native_capture": False,
                    "unavailable_reason": (
                        "Native Network 代理/SDK 尚未配置；当前只记录 Appium 已提供的网络元数据"
                    ),
                },
                "host_os": {
                    "system": host_platform.system(),
                    "release": host_platform.release(),
                    "machine": host_platform.machine(),
                },
                "scenario_restore": restore_result,
            },
        )
        await runtime.capture_snapshot("session.start")
    except Exception as exc:  # noqa: BLE001
        await runtime.close()
        raise HTTPException(status_code=502, detail=f"采集模拟器初始画面失败：{exc}") from exc
    return runtime


def _scenario_artifact_path(value: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (_PROJECT_ROOT / path).resolve()
    root = _ARTIFACT_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("场景制品路径逃逸")
    return resolved


def _restore_mobile_scenario_sync(body: MobileRecorderStartRequest) -> dict[str, Any]:
    scenario = dict(body.restore_scenario or {})
    if not scenario:
        return {"requested": False, "restored": False, "mode": "fresh"}
    mode = str(scenario.get("restore_mode") or "")
    if body.platform == "android" and mode == "emulator_snapshot":
        snapshot_name = str(scenario.get("snapshot_name") or "")
        if not snapshot_name:
            return {"requested": True, "restored": False, "reason": "缺少 Android snapshot_name"}
        code, output = _run_preflight_command([
            "adb", "-s", body.udid, "emu", "avd", "snapshot", "load", snapshot_name,
        ])
        restored = code == 0 and "error" not in output.lower()
        return {
            "requested": True,
            "restored": restored,
            "mode": mode,
            "snapshot_name": snapshot_name,
            "reason": None if restored else output[:500],
        }
    if body.platform == "ios" and mode == "app_data_archive":
        if not body.app_identifier:
            return {"requested": True, "restored": False, "reason": "缺少 iOS bundle id"}
        archive_value = str(scenario.get("archive_path") or "")
        try:
            archive_path = _scenario_artifact_path(archive_value)
        except ValueError as exc:
            return {"requested": True, "restored": False, "reason": str(exc)}
        code, container = _run_preflight_command([
            "xcrun", "simctl", "get_app_container", body.udid, body.app_identifier, "data",
        ])
        container_path = Path(container).resolve() if container else None
        if (
            code != 0
            or not archive_path.is_file()
            or container_path is None
            or not container_path.is_dir()
            or container_path in {Path("/"), Path.home().resolve(), _PROJECT_ROOT.resolve()}
            or len(container_path.parts) < 5
        ):
            return {"requested": True, "restored": False, "reason": "iOS App 数据容器或归档不存在"}
        try:
            with tempfile.TemporaryDirectory(prefix="ui-recorder-ios-restore-") as staging_value:
                staging = Path(staging_value)
                shutil.unpack_archive(str(archive_path), staging)
                for child in container_path.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                for child in staging.iterdir():
                    target = container_path / child.name
                    if child.is_dir():
                        shutil.copytree(child, target, symlinks=True)
                    else:
                        shutil.copy2(child, target, follow_symlinks=False)
        except (OSError, shutil.Error, ValueError) as exc:
            return {"requested": True, "restored": False, "reason": str(exc)}
        return {"requested": True, "restored": True, "mode": mode, "archive_path": archive_value}
    return {"requested": True, "restored": False, "reason": f"不支持的场景恢复模式：{mode or '<empty>'}"}


def _save_mobile_scenario_sync(runtime: MobileRecorderRuntime) -> dict[str, Any]:
    if runtime.platform == "android":
        snapshot_name = f"ui-recorder-{runtime.session_id}"
        code, output = _run_preflight_command([
            "adb", "-s", runtime.udid, "emu", "avd", "snapshot", "save", snapshot_name,
        ])
        ready = code == 0 and "error" not in output.lower()
        return {
            "ready": ready,
            "restore_mode": "emulator_snapshot",
            "snapshot_name": snapshot_name,
            "reason": None if ready else output[:500],
        }
    if not runtime.app_identifier:
        return {"ready": False, "restore_mode": "app_data_archive", "reason": "缺少 iOS bundle id"}
    code, container = _run_preflight_command([
        "xcrun", "simctl", "get_app_container", runtime.udid, runtime.app_identifier, "data",
    ])
    if code != 0 or not container:
        return {"ready": False, "restore_mode": "app_data_archive", "reason": container[:500]}
    scenario_dir = runtime.session_root / "scenario"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    archive_base = scenario_dir / "ios_app_data"
    try:
        archive = Path(shutil.make_archive(str(archive_base), "zip", container))
    except (OSError, shutil.Error) as exc:
        return {"ready": False, "restore_mode": "app_data_archive", "reason": str(exc)}
    if archive.stat().st_size > _MAX_ARCHIVE_BYTES:
        archive.unlink(missing_ok=True)
        return {"ready": False, "restore_mode": "app_data_archive", "reason": "iOS App 数据超过 100MB 上限"}
    return {
        "ready": True,
        "restore_mode": "app_data_archive",
        "archive_path": str(archive.relative_to(_PROJECT_ROOT)),
        "archive_bytes": archive.stat().st_size,
    }


def _run_preflight_command(args: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout or completed.stderr or "").strip()


def _mobile_preflight_sync() -> dict[str, Any]:
    """检查宿主机 Appium、Android Emulator 与 iOS Simulator 条件。"""
    tools = {name: shutil.which(name) for name in ("adb", "appium", "xcrun")}
    android_devices: list[dict[str, str]] = []
    if tools["adb"]:
        _code, output = _run_preflight_command([str(tools["adb"]), "devices", "-l"])
        for line in output.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            android_devices.append({
                "udid": parts[0],
                "state": parts[1],
                "description": " ".join(parts[2:]),
            })

    ios_devices: list[dict[str, str]] = []
    if tools["xcrun"]:
        code, output = _run_preflight_command([
            str(tools["xcrun"]),
            "simctl",
            "list",
            "devices",
            "booted",
            "--json",
        ])
        if code == 0:
            try:
                payload = json.loads(output)
                for runtime_name, devices in (payload.get("devices") or {}).items():
                    for device in devices or []:
                        if device.get("state") != "Booted":
                            continue
                        ios_devices.append({
                            "udid": str(device.get("udid") or ""),
                            "state": "booted",
                            "description": f"{device.get('name') or ''} · {runtime_name}",
                        })
            except ValueError:
                pass

    appium: dict[str, Any] = {
        "installed": bool(tools["appium"]),
        "running": False,
        "url": "http://127.0.0.1:4723",
    }
    try:
        with urlopen("http://127.0.0.1:4723/status", timeout=2) as response:  # noqa: S310
            status_payload = json.loads(response.read().decode("utf-8"))
        appium["running"] = response.status == 200
        appium["version"] = (
            (status_payload.get("value") or {}).get("build") or {}
        ).get("version")
    except Exception as exc:  # noqa: BLE001
        appium["reason"] = str(exc)[:500]

    drivers: dict[str, Any] = {}
    if tools["appium"]:
        code, output = _run_preflight_command([
            str(tools["appium"]),
            "driver",
            "list",
            "--installed",
            "--json",
        ])
        if code == 0:
            try:
                drivers = json.loads(output)
            except ValueError:
                pass
    ios_issues: list[str] = []
    xcuitest = drivers.get("xcuitest") or {}
    if not xcuitest.get("installed"):
        ios_issues.append("未安装 Appium XCUITest Driver")
    install_path = Path(str(xcuitest.get("installPath") or ""))
    if install_path.is_dir() and not os.access(install_path, os.W_OK):
        ios_issues.append(
            f"XCUITest Driver 目录不可写（{install_path}），WebDriverAgent 构建会失败"
        )

    platform_ready = {
        "android": bool(appium["running"] and android_devices and (drivers.get("uiautomator2") or {}).get("installed")),
        "ios": bool(appium["running"] and ios_devices and not ios_issues),
    }

    return {
        "tools": tools,
        "appium": appium,
        "drivers": {
            name: {
                "installed": bool(value.get("installed")),
                "version": value.get("version"),
                "install_path": value.get("installPath"),
            }
            for name, value in drivers.items()
            if name in {"uiautomator2", "xcuitest"}
        },
        "android_devices": android_devices,
        "ios_devices": ios_devices,
        "ios_issues": ios_issues,
        "platform_ready": platform_ready,
        "ready": any(platform_ready.values()),
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    replay_janitor = asyncio.create_task(_cached_replay_janitor())
    yield
    replay_janitor.cancel()
    try:
        await replay_janitor
    except asyncio.CancelledError:
        pass
    await asyncio.gather(
        *(runtime.close() for runtime in list(_SESSIONS.values())),
        return_exceptions=True,
    )
    await asyncio.gather(
        *(runtime.close() for runtime in list(_REPLAYS.values())),
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


@app.get("/mobile/preflight", dependencies=[Depends(_authorize)])
async def mobile_preflight():
    return {
        "status": "success",
        "data": await asyncio.to_thread(_mobile_preflight_sync),
    }


@app.post("/mobile/sessions", dependencies=[Depends(_authorize)])
async def start_mobile_session(body: MobileRecorderStartRequest):
    runtime = await _start_mobile_runtime(body)
    return {
        "status": "success",
        "data": {
            "session_id": runtime.session_id,
            "status": "recording",
            "agent_id": f"mobile-agent-{os.getpid()}",
            "capabilities": {
                "screen": True,
                "ui_tree": True,
                "user_events": True,
                "device_logs": "best_effort",
                "native_network": False,
                "mobile_remote_actions": True,
            },
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
            "exploration": (
                runtime.exploration.serialize()
                if isinstance(runtime, RecorderRuntime)
                else None
            ),
        },
    }


@app.post("/sessions/{session_id}/exploration/start", dependencies=[Depends(_authorize)])
async def start_ai_exploration(session_id: int, body: AiExplorationRequest):
    runtime = _SESSIONS.get(session_id)
    if not isinstance(runtime, RecorderRuntime) or runtime.stopped:
        raise HTTPException(status_code=404, detail="Web 录制会话不存在或已停止")
    if runtime.exploration.task is not None and not runtime.exploration.task.done():
        raise HTTPException(status_code=409, detail="当前会话的 AI 探索已经在运行")
    runtime.exploration = AiExplorationState(
        status="starting",
        message="正在准备 AI 安全探索",
        config=body.model_dump(),
    )
    runtime.exploration.task = asyncio.create_task(_run_ai_exploration(runtime, body))
    return {"status": "success", "data": runtime.exploration.serialize()}


@app.get("/sessions/{session_id}/exploration", dependencies=[Depends(_authorize)])
async def get_ai_exploration(session_id: int):
    runtime = _SESSIONS.get(session_id)
    if not isinstance(runtime, RecorderRuntime):
        raise HTTPException(status_code=404, detail="Web 录制会话不存在")
    return {"status": "success", "data": runtime.exploration.serialize()}


@app.post("/sessions/{session_id}/exploration/stop", dependencies=[Depends(_authorize)])
async def stop_ai_exploration(session_id: int):
    runtime = _SESSIONS.get(session_id)
    if not isinstance(runtime, RecorderRuntime):
        raise HTTPException(status_code=404, detail="Web 录制会话不存在")
    runtime.exploration.cancel_requested = True
    runtime.exploration.message = "正在停止 AI 探索"
    return {"status": "success", "data": runtime.exploration.serialize()}


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


@app.post("/sessions/{session_id}/pick-mode", dependencies=[Depends(_authorize)])
async def update_pick_mode(session_id: int, body: PickModeRequest):
    runtime = _SESSIONS.get(session_id)
    if runtime is None or runtime.stopped:
        raise HTTPException(status_code=404, detail="Agent 会话不存在或已停止")
    await runtime.set_pick_mode(body.enabled)
    return {
        "status": "success",
        "data": {"enabled": runtime.pick_mode},
    }


@app.post("/sessions/{session_id}/web-actions", dependencies=[Depends(_authorize)])
async def perform_web_action(session_id: int, body: WebActionRequest):
    runtime = _SESSIONS.get(session_id)
    if not isinstance(runtime, RecorderRuntime) or runtime.stopped:
        raise HTTPException(status_code=404, detail="Web 录制会话不存在或已停止")
    try:
        await runtime.perform_web_action(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        await runtime.emit(
            "agent.error",
            "agent",
            {"message": f"Web 动作执行失败：{exc}"},
            severity="error",
        )
        raise HTTPException(status_code=502, detail=f"Web 动作执行失败：{exc}") from exc
    return {
        "status": "success",
        "data": {
            "status": "paused" if runtime.paused else "recording",
            "event_count": len(runtime.events),
        },
    }


@app.post("/sessions/{session_id}/actions", dependencies=[Depends(_authorize)])
async def perform_mobile_action(session_id: int, body: MobileActionRequest):
    runtime = _SESSIONS.get(session_id)
    if not isinstance(runtime, MobileRecorderRuntime) or runtime.stopped:
        raise HTTPException(status_code=404, detail="移动录制会话不存在或已停止")
    try:
        await runtime.perform_action(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        await runtime.emit(
            "agent.error",
            "agent",
            {"message": f"移动动作执行失败：{exc}"},
            severity="error",
        )
        raise HTTPException(status_code=502, detail=f"移动动作执行失败：{exc}") from exc
    return {
        "status": "success",
        "data": {
            "status": "paused" if runtime.paused else "recording",
            "event_count": len(runtime.events),
        },
    }


@app.post("/sessions/{session_id}/stop", dependencies=[Depends(_authorize)])
async def stop_session(session_id: int):
    runtime = _SESSIONS.get(session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Agent 会话不存在")
    if isinstance(runtime, RecorderRuntime):
        exploration_task = runtime.exploration.task
        if exploration_task is not None and not exploration_task.done():
            runtime.exploration.cancel_requested = True
            try:
                await asyncio.wait_for(asyncio.shield(exploration_task), timeout=20)
            except TimeoutError:
                exploration_task.cancel()
                await asyncio.gather(exploration_task, return_exceptions=True)
        if not runtime.stopped:
            package = await runtime.build_offline_package()
            await runtime.emit("agent.disconnected", "agent", {"reason": "stopped"})
            await runtime.close()
        else:
            package = runtime.offline_package or {}
    else:
        package = {}
        if not runtime.stopped:
            await runtime.capture_snapshot("session.stop")
            await runtime.capture_device_logs()
            saved_scenario = await asyncio.to_thread(_save_mobile_scenario_sync, runtime)
            await runtime.emit("agent.disconnected", "agent", {"reason": "stopped"})
            await runtime.close()
        else:
            saved_scenario = {"ready": False, "reason": "模拟器会话已经停止"}
        mobile_scenario = {
            **saved_scenario,
            "platform": runtime.platform,
            "udid": runtime.udid,
            "app_identifier": runtime.app_identifier,
            "snapshot_count": len(runtime.snapshot_fingerprints),
            "limitations": [
                "Native Network 需要测试代理或 SDK；未配置时保持显式降级",
            ],
        }
    if isinstance(runtime, RecorderRuntime):
        mobile_scenario = {}
    return {
        "status": "success",
        "data": {
            "status": "stopped",
            "offline_package": package,
            "mobile_scenario": mobile_scenario,
        },
    }


@app.post("/replays", dependencies=[Depends(_authorize)])
async def start_replay(body: ReplayStartRequest):
    if body.reuse_key:
        reuse_lock = _REPLAY_REUSE_LOCKS.setdefault(body.reuse_key, asyncio.Lock())
        async with reuse_lock:
            runtime, manifest, reused = await _start_offline_replay(body)
    else:
        runtime, manifest, reused = await _start_offline_replay(body)
    active_element = await runtime.active_element()
    return {
        "status": "success",
        "data": {
            "replay_id": runtime.replay_id,
            "session_id": body.session_id,
            "source_session_ids": list(runtime.source_session_ids),
            "entry_url": runtime.page.url,
            "page_count": len(manifest.get("pages") or []),
            "resource_count": len(manifest.get("resources") or []),
            "mock_count": len(manifest.get("mocks") or []),
            "offline_enforced": True,
            "integrity_verified": True,
            "reused": reused,
            "freeze_dom": body.freeze_dom,
            "limitations": manifest.get("limitations") or [],
            "url": runtime.page.url,
            "title": await runtime.page.title(),
            "stats": dict(runtime.stats),
            "active_element": active_element,
        },
    }


@app.post("/replays/{replay_id}/actions", dependencies=[Depends(_authorize)])
async def perform_replay_action(replay_id: str, body: WebActionRequest):
    runtime = _REPLAYS.get(replay_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="离线回放会话不存在")
    try:
        data = await runtime.perform_action(body)
    except (ValueError, TimeoutError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"离线页面动作执行失败：{exc}") from exc
    return {"status": "success", "data": data}


@app.get("/replays/{replay_id}/screenshot", dependencies=[Depends(_authorize)])
async def get_replay_screenshot(replay_id: str):
    runtime = _REPLAYS.get(replay_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="离线回放会话不存在")
    try:
        content = await runtime.screenshot()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"离线页面画面获取失败：{exc}") from exc
    return FastAPIResponse(content=content, media_type="image/png")


@app.post("/replays/{replay_id}/locators:validate", dependencies=[Depends(_authorize)])
async def validate_replay_locator(replay_id: str, body: LocatorValidationRequest):
    runtime = _REPLAYS.get(replay_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="离线回放会话不存在")
    try:
        data = await runtime.validate_locator(body.strategy, body.locator)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"定位器验证失败：{exc}") from exc
    return {"status": "success", "data": data}


@app.post("/replays/{replay_id}/stop", dependencies=[Depends(_authorize)])
async def stop_replay(replay_id: str):
    runtime = _REPLAYS.pop(replay_id, None)
    if runtime is None:
        raise HTTPException(status_code=404, detail="离线回放会话不存在")
    await runtime.close()
    return {"status": "success", "data": {"status": "stopped"}}


@app.get("/replays/{replay_id}", dependencies=[Depends(_authorize)])
async def get_replay(replay_id: str):
    runtime = _REPLAYS.get(replay_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="离线回放会话不存在")
    active_element = await runtime.active_element()
    return {
        "status": "success",
        "data": {
            "replay_id": replay_id,
            "session_id": runtime.session_id,
            "url": runtime.page.url,
            "title": await runtime.page.title(),
            "stats": dict(runtime.stats),
            "active_element": active_element,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "recorder_agent.main:app",
        host=os.getenv("UI_RECORDER_AGENT_HOST", "127.0.0.1"),
        port=int(os.getenv("UI_RECORDER_AGENT_PORT", "54352")),
        reload=False,
    )
