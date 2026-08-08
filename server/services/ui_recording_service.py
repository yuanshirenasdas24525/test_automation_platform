"""UI 录制会话状态机、事件接收与序列化服务。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

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
    UiMockExchange,
    UiPageSnapshot,
    UiRecordingEvent,
    UiRecordingSession,
)
from database.schemas.ui_recording import UiRecordingEventCreate


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

        row = UiRecordingEvent(
            session_id=session.id,
            event_key=item.event_key,
            sequence_no=sequence_no,
            event_type=item.event_type,
            source=item.source,
            severity=item.severity,
            page_key=item.page_key,
            element_id=item.element_id,
            snapshot_before_id=item.snapshot_before_id,
            snapshot_after_id=item.snapshot_after_id,
            occurred_at=item.occurred_at,
            monotonic_ms=item.monotonic_ms,
            payload=item.payload,
        )
        db.add(row)
        created.append(row)

        if item.event_type == "agent.connected":
            agent_id = str(item.payload.get("agent_id") or "").strip()
            if agent_id:
                session.recorder_agent_id = agent_id[:128]
            capabilities = {
                **capabilities,
                "recorder_agent_connected": True,
                "reported": item.payload.get("capabilities") or {},
            }
        elif item.event_type == "agent.disconnected":
            capabilities = {**capabilities, "recorder_agent_connected": False}
        elif item.event_type == "offline.package":
            capabilities = {
                **capabilities,
                "offline_replay": dict(item.payload or {}),
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
    for item in items[:500]:
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
                )[:500],
                "visible_element_bounds": _visible_element_bounds(
                    payload.get("visible_elements"),
                ),
                "modal_open": bool(payload.get("modal_open")),
            },
            environment=environment,
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
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


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
                "url": _normalized_request_url(url),
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
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
                for item in list(payload.get("visible_elements") or [])[:500]
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
                    element.first_snapshot_id = element.first_snapshot_id or snapshot.id
                    element.last_snapshot_id = snapshot.id

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
                if locator is None:
                    locator = UiElementLocator(
                        element_id=element.id,
                        strategy=strategy,
                        locator=locator_value,
                        score=score,
                        source="recorder",
                    )
                    db.add(locator)
                else:
                    locator.score = max(locator.score or 0, score)
            db.flush()
            locators = (
                db.query(UiElementLocator)
                .filter(UiElementLocator.element_id == element.id)
                .order_by(UiElementLocator.score.desc(), UiElementLocator.id)
                .all()
            )
            for index, locator in enumerate(locators):
                locator.is_primary = index == 0
            if raw is raw_element:
                event.element_id = element.id


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
