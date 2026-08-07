from __future__ import annotations

from pathlib import Path

import pytest

from database.models import UiRecordingEvent, UiRecordingSession
from recorder_agent.main import (
    _mobile_element_from_source,
    _package_artifact_path,
    _safe_replay_headers,
)
from server.services.ui_recording_service import (
    UiRecordingControlLeaseError,
    UiRecordingTransitionError,
    apply_control_action,
    compile_recording_step_draft,
    ensure_control_lease,
    update_control_lease,
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


def test_ui_recording_control_lease_and_command_idempotency() -> None:
    session = UiRecordingSession(
        project_id=1,
        platform="web",
        name="租约测试",
        status="recording",
        capabilities={},
    )

    assert ensure_control_lease(session, "client-main", "command-pause") is False
    assert ensure_control_lease(session, "client-main", "command-pause") is True

    with pytest.raises(UiRecordingControlLeaseError, match="另一个窗口"):
        update_control_lease(session, "client-popout", "heartbeat")

    lease = update_control_lease(session, "client-popout", "takeover")
    assert lease["owner_id"] == "client-popout"

    update_control_lease(session, "client-popout", "release")
    assert "control_lease" not in session.capabilities


def test_offline_replay_headers_and_artifact_path_are_restricted(tmp_path: Path) -> None:
    headers = _safe_replay_headers({
        "content-type": "application/json",
        "content-length": "123",
        "set-cookie": "secret=1",
    })
    assert headers == {"content-type": "application/json"}
    assert _package_artifact_path(tmp_path, "resources/a.bin") == (
        tmp_path / "resources" / "a.bin"
    ).resolve()
    with pytest.raises(ValueError, match="逃逸"):
        _package_artifact_path(tmp_path, "../outside.bin")


def test_mobile_ui_tree_coordinate_generates_platform_locators() -> None:
    android_source = """
    <hierarchy>
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
        <node class="android.widget.Button" resource-id="com.demo:id/login"
          content-desc="登录" text="登录" bounds="[100,200][500,320]" />
      </node>
    </hierarchy>
    """
    android = _mobile_element_from_source(android_source, 120, 220, "android")
    assert android is not None
    assert android["semantic_name"] == "登录"
    assert {item["strategy"] for item in android["locators"]} >= {
        "id",
        "accessibility_id",
        "android_uiautomator",
        "xpath",
    }

    ios_source = """
    <AppiumAUT>
      <XCUIElementTypeApplication type="XCUIElementTypeApplication"
        x="0" y="0" width="390" height="844">
        <XCUIElementTypeButton type="XCUIElementTypeButton" name="Login" label="登录"
          x="20" y="100" width="200" height="44" />
      </XCUIElementTypeApplication>
    </AppiumAUT>
    """
    ios = _mobile_element_from_source(ios_source, 30, 110, "ios")
    assert ios is not None
    assert ios["semantic_name"] == "Login"
    assert {item["strategy"] for item in ios["locators"]} >= {
        "accessibility_id",
        "ios_predicate",
        "ios_class_chain",
        "xpath",
    }


def test_recording_events_compile_to_existing_runner_steps() -> None:
    session = UiRecordingSession(
        id=12,
        project_id=1,
        platform="web",
        name="登录流程",
        source_url="https://example.test/login",
    )
    button = {
        "semantic_name": "登录",
        "locators": [
            {"strategy": "xpath", "locator": "//button", "score": 60},
            {"strategy": "id", "locator": "login", "score": 98},
        ],
    }
    password = {
        "semantic_name": "密码",
        "locators": [{"strategy": "css", "locator": "#password", "score": 90}],
    }
    events = [
        UiRecordingEvent(
            id=1,
            session_id=12,
            event_type="user.click",
            sequence_no=1,
            payload={"element": button},
        ),
        UiRecordingEvent(
            id=2,
            session_id=12,
            event_type="user.input",
            sequence_no=2,
            payload={"element": password, "value": "${password}", "redacted": True},
        ),
        UiRecordingEvent(
            id=3,
            session_id=12,
            event_type="user.pick",
            sequence_no=3,
            payload={"element": button},
        ),
        UiRecordingEvent(
            id=4,
            session_id=12,
            event_type="user.scroll",
            sequence_no=4,
            payload={},
        ),
    ]

    draft = compile_recording_step_draft(session, events)

    assert [step["step_type"] for step in draft["steps"]] == [
        "web_goto",
        "web_click",
        "web_input",
    ]
    assert draft["steps"][1]["config"] == {"by": "id", "locator": "login"}
    assert draft["steps"][2]["config"]["value"] == "${password}"
    assert any("脱敏变量" in item for item in draft["warnings"])
    assert any("user.scroll" in item for item in draft["warnings"])

    mobile_session = UiRecordingSession(
        id=13,
        project_id=1,
        platform="android",
        name="移动登录",
    )
    mobile_events = [
        UiRecordingEvent(
            id=5,
            session_id=13,
            event_type="user.tap",
            sequence_no=1,
            payload={
                "element": {
                    "semantic_name": "登录",
                    "locators": [
                        {
                            "strategy": "android_uiautomator",
                            "locator": 'new UiSelector().text("登录")',
                            "score": 98,
                        },
                    ],
                },
            },
        ),
        UiRecordingEvent(
            id=6,
            session_id=13,
            event_type="user.swipe",
            sequence_no=2,
            payload={"x": 500, "y": 1200, "end_x": 500, "end_y": 300, "duration_ms": 450},
        ),
    ]
    mobile_draft = compile_recording_step_draft(mobile_session, mobile_events)
    assert [step["step_type"] for step in mobile_draft["steps"]] == ["app_tap", "app_swipe"]
    assert mobile_draft["steps"][0]["config"]["by"] == "android_uiautomator"
    assert mobile_draft["steps"][1]["config"]["duration"] == 450
