from __future__ import annotations

import pytest

from database.models import UiRecordingSession
from server.services.ui_recording_service import (
    UiRecordingTransitionError,
    apply_control_action,
)


def test_ui_recording_happy_path() -> None:
    session = UiRecordingSession(project_id=1, platform="web", name="登录流程")

    apply_control_action(session, "start")
    assert session.status == "recording"
    assert session.started_at is not None

    apply_control_action(session, "pause")
    assert session.status == "paused"
    assert session.paused_at is not None

    apply_control_action(session, "resume")
    assert session.status == "recording"
    assert session.paused_at is None

    apply_control_action(session, "stop")
    assert session.status == "completed"
    assert session.ended_at is not None


def test_ui_recording_rejects_invalid_transition() -> None:
    session = UiRecordingSession(
        project_id=1,
        platform="android",
        name="订单流程",
        status="draft",
    )

    with pytest.raises(UiRecordingTransitionError, match="不能执行 pause"):
        apply_control_action(session, "pause")
