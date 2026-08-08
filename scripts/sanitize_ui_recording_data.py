"""清理历史 UI 录制中的凭据，并修复离线包完整性哈希。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from scripts._env import load_dotenv  # noqa: E402

load_dotenv()

from database.db import DB  # noqa: E402
from database.models import (
    UiContextArtifact,
    UiContextEvent,
    UiContextSession,
    UiElement,
    UiElementOccurrence,
    UiMockExchange,
    UiPageSnapshot,
    UiPageTransition,
    UiRecordedAction,
    UiRecordingEvent,
    UiRecordingSession,
)  # noqa: E402
from recorder_agent.main import _redact_html  # noqa: E402
from server.services.ui_recording_service import (  # noqa: E402
    _normalized_request_url,
    _request_body_hash,
)
from server.services.ui_recording_redaction import (
    redact_context_body,
    redact_context_payload,
    redact_context_text,
    redact_context_url,
)  # noqa: E402


_ARTIFACT_ROOT = _PROJECT_ROOT / "data" / "ui_recordings"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_artifact(session_root: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    path = (session_root / relative_path).resolve()
    root = session_root.resolve()
    if path != root and root not in path.parents:
        return None
    return path


def _write_text_if_changed(path: Path, value: str, *, apply: bool) -> bool:
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8", errors="replace")
    if original == value:
        return False
    if apply:
        path.write_text(value, encoding="utf-8")
    return True


def _decode_legacy_html(value: str) -> str:
    """修复旧脱敏逻辑误把整份 HTML 当作 Form URL 编码的离线文档。"""
    if value.lstrip().startswith("<"):
        return value
    decoded = unquote_plus(value)
    return decoded if decoded.lstrip().startswith("<") else value


def sanitize_offline_manifests(*, apply: bool) -> dict[str, int]:
    """脱敏清单、DOM 和文本资源，保持严格离线回放的哈希可验证。"""
    counters = {"manifests": 0, "documents": 0, "resources": 0}
    if not _ARTIFACT_ROOT.is_dir():
        return counters
    for manifest_path in _ARTIFACT_ROOT.glob("session_*/offline/manifest.json"):
        try:
            original = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        session_root = manifest_path.parent.parent
        url_replacements: dict[str, str] = {}
        for collection in (original.get("pages") or [], original.get("resources") or [], original.get("mocks") or []):
            for item in collection:
                if not isinstance(item, dict):
                    continue
                raw_url = str(item.get("url") or "")
                safe_url = redact_context_url(raw_url)
                if raw_url and raw_url != safe_url:
                    url_replacements[raw_url] = safe_url

        manifest = redact_context_payload(original)
        for exchange in manifest.get("mocks") or []:
            if not isinstance(exchange, dict):
                continue
            request = exchange.get("request") if isinstance(exchange.get("request"), dict) else {}
            method = str(exchange.get("method") or request.get("method") or "GET").upper()
            url = str(exchange.get("url") or request.get("url") or "")
            body = str(request.get("body") or "")
            exchange["match_rule"] = {
                "method": method,
                "normalized_url": _normalized_request_url(url),
                "body_sha256": _request_body_hash(body),
            }
        for page in manifest.get("pages") or []:
            if not isinstance(page, dict):
                continue
            document = _safe_artifact(session_root, str(page.get("document_path") or ""))
            if document and document.is_file():
                html = _redact_html(_decode_legacy_html(
                    document.read_text(encoding="utf-8", errors="replace"),
                ))
                for raw_url, safe_url in url_replacements.items():
                    html = html.replace(raw_url, safe_url)
                if _write_text_if_changed(document, html, apply=apply):
                    counters["documents"] += 1
                if apply:
                    page["document_sha256"] = _sha256(document)

        for resource in manifest.get("resources") or []:
            if not isinstance(resource, dict):
                continue
            content_type = str((resource.get("headers") or {}).get("content-type") or "").lower()
            if not any(kind in content_type for kind in ("html", "json", "javascript", "css", "text", "xml")):
                continue
            target = _safe_artifact(session_root, str(resource.get("path") or ""))
            if target is None or not target.is_file():
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
            if "html" in content_type:
                safe_text = _redact_html(_decode_legacy_html(text))
            elif "json" in content_type:
                safe_text = redact_context_body(text, content_type)
            elif "text/plain" in content_type or "xml" in content_type:
                safe_text = redact_context_text(text)
            else:
                # 不对 JS/CSS 源码做正则键值替换，避免破坏变量声明和选择器。
                safe_text = text
            for raw_url, safe_url in url_replacements.items():
                safe_text = safe_text.replace(raw_url, safe_url)
            if _write_text_if_changed(target, safe_text, apply=apply):
                counters["resources"] += 1
            if apply:
                resource["size"] = target.stat().st_size
                resource["sha256"] = _sha256(target)

        serialized = json.dumps(manifest, ensure_ascii=False, indent=2)
        if serialized != json.dumps(original, ensure_ascii=False, indent=2):
            counters["manifests"] += 1
            if apply:
                manifest_path.write_text(serialized, encoding="utf-8")
    return counters


def _replace_json(row: Any, attribute: str) -> bool:
    original = getattr(row, attribute)
    sanitized = redact_context_payload(original)
    if original == sanitized:
        return False
    setattr(row, attribute, sanitized)
    return True


def sanitize_database(*, apply: bool) -> dict[str, int]:
    """纵深清理旧 Agent 或旧版本已经写入 PostgreSQL 的上下文。"""
    db = DB()
    session = db.session
    changed: dict[str, int] = {}

    def mark(name: str, did_change: bool) -> None:
        if did_change:
            changed[name] = changed.get(name, 0) + 1

    try:
        for row in session.query(UiRecordingSession).yield_per(200):
            source_url = redact_context_url(row.source_url) if row.source_url else None
            did_change = row.source_url != source_url
            row.source_url = source_url
            for field in ("capture_config", "capabilities", "context_summary"):
                did_change = _replace_json(row, field) or did_change
            mark("sessions", did_change)
        for row in session.query(UiRecordingEvent).yield_per(500):
            did_change = _replace_json(row, "payload")
            safe_page_key = redact_context_text(row.page_key)[:255] if row.page_key else None
            did_change = did_change or row.page_key != safe_page_key
            row.page_key = safe_page_key
            mark("events", did_change)
        for row in session.query(UiPageSnapshot).yield_per(200):
            safe_url = redact_context_url(row.url) if row.url else None
            did_change = row.url != safe_url
            row.url = safe_url
            for field in ("resource_manifest", "environment", "limitations"):
                did_change = _replace_json(row, field) or did_change
            mark("snapshots", did_change)
        for row in session.query(UiElement).yield_per(500):
            mark("elements", _replace_json(row, "attributes"))
        for row in session.query(UiElementOccurrence).yield_per(500):
            did_change = _replace_json(row, "attributes")
            did_change = _replace_json(row, "locators") or did_change
            mark("occurrences", did_change)
        for row in session.query(UiRecordedAction).yield_per(500):
            mark("actions", _replace_json(row, "payload"))
        for row in session.query(UiPageTransition).yield_per(500):
            mark("transitions", _replace_json(row, "metadata_json"))
        for row in session.query(UiContextSession).yield_per(200):
            did_change = False
            for field in ("capabilities", "limitations", "summary"):
                did_change = _replace_json(row, field) or did_change
            mark("context_sessions", did_change)
        for row in session.query(UiContextEvent).yield_per(500):
            mark("context_events", _replace_json(row, "payload"))
        for row in session.query(UiContextArtifact).yield_per(500):
            mark("artifacts", _replace_json(row, "metadata_json"))
        for row in session.query(UiMockExchange).yield_per(500):
            safe_url = redact_context_url(row.url)
            did_change = row.url != safe_url
            row.url = safe_url
            for field in ("request", "response", "timing"):
                did_change = _replace_json(row, field) or did_change
            next_rule = {
                "method": str(row.method or "GET").upper(),
                "normalized_url": _normalized_request_url(row.url),
                "body_sha256": _request_body_hash(str((row.request or {}).get("body") or "")),
            }
            did_change = did_change or row.match_rule != next_rule
            row.match_rule = next_rule
            mark("mocks", did_change)
        if apply:
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="脱敏历史 UI 录制数据")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预览数量")
    parser.add_argument("--skip-database", action="store_true", help="只清理离线制品")
    args = parser.parse_args()
    files = sanitize_offline_manifests(apply=args.apply)
    database = {} if args.skip_database else sanitize_database(apply=args.apply)
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "files": files,
        "database": database,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
