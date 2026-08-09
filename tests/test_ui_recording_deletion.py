from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from database.db import DB
from database.models import (
    Project,
    UiDeletionAudit,
    UiElement,
    UiElementLocator,
    UiPageSnapshot,
    UiRecordingSession,
    User,
)
from server.api.ui_recordings import (
    delete_recording,
    delete_ui_element,
    delete_ui_page_group,
)


def test_ui_fact_deletions_require_confirmation_and_write_audit() -> None:
    """删除接口级联事实数据且留下操作审计，测试结束统一回滚。"""
    db = DB()
    try:
        user = db.session.query(User).filter(User.is_active.is_(True)).first()
        project = db.session.query(Project).first()
        assert user is not None
        assert project is not None
        token = uuid.uuid4().hex
        session = UiRecordingSession(
            project_id=project.id,
            platform="web",
            status="completed",
            name=f"delete-test-{token}",
        )
        db.session.add(session)
        db.session.flush()

        element_snapshot = UiPageSnapshot(
            session_id=session.id,
            project_id=project.id,
            platform="web",
            page_key=f"delete-element-{token}",
            page_name="待删除元素页",
            snapshot_version=1,
            fingerprint=f"snapshot-element-{token}",
        )
        page_snapshot = UiPageSnapshot(
            session_id=session.id,
            project_id=project.id,
            platform="web",
            page_key=f"delete-page-{token}",
            page_name="待删除页面",
            snapshot_version=1,
            fingerprint=f"snapshot-page-{token}",
        )
        db.session.add_all([element_snapshot, page_snapshot])
        db.session.flush()
        element = UiElement(
            project_id=project.id,
            platform="web",
            page_key=element_snapshot.page_key,
            page_name=element_snapshot.page_name,
            semantic_name="待删除按钮",
            element_type="button",
            fingerprint=f"element-{token}",
            first_snapshot_id=element_snapshot.id,
            last_snapshot_id=element_snapshot.id,
        )
        page_element = UiElement(
            project_id=project.id,
            platform="web",
            page_key=page_snapshot.page_key,
            page_name=page_snapshot.page_name,
            semantic_name="页面内按钮",
            element_type="button",
            fingerprint=f"page-element-{token}",
            first_snapshot_id=page_snapshot.id,
            last_snapshot_id=page_snapshot.id,
        )
        db.session.add_all([element, page_element])
        db.session.flush()
        db.session.add(UiElementLocator(
            element_id=element.id,
            strategy="id",
            locator="delete-target",
            score=100,
            is_primary=True,
        ))
        db.session.flush()

        with pytest.raises(HTTPException) as exc_info:
            delete_ui_element(element.id, db, user, confirm=False)
        assert exc_info.value.status_code == 422

        deleted_element = delete_ui_element(element.id, db, user, confirm=True)
        assert deleted_element["data"]["deleted_id"] == element.id
        assert db.session.get(UiElement, element.id) is None

        deleted_page = delete_ui_page_group(
            db,
            user,
            project_id=project.id,
            platform="web",
            page_key=page_snapshot.page_key,
            confirm=True,
        )
        assert deleted_page["data"]["cascade_scope"]["snapshots"] == 1
        assert db.session.get(UiPageSnapshot, page_snapshot.id) is None
        assert db.session.get(UiElement, page_element.id) is None

        deleted_recording = delete_recording(session.id, db, user, confirm=True)
        assert deleted_recording["data"]["deleted_id"] == session.id
        assert db.session.get(UiRecordingSession, session.id) is None

        audits = (
            db.session.query(UiDeletionAudit)
            .filter(UiDeletionAudit.object_id.in_([
                str(element.id),
                f"web:{page_snapshot.page_key}",
                str(session.id),
            ]))
            .all()
        )
        assert {item.object_type for item in audits} == {
            "ui_element",
            "page_group",
            "recording_session",
        }
        assert all(item.operator_id == user.id for item in audits)
    finally:
        db.rollback()
        db.close()
