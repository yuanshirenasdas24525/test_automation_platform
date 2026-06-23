"""可回滚编辑历史服务。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from database.models import Requirement, RequirementAssignee, TestCase
from database.models.test_step import TestStep
from database.models.edit_operation import (
    EditOperationBatch,
    EditOperationEvent,
    EDIT_ACTION_CREATE,
    EDIT_ACTION_UPDATE,
    EDIT_ACTION_DELETE,
    EDIT_ACTION_MIXED,
    ENTITY_TYPE_REQUIREMENT,
    ENTITY_TYPE_TEST_CASE,
    ROLLBACK_STATUS_FULL,
    ROLLBACK_STATUS_NONE,
    ROLLBACK_STATUS_PARTIAL,
    ROLLBACK_STATUS_ROLLED_BACK,
)


SNAPSHOT_RETENTION_DAYS = 180
REQUIREMENT_SNAPSHOT_FIELDS = [
    "id",
    "project_id",
    "title",
    "description",
    "acceptance_criteria",
    "priority",
    "tags",
    "depends_on",
    "status",
    "source",
    "ai_run_id",
    "sort_order",
    "version_id",
    "parent_requirement_id",
    "module_id",
    "planned_start_at",
    "planned_end_at",
    "system_status",
    "business_status",
    "assignee_pm_id",
    "accepted_at",
    "spec_json",
    "source_dialogue_session_id",
]
TEST_CASE_SNAPSHOT_FIELDS = [
    "id",
    "module_id",
    "name",
    "description",
    "sort_order",
    "case_type",
    "tags",
    "skip",
    "priority",
    "version_id",
    "env_id",
    "pre_hook",
    "post_hook",
    "variables",
    "timeout",
    "retry",
    "method",
    "path",
    "headers",
    "data_type",
    "params",
    "file_path",
    "extract_data",
    "sql_query",
    "assertion",
    "wait_time",
    "functional_spec",
    "source",
    "draft_id",
    "requirement_id",
    "business_steps",
]
TEST_STEP_SNAPSHOT_FIELDS = [
    "step_order",
    "step_name",
    "step_type",
    "skip",
    "config",
    "extract",
    "assertion",
    "wait_before",
    "timeout",
    "retry",
    "on_failure",
]


def snapshot_requirement(req: Requirement) -> dict:
    """生成需求完整快照，包含协作人员。"""
    data: dict[str, Any] = {}
    for field in REQUIREMENT_SNAPSHOT_FIELDS:
        value = getattr(req, field)
        data[field] = _json_value(value)
    assignees: dict[str, list[int]] = {}
    for item in req.assignees:
        assignees.setdefault(item.role, []).append(item.user_id)
    data["assignees"] = {role: sorted(ids) for role, ids in assignees.items()}
    return data


def snapshot_test_case(case: TestCase) -> dict:
    """生成用例完整快照，包含自动化步骤。"""
    data: dict[str, Any] = {}
    for field in TEST_CASE_SNAPSHOT_FIELDS:
        value = getattr(case, field)
        data[field] = _json_value(value)
    data["steps"] = [
        {field: _json_value(getattr(step, field)) for field in TEST_STEP_SNAPSHOT_FIELDS}
        for step in sorted(case.steps, key=lambda item: (item.step_order, item.id or 0))
    ]
    return data


def record_requirement_create(
    session: Session,
    req: Requirement,
    *,
    operator_id: int | None = None,
    summary: str | None = None,
    batch: EditOperationBatch | None = None,
) -> EditOperationEvent:
    """记录需求新增。"""
    return _record_requirement_event(
        session,
        req,
        action=EDIT_ACTION_CREATE,
        before_snapshot=None,
        after_snapshot=snapshot_requirement(req),
        field_changes=_snapshot_to_changes(snapshot_requirement(req)),
        operator_id=operator_id,
        summary=summary or f"新增需求 REQ-{req.id}",
        batch=batch,
    )


def record_requirement_update(
    session: Session,
    req: Requirement,
    *,
    before_snapshot: dict,
    field_changes: list[dict],
    operator_id: int | None = None,
    summary: str | None = None,
) -> EditOperationEvent | None:
    """记录需求修改。"""
    if not field_changes and not summary:
        return None
    return _record_requirement_event(
        session,
        req,
        action=EDIT_ACTION_UPDATE,
        before_snapshot=before_snapshot,
        after_snapshot=snapshot_requirement(req),
        field_changes=field_changes,
        operator_id=operator_id,
        summary=summary or f"修改需求 REQ-{req.id}",
    )


def record_requirement_delete(
    session: Session,
    req: Requirement,
    *,
    operator_id: int | None = None,
    summary: str | None = None,
    batch: EditOperationBatch | None = None,
) -> EditOperationEvent:
    """记录需求删除。必须在真正 delete 前调用。"""
    return _record_requirement_event(
        session,
        req,
        action=EDIT_ACTION_DELETE,
        before_snapshot=snapshot_requirement(req),
        after_snapshot=None,
        field_changes=[],
        operator_id=operator_id,
        summary=summary or f"删除需求 REQ-{req.id}",
        batch=batch,
    )


def create_requirement_batch(
    session: Session,
    *,
    action: str,
    operator_id: int | None = None,
    summary: str | None = None,
) -> EditOperationBatch:
    """创建需求编辑批次。"""
    batch = EditOperationBatch(
        entity_type=ENTITY_TYPE_REQUIREMENT,
        action=action,
        operator_id=operator_id,
        summary=summary,
    )
    session.add(batch)
    session.flush()
    return batch


def record_test_case_create(
    session: Session,
    case: TestCase,
    *,
    operator_id: int | None = None,
    summary: str | None = None,
    batch: EditOperationBatch | None = None,
) -> EditOperationEvent:
    """记录用例新增。"""
    return _record_test_case_event(
        session,
        case,
        action=EDIT_ACTION_CREATE,
        before_snapshot=None,
        after_snapshot=snapshot_test_case(case),
        field_changes=_test_case_snapshot_to_changes(snapshot_test_case(case)),
        operator_id=operator_id,
        summary=summary or f"新增用例 #{case.id}",
        batch=batch,
    )


def record_test_case_update(
    session: Session,
    case: TestCase,
    *,
    before_snapshot: dict,
    field_changes: list[dict],
    operator_id: int | None = None,
    summary: str | None = None,
) -> EditOperationEvent | None:
    """记录用例修改。"""
    after_snapshot = snapshot_test_case(case)
    changes = list(field_changes)
    if before_snapshot.get("steps") != after_snapshot.get("steps"):
        changes.append({
            "field": "steps",
            "label": "步骤",
            "old": before_snapshot.get("steps") or [],
            "new": after_snapshot.get("steps") or [],
        })
    if not changes and not summary:
        return None
    return _record_test_case_event(
        session,
        case,
        action=EDIT_ACTION_UPDATE,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        field_changes=changes,
        operator_id=operator_id,
        summary=summary or f"修改用例 #{case.id}",
    )


def record_test_case_delete(
    session: Session,
    case: TestCase,
    *,
    operator_id: int | None = None,
    summary: str | None = None,
    batch: EditOperationBatch | None = None,
) -> EditOperationEvent:
    """记录用例删除。必须在 delete 前调用。"""
    return _record_test_case_event(
        session,
        case,
        action=EDIT_ACTION_DELETE,
        before_snapshot=snapshot_test_case(case),
        after_snapshot=None,
        field_changes=[],
        operator_id=operator_id,
        summary=summary or f"删除用例 #{case.id}",
        batch=batch,
    )


def create_test_case_batch(
    session: Session,
    *,
    action: str,
    operator_id: int | None = None,
    summary: str | None = None,
) -> EditOperationBatch:
    """创建用例编辑批次。"""
    batch = EditOperationBatch(
        entity_type=ENTITY_TYPE_TEST_CASE,
        action=action,
        operator_id=operator_id,
        summary=summary,
    )
    session.add(batch)
    session.flush()
    return batch


def rollback_requirement_events(
    session: Session,
    *,
    batch_id: int,
    event_ids: list[int] | None = None,
    fields_by_event: dict[int, list[str]] | None = None,
    operator_id: int | None = None,
    reason: str | None = None,
    force: bool = False,
) -> dict:
    """回滚需求编辑事件。返回冲突或回滚结果。"""
    return rollback_edit_events(
        session,
        batch_id=batch_id,
        entity_type=ENTITY_TYPE_REQUIREMENT,
        event_ids=event_ids,
        fields_by_event=fields_by_event,
        operator_id=operator_id,
        reason=reason,
        force=force,
    )


def rollback_test_case_events(
    session: Session,
    *,
    batch_id: int,
    event_ids: list[int] | None = None,
    fields_by_event: dict[int, list[str]] | None = None,
    operator_id: int | None = None,
    reason: str | None = None,
    force: bool = False,
) -> dict:
    """回滚用例编辑事件。"""
    return rollback_edit_events(
        session,
        batch_id=batch_id,
        entity_type=ENTITY_TYPE_TEST_CASE,
        event_ids=event_ids,
        fields_by_event=fields_by_event,
        operator_id=operator_id,
        reason=reason,
        force=force,
    )


def rollback_edit_events(
    session: Session,
    *,
    batch_id: int,
    entity_type: str,
    event_ids: list[int] | None = None,
    fields_by_event: dict[int, list[str]] | None = None,
    operator_id: int | None = None,
    reason: str | None = None,
    force: bool = False,
) -> dict:
    """按实体类型回滚编辑事件。"""
    batch = session.query(EditOperationBatch).filter(EditOperationBatch.id == batch_id).first()
    if batch is None:
        raise ValueError("编辑批次不存在")

    query = session.query(EditOperationEvent).filter(
        EditOperationEvent.batch_id == batch_id,
        EditOperationEvent.entity_type == entity_type,
        EditOperationEvent.rollback_status == ROLLBACK_STATUS_NONE,
    )
    if event_ids:
        query = query.filter(EditOperationEvent.id.in_(event_ids))
    events = query.order_by(EditOperationEvent.id.desc()).all()
    if not events:
        return {"rolled_back": 0, "conflicts": []}

    fields_by_event = fields_by_event or {}
    conflicts = []
    for event in events:
        selected_fields = fields_by_event.get(event.id)
        conflicts.extend(_detect_conflicts(session, event, selected_fields))
    if conflicts and not force:
        return {"rolled_back": 0, "conflicts": conflicts}

    rollback_batch = EditOperationBatch(
        entity_type=entity_type,
        action=EDIT_ACTION_MIXED,
        operator_id=operator_id,
        summary=reason or f"回滚编辑批次 #{batch_id}",
    )
    session.add(rollback_batch)
    session.flush()
    rollback_count = 0
    rollback_event_ids: list[int] = []
    for event in events:
        selected_fields = fields_by_event.get(event.id)
        inverse = _apply_inverse(session, event, selected_fields)
        if inverse is None:
            continue
        inverse.batch_id = rollback_batch.id
        session.add(inverse)
        session.flush()
        event.rollback_status = ROLLBACK_STATUS_ROLLED_BACK
        event.rollback_event_id = inverse.id
        rollback_count += 1
        rollback_event_ids.append(inverse.id)

    total_events = session.query(EditOperationEvent).filter(
        EditOperationEvent.batch_id == batch_id,
    ).count()
    rolled_events = session.query(EditOperationEvent).filter(
        EditOperationEvent.batch_id == batch_id,
        EditOperationEvent.rollback_status == ROLLBACK_STATUS_ROLLED_BACK,
    ).count()
    batch.rollback_status = (
        ROLLBACK_STATUS_FULL if total_events == rolled_events else ROLLBACK_STATUS_PARTIAL
    )
    batch.rollback_batch_id = rollback_batch.id
    session.flush()
    return {
        "rolled_back": rollback_count,
        "rollback_batch_id": rollback_batch.id,
        "rollback_event_ids": rollback_event_ids,
        "conflicts": [],
    }


def serialize_requirement_event(event: EditOperationEvent) -> dict:
    """转成前端历史时间线使用的结构。"""
    batch = event.batch
    changes = event.field_changes or []
    if event.action == EDIT_ACTION_CREATE and not changes:
        changes = _snapshot_to_changes(event.after_snapshot or {})
    return {
        "id": event.id,
        "batch_id": event.batch_id,
        "requirement_id": event.entity_id,
        "edited_by_id": batch.operator_id if batch else None,
        "action": event.action,
        "entity_label": event.entity_label,
        "changes": changes,
        "change_summary": batch.summary if batch else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "rollback_available": _event_can_rollback(event),
        "rollback_status": event.rollback_status,
        "snapshot_expires_at": (
            event.snapshot_expires_at.isoformat() if event.snapshot_expires_at else None
        ),
    }


def serialize_test_case_event(event: EditOperationEvent) -> dict:
    """转成用例编辑历史接口兼容的结构。"""
    batch = event.batch
    return {
        "id": event.id,
        "batch_id": event.batch_id,
        "case_id": event.entity_id,
        "module_id": _snapshot_module_id(event),
        "case_name": event.entity_label,
        "action": event.action,
        "changes": event.field_changes or [],
        "session_id": None,
        "operator": f"#{batch.operator_id}" if batch and batch.operator_id else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "rollback_available": _event_can_rollback(event),
        "rollback_status": event.rollback_status,
        "snapshot_expires_at": (
            event.snapshot_expires_at.isoformat() if event.snapshot_expires_at else None
        ),
    }


def _snapshot_module_id(event: EditOperationEvent) -> int | None:
    snapshot = event.after_snapshot or event.before_snapshot or {}
    value = snapshot.get("module_id")
    return int(value) if value is not None else None


def _record_requirement_event(
    session: Session,
    req: Requirement,
    *,
    action: str,
    before_snapshot: dict | None,
    after_snapshot: dict | None,
    field_changes: list[dict],
    operator_id: int | None,
    summary: str | None,
    batch: EditOperationBatch | None = None,
) -> EditOperationEvent:
    if batch is None:
        batch = create_requirement_batch(
            session,
            action=action,
            operator_id=operator_id,
            summary=summary,
        )
    expires_at = datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS)
    event = EditOperationEvent(
        batch_id=batch.id,
        entity_type=ENTITY_TYPE_REQUIREMENT,
        entity_id=req.id,
        entity_label=req.title,
        action=action,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        field_changes=field_changes,
        snapshot_expires_at=expires_at,
    )
    session.add(event)
    session.flush()
    return event


def _record_test_case_event(
    session: Session,
    case: TestCase,
    *,
    action: str,
    before_snapshot: dict | None,
    after_snapshot: dict | None,
    field_changes: list[dict],
    operator_id: int | None,
    summary: str | None,
    batch: EditOperationBatch | None = None,
) -> EditOperationEvent:
    if batch is None:
        batch = create_test_case_batch(
            session,
            action=action,
            operator_id=operator_id,
            summary=summary,
        )
    expires_at = datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS)
    event = EditOperationEvent(
        batch_id=batch.id,
        entity_type=ENTITY_TYPE_TEST_CASE,
        entity_id=case.id,
        entity_label=case.name,
        action=action,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        field_changes=field_changes,
        snapshot_expires_at=expires_at,
    )
    session.add(event)
    session.flush()
    return event


def _event_can_rollback(event: EditOperationEvent) -> bool:
    """判断一条事件是否还有足够快照执行回滚。"""
    if not event.rollback_available or event.rollback_status != ROLLBACK_STATUS_NONE:
        return False
    if event.action == EDIT_ACTION_CREATE:
        return event.after_snapshot is not None
    if event.action == EDIT_ACTION_DELETE:
        return event.before_snapshot is not None
    if event.action == EDIT_ACTION_UPDATE:
        return bool(event.field_changes) and event.before_snapshot is not None
    return False


def purge_expired_snapshots(session: Session, *, now: datetime | None = None, limit: int = 1000) -> int:
    """清理过期快照，只保留审计信息和字段 diff。"""
    now = now or datetime.utcnow()
    rows = (
        session.query(EditOperationEvent)
        .filter(
            EditOperationEvent.snapshot_expires_at.isnot(None),
            EditOperationEvent.snapshot_expires_at < now,
            EditOperationEvent.snapshot_purged_at.is_(None),
            or_(
                EditOperationEvent.before_snapshot.isnot(None),
                EditOperationEvent.after_snapshot.isnot(None),
            ),
        )
        .order_by(EditOperationEvent.snapshot_expires_at.asc(), EditOperationEvent.id.asc())
        .limit(limit)
        .all()
    )
    for row in rows:
        row.before_snapshot = None
        row.after_snapshot = None
        row.rollback_available = False
        row.snapshot_purged_at = now
        row.purge_reason = "snapshot_expired"
    session.flush()
    return len(rows)


def _apply_inverse(
    session: Session,
    event: EditOperationEvent,
    selected_fields: list[str] | None,
) -> EditOperationEvent | None:
    if event.entity_type == ENTITY_TYPE_REQUIREMENT:
        return _apply_requirement_inverse(session, event, selected_fields)
    if event.entity_type == ENTITY_TYPE_TEST_CASE:
        return _apply_test_case_inverse(session, event, selected_fields)
    return None


def _detect_conflicts(
    session: Session,
    event: EditOperationEvent,
    selected_fields: list[str] | None,
) -> list[dict]:
    if event.entity_type == ENTITY_TYPE_REQUIREMENT:
        return _detect_requirement_conflicts(session, event, selected_fields)
    if event.entity_type == ENTITY_TYPE_TEST_CASE:
        return _detect_test_case_conflicts(session, event, selected_fields)
    return []


def _apply_requirement_inverse(
    session: Session,
    event: EditOperationEvent,
    selected_fields: list[str] | None,
) -> EditOperationEvent | None:
    if event.action == EDIT_ACTION_CREATE:
        req = session.query(Requirement).filter(Requirement.id == event.entity_id).first()
        if req is None:
            return None
        before = snapshot_requirement(req)
        session.delete(req)
        return EditOperationEvent(
            entity_type=ENTITY_TYPE_REQUIREMENT,
            entity_id=event.entity_id,
            entity_label=event.entity_label,
            action=EDIT_ACTION_DELETE,
            before_snapshot=before,
            after_snapshot=None,
            field_changes=[],
            rollback_available=True,
            snapshot_expires_at=datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS),
        )

    if event.action == EDIT_ACTION_DELETE:
        restored = _restore_requirement(session, event.before_snapshot or {})
        return EditOperationEvent(
            entity_type=ENTITY_TYPE_REQUIREMENT,
            entity_id=restored.id,
            entity_label=restored.title,
            action=EDIT_ACTION_CREATE,
            before_snapshot=None,
            after_snapshot=snapshot_requirement(restored),
            field_changes=_snapshot_to_changes(snapshot_requirement(restored)),
            rollback_available=True,
            snapshot_expires_at=datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS),
        )

    if event.action == EDIT_ACTION_UPDATE:
        req = (
            session.query(Requirement)
            .options(selectinload(Requirement.assignees))
            .filter(Requirement.id == event.entity_id)
            .first()
        )
        if req is None:
            return None
        before = snapshot_requirement(req)
        target = event.before_snapshot or {}
        fields = selected_fields or [c["field"] for c in event.field_changes or []]
        for field in fields:
            if field == "assignees":
                _replace_requirement_assignees(session, req.id, target.get("assignees") or {})
            elif field in REQUIREMENT_SNAPSHOT_FIELDS and field != "id":
                setattr(req, field, _model_value(field, target.get(field)))
        session.flush()
        session.refresh(req)
        after = snapshot_requirement(req)
        return EditOperationEvent(
            entity_type=ENTITY_TYPE_REQUIREMENT,
            entity_id=req.id,
            entity_label=req.title,
            action=EDIT_ACTION_UPDATE,
            before_snapshot=before,
            after_snapshot=after,
            field_changes=_changes_for_fields(before, after, fields),
            rollback_available=True,
            snapshot_expires_at=datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS),
        )

    return None


def _apply_test_case_inverse(
    session: Session,
    event: EditOperationEvent,
    selected_fields: list[str] | None,
) -> EditOperationEvent | None:
    if event.action == EDIT_ACTION_CREATE:
        case = (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id == event.entity_id)
            .first()
        )
        if case is None:
            return None
        before = snapshot_test_case(case)
        session.delete(case)
        return EditOperationEvent(
            entity_type=ENTITY_TYPE_TEST_CASE,
            entity_id=event.entity_id,
            entity_label=event.entity_label,
            action=EDIT_ACTION_DELETE,
            before_snapshot=before,
            after_snapshot=None,
            field_changes=[],
            rollback_available=True,
            snapshot_expires_at=datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS),
        )

    if event.action == EDIT_ACTION_DELETE:
        restored = _restore_test_case(session, event.before_snapshot or {})
        return EditOperationEvent(
            entity_type=ENTITY_TYPE_TEST_CASE,
            entity_id=restored.id,
            entity_label=restored.name,
            action=EDIT_ACTION_CREATE,
            before_snapshot=None,
            after_snapshot=snapshot_test_case(restored),
            field_changes=_test_case_snapshot_to_changes(snapshot_test_case(restored)),
            rollback_available=True,
            snapshot_expires_at=datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS),
        )

    if event.action == EDIT_ACTION_UPDATE:
        case = (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id == event.entity_id)
            .first()
        )
        if case is None:
            return None
        before = snapshot_test_case(case)
        target = event.before_snapshot or {}
        fields = selected_fields or [c["field"] for c in event.field_changes or []]
        for field in fields:
            if field == "steps":
                _replace_test_case_steps_from_snapshot(session, case.id, target.get("steps") or [])
            elif field in TEST_CASE_SNAPSHOT_FIELDS and field != "id":
                setattr(case, field, _model_value(field, target.get(field)))
        session.flush()
        session.refresh(case)
        after = snapshot_test_case(case)
        return EditOperationEvent(
            entity_type=ENTITY_TYPE_TEST_CASE,
            entity_id=case.id,
            entity_label=case.name,
            action=EDIT_ACTION_UPDATE,
            before_snapshot=before,
            after_snapshot=after,
            field_changes=_changes_for_fields(before, after, fields),
            rollback_available=True,
            snapshot_expires_at=datetime.utcnow() + timedelta(days=SNAPSHOT_RETENTION_DAYS),
        )

    return None


def _restore_requirement(session: Session, snapshot: dict) -> Requirement:
    req = session.query(Requirement).filter(Requirement.id == snapshot.get("id")).first()
    if req is None:
        req = Requirement(id=snapshot["id"], project_id=snapshot["project_id"], title=snapshot["title"])
        session.add(req)
        session.flush()
    for field in REQUIREMENT_SNAPSHOT_FIELDS:
        if field == "id":
            continue
        if field in snapshot:
            setattr(req, field, _model_value(field, snapshot.get(field)))
    session.flush()
    _replace_requirement_assignees(session, req.id, snapshot.get("assignees") or {})
    session.flush()
    session.refresh(req)
    return req


def _restore_test_case(session: Session, snapshot: dict) -> TestCase:
    case = (
        session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == snapshot.get("id"))
        .first()
    )
    if case is None:
        case = TestCase(id=snapshot["id"], module_id=snapshot["module_id"], name=snapshot["name"])
        session.add(case)
        session.flush()
    for field in TEST_CASE_SNAPSHOT_FIELDS:
        if field == "id":
            continue
        if field in snapshot:
            setattr(case, field, _model_value(field, snapshot.get(field)))
    session.flush()
    _replace_test_case_steps_from_snapshot(session, case.id, snapshot.get("steps") or [])
    session.flush()
    session.refresh(case)
    return case


def _replace_test_case_steps_from_snapshot(session: Session, case_id: int, steps: list[dict]) -> None:
    session.query(TestStep).filter(TestStep.case_id == case_id).delete(synchronize_session=False)
    for raw in steps:
        session.add(TestStep(
            case_id=case_id,
            step_order=int(raw.get("step_order") or 0),
            step_name=raw.get("step_name") or "step",
            step_type=raw.get("step_type") or "http_request",
            skip=bool(raw.get("skip") or False),
            config=raw.get("config") or {},
            extract=raw.get("extract"),
            assertion=raw.get("assertion"),
            wait_before=float(raw.get("wait_before") or 0),
            timeout=int(raw.get("timeout") or 30),
            retry=int(raw.get("retry") or 0),
            on_failure=raw.get("on_failure") or "stop",
        ))


def _replace_requirement_assignees(session: Session, req_id: int, assignees: dict[str, list[int]]) -> None:
    session.query(RequirementAssignee).filter(
        RequirementAssignee.requirement_id == req_id,
    ).delete(synchronize_session=False)
    for role, ids in assignees.items():
        seen = set()
        for uid in ids or []:
            if uid in seen:
                continue
            seen.add(uid)
            session.add(RequirementAssignee(requirement_id=req_id, role=role, user_id=uid))


def _detect_requirement_conflicts(
    session: Session,
    event: EditOperationEvent,
    selected_fields: list[str] | None,
) -> list[dict]:
    if event.action == EDIT_ACTION_CREATE:
        req = (
            session.query(Requirement)
            .options(selectinload(Requirement.assignees))
            .filter(Requirement.id == event.entity_id)
            .first()
        )
        if req is None:
            return []
        current = snapshot_requirement(req)
        after = event.after_snapshot or {}
        if current != after:
            return [{
                "event_id": event.id,
                "entity_id": event.entity_id,
                "field": "__entity__",
                "before": None,
                "after": after,
                "current": current,
                "message": "新增后的需求已被修改，删除回滚前需要确认",
            }]
        return []

    if event.action == EDIT_ACTION_DELETE:
        exists = session.query(Requirement.id).filter(Requirement.id == event.entity_id).first()
        if exists:
            return [{
                "event_id": event.id,
                "entity_id": event.entity_id,
                "field": "__entity__",
                "before": None,
                "after": "deleted",
                "current": "exists",
                "message": "需求已重新存在，请确认是否覆盖当前记录",
            }]
        return []
    if event.action != EDIT_ACTION_UPDATE:
        return []

    req = (
        session.query(Requirement)
        .options(selectinload(Requirement.assignees))
        .filter(Requirement.id == event.entity_id)
        .first()
    )
    if req is None:
        return [{
            "event_id": event.id,
            "entity_id": event.entity_id,
            "field": "__entity__",
            "before": event.before_snapshot,
            "after": event.after_snapshot,
            "current": None,
            "message": "需求已被删除，无法按字段回滚",
        }]
    current = snapshot_requirement(req)
    after = event.after_snapshot or {}
    before = event.before_snapshot or {}
    fields = selected_fields or [c["field"] for c in event.field_changes or []]
    conflicts = []
    for field in fields:
        if current.get(field) != after.get(field):
            conflicts.append({
                "event_id": event.id,
                "entity_id": event.entity_id,
                "field": field,
                "before": before.get(field),
                "after": after.get(field),
                "current": current.get(field),
                "message": "字段在该记录后又被修改过",
            })
    return conflicts


def _detect_test_case_conflicts(
    session: Session,
    event: EditOperationEvent,
    selected_fields: list[str] | None,
) -> list[dict]:
    if event.action == EDIT_ACTION_CREATE:
        case = (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id == event.entity_id)
            .first()
        )
        if case is None:
            return []
        current = snapshot_test_case(case)
        after = event.after_snapshot or {}
        if current != after:
            return [{
                "event_id": event.id,
                "entity_id": event.entity_id,
                "field": "__entity__",
                "before": None,
                "after": after,
                "current": current,
                "message": "新增后的用例已被修改，删除回滚前需要确认",
            }]
        return []

    if event.action == EDIT_ACTION_DELETE:
        exists = session.query(TestCase.id).filter(TestCase.id == event.entity_id).first()
        if exists:
            return [{
                "event_id": event.id,
                "entity_id": event.entity_id,
                "field": "__entity__",
                "before": None,
                "after": "deleted",
                "current": "exists",
                "message": "用例已重新存在，请确认是否覆盖当前记录",
            }]
        return []
    if event.action != EDIT_ACTION_UPDATE:
        return []

    case = (
        session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == event.entity_id)
        .first()
    )
    if case is None:
        return [{
            "event_id": event.id,
            "entity_id": event.entity_id,
            "field": "__entity__",
            "before": event.before_snapshot,
            "after": event.after_snapshot,
            "current": None,
            "message": "用例已被删除，无法按字段回滚",
        }]
    current = snapshot_test_case(case)
    after = event.after_snapshot or {}
    before = event.before_snapshot or {}
    fields = selected_fields or [c["field"] for c in event.field_changes or []]
    conflicts = []
    for field in fields:
        if current.get(field) != after.get(field):
            conflicts.append({
                "event_id": event.id,
                "entity_id": event.entity_id,
                "field": field,
                "before": before.get(field),
                "after": after.get(field),
                "current": current.get(field),
                "message": "字段在该记录后又被修改过",
            })
    return conflicts


def _changes_for_fields(before: dict, after: dict, fields: list[str]) -> list[dict]:
    changes = []
    for field in fields:
        old = before.get(field)
        new = after.get(field)
        if old == new:
            continue
        changes.append({"field": field, "label": _field_label(field), "old": old, "new": new})
    return changes


def _snapshot_to_changes(snapshot: dict) -> list[dict]:
    changes = []
    for field in ["title", "description", "acceptance_criteria", "priority", "tags", "version_id", "module_id"]:
        if field in snapshot and snapshot.get(field) not in (None, [], ""):
            changes.append({"field": field, "label": _field_label(field), "old": None, "new": snapshot.get(field)})
    return changes


def _test_case_snapshot_to_changes(snapshot: dict) -> list[dict]:
    changes = []
    for field in ["name", "description", "priority", "tags", "case_type", "functional_spec", "method", "path"]:
        if field in snapshot and snapshot.get(field) not in (None, [], "", {}):
            changes.append({"field": field, "label": _field_label(field), "old": None, "new": snapshot.get(field)})
    if snapshot.get("steps"):
        changes.append({"field": "steps", "label": "步骤", "old": None, "new": snapshot.get("steps")})
    return changes


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _model_value(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in {"planned_start_at", "planned_end_at", "accepted_at"}:
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    return value


def _field_label(field: str) -> str:
    return {
        "title": "标题",
        "description": "描述",
        "acceptance_criteria": "验收标准",
        "priority": "优先级",
        "tags": "标签",
        "depends_on": "依赖需求",
        "version_id": "关联迭代",
        "module_id": "模块",
        "planned_start_at": "预计开始",
        "planned_end_at": "预计完成",
        "system_status": "状态",
        "business_status": "业务状态",
        "assignees": "协作人员",
        "name": "名称",
        "case_type": "用例类型",
        "skip": "跳过",
        "functional_spec": "功能步骤",
        "method": "请求方法",
        "path": "请求路径",
        "steps": "步骤",
    }.get(field, field)
