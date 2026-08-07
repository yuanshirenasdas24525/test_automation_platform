"""UI 录制会话状态机、事件接收与序列化服务。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

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
    UiRecordingEvent,
    UiRecordingSession,
)
from database.schemas.ui_recording import UiRecordingEventCreate


class UiRecordingTransitionError(ValueError):
    """会话状态不允许执行目标动作。"""


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


def apply_control_action(session: UiRecordingSession, action: str) -> UiRecordingSession:
    """按状态机执行控制动作，并更新生命周期时间。"""
    rule = _ACTION_TARGETS.get(action)
    if rule is None:
        raise UiRecordingTransitionError(f"未知录制动作：{action}")
    allowed, target = rule
    current_status = session.status or UI_RECORDING_DRAFT
    if current_status not in allowed:
        raise UiRecordingTransitionError(
            f"当前状态 {current_status} 不能执行 {action}"
        )

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

    if created:
        session.capabilities = capabilities
        db.flush()
        for row in created:
            db.refresh(row)
        _materialize_elements(db, session, created)
        session.context_summary = _updated_context_summary(
            session.context_summary or {},
            created,
            next_sequence,
        )
        db.flush()
    return created, skipped


def _materialize_elements(
    db: Session,
    session: UiRecordingSession,
    events: list[UiRecordingEvent],
) -> None:
    """把用户事件里的真实 DOM 元素证据沉淀到项目元素库。"""
    for event in events:
        raw = (event.payload or {}).get("element")
        if not isinstance(raw, dict):
            continue
        fingerprint = str(raw.get("fingerprint") or "").strip()
        if not fingerprint:
            continue
        page_key = event.page_key or "about:blank"
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
        attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
        if element is None:
            element = UiElement(
                project_id=session.project_id,
                platform=session.platform,
                page_key=page_key,
                page_name=str((event.payload or {}).get("page_title") or page_key)[:200],
                semantic_name=str(raw.get("semantic_name") or "未命名元素")[:200],
                element_type=str(raw.get("element_type") or "element")[:100],
                fingerprint=fingerprint,
                attributes=attributes,
            )
            db.add(element)
            db.flush()
        else:
            element.semantic_name = str(raw.get("semantic_name") or element.semantic_name)[:200]
            element.element_type = str(raw.get("element_type") or element.element_type)[:100]
            element.attributes = {**(element.attributes or {}), **attributes}

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
