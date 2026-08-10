"""UI 录制会话状态机、事件接收与序列化服务。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    UI_RECORDING_CANCELLED,
    UI_RECORDING_COMPLETED,
    UI_RECORDING_DRAFT,
    UI_RECORDING_FAILED,
    UI_RECORDING_PAUSED,
    UI_RECORDING_RECORDING,
    UiElement,
    UiElementLocator,
    UiElementOccurrence,
    UiContextArtifact,
    UiContextSession,
    UiMockExchange,
    UiPageTransition,
    UiPageSnapshot,
    UiRecordedAction,
    UiRecordingEvent,
    UiRecordingSession,
    UiStepContextLink,
)
from database.schemas.ui_recording import UiRecordingEventCreate
from server.services.ui_recording_redaction import redact_context_payload, redact_context_text


class UiRecordingTransitionError(ValueError):
    """会话状态不允许执行目标动作。"""


class UiRecordingControlLeaseError(ValueError):
    """录制控制权当前由另一个页面持有。"""


CONTROL_LEASE_SECONDS = 8


def _active_control_lease(session: UiRecordingSession) -> dict[str, Any]:
    lease = dict((session.capabilities or {}).get("control_lease") or {})
    expires_at = lease.get("expires_at")
    if not expires_at:
        return {}
    try:
        expires = datetime.fromisoformat(str(expires_at))
    except ValueError:
        return {}
    return lease if expires > datetime.now() else {}


def update_control_lease(
    session: UiRecordingSession,
    client_instance_id: str,
    action: str,
) -> dict[str, Any]:
    """领取、续约、接管或释放录制控制权。"""
    current = _active_control_lease(session)
    owner_id = str(current.get("owner_id") or "")
    if action == "release":
        if owner_id and owner_id != client_instance_id:
            raise UiRecordingControlLeaseError("当前窗口不是录制控制端，不能释放控制权")
        next_capabilities = dict(session.capabilities or {})
        next_capabilities.pop("control_lease", None)
        session.capabilities = next_capabilities
        return {}

    if owner_id and owner_id != client_instance_id and action != "takeover":
        raise UiRecordingControlLeaseError("录制控制权正在另一个窗口中使用")

    lease = {
        "owner_id": client_instance_id,
        "expires_at": (datetime.now() + timedelta(seconds=CONTROL_LEASE_SECONDS)).isoformat(),
        "lease_seconds": CONTROL_LEASE_SECONDS,
        "processed_commands": list(current.get("processed_commands") or [])[-20:],
    }
    session.capabilities = {
        **(session.capabilities or {}),
        "control_lease": lease,
    }
    return lease


def ensure_control_lease(
    session: UiRecordingSession,
    client_instance_id: str | None,
    command_id: str | None,
    *,
    takeover: bool = False,
) -> bool:
    """校验控制权并登记幂等命令；返回 True 表示命令已处理。"""
    if not client_instance_id:
        return False
    action = "takeover" if takeover else "heartbeat"
    lease = update_control_lease(session, client_instance_id, action)
    commands = list(lease.get("processed_commands") or [])
    if command_id and command_id in commands:
        return True
    if command_id:
        commands.append(command_id)
        lease["processed_commands"] = commands[-20:]
        session.capabilities = {
            **(session.capabilities or {}),
            "control_lease": lease,
        }
    return False


def is_control_command_processed(
    session: UiRecordingSession,
    client_instance_id: str | None,
    command_id: str | None,
) -> bool:
    """判断同一控制端的命令是否已成功登记。"""
    if not client_instance_id or not command_id:
        return False
    lease = _active_control_lease(session)
    return (
        lease.get("owner_id") == client_instance_id
        and command_id in set(lease.get("processed_commands") or [])
    )


_ACTION_TARGETS: dict[str, tuple[set[str], str]] = {
    "start": ({UI_RECORDING_DRAFT, UI_RECORDING_FAILED}, UI_RECORDING_RECORDING),
    "pause": ({UI_RECORDING_RECORDING}, UI_RECORDING_PAUSED),
    "resume": ({UI_RECORDING_PAUSED}, UI_RECORDING_RECORDING),
    "stop": ({UI_RECORDING_RECORDING, UI_RECORDING_PAUSED}, UI_RECORDING_COMPLETED),
    "cancel": (
        {UI_RECORDING_DRAFT, UI_RECORDING_RECORDING, UI_RECORDING_PAUSED, UI_RECORDING_FAILED},
        UI_RECORDING_CANCELLED,
    ),
}


def validate_control_action(session: UiRecordingSession, action: str) -> str:
    """只校验状态转换，供 API 在调用外部 Agent 前使用。"""
    rule = _ACTION_TARGETS.get(action)
    if rule is None:
        raise UiRecordingTransitionError(f"未知录制动作：{action}")
    allowed, target = rule
    current_status = session.status or UI_RECORDING_DRAFT
    if current_status not in allowed:
        raise UiRecordingTransitionError(
            f"当前状态 {current_status} 不能执行 {action}"
        )
    return target


def apply_control_action(session: UiRecordingSession, action: str) -> UiRecordingSession:
    """按状态机执行控制动作，并更新生命周期时间。"""
    target = validate_control_action(session, action)

    now = datetime.now()
    session.status = target
    if action == "start":
        session.started_at = now
        session.paused_at = None
        session.ended_at = None
        session.error = None
    elif action == "pause":
        session.paused_at = now
    elif action == "resume":
        session.paused_at = None
    elif action in {"stop", "cancel"}:
        session.ended_at = now
        session.paused_at = None
    return session


def append_events(
    db: Session,
    session: UiRecordingSession,
    items: Iterable[UiRecordingEventCreate],
) -> tuple[list[UiRecordingEvent], int]:
    """幂等追加一批事件。

    `event_key` 是 Recorder Agent 生成的幂等键；重复上报会跳过。没有显式
    `sequence_no` 时由服务端接在当前最大序号之后。
    """
    if session.status not in {UI_RECORDING_RECORDING, UI_RECORDING_PAUSED}:
        raise UiRecordingTransitionError(
            f"当前状态 {session.status} 不接收录制事件"
        )

    payloads = list(items)
    event_keys = [item.event_key for item in payloads]
    existing_keys = {
        row[0]
        for row in (
            db.query(UiRecordingEvent.event_key)
            .filter(
                UiRecordingEvent.session_id == session.id,
                UiRecordingEvent.event_key.in_(event_keys),
            )
            .all()
        )
    }
    next_sequence = int(
        db.query(func.coalesce(func.max(UiRecordingEvent.sequence_no), 0))
        .filter(UiRecordingEvent.session_id == session.id)
        .scalar()
        or 0
    )
    explicit_sequences = {
        item.sequence_no for item in payloads if item.sequence_no is not None
    }
    occupied_sequences = {
        int(row[0])
        for row in (
            db.query(UiRecordingEvent.sequence_no)
            .filter(
                UiRecordingEvent.session_id == session.id,
                UiRecordingEvent.sequence_no.in_(explicit_sequences),
            )
            .all()
        )
    } if explicit_sequences else set()

    created: list[UiRecordingEvent] = []
    seen_keys = set(existing_keys)
    seen_sequences = set(occupied_sequences)
    skipped = 0
    capabilities = dict(session.capabilities or {})
    for item in payloads:
        if item.event_key in seen_keys:
            skipped += 1
            continue
        seen_keys.add(item.event_key)

        if item.sequence_no is None:
            next_sequence += 1
            while next_sequence in seen_sequences:
                next_sequence += 1
            sequence_no = next_sequence
        else:
            sequence_no = item.sequence_no
            if sequence_no in seen_sequences:
                raise UiRecordingTransitionError(
                    f"sequence_no={sequence_no} 已被其它事件占用"
                )
            next_sequence = max(next_sequence, sequence_no)
        seen_sequences.add(sequence_no)
        safe_payload = redact_context_payload(item.payload)

        row = UiRecordingEvent(
            session_id=session.id,
            event_key=item.event_key,
            sequence_no=sequence_no,
            event_type=item.event_type,
            source=item.source,
            severity=item.severity,
            page_key=(redact_context_text(item.page_key)[:255] if item.page_key else None),
            element_id=item.element_id,
            snapshot_before_id=item.snapshot_before_id,
            snapshot_after_id=item.snapshot_after_id,
            occurred_at=item.occurred_at,
            monotonic_ms=item.monotonic_ms,
            payload=safe_payload,
        )
        db.add(row)
        created.append(row)

        if item.event_type == "agent.connected":
            agent_id = str(safe_payload.get("agent_id") or "").strip()
            if agent_id:
                session.recorder_agent_id = agent_id[:128]
            capabilities = {
                **capabilities,
                "recorder_agent_connected": True,
                "reported": safe_payload.get("capabilities") or {},
            }
        elif item.event_type == "agent.disconnected":
            capabilities = {**capabilities, "recorder_agent_connected": False}
        elif item.event_type == "offline.package":
            capabilities = {
                **capabilities,
                "offline_replay": dict(safe_payload or {}),
            }

    if created:
        session.capabilities = capabilities
        db.flush()
        for row in created:
            db.refresh(row)
        _materialize_snapshots(db, session, created)
        _materialize_mock_exchanges(db, session, created)
        _materialize_elements(db, session, created)
        session.context_summary = _updated_context_summary(
            session.context_summary or {},
            created,
            next_sequence,
        )
        _materialize_occurrences_actions_context(db, session, created)
        db.flush()
    return created, skipped


def _artifact_uri(session_id: int, relative_path: Any) -> str | None:
    path = str(relative_path or "").strip()
    if not path:
        return None
    if path.startswith("/"):
        return path
    return f"data/ui_recordings/session_{session_id}/{path}"


def _visible_element_bounds(items: Any) -> dict[str, dict[str, Any]]:
    """从 Agent 可见元素清单提取当前快照坐标，忽略不可信结构。"""
    if not isinstance(items, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in items[:800]:
        if not isinstance(item, dict) or not item.get("fingerprint"):
            continue
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            continue
        bounds = attributes.get("bounds")
        if isinstance(bounds, dict):
            result[str(item["fingerprint"])] = dict(bounds)
    return result


def _materialize_snapshots(
    db: Session,
    session: UiRecordingSession,
    events: list[UiRecordingEvent],
) -> None:
    """把 Agent 产出的 DOM/截图清单沉淀为页面快照版本。"""
    environment_event = next(
        (event for event in reversed(events) if event.event_type == "environment.snapshot"),
        None,
    )
    if environment_event is None:
        environment_event = (
            db.query(UiRecordingEvent)
            .filter(
                UiRecordingEvent.session_id == session.id,
                UiRecordingEvent.event_type == "environment.snapshot",
            )
            .order_by(UiRecordingEvent.sequence_no.desc())
            .first()
        )
    environment = dict(environment_event.payload or {}) if environment_event else {}
    for event in events:
        if event.event_type != "page.snapshot":
            continue
        payload = event.payload or {}
        fingerprint = str(payload.get("fingerprint") or "").strip()
        if not fingerprint:
            continue
        existing = (
            db.query(UiPageSnapshot)
            .filter(
                UiPageSnapshot.session_id == session.id,
                UiPageSnapshot.fingerprint == fingerprint,
            )
            .first()
        )
        if existing is not None:
            event.snapshot_after_id = existing.id
            continue
        page_key = str(payload.get("page_key") or event.page_key or "about:blank")[:255]
        version = int(
            db.query(func.count(UiPageSnapshot.id))
            .filter(
                UiPageSnapshot.project_id == session.project_id,
                UiPageSnapshot.platform == session.platform,
                UiPageSnapshot.page_key == page_key,
            )
            .scalar()
            or 0
        ) + 1
        snapshot = UiPageSnapshot(
            session_id=session.id,
            project_id=session.project_id,
            platform=session.platform,
            page_key=page_key,
            page_name=str(payload.get("page_name") or payload.get("title") or page_key)[:200],
            state_name=str(payload.get("state_name") or "")[:120] or None,
            url=str(payload.get("url") or "") or None,
            route=urlsplit(str(payload.get("url") or "")).path[:500] or None,
            app_identifier=str(payload.get("app_identifier") or "")[:255] or None,
            snapshot_version=version,
            fingerprint=fingerprint,
            screenshot_uri=_artifact_uri(session.id, payload.get("screenshot_path")),
            document_uri=_artifact_uri(session.id, payload.get("document_path")),
            tree_uri=(
                _artifact_uri(session.id, payload.get("document_path"))
                if session.platform in {"android", "ios"}
                else None
            ),
            is_interactive=True,
            resource_manifest={
                "visible_element_fingerprints": list(
                    payload.get("visible_element_fingerprints") or [],
                )[:800],
                "visible_element_bounds": _visible_element_bounds(
                    payload.get("visible_elements"),
                ),
                "modal_open": bool(payload.get("modal_open")),
            },
            environment={
                **environment,
                "viewport": (
                    dict(payload.get("viewport") or {})
                    or dict(environment.get("viewport") or {})
                ),
            },
            limitations=[],
        )
        db.add(snapshot)
        db.flush()
        event.snapshot_after_id = snapshot.id

    package_event = next(
        (event for event in reversed(events) if event.event_type == "offline.package"),
        None,
    )
    if package_event is None:
        return
    package = dict(package_event.payload or {})
    manifest_path = str(package.get("manifest_path") or "") or None
    limitations = list(package.get("limitations") or [])
    manifest_summary = {
        "page_count": int(package.get("page_count") or 0),
        "resource_count": int(package.get("resource_count") or 0),
        "mock_count": int(package.get("mock_count") or 0),
        "archive_bytes": int(package.get("archive_bytes") or 0),
        "ready": bool(package.get("ready")),
        "integrity_verified": bool(package.get("integrity_verified")),
    }
    for snapshot in (
        db.query(UiPageSnapshot)
        .filter(UiPageSnapshot.session_id == session.id)
        .all()
    ):
        snapshot.offline_package_uri = manifest_path
        snapshot.resource_manifest = {
            **(snapshot.resource_manifest or {}),
            **manifest_summary,
        }
        snapshot.limitations = limitations
        snapshot.is_interactive = bool(package.get("ready"))


def _normalized_request_url(value: str) -> str:
    """生成与 Agent 一致的离线请求键，忽略缓存参数和敏感值。"""
    parts = urlsplit(value)
    ignored = {"_", "cache", "cachebuster", "nonce", "timestamp", "ts"}
    query = urlencode(sorted(
        (
            key,
            "***" if re.search(
                r"(?i)(password|passwd|secret|credential|authorization|cookie|token|signature|card|cvv|cvc)",
                key,
            ) else item_value,
        )
        for key, item_value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in ignored and not key.lower().startswith("utm_")
    ))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def _request_body_hash(value: str) -> str:
    """JSON 请求体按键排序后计算签名，避免字段顺序导致离线误判。"""
    try:
        normalized = json.dumps(
            json.loads(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        normalized = value
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _materialize_mock_exchanges(
    db: Session,
    session: UiRecordingSession,
    events: list[UiRecordingEvent],
) -> None:
    """将 network.request/response 配对为可顺序回放的本地 Mock。"""
    responses = [event for event in events if event.event_type == "network.response"]
    if not responses:
        return
    request_events = (
        db.query(UiRecordingEvent)
        .filter(
            UiRecordingEvent.session_id == session.id,
            UiRecordingEvent.event_type == "network.request",
        )
        .all()
    )
    requests_by_key = {
        str((event.payload or {}).get("request_key") or ""): event
        for event in request_events
        if (event.payload or {}).get("request_key")
    }
    existing_keys = {
        row[0]
        for row in (
            db.query(UiMockExchange.exchange_key)
            .filter(UiMockExchange.session_id == session.id)
            .all()
        )
    }
    for response_event in responses:
        if response_event.event_key in existing_keys:
            continue
        response_payload = dict(response_event.payload or {})
        request_key = str(response_payload.get("request_key") or "")
        request_event = requests_by_key.get(request_key)
        if request_event is None:
            continue
        request_payload = dict(request_event.payload or {})
        method = str(request_payload.get("method") or "GET").upper()[:12]
        url = str(request_payload.get("url") or response_payload.get("url") or "")
        body = str(request_payload.get("body") or "")
        db.add(UiMockExchange(
            session_id=session.id,
            exchange_key=response_event.event_key,
            sequence_no=response_event.sequence_no,
            method=method,
            url=url,
            request_key=request_key[:64],
            request=request_payload,
            response=response_payload,
            match_rule={
                "method": method,
                "normalized_url": _normalized_request_url(url),
                "body_sha256": _request_body_hash(body),
            },
            timing={"duration_ms": response_payload.get("duration_ms")},
        ))


def _materialize_elements(
    db: Session,
    session: UiRecordingSession,
    events: list[UiRecordingEvent],
) -> None:
    """把用户事件和页面快照里的可交互 DOM 元素沉淀到项目元素库。"""
    for event in events:
        payload = dict(event.payload or {})
        raw_items: list[dict[str, Any]] = []
        raw_element = payload.get("element")
        if isinstance(raw_element, dict):
            raw_items.append(raw_element)
        if event.event_type == "page.snapshot":
            raw_items.extend(
                item
                for item in list(payload.get("visible_elements") or [])[:800]
                if isinstance(item, dict)
            )
        if not raw_items:
            continue
        page_key = event.page_key or "about:blank"
        snapshot = (
            db.get(UiPageSnapshot, event.snapshot_after_id)
            if event.snapshot_after_id is not None
            else None
        )
        if snapshot is None:
            snapshot = (
                db.query(UiPageSnapshot)
                .filter(
                    UiPageSnapshot.session_id == session.id,
                    UiPageSnapshot.page_key == page_key,
                )
                .order_by(UiPageSnapshot.created_at.desc(), UiPageSnapshot.id.desc())
                .first()
            )
        page_name = str(
            payload.get("page_name")
            or payload.get("page_title")
            or (snapshot.page_name if snapshot is not None else page_key)
        )[:200]

        for raw in raw_items:
            fingerprint = str(raw.get("fingerprint") or "").strip()
            if not fingerprint:
                continue
            element = (
                db.query(UiElement)
                .filter(
                    UiElement.project_id == session.project_id,
                    UiElement.platform == session.platform,
                    UiElement.page_key == page_key,
                    UiElement.fingerprint == fingerprint,
                )
                .first()
            )
            attributes = (
                raw.get("attributes")
                if isinstance(raw.get("attributes"), dict)
                else {}
            )
            if element is None:
                element = UiElement(
                    project_id=session.project_id,
                    platform=session.platform,
                    page_key=page_key,
                    page_name=page_name,
                    semantic_name=str(raw.get("semantic_name") or "未命名元素")[:200],
                    element_type=str(raw.get("element_type") or "element")[:100],
                    fingerprint=fingerprint,
                    attributes=attributes,
                    first_snapshot_id=snapshot.id if snapshot else None,
                    last_snapshot_id=snapshot.id if snapshot else None,
                )
                db.add(element)
                db.flush()
            else:
                element.page_name = page_name
                element.semantic_name = str(
                    raw.get("semantic_name") or element.semantic_name,
                )[:200]
                element.element_type = str(
                    raw.get("element_type") or element.element_type,
                )[:100]
                element.attributes = {**(element.attributes or {}), **attributes}
                if snapshot is not None:
                    snapshot_ids = [
                        item
                        for item in (
                            element.first_snapshot_id,
                            element.last_snapshot_id,
                            snapshot.id,
                        )
                        if item is not None
                    ]
                    element.first_snapshot_id = min(snapshot_ids)
                    element.last_snapshot_id = max(snapshot_ids)

            locator_items = raw.get("locators") if isinstance(raw.get("locators"), list) else []
            for item in locator_items:
                if not isinstance(item, dict):
                    continue
                strategy = str(item.get("strategy") or "").strip().lower()
                locator_value = str(item.get("locator") or "").strip()
                if not strategy or not locator_value:
                    continue
                locator = (
                    db.query(UiElementLocator)
                    .filter(
                        UiElementLocator.element_id == element.id,
                        UiElementLocator.strategy == strategy,
                        UiElementLocator.locator == locator_value,
                    )
                    .first()
                )
                score = max(0, min(100, int(item.get("score") or 0)))
                match_count = item.get("match_count")
                match_count = int(match_count) if isinstance(match_count, (int, float)) else None
                is_unique = item.get("is_unique")
                is_unique = bool(is_unique) if isinstance(is_unique, bool) else None
                if locator is None:
                    locator = UiElementLocator(
                        element_id=element.id,
                        strategy=strategy,
                        locator=locator_value,
                        score=score,
                        is_unique=is_unique,
                        match_count=match_count,
                        source="recorder",
                    )
                    db.add(locator)
                else:
                    locator.score = max(locator.score or 0, score)
                    if is_unique is not None:
                        locator.is_unique = is_unique
                        locator.match_count = match_count
                if is_unique is not None:
                    locator.last_verified_at = datetime.now()
                    locator.last_verified_snapshot_id = snapshot.id if snapshot else None
            db.flush()
            locators = (
                db.query(UiElementLocator)
                .filter(UiElementLocator.element_id == element.id)
                .order_by(UiElementLocator.score.desc(), UiElementLocator.id)
                .all()
            )
            for index, locator in enumerate(locators):
                locator.is_primary = index == 0
            if any(locator.is_unique is True for locator in locators):
                element.status = "verified"
                element.last_verified_at = datetime.now()
            if raw is raw_element:
                event.element_id = element.id


def materialize_snapshot_element(
    db: Session,
    session: UiRecordingSession,
    snapshot: UiPageSnapshot,
    raw_element: dict[str, Any],
) -> UiElement:
    """把只读快照坐标拾取到的元素补充进项目元素库。"""
    fingerprint = str(raw_element.get("fingerprint") or "").strip()
    if not fingerprint:
        raise ValueError("拾取结果缺少元素指纹")
    synthetic_event = UiRecordingEvent(
        session_id=session.id,
        event_key=f"snapshot-pick-{fingerprint[:32]}",
        sequence_no=0,
        event_type="page.snapshot",
        source="snapshot",
        severity="info",
        page_key=snapshot.page_key,
        occurred_at=datetime.now(),
        snapshot_after_id=snapshot.id,
        payload={
            "page_name": snapshot.page_name,
            "visible_elements": [raw_element],
        },
    )
    _materialize_elements(db, session, [synthetic_event])
    element = (
        db.query(UiElement)
        .filter(
            UiElement.project_id == session.project_id,
            UiElement.platform == session.platform,
            UiElement.page_key == snapshot.page_key,
            UiElement.fingerprint == fingerprint,
        )
        .first()
    )
    if element is None:
        raise ValueError("拾取元素未能写入元素库")

    attributes = (
        raw_element.get("attributes")
        if isinstance(raw_element.get("attributes"), dict)
        else {}
    )
    occurrence = (
        db.query(UiElementOccurrence)
        .filter(
            UiElementOccurrence.element_id == element.id,
            UiElementOccurrence.snapshot_id == snapshot.id,
        )
        .first()
    )
    if occurrence is None:
        db.add(UiElementOccurrence(
            session_id=session.id,
            snapshot_id=snapshot.id,
            element_id=element.id,
            bounds=dict(attributes.get("bounds") or {}),
            attributes=attributes,
            locators=list(raw_element.get("locators") or []),
        ))

    manifest = dict(snapshot.resource_manifest or {})
    fingerprints = list(manifest.get("visible_element_fingerprints") or [])
    if fingerprint not in fingerprints:
        fingerprints.append(fingerprint)
    bounds = dict(manifest.get("visible_element_bounds") or {})
    if isinstance(attributes.get("bounds"), dict):
        bounds[fingerprint] = dict(attributes["bounds"])
    snapshot.resource_manifest = {
        **manifest,
        "visible_element_fingerprints": fingerprints[:800],
        "visible_element_bounds": bounds,
    }
    db.flush()
    db.expire(element, ["locators"])
    return element


_ACTION_EVENT_TYPES = {
    "user.click",
    "user.input",
    "user.change",
    "user.submit",
    "user.scroll",
    "user.tap",
    "user.swipe",
    "user.back",
    "user.refresh",
}


def _action_display_name(event: UiRecordingEvent) -> str:
    payload = dict(event.payload or {})
    element = payload.get("element") if isinstance(payload.get("element"), dict) else {}
    semantic_name = str(element.get("semantic_name") or "当前页面")
    verbs = {
        "user.click": "点击",
        "user.input": "输入",
        "user.change": "切换",
        "user.submit": "提交",
        "user.scroll": "滚动",
        "user.tap": "点击",
        "user.swipe": "滑动",
        "user.back": "返回",
        "user.refresh": "刷新",
    }
    return f"{verbs.get(event.event_type, event.event_type)} {semantic_name}"[:255]


def _ensure_authoring_context(db: Session, session: UiRecordingSession) -> UiContextSession:
    context = (
        db.query(UiContextSession)
        .filter(UiContextSession.recording_session_id == session.id)
        .first()
    )
    if context is None:
        reported = dict((session.capabilities or {}).get("reported") or {})
        context = UiContextSession(
            project_id=session.project_id,
            recording_session_id=session.id,
            kind="authoring",
            platform=session.platform,
            status="active",
            capabilities=reported or dict(session.capabilities or {}),
            limitations=[],
            summary=dict(session.context_summary or {}),
            started_at=session.started_at or datetime.now(),
        )
        db.add(context)
        db.flush()
    return context


def _artifact_file_metadata(uri: str | None) -> tuple[int | None, str | None]:
    if not uri:
        return None, None
    path = Path(uri)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    try:
        content = path.read_bytes()
    except OSError:
        return None, None
    return len(content), hashlib.sha256(content).hexdigest()


def _append_context_artifact(
    db: Session,
    context: UiContextSession,
    event: UiRecordingEvent,
    artifact_type: str,
    uri: str | None,
    *,
    mime_type: str | None = None,
) -> UiContextArtifact | None:
    if not uri:
        return None
    existing = (
        db.query(UiContextArtifact)
        .filter(
            UiContextArtifact.context_session_id == context.id,
            UiContextArtifact.event_id == event.id,
            UiContextArtifact.artifact_type == artifact_type,
            UiContextArtifact.uri == uri,
        )
        .first()
    )
    if existing is not None:
        return existing
    size_bytes, sha256 = _artifact_file_metadata(uri)
    artifact = UiContextArtifact(
        context_session_id=context.id,
        event_id=event.id,
        artifact_type=artifact_type,
        uri=uri,
        mime_type=mime_type,
        size_bytes=size_bytes,
        sha256=sha256,
        metadata_json={"event_type": event.event_type, "sequence_no": event.sequence_no},
    )
    db.add(artifact)
    db.flush()
    return artifact


def _materialize_occurrences_actions_context(
    db: Session,
    session: UiRecordingSession,
    events: list[UiRecordingEvent],
) -> None:
    """把事件流物化为元素证据、动作、跳转和可查询上下文制品。"""
    if not events:
        return
    context = _ensure_authoring_context(db, session)
    previous_action = (
        db.query(UiRecordedAction)
        .filter(UiRecordedAction.session_id == session.id)
        .order_by(UiRecordedAction.sequence_no.desc())
        .first()
    )
    previous_page_key = None
    previous_page_event = (
        db.query(UiRecordingEvent)
        .filter(
            UiRecordingEvent.session_id == session.id,
            UiRecordingEvent.sequence_no < min(event.sequence_no for event in events),
            UiRecordingEvent.page_key.isnot(None),
        )
        .order_by(UiRecordingEvent.sequence_no.desc())
        .first()
    )
    if previous_page_event is not None:
        previous_page_key = previous_page_event.page_key

    for event in sorted(events, key=lambda item: item.sequence_no):
        payload = dict(event.payload or {})
        if event.event_type in _ACTION_EVENT_TYPES:
            existing_action = (
                db.query(UiRecordedAction)
                .filter(
                    UiRecordedAction.session_id == session.id,
                    UiRecordedAction.source_event_id == event.id,
                )
                .first()
            )
            if existing_action is None:
                if previous_action is not None and previous_action.context_event_to_seq is None:
                    previous_action.context_event_to_seq = max(
                        previous_action.context_event_from_seq,
                        event.sequence_no - 1,
                    )
                    previous_action.ended_at = event.occurred_at
                    if event.monotonic_ms is not None:
                        started_ms = int((previous_action.payload or {}).get("monotonic_ms") or 0)
                        previous_action.duration_ms = max(0, event.monotonic_ms - started_ms)
                before_event = (
                    db.query(UiRecordingEvent)
                    .filter(
                        UiRecordingEvent.session_id == session.id,
                        UiRecordingEvent.event_type == "page.snapshot",
                        UiRecordingEvent.sequence_no < event.sequence_no,
                        UiRecordingEvent.snapshot_after_id.isnot(None),
                    )
                    .order_by(UiRecordingEvent.sequence_no.desc())
                    .first()
                )
                before_snapshot = (
                    db.get(UiPageSnapshot, before_event.snapshot_after_id)
                    if before_event is not None
                    else None
                )
                previous_action = UiRecordedAction(
                    session_id=session.id,
                    source_event_id=event.id,
                    sequence_no=event.sequence_no,
                    action_type=event.event_type,
                    name=_action_display_name(event),
                    target_element_id=event.element_id,
                    page_before_key=event.page_key or previous_page_key,
                    snapshot_before_id=before_snapshot.id if before_snapshot else None,
                    screenshot_before_uri=before_snapshot.screenshot_uri if before_snapshot else None,
                    started_at=event.occurred_at,
                    context_event_from_seq=event.sequence_no,
                    payload={**payload, "monotonic_ms": event.monotonic_ms},
                )
                db.add(previous_action)
                db.flush()
                db.add(UiStepContextLink(
                    context_session_id=context.id,
                    recorded_action_id=previous_action.id,
                    event_from_seq=previous_action.context_event_from_seq,
                    event_to_seq=previous_action.context_event_to_seq,
                    summary={"action_type": previous_action.action_type},
                ))
                db.flush()
        elif event.event_type == "page.snapshot" and event.snapshot_after_id is not None:
            if previous_action is not None and previous_action.sequence_no < event.sequence_no:
                previous_action.snapshot_after_id = event.snapshot_after_id
                previous_action.page_after_key = event.page_key
                previous_action.context_event_to_seq = event.sequence_no
                previous_action.ended_at = event.occurred_at
                if event.monotonic_ms is not None:
                    started_ms = int((previous_action.payload or {}).get("monotonic_ms") or 0)
                    previous_action.duration_ms = max(0, event.monotonic_ms - started_ms)
            snapshot = db.get(UiPageSnapshot, event.snapshot_after_id)
            if snapshot is not None:
                for raw in list(payload.get("visible_elements") or [])[:800]:
                    if not isinstance(raw, dict) or not raw.get("fingerprint"):
                        continue
                    element = (
                        db.query(UiElement)
                        .filter(
                            UiElement.project_id == session.project_id,
                            UiElement.platform == session.platform,
                            UiElement.page_key == snapshot.page_key,
                            UiElement.fingerprint == str(raw["fingerprint"]),
                        )
                        .first()
                    )
                    if element is None:
                        continue
                    exists = (
                        db.query(UiElementOccurrence.id)
                        .filter(
                            UiElementOccurrence.element_id == element.id,
                            UiElementOccurrence.snapshot_id == snapshot.id,
                        )
                        .first()
                    )
                    if exists is None:
                        attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
                        db.add(UiElementOccurrence(
                            session_id=session.id,
                            snapshot_id=snapshot.id,
                            element_id=element.id,
                            bounds=dict(attributes.get("bounds") or {}),
                            attributes=attributes,
                            locators=list(raw.get("locators") or []),
                        ))
                screenshot = _append_context_artifact(
                    db,
                    context,
                    event,
                    "page_screenshot",
                    snapshot.screenshot_uri,
                    mime_type="image/png",
                )
                _append_context_artifact(
                    db,
                    context,
                    event,
                    "page_document" if session.platform == "web" else "ui_tree",
                    snapshot.document_uri,
                    mime_type="text/html" if session.platform == "web" else "application/xml",
                )
                if previous_action is not None and screenshot is not None:
                    previous_action.screenshot_after_uri = screenshot.uri
        elif event.event_type == "screen.capture":
            artifact = _append_context_artifact(
                db,
                context,
                event,
                "screenshot",
                _artifact_uri(session.id, payload.get("path")),
                mime_type="image/png",
            )
            if previous_action is not None and artifact is not None:
                previous_action.screenshot_after_uri = artifact.uri

        if event.event_type == "page.navigation" and event.page_key:
            existing_transition = (
                db.query(UiPageTransition.id)
                .filter(
                    UiPageTransition.session_id == session.id,
                    UiPageTransition.source_event_id == event.id,
                )
                .first()
            )
            if existing_transition is None:
                db.add(UiPageTransition(
                    project_id=session.project_id,
                    session_id=session.id,
                    source_event_id=event.id,
                    platform=session.platform,
                    source_page_key=previous_page_key,
                    target_page_key=event.page_key,
                    action_id=previous_action.id if previous_action else None,
                    occurred_at=event.occurred_at,
                    metadata_json=payload,
                ))
        if event.page_key:
            previous_page_key = event.page_key

        if previous_action is not None:
            link = (
                db.query(UiStepContextLink)
                .filter(
                    UiStepContextLink.context_session_id == context.id,
                    UiStepContextLink.recorded_action_id == previous_action.id,
                    UiStepContextLink.test_step_report_id.is_(None),
                )
                .first()
            )
            if link is not None:
                link.event_from_seq = previous_action.context_event_from_seq
                link.event_to_seq = previous_action.context_event_to_seq
                link.summary = {
                    "action_type": previous_action.action_type,
                    "page_before_key": previous_action.page_before_key,
                    "page_after_key": previous_action.page_after_key,
                }

    context.capabilities = dict((session.capabilities or {}).get("reported") or session.capabilities or {})
    context.summary = dict(session.context_summary or {})
    db.flush()


def finalize_recording_context(
    db: Session,
    session: UiRecordingSession,
    *,
    cancelled: bool = False,
) -> UiContextSession | None:
    """关闭录制上下文，并补齐最后一个动作尚未结束的时间窗。"""
    context = (
        db.query(UiContextSession)
        .filter(UiContextSession.recording_session_id == session.id)
        .first()
    )
    if context is None:
        return None
    last_sequence = int(
        db.query(func.coalesce(func.max(UiRecordingEvent.sequence_no), 0))
        .filter(UiRecordingEvent.session_id == session.id)
        .scalar()
        or 0
    )
    last_action = (
        db.query(UiRecordedAction)
        .filter(UiRecordedAction.session_id == session.id)
        .order_by(UiRecordedAction.sequence_no.desc())
        .first()
    )
    if last_action is not None and last_action.context_event_to_seq is None:
        last_action.context_event_to_seq = max(last_action.context_event_from_seq, last_sequence)
        last_action.ended_at = session.ended_at or datetime.now()
    if last_action is not None:
        link = (
            db.query(UiStepContextLink)
            .filter(
                UiStepContextLink.context_session_id == context.id,
                UiStepContextLink.recorded_action_id == last_action.id,
                UiStepContextLink.test_step_report_id.is_(None),
            )
            .first()
        )
        if link is not None:
            link.event_to_seq = last_action.context_event_to_seq
    offline = dict((session.capabilities or {}).get("offline_replay") or {})
    context.status = "cancelled" if cancelled else "completed"
    context.ended_at = session.ended_at or datetime.now()
    context.capabilities = dict((session.capabilities or {}).get("reported") or session.capabilities or {})
    context.limitations = list(offline.get("limitations") or [])
    context.summary = {
        **(session.context_summary or {}),
        "last_sequence": last_sequence,
        "offline_ready": bool(offline.get("ready")),
    }
    db.flush()
    return context


def ensure_recording_context_materialized(
    db: Session,
    session: UiRecordingSession,
) -> UiContextSession:
    """兼容历史会话：首次打开结果页时按既有事件流补建上下文索引。"""
    context = (
        db.query(UiContextSession)
        .filter(UiContextSession.recording_session_id == session.id)
        .first()
    )
    if context is None:
        events = (
            db.query(UiRecordingEvent)
            .filter(UiRecordingEvent.session_id == session.id)
            .order_by(UiRecordingEvent.sequence_no)
            .all()
        )
        _materialize_occurrences_actions_context(db, session, events)
        context = (
            db.query(UiContextSession)
            .filter(UiContextSession.recording_session_id == session.id)
            .one()
        )
    if session.status in {UI_RECORDING_COMPLETED, UI_RECORDING_CANCELLED}:
        finalize_recording_context(
            db,
            session,
            cancelled=session.status == UI_RECORDING_CANCELLED,
        )
    return context


def _updated_context_summary(
    current: dict[str, Any],
    events: list[UiRecordingEvent],
    last_sequence: int,
) -> dict[str, Any]:
    next_summary = dict(current)
    next_summary["last_sequence"] = last_sequence
    counters = dict(next_summary.get("counters") or {})
    for event in events:
        bucket = event.source or "other"
        counters[bucket] = int(counters.get(bucket) or 0) + 1
    next_summary["counters"] = counters
    return next_summary


def serialize_session(
    db: Session,
    session: UiRecordingSession,
) -> dict[str, Any]:
    """输出前端需要的会话摘要。"""
    event_count = (
        db.query(func.count(UiRecordingEvent.id))
        .filter(UiRecordingEvent.session_id == session.id)
        .scalar()
        or 0
    )
    snapshot_count = len(session.snapshots or [])
    return {
        "id": session.id,
        "project_id": session.project_id,
        "platform": session.platform,
        "status": session.status,
        "name": session.name,
        "recording_role": session.recording_role,
        "baseline_session_id": session.baseline_session_id,
        "baseline_included": session.baseline_included,
        "baseline_version": session.baseline_version,
        "merged_at": session.merged_at,
        "environment_id": session.environment_id,
        "device_id": session.device_id,
        "app_package_id": session.app_package_id,
        "created_by_id": session.created_by_id,
        "source_url": session.source_url,
        "recorder_agent_id": session.recorder_agent_id,
        "offline_level": session.offline_level,
        "capture_config": session.capture_config or {},
        "capabilities": session.capabilities or {},
        "context_summary": session.context_summary or {},
        "error": session.error,
        "event_count": int(event_count),
        "snapshot_count": snapshot_count,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "started_at": session.started_at,
        "paused_at": session.paused_at,
        "ended_at": session.ended_at,
    }


def serialize_event(event: UiRecordingEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "event_key": event.event_key,
        "sequence_no": event.sequence_no,
        "event_type": event.event_type,
        "source": event.source,
        "severity": event.severity,
        "page_key": event.page_key,
        "element_id": event.element_id,
        "snapshot_before_id": event.snapshot_before_id,
        "snapshot_after_id": event.snapshot_after_id,
        "occurred_at": event.occurred_at,
        "monotonic_ms": event.monotonic_ms,
        "payload": event.payload or {},
        "created_at": event.created_at,
    }


def serialize_snapshot(snapshot: UiPageSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "session_id": snapshot.session_id,
        "project_id": snapshot.project_id,
        "platform": snapshot.platform,
        "page_key": snapshot.page_key,
        "page_name": snapshot.page_name,
        "state_name": snapshot.state_name,
        "url": snapshot.url,
        "snapshot_version": snapshot.snapshot_version,
        "fingerprint": snapshot.fingerprint,
        "has_screenshot": bool(snapshot.screenshot_uri),
        "has_document": bool(snapshot.document_uri),
        "has_offline_package": bool(snapshot.offline_package_uri),
        "is_interactive": snapshot.is_interactive,
        "resource_manifest": snapshot.resource_manifest or {},
        "environment": snapshot.environment or {},
        "limitations": snapshot.limitations or [],
        "created_at": snapshot.created_at,
    }


def serialize_recorded_action(action: UiRecordedAction) -> dict[str, Any]:
    """输出结果页动作摘要和动作前后证据。"""
    return {
        "id": action.id,
        "session_id": action.session_id,
        "source_event_id": action.source_event_id,
        "sequence_no": action.sequence_no,
        "action_type": action.action_type,
        "name": action.name,
        "status": action.status,
        "target_element_id": action.target_element_id,
        "page_before_key": action.page_before_key,
        "page_after_key": action.page_after_key,
        "snapshot_before_id": action.snapshot_before_id,
        "snapshot_after_id": action.snapshot_after_id,
        "screenshot_before_uri": action.screenshot_before_uri,
        "screenshot_after_uri": action.screenshot_after_uri,
        "element_screenshot_uri": action.element_screenshot_uri,
        "started_at": action.started_at,
        "ended_at": action.ended_at,
        "duration_ms": action.duration_ms,
        "context_event_from_seq": action.context_event_from_seq,
        "context_event_to_seq": action.context_event_to_seq,
        "payload": action.payload or {},
        "created_at": action.created_at,
        "updated_at": action.updated_at,
    }


def serialize_context_artifact(artifact: UiContextArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "context_session_id": artifact.context_session_id,
        "event_id": artifact.event_id,
        "context_event_id": artifact.context_event_id,
        "artifact_type": artifact.artifact_type,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "metadata": artifact.metadata_json or {},
        "created_at": artifact.created_at,
    }


def serialize_context_session(context: UiContextSession) -> dict[str, Any]:
    return {
        "id": context.id,
        "project_id": context.project_id,
        "recording_session_id": context.recording_session_id,
        "report_id": context.report_id,
        "kind": context.kind,
        "platform": context.platform,
        "status": context.status,
        "capabilities": context.capabilities or {},
        "limitations": context.limitations or [],
        "summary": context.summary or {},
        "started_at": context.started_at,
        "ended_at": context.ended_at,
        "created_at": context.created_at,
    }


def serialize_page_transition(transition: UiPageTransition) -> dict[str, Any]:
    return {
        "id": transition.id,
        "source_event_id": transition.source_event_id,
        "source_page_key": transition.source_page_key,
        "target_page_key": transition.target_page_key,
        "action_id": transition.action_id,
        "occurred_at": transition.occurred_at,
        "metadata": transition.metadata_json or {},
    }


def _preferred_event_locator(
    element: dict[str, Any] | None,
    platform: str,
) -> tuple[str, str] | None:
    if not isinstance(element, dict):
        return None
    supported = (
        {"id", "css", "name", "text", "link", "xpath"}
        if platform == "web"
        else {
            "id",
            "xpath",
            "accessibility_id",
            "android_uiautomator",
            "ios_predicate",
            "ios_class_chain",
            "class_name",
            "name",
        }
    )
    candidates = [
        item
        for item in (element.get("locators") or [])
        if isinstance(item, dict)
        and str(item.get("strategy") or "").lower() in supported
        and str(item.get("locator") or "").strip()
    ]
    if not candidates:
        return None
    selected = max(candidates, key=lambda item: int(item.get("score") or 0))
    return (
        str(selected.get("strategy") or "").lower(),
        str(selected.get("locator") or ""),
    )


def compile_recording_step_draft(
    session: UiRecordingSession,
    events: list[UiRecordingEvent],
) -> dict[str, Any]:
    """把已录制用户动作编译成现有 v2 Runner 可执行的 TestStep 草稿。"""
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []

    def append_step(
        event: UiRecordingEvent | None,
        step_type: str,
        step_name: str,
        config: dict[str, Any],
    ) -> None:
        steps.append({
            "step_order": len(steps) + 1,
            "step_name": step_name[:255],
            "step_type": step_type,
            "skip": False,
            "config": config,
            "extract": [],
            "assertion": [],
            "wait_before": 0,
            "timeout": 30,
            "retry": 0,
            "on_failure": "stop",
            "source_event_id": event.id if event else None,
        })

    if session.platform == "web" and session.source_url:
        append_step(None, "web_goto", f"打开 {session.source_url}", {"url": session.source_url})

    for event in events:
        event_type = event.event_type
        if event_type == "user.pick":
            continue
        payload = dict(event.payload or {})
        element = payload.get("element") if isinstance(payload.get("element"), dict) else None
        locator = _preferred_event_locator(element, session.platform)
        semantic_name = str((element or {}).get("semantic_name") or "当前元素")

        if session.platform == "web":
            if event_type not in {"user.click", "user.input", "user.change"}:
                if event_type in {"user.submit", "user.scroll"}:
                    warnings.append(
                        f"事件 #{event.sequence_no} {event_type} 没有独立 Runner，已保留在技术上下文中"
                    )
                continue
            if locator is None:
                warnings.append(f"事件 #{event.sequence_no} 缺少可执行定位器，未生成步骤")
                continue
            by, locator_value = locator
            if event_type == "user.click":
                append_step(
                    event,
                    "web_click",
                    f"点击 {semantic_name}",
                    {"by": by, "locator": locator_value},
                )
            elif event_type == "user.input":
                append_step(
                    event,
                    "web_input",
                    f"输入 {semantic_name}",
                    {
                        "by": by,
                        "locator": locator_value,
                        "value": payload.get("value") or "",
                        "clear_first": True,
                    },
                )
                if payload.get("redacted"):
                    warnings.append(f"步骤 {len(steps)} 含脱敏变量 ${{password}}，执行前需配置变量")
            else:
                tag = str(((element or {}).get("attributes") or {}).get("tag") or "")
                if tag == "select":
                    append_step(
                        event,
                        "web_select",
                        f"选择 {semantic_name}",
                        {"by": by, "locator": locator_value, "value": payload.get("value")},
                    )
                else:
                    append_step(
                        event,
                        "web_click",
                        f"切换 {semantic_name}",
                        {"by": by, "locator": locator_value},
                    )
            continue

        if event_type in {"user.tap", "user.input"}:
            if locator is None:
                warnings.append(f"事件 #{event.sequence_no} 缺少可执行移动定位器，未生成步骤")
                continue
            by, locator_value = locator
            if event_type == "user.tap":
                append_step(
                    event,
                    "app_tap",
                    f"点击 {semantic_name}",
                    {"by": by, "locator": locator_value},
                )
            else:
                append_step(
                    event,
                    "app_input",
                    f"输入 {semantic_name}",
                    {
                        "by": by,
                        "locator": locator_value,
                        "value": payload.get("value") or "",
                        "clear_first": True,
                    },
                )
                if payload.get("redacted"):
                    warnings.append(f"步骤 {len(steps)} 含脱敏变量 ${{password}}，执行前需配置变量")
        elif event_type == "user.swipe":
            append_step(
                event,
                "app_swipe",
                "滑动模拟器画面",
                {
                    "x1": payload.get("x"),
                    "y1": payload.get("y"),
                    "x2": payload.get("end_x"),
                    "y2": payload.get("end_y"),
                    "duration": payload.get("duration_ms") or 400,
                },
            )
        elif event_type == "user.back":
            append_step(event, "app_back", "返回上一页", {})
        elif event_type == "user.refresh":
            warnings.append(
                f"事件 #{event.sequence_no} refresh 仅用于刷新录制画面，不生成执行步骤"
            )

    if not any(step.get("source_event_id") is not None for step in steps):
        warnings.insert(
            0,
            "本次录制没有可转换的点击、输入或滑动动作；只读拾取只用于采集定位器，"
            "不会生成用例步骤。请开始录制后在受控浏览器或模拟器中执行实际业务操作。",
        )

    return {
        "session_id": session.id,
        "case_type": session.platform,
        "suggested_name": session.name,
        "steps": steps,
        "warnings": warnings,
        "source_event_count": len(events),
    }


def build_recording_step_draft(db: Session, session: UiRecordingSession) -> dict[str, Any]:
    """读取会话事件并生成 TestStep 草稿。"""
    actions = (
        db.query(UiRecordedAction)
        .filter(
            UiRecordedAction.session_id == session.id,
            UiRecordedAction.status != "ignored",
        )
        .order_by(UiRecordedAction.sequence_no, UiRecordedAction.id)
        .all()
    )
    if actions:
        event_ids = [item.source_event_id for item in actions]
        events_by_id = {
            event.id: event
            for event in db.query(UiRecordingEvent)
            .filter(UiRecordingEvent.id.in_(event_ids))
            .all()
        }
        events = [events_by_id[item.source_event_id] for item in actions if item.source_event_id in events_by_id]
        result = compile_recording_step_draft(session, events)
        action_names = {item.source_event_id: item.name for item in actions}
        for step in result["steps"]:
            source_event_id = step.get("source_event_id")
            if source_event_id in action_names:
                step["step_name"] = action_names[source_event_id]
        result["source_event_count"] = len(actions)
        return result
    events = (
        db.query(UiRecordingEvent)
        .filter(UiRecordingEvent.session_id == session.id)
        .order_by(UiRecordingEvent.sequence_no)
        .all()
    )
    return compile_recording_step_draft(session, events)


def serialize_element(element: UiElement) -> dict[str, Any]:
    return {
        "id": element.id,
        "project_id": element.project_id,
        "platform": element.platform,
        "page_key": element.page_key,
        "page_name": element.page_name,
        "semantic_name": element.semantic_name,
        "element_type": element.element_type,
        "status": element.status,
        "fingerprint": element.fingerprint,
        "attributes": element.attributes or {},
        "first_snapshot_id": element.first_snapshot_id,
        "last_snapshot_id": element.last_snapshot_id,
        "usage_count": element.usage_count,
        "last_verified_at": element.last_verified_at,
        "created_at": element.created_at,
        "updated_at": element.updated_at,
        "locators": [
            {
                "id": locator.id,
                "strategy": locator.strategy,
                "locator": locator.locator,
                "score": locator.score,
                "is_primary": locator.is_primary,
                "is_unique": locator.is_unique,
                "match_count": locator.match_count,
                "source": locator.source,
                "last_verified_snapshot_id": locator.last_verified_snapshot_id,
                "last_verified_at": locator.last_verified_at,
            }
            for locator in (element.locators or [])
        ],
    }
