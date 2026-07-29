from __future__ import annotations

from datetime import datetime

from database.models import ApiCaseEditHistory
from database.models.edit_operation import (
    EDIT_ACTION_CREATE,
    EDIT_ACTION_UPDATE,
    ENTITY_TYPE_TEST_CASE,
    EditOperationBatch,
    EditOperationEvent,
)
from server.services.edit_history_service import merge_test_case_edit_history


def test_merge_test_case_edit_history_deduplicates_new_and_legacy_rows() -> None:
    """同一次 CRUD 写出的新旧审计行只展示一次，并保留两边的有效信息。"""
    batch = EditOperationBatch(id=7, entity_type=ENTITY_TYPE_TEST_CASE, action=EDIT_ACTION_UPDATE)
    event = EditOperationEvent(
        id=11,
        batch_id=7,
        entity_type=ENTITY_TYPE_TEST_CASE,
        entity_id=31,
        entity_label="登录接口",
        action=EDIT_ACTION_UPDATE,
        before_snapshot={"id": 31, "name": "登录接口"},
        field_changes=[{"field": "name", "old": "旧名称", "new": "登录接口"}],
        rollback_available=True,
        rollback_status="none",
        created_at=datetime(2026, 7, 22, 11, 42, 56),
    )
    event.batch = batch
    legacy = ApiCaseEditHistory(
        id=21,
        case_id=31,
        module_id=3,
        case_name="登录接口",
        action=EDIT_ACTION_UPDATE,
        changes=[{"field": "name", "old": "旧名称", "new": "登录接口"}],
        session_id="quick-edit-1",
        operator="tester",
        created_at=datetime(2026, 7, 22, 11, 42, 58),
    )

    result = merge_test_case_edit_history([event], [legacy], limit=20)

    assert len(result) == 1
    assert result[0]["id"] == 11
    assert result[0]["batch_id"] == 7
    assert result[0]["session_id"] == "quick-edit-1"
    assert result[0]["operator"] == "tester"
    assert result[0]["rollback_available"] is True
    assert result[0]["created_at"] == "2026-07-22T11:42:56Z"


def test_merge_test_case_edit_history_keeps_unmatched_legacy_rows_and_sorts() -> None:
    """只有真正成对的新旧记录才去重，历史旧记录仍然保留并按时间倒序。"""
    batch = EditOperationBatch(id=8, entity_type=ENTITY_TYPE_TEST_CASE, action=EDIT_ACTION_UPDATE)
    event = EditOperationEvent(
        id=12,
        batch_id=8,
        entity_type=ENTITY_TYPE_TEST_CASE,
        entity_id=32,
        entity_label="资料接口",
        action=EDIT_ACTION_UPDATE,
        before_snapshot={"id": 32, "name": "资料接口"},
        field_changes=[{"field": "description", "old": "", "new": "查询资料"}],
        rollback_available=True,
        rollback_status="none",
        created_at=datetime(2026, 7, 22, 12, 0, 0),
    )
    event.batch = batch
    legacy = ApiCaseEditHistory(
        id=22,
        case_id=30,
        module_id=3,
        case_name="旧接口",
        action=EDIT_ACTION_CREATE,
        created_at=datetime(2026, 7, 21, 9, 0, 0),
    )

    result = merge_test_case_edit_history([event], [legacy], limit=20)

    assert [item["id"] for item in result] == [12, 22]
    assert [item["created_at"] for item in result] == [
        "2026-07-22T12:00:00Z",
        "2026-07-21T09:00:00Z",
    ]
