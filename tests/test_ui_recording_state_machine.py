from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from database.models import (
    UiElementLocator,
    UiPageSnapshot,
    UiRecordingEvent,
    UiRecordingSession,
)
from database.schemas.ui_recording import UiPageSnapshotPickRequest
from recorder_agent.main import (
    AiExplorationRequest,
    MobileRecorderRuntime,
    MobileRecorderStartRequest,
    RecorderStartRequest,
    ReplayStartRequest,
    WebActionRequest,
    _RECORDER_SCRIPT,
    _REPLAY_INTERACTION_SCRIPT,
    _freeze_replay_document,
    _mobile_element_from_source,
    _legacy_replay_storage,
    _live_browser_context_options,
    _live_browser_launch_options,
    _normalized_replay_url,
    _page_key,
    _package_artifact_path,
    _redact_headers,
    _redact_html,
    _redact_storage_value,
    _redact_text,
    _redact_url,
    _request_body_signature,
    _replay_storage_script,
    _restore_mobile_scenario_sync,
    _safe_replay_headers,
    _safe_exploration_candidate,
    _save_mobile_scenario_sync,
)
from scripts.sanitize_ui_recording_data import _decode_legacy_html
from server.api.ui_recordings import (
    _mobile_locator_match_count,
    _pull_agent_events,
    router as ui_recordings_router,
)
from server.services.ui_recording_service import (
    UiRecordingControlLeaseError,
    UiRecordingTransitionError,
    _normalized_request_url,
    _request_body_hash,
    _visible_element_bounds,
    apply_control_action,
    compile_recording_step_draft,
    ensure_control_lease,
    update_control_lease,
)
from server.services.ui_recording_redaction import redact_context_payload


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


def test_recording_redaction_covers_headers_urls_bodies_and_html() -> None:
    assert _redact_headers({
        "Authorization": "Bearer live-token",
        "Cookie": "session=secret",
        "X-Trace": "trace-1",
    }) == {
        "Authorization": "***",
        "Cookie": "***",
        "X-Trace": "trace-1",
    }
    redacted_url = _redact_url(
        "https://example.test/login?username=admin&access_token=live-token#fragment"
    )
    assert "live-token" not in redacted_url
    assert "fragment" not in redacted_url

    redacted_json = _redact_text(
        '{"username":"admin","password":"s3cret","nested":{"refresh_token":"jwt"}}',
        "application/json",
    )
    assert redacted_json is not None
    assert "s3cret" not in redacted_json
    assert "jwt" not in redacted_json
    assert redacted_json.count("***") == 2

    redacted_html = _redact_html(
        '<form><input name="username" value="admin">'
        '<input type="password" value="s3cret"></form>'
    )
    assert "s3cret" not in redacted_html
    assert 'name="username" value="admin"' in redacted_html

    server_safe = redact_context_payload({
        "url": "https://example.test/login?token=live-token",
        "headers": {"Authorization": "Bearer live-token", "X-Trace": "trace-1"},
        "body": '{"username":"admin","password":"live-password"}',
        "element": {"attributes": {"type": "password"}},
        "value": "typed-password",
    })
    assert "live-token" not in str(server_safe)
    assert "live-password" not in str(server_safe)
    assert "typed-password" not in str(server_safe)
    assert server_safe["value"] == "${password}"
    assert _decode_legacy_html("%3Cmain%3E%E7%99%BB%E5%BD%95%3C%2Fmain%3E") == "<main>登录</main>"


def test_offline_storage_redacts_camel_case_tokens_and_restores_legacy_login() -> None:
    assert _redact_storage_value("pm.accessToken", "live-access-token") == "***"
    assert _redact_storage_value("pm.refreshToken", "live-refresh-token") == "***"
    current_user = _redact_storage_value(
        "pm.currentUser",
        json.dumps({
            "user": {"id": 7, "username": "admin"},
            "nested": {"access_token": "live-token"},
        }),
    )
    assert "admin" in current_user
    assert "live-token" not in current_user

    manifest = {
        "mocks": [{
            "method": "POST",
            "url": "https://example.test/api/auth/login",
            "response": {
                "status": 200,
                "body": json.dumps({
                    "status": "success",
                    "data": {
                        "access_token": "***",
                        "refresh_token": "***",
                        "user": {
                            "id": 7,
                            "username": "admin",
                            "role_codes": ["admin"],
                        },
                    },
                }),
            },
        }],
    }
    storage = _legacy_replay_storage(manifest, "https://example.test/projects")
    assert storage["origin"] == "https://example.test"
    assert storage["local_storage"]["pm.accessToken"] == "offline-replay-token"
    persisted = json.loads(storage["local_storage"]["pm.currentUser"])
    assert persisted["user"]["username"] == "admin"
    assert persisted["activeRole"] == "admin"
    assert _legacy_replay_storage(manifest, "https://example.test/login") == {}

    script = _replay_storage_script(storage)
    assert "location.origin !== state.origin" in script
    assert "localStorage.setItem" in script
    assert "live-token" not in script


def test_offline_request_matching_preserves_business_query_and_ignores_secrets() -> None:
    first = _normalized_replay_url(
        "HTTPS://EXAMPLE.TEST/users?page=2&token=first&ts=100&status=active"
    )
    second = _normalized_replay_url(
        "https://example.test/users?status=active&token=second&page=2&ts=200#ignored"
    )
    assert first == second
    assert "page=2" in first
    assert "status=active" in first
    assert "first" not in first
    assert "second" not in second

    recorded = _request_body_signature(
        '{"username":"admin","password":"recorded"}',
        "application/json",
    )
    replayed = _request_body_signature(
        '{"password":"typed-later","username":"admin"}',
        "application/json",
    )
    assert recorded == replayed
    assert _page_key("https://example.test/users?page=2&status=active") != _page_key(
        "https://example.test/users?page=3&status=active"
    )
    assert _normalized_request_url(
        "https://example.test/users?token=first&page=2&ts=100"
    ) == _normalized_request_url(
        "https://example.test/users?page=2&token=second&ts=200"
    )
    assert _request_body_hash('{"b":2,"a":1}') == _request_body_hash('{"a":1,"b":2}')


def test_android_simulator_scenario_can_be_saved_and_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_command(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        return 0, "OK"

    monkeypatch.setattr("recorder_agent.main._run_preflight_command", fake_command)
    runtime = MobileRecorderRuntime(
        session_id=19,
        driver=object(),
        platform="android",
        udid="emulator-5554",
        app_identifier="com.example.demo",
    )
    scenario = _save_mobile_scenario_sync(runtime)
    assert scenario == {
        "ready": True,
        "restore_mode": "emulator_snapshot",
        "snapshot_name": "ui-recorder-19",
        "reason": None,
    }

    request = MobileRecorderStartRequest(
        session_id=20,
        platform="android",
        appium_url="http://127.0.0.1:4723",
        udid="emulator-5554",
        restore_scenario=scenario,
    )
    restored = _restore_mobile_scenario_sync(request)
    assert restored["restored"] is True
    assert [call[-2] for call in calls] == ["save", "load"]


def test_ios_simulator_scenario_restore_replaces_stale_app_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "data" / "ui_recordings"
    container = tmp_path / "simulators" / "ios-udid" / "app-data" / "container"
    container.mkdir(parents=True)
    (container / "state.json").write_text('{"screen":"home"}', encoding="utf-8")
    monkeypatch.setattr("recorder_agent.main._PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("recorder_agent.main._ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(
        "recorder_agent.main._run_preflight_command",
        lambda _args: (0, str(container)),
    )
    runtime = MobileRecorderRuntime(
        session_id=31,
        driver=object(),
        platform="ios",
        udid="ios-udid",
        app_identifier="com.example.demo",
    )
    scenario = _save_mobile_scenario_sync(runtime)
    assert scenario["ready"] is True

    (container / "state.json").write_text('{"screen":"changed"}', encoding="utf-8")
    (container / "stale.tmp").write_text("stale", encoding="utf-8")
    request = MobileRecorderStartRequest(
        session_id=32,
        platform="ios",
        appium_url="http://127.0.0.1:4723",
        udid="ios-udid",
        app_identifier="com.example.demo",
        restore_scenario=scenario,
    )
    restored = _restore_mobile_scenario_sync(request)

    assert restored["restored"] is True
    assert (container / "state.json").read_text(encoding="utf-8") == '{"screen":"home"}'
    assert not (container / "stale.tmp").exists()


def test_web_snapshot_collects_state_elements_and_validates_remote_actions() -> None:
    assert "__uiRecorderCollectElements" in _RECORDER_SCRIPT
    assert "__uiRecorderPageMeta" in _RECORDER_SCRIPT
    assert 'role="dialog"' in _RECORDER_SCRIPT
    assert "TEXT_SELECTOR" in _RECORDER_SCRIPT
    assert '"h1", "h2", "h3"' in _RECORDER_SCRIPT
    assert '"li", "div", "span"' in _RECORDER_SCRIPT
    assert "environment.resize" in _RECORDER_SCRIPT

    click = WebActionRequest(action="click", x=120, y=240)
    assert click.model_dump(exclude_defaults=True) == {"action": "click", "x": 120, "y": 240}
    input_action = WebActionRequest(action="input", text="admin")
    assert input_action.text == "admin"
    scroll = WebActionRequest(action="scroll", delta_y=560)
    assert scroll.delta_y == 560
    with pytest.raises(ValueError):
        WebActionRequest(action="select", x=10, y=20)
    assert UiPageSnapshotPickRequest(x=120, y=240).model_dump() == {"x": 120, "y": 240}
    assert _visible_element_bounds([
        {"fingerprint": "button", "attributes": {"bounds": {"x": 1, "y": 2}}},
        {"fingerprint": "invalid", "attributes": "bad"},
    ]) == {"button": {"x": 1, "y": 2}}


def test_headed_recorder_viewport_follows_resizable_window() -> None:
    headed = RecorderStartRequest(
        session_id=1,
        target_url="https://example.test/login",
        browser="chromium",
        headless=False,
        viewport={"width": 1440, "height": 900},
    )
    assert _live_browser_context_options(headed) == {"no_viewport": True}
    assert _live_browser_launch_options(headed)["args"] == ["--window-size=1440,900"]

    headless = headed.model_copy(update={"headless": True})
    assert _live_browser_context_options(headless) == {
        "viewport": {"width": 1440, "height": 900},
    }
    assert "args" not in _live_browser_launch_options(headless)

    replay = ReplayStartRequest(
        session_id=1,
        entry_url="https://example.test/login",
        page_fingerprint="a" * 64,
        page_source_session_id=2,
        reuse_key=f"snapshot-pick:frozen:1:{'a' * 64}",
        freeze_dom=True,
        source_session_ids=[1, 2],
    )
    assert replay.reuse_key.startswith("snapshot-pick:frozen:1:")
    assert replay.freeze_dom is True
    assert replay.source_session_ids == [1, 2]
    assert replay.page_source_session_id == 2


def test_frozen_replay_document_preserves_dom_and_removes_scripts() -> None:
    source = b"""
    <!doctype html>
    <html>
      <head><script src="/assets/app.js"></script></head>
      <body>
        <div id="members" data-expanded="true">MCP_01</div>
        <script>document.querySelector('#members').remove()</script>
      </body>
    </html>
    """

    frozen = _freeze_replay_document(source).decode("utf-8")

    assert "<script" not in frozen
    assert 'id="members"' in frozen
    assert "MCP_01" in frozen


def test_replay_bridge_recognizes_and_dismisses_radix_portals() -> None:
    assert '[role="dialog"][data-state="open"]' in _RECORDER_SCRIPT
    assert '[role="dialog"][data-state="open"]' in _REPLAY_INTERACTION_SCRIPT
    assert "scheduleFallback" in _REPLAY_INTERACTION_SCRIPT
    assert "data-scroll-locked" in _REPLAY_INTERACTION_SCRIPT


def test_ai_exploration_applies_bounded_safe_action_policy() -> None:
    request = AiExplorationRequest(
        max_pages=20,
        max_depth=3,
        timeout_seconds=120,
        seed_urls=["/projects", "/runs"],
    )
    assert request.max_pages == 20
    assert request.max_depth == 3
    assert request.seed_urls == ["/projects", "/runs"]

    safe, reason = _safe_exploration_candidate(
        {
            "text": "查看项目详情",
            "href": "https://example.test/projects/1",
            "disabled": False,
            "in_form": False,
            "input_type": "",
            "tag": "a",
            "role": "link",
        },
        allowed_hosts={"example.test"},
    )
    assert safe is True
    assert reason == "同域页面链接"

    dangerous, reason = _safe_exploration_candidate(
        {
            "text": "删除项目",
            "href": "",
            "disabled": False,
            "in_form": False,
            "input_type": "",
            "tag": "button",
            "role": "button",
        },
        allowed_hosts={"example.test"},
    )
    assert dangerous is False
    assert reason == "危险动作关键词"

    dangerous_link, reason = _safe_exploration_candidate(
        {
            "text": "项目详情",
            "href": "https://example.test/projects/1/delete",
            "disabled": False,
            "in_form": False,
            "input_type": "",
            "tag": "a",
            "role": "link",
        },
        allowed_hosts={"example.test"},
    )
    assert dangerous_link is False
    assert reason == "危险链接地址"

    cross_origin, reason = _safe_exploration_candidate(
        {
            "text": "帮助文档",
            "href": "https://outside.test/docs",
            "disabled": False,
            "in_form": False,
            "input_type": "",
            "tag": "a",
            "role": "link",
        },
        allowed_hosts={"example.test"},
    )
    assert cross_origin is False
    assert reason == "超出允许域名"


def test_agent_event_pull_drains_all_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """停止录制前必须排空 Agent 事件，不能只同步首个 500 条批次。"""
    db = SimpleNamespace(session=MagicMock())
    db.session.query.return_value.filter.return_value.scalar.return_value = 0
    session = SimpleNamespace(
        id=99,
        status="recording",
        capabilities={},
        error=None,
    )
    occurred_at = datetime.now().isoformat()

    def event(sequence_no: int) -> dict[str, object]:
        return {
            "event_key": f"event-{sequence_no}",
            "sequence_no": sequence_no,
            "event_type": "network.request",
            "source": "network",
            "occurred_at": occurred_at,
            "payload": {},
        }

    requested_after: list[int] = []

    def pull_events(
        session_id: int,
        after_sequence: int,
        limit: int,
    ) -> list[dict[str, object]]:
        assert session_id == 99
        assert limit == 500
        requested_after.append(after_sequence)
        if after_sequence == 0:
            return [event(index) for index in range(1, 501)]
        if after_sequence == 500:
            return [event(501), event(502)]
        return []

    def append_batch(
        _db_session: object,
        _session: object,
        items: list[object],
    ) -> tuple[list[SimpleNamespace], int]:
        return [
            SimpleNamespace(sequence_no=item.sequence_no)
            for item in items
        ], 0

    monkeypatch.setattr("server.api.ui_recordings.pull_agent_events", pull_events)
    monkeypatch.setattr("server.api.ui_recordings.append_events", append_batch)

    created = _pull_agent_events(db, session, strict=True)

    assert created == 502
    assert requested_after == [0, 500]
    assert session.error is None
    assert session.capabilities["recorder_agent_connected"] is True


def test_ui_element_list_exposes_stable_offset_pagination() -> None:
    """元素超过单页上限后，前端仍能继续拉取而不会丢失刚拾取的元素。"""
    app = FastAPI()
    app.include_router(ui_recordings_router, prefix="/api")
    operation = app.openapi()["paths"]["/api/ui-elements"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert parameters["offset"]["schema"]["default"] == 0
    assert parameters["offset"]["schema"]["minimum"] == 0


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


def test_mobile_locator_validation_supports_generated_absolute_xpath(tmp_path: Path) -> None:
    tree = tmp_path / "android.xml"
    tree.write_text(
        """
        <hierarchy>
          <node class="android.widget.FrameLayout">
            <node class="android.widget.Button" resource-id="com.demo:id/login" />
          </node>
        </hierarchy>
        """,
        encoding="utf-8",
    )
    snapshot = UiPageSnapshot(document_uri=str(tree))
    locator = UiElementLocator(
        strategy="xpath",
        locator="/hierarchy[1]/node[1]/node[1]",
    )

    assert _mobile_locator_match_count(snapshot, locator) == 1


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

    pick_only_draft = compile_recording_step_draft(session, [events[2]])
    assert [step["step_type"] for step in pick_only_draft["steps"]] == ["web_goto"]
    assert any("只读拾取" in item for item in pick_only_draft["warnings"])

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
