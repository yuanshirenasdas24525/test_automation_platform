"""UI 录制会话、统一事件流和项目元素库 API。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import String, func
from sqlalchemy.orm import selectinload

from database.models import (
    ALL_UI_ELEMENT_STATUSES,
    ALL_UI_PLATFORMS,
    ALL_UI_RECORDING_STATUSES,
    AppPackage,
    DEVICE_STATUS_BUSY,
    DEVICE_STATUS_IDLE,
    Device,
    Project,
    TestEnvironment,
    UiElement,
    UiElementLocator,
    UiContextArtifact,
    UiContextEvent,
    UiContextSession,
    UiPageSnapshot,
    UiPageTransition,
    UiRecordedAction,
    UiRecordingEvent,
    UiRecordingSession,
    UiStepContextLink,
    TestStepReport,
)
from database.schemas.ui_recording import (
    UiElementLocatorCreate,
    UiElementLocatorUpdate,
    UiElementUpdate,
    UiPageSnapshotUpdate,
    UiRecordingControlRequest,
    UiRecordingCreate,
    UiRecordingEventCreate,
    UiRecordingEventBatchCreate,
    UiRecordingLeaseRequest,
    UiRecordingMobileActionRequest,
    UiRecordingPickModeRequest,
    UiRecordingReplayActionRequest,
    UiRecordingReplayRequest,
    UiRecordedActionUpdate,
    UiRecordingWebActionRequest,
)
from server.api.authz import assert_project_access
from server.api.deps import CurrentUserDep, DBDep
from server.services.ui_recording_service import (
    UiRecordingControlLeaseError,
    UiRecordingTransitionError,
    append_events,
    apply_control_action,
    build_recording_step_draft,
    ensure_control_lease,
    ensure_recording_context_materialized,
    finalize_recording_context,
    is_control_command_processed,
    serialize_context_artifact,
    serialize_context_session,
    serialize_element,
    serialize_event,
    serialize_session,
    serialize_snapshot,
    serialize_page_transition,
    serialize_recorded_action,
    update_control_lease,
    validate_control_action,
)
from server.services.ui_recorder_agent_client import (
    RecorderAgentError,
    control_agent_session,
    mobile_preflight as get_mobile_preflight,
    get_web_replay,
    get_web_replay_screenshot,
    perform_mobile_action as perform_mobile_agent_action,
    perform_web_action as perform_web_agent_action,
    perform_web_replay_action,
    pull_agent_events,
    set_agent_pick_mode,
    start_web_replay,
    stop_web_replay,
    validate_web_replay_locator,
    start_mobile_session,
    start_web_session,
)

router = APIRouter(tags=["ui-recordings"])
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_UI_ARTIFACT_ROOT = (_PROJECT_ROOT / "data" / "ui_recordings").resolve()


def _get_session_or_404(
    db: DBDep,
    session_id: int,
    *,
    for_update: bool = False,
) -> UiRecordingSession:
    query = (
        db.session.query(UiRecordingSession)
        .options(selectinload(UiRecordingSession.snapshots))
        .filter(UiRecordingSession.id == session_id)
    )
    if for_update:
        query = query.with_for_update()
    session = query.first()
    if session is None:
        raise HTTPException(status_code=404, detail="录制会话不存在")
    return session


def _get_element_or_404(db: DBDep, element_id: int) -> UiElement:
    element = (
        db.session.query(UiElement)
        .options(selectinload(UiElement.locators))
        .filter(UiElement.id == element_id)
        .first()
    )
    if element is None:
        raise HTTPException(status_code=404, detail="元素不存在")
    return element


def _get_locator_or_404(db: DBDep, element_id: int, locator_id: int) -> UiElementLocator:
    locator = (
        db.session.query(UiElementLocator)
        .filter(
            UiElementLocator.id == locator_id,
            UiElementLocator.element_id == element_id,
        )
        .first()
    )
    if locator is None:
        raise HTTPException(status_code=404, detail="定位器不存在")
    return locator


def _lease_or_409(
    session: UiRecordingSession,
    client_instance_id: str | None,
    command_id: str | None,
    *,
    takeover: bool = False,
) -> bool:
    try:
        return ensure_control_lease(
            session,
            client_instance_id,
            command_id,
            takeover=takeover,
        )
    except UiRecordingControlLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _assert_simulator(device: Device) -> None:
    """首期移动端只允许显式标识的模拟器设备。"""
    capabilities = device.capabilities or {}
    device_type = str(capabilities.get("device_type") or "").strip().lower()
    udid = (device.udid or "").strip().lower()
    is_simulator = (
        capabilities.get("is_simulator") is True
        or device_type in {"emulator", "simulator"}
        or udid.startswith("emulator-")
        or udid.startswith("simulator-")
    )
    if not is_simulator:
        raise HTTPException(
            status_code=422,
            detail="首期移动端录制只支持模拟器，请为设备配置 is_simulator=true",
        )


def _assert_replay_session(replay_id: str, session_id: int) -> dict:
    """确认不可枚举的 Replay ID 仍属于当前已授权录制会话。"""
    try:
        replay = get_web_replay(replay_id)
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if int(replay.get("session_id") or 0) != session_id:
        raise HTTPException(status_code=404, detail="离线回放会话不存在")
    return replay


def _mobile_locator_match_count(snapshot: UiPageSnapshot, locator: UiElementLocator) -> int:
    """在已归档 UI Tree 中离线验证常用 Appium 定位器。"""
    if not snapshot.document_uri:
        return 0
    raw_path = Path(snapshot.document_uri)
    path = raw_path if raw_path.is_absolute() else _PROJECT_ROOT / raw_path
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError):
        return 0
    strategy = locator.strategy.lower()
    value = locator.locator
    nodes = list(root.iter())
    if strategy == "id":
        return sum(
            1 for node in nodes
            if value in {node.attrib.get("resource-id"), node.attrib.get("resourceId"), node.attrib.get("id")}
        )
    if strategy in {"accessibility_id", "name"}:
        return sum(
            1 for node in nodes
            if value in {node.attrib.get("content-desc"), node.attrib.get("name"), node.attrib.get("label")}
        )
    if strategy == "text":
        return sum(1 for node in nodes if value in {node.attrib.get("text"), node.attrib.get("label"), node.attrib.get("value")})
    if strategy == "class_name":
        return sum(1 for node in nodes if value in {node.tag, node.attrib.get("class"), node.attrib.get("type")})
    if strategy == "android_uiautomator":
        matched = re.search(r'\.text\("(.*)"\)', value)
        return sum(1 for node in nodes if matched and node.attrib.get("text") == matched.group(1).replace('\\"', '"'))
    if strategy in {"ios_predicate", "ios_class_chain"}:
        matched = re.search(r"name\s*==\s*'([^']*)'", value)
        class_match = re.search(r"\*\*/([^\[`]+)", value) if strategy == "ios_class_chain" else None
        return sum(
            1
            for node in nodes
            if matched
            and node.attrib.get("name") == matched.group(1).replace("\\'", "'")
            and (
                class_match is None
                or str(node.tag).split("}")[-1] == class_match.group(1)
                or node.attrib.get("type") == class_match.group(1)
            )
        )
    if strategy == "xpath":
        try:
            if value.startswith("/"):
                segments = [item for item in value.split("/") if item]
                root_name = str(root.tag).split("}")[-1]
                first_name = re.sub(r"\[\d+\]$", "", segments[0]) if segments else ""
                if first_name == root_name:
                    query = "." + (f"/{'/'.join(segments[1:])}" if len(segments) > 1 else "")
                else:
                    query = "." + value
            else:
                query = value
            return len(root.findall(query))
        except (KeyError, SyntaxError):
            return 0
    return 0


def _control(
    db: DBDep,
    current_user: CurrentUserDep,
    session_id: int,
    action: str,
    body: UiRecordingControlRequest | None = None,
) -> dict:
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if body and is_control_command_processed(
        session,
        body.client_instance_id,
        body.command_id,
    ):
        return {"status": "success", "data": serialize_session(db.session, session)}
    try:
        validate_control_action(session, action)
    except UiRecordingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    duplicated = _lease_or_409(
        session,
        body.client_instance_id if body else None,
        body.command_id if body else None,
        takeover=body.takeover if body else False,
    )
    if duplicated:
        return {"status": "success", "data": serialize_session(db.session, session)}
    if action == "start":
        try:
            if session.platform == "web":
                agent_data = start_web_session(session)
            else:
                device = (
                    db.session.query(Device)
                    .filter(Device.id == session.device_id)
                    .with_for_update()
                    .first()
                )
                if device is None:
                    raise HTTPException(status_code=422, detail="移动录制必须绑定模拟器")
                _assert_simulator(device)
                if device.status == DEVICE_STATUS_BUSY and device.owner_execution_id != session.id:
                    raise HTTPException(status_code=409, detail="所选模拟器正在被其他任务占用")
                app_package = None
                if session.app_package_id is not None:
                    app_package = (
                        db.session.query(AppPackage)
                        .filter(AppPackage.id == session.app_package_id)
                        .first()
                    )
                agent_data = start_mobile_session(session, device, app_package)
                device.status = DEVICE_STATUS_BUSY
                device.owner_execution_id = session.id
                device.busy_since = datetime.now()
        except RecorderAgentError as exc:
            session.capabilities = {
                **(session.capabilities or {}),
                "recorder_agent_connected": False,
            }
            session.error = str(exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        session.recorder_agent_id = str(agent_data.get("agent_id") or "")[:128] or None
        session.capabilities = {
            **(session.capabilities or {}),
            **(agent_data.get("capabilities") or {}),
            "recorder_agent_connected": True,
        }

    if action in {"pause", "resume"}:
        try:
            control_agent_session(session.id, action)
        except RecorderAgentError as exc:
            session.capabilities = {
                **(session.capabilities or {}),
                "recorder_agent_connected": False,
            }
            session.error = str(exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if action in {"stop", "cancel"} and session.status in {"recording", "paused"}:
        _pull_agent_events(db, session, strict=False)
        try:
            stopped = control_agent_session(session.id, "stop")
            package = dict((stopped or {}).get("offline_package") or {})
            if package:
                session.capabilities = {
                    **(session.capabilities or {}),
                    "offline_replay": package,
                }
            mobile_scenario = dict((stopped or {}).get("mobile_scenario") or {})
            if mobile_scenario:
                session.capabilities = {
                    **(session.capabilities or {}),
                    "mobile_scenario": mobile_scenario,
                }
            _pull_agent_events(db, session, strict=False)
        except RecorderAgentError as exc:
            session.error = f"停止时 Recorder Agent 不可达：{exc}"
        session.capabilities = {
            **(session.capabilities or {}),
            "recorder_agent_connected": False,
        }
        if session.device_id is not None:
            device = (
                db.session.query(Device)
                .filter(Device.id == session.device_id)
                .with_for_update()
                .first()
            )
            if device is not None and device.owner_execution_id == session.id:
                device.status = DEVICE_STATUS_IDLE
                device.owner_execution_id = None
                device.busy_since = None

    try:
        apply_control_action(session, action)
    except UiRecordingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if action in {"stop", "cancel"}:
        next_capabilities = dict(session.capabilities or {})
        next_capabilities.pop("control_lease", None)
        next_capabilities["pick_mode"] = False
        session.capabilities = next_capabilities
        finalize_recording_context(
            db.session,
            session,
            cancelled=action == "cancel",
        )
    db.session.flush()
    db.session.refresh(session)
    return {"status": "success", "data": serialize_session(db.session, session)}


def _pull_agent_events(
    db: DBDep,
    session: UiRecordingSession,
    *,
    strict: bool,
) -> int:
    """从宿主机 Agent 拉取增量事件并落库。"""
    after_sequence = int(
        db.session.query(func.coalesce(func.max(UiRecordingEvent.sequence_no), 0))
        .filter(UiRecordingEvent.session_id == session.id)
        .scalar()
        or 0
    )
    try:
        payloads = pull_agent_events(session.id, after_sequence)
    except RecorderAgentError as exc:
        session.capabilities = {
            **(session.capabilities or {}),
            "recorder_agent_connected": False,
        }
        session.error = str(exc)
        if strict:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return 0
    if not payloads:
        return 0
    try:
        items = [UiRecordingEventCreate.model_validate(item) for item in payloads]
        created, _skipped = append_events(db.session, session, items)
    except (ValueError, UiRecordingTransitionError) as exc:
        if strict:
            raise HTTPException(status_code=422, detail=f"Agent 事件格式错误：{exc}") from exc
        session.error = f"Agent 事件格式错误：{exc}"
        return 0
    session.capabilities = {
        **(session.capabilities or {}),
        "recorder_agent_connected": True,
    }
    session.error = None
    return len(created)


@router.post("/ui-recordings")
def create_recording(
    body: UiRecordingCreate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    project = db.session.query(Project).filter(Project.id == body.project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    assert_project_access(db, current_user, project.id)

    if body.environment_id is not None:
        environment = (
            db.session.query(TestEnvironment)
            .filter(TestEnvironment.id == body.environment_id)
            .first()
        )
        if environment is None or environment.project_id != project.id:
            raise HTTPException(status_code=422, detail="测试环境不属于当前项目")

    if body.platform in {"android", "ios"} and body.device_id is None:
        raise HTTPException(status_code=422, detail="Android/iOS 录制必须选择一台模拟器")

    if body.device_id is not None:
        device = db.session.query(Device).filter(Device.id == body.device_id).first()
        if device is None:
            raise HTTPException(status_code=422, detail="设备不存在")
        if body.platform == "web":
            raise HTTPException(status_code=422, detail="Web 录制不能绑定移动设备")
        if device.platform.strip().lower() != body.platform:
            raise HTTPException(status_code=422, detail="设备平台与录制平台不一致")
        _assert_simulator(device)

    if body.app_package_id is not None:
        app_package = (
            db.session.query(AppPackage)
            .filter(AppPackage.id == body.app_package_id)
            .first()
        )
        if app_package is None:
            raise HTTPException(status_code=422, detail="应用包不存在")
        if app_package.project_id not in {None, project.id}:
            raise HTTPException(status_code=422, detail="应用包不属于当前项目")
        if app_package.platform.strip().lower() != body.platform:
            raise HTTPException(status_code=422, detail="应用包平台与录制平台不一致")

    session = UiRecordingSession(
        project_id=project.id,
        platform=body.platform,
        name=body.name.strip(),
        environment_id=body.environment_id,
        device_id=body.device_id,
        app_package_id=body.app_package_id,
        created_by_id=current_user.id,
        source_url=body.source_url,
        offline_level=3,
        capture_config={
            "screen": True,
            "console": body.platform == "web",
            "network": True,
            "user_events": True,
            "environment": True,
            "offline_business_replay": body.platform == "web",
            **body.capture_config,
        },
        capabilities={
            "control_plane": True,
            "recorder_agent_connected": False,
            "simulator_only": body.platform in {"android", "ios"},
        },
    )
    db.session.add(session)
    db.session.flush()
    db.session.refresh(session)
    return {"status": "success", "data": serialize_session(db.session, session)}


@router.get("/ui-recordings")
def list_recordings(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Query(..., gt=0),
    platform: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    assert_project_access(db, current_user, project_id)
    if platform is not None and platform not in ALL_UI_PLATFORMS:
        raise HTTPException(status_code=422, detail="platform 必须是 web/android/ios")
    if status is not None and status not in ALL_UI_RECORDING_STATUSES:
        raise HTTPException(status_code=422, detail="非法录制状态")

    query = (
        db.session.query(UiRecordingSession)
        .options(selectinload(UiRecordingSession.snapshots))
        .filter(UiRecordingSession.project_id == project_id)
    )
    if platform:
        query = query.filter(UiRecordingSession.platform == platform)
    if status:
        query = query.filter(UiRecordingSession.status == status)
    sessions = query.order_by(UiRecordingSession.created_at.desc()).limit(limit).all()
    return {
        "status": "success",
        "data": [serialize_session(db.session, session) for session in sessions],
    }


@router.get("/ui-recordings/mobile-preflight")
def get_recording_mobile_preflight(current_user: CurrentUserDep):
    """返回宿主机 Appium 和已启动模拟器状态；只读、不自动启动外部进程。"""
    del current_user
    try:
        data = get_mobile_preflight()
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "success", "data": data}


@router.get("/ui-recordings/{session_id}")
def get_recording(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    return {"status": "success", "data": serialize_session(db.session, session)}


@router.post("/ui-recordings/{session_id}/start")
def start_recording(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    body: UiRecordingControlRequest | None = None,
):
    return _control(db, current_user, session_id, "start", body)


@router.post("/ui-recordings/{session_id}/pause")
def pause_recording(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    body: UiRecordingControlRequest | None = None,
):
    return _control(db, current_user, session_id, "pause", body)


@router.post("/ui-recordings/{session_id}/resume")
def resume_recording(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    body: UiRecordingControlRequest | None = None,
):
    return _control(db, current_user, session_id, "resume", body)


@router.post("/ui-recordings/{session_id}/stop")
def stop_recording(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    body: UiRecordingControlRequest | None = None,
):
    return _control(db, current_user, session_id, "stop", body)


@router.post("/ui-recordings/{session_id}/cancel")
def cancel_recording(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    body: UiRecordingControlRequest | None = None,
):
    return _control(db, current_user, session_id, "cancel", body)


@router.post("/ui-recordings/{session_id}/control-lease")
def update_recording_control_lease(
    session_id: int,
    body: UiRecordingLeaseRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    try:
        update_control_lease(session, body.client_instance_id, body.action)
    except UiRecordingControlLeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.session.flush()
    return {"status": "success", "data": serialize_session(db.session, session)}


@router.post("/ui-recordings/{session_id}/pick-mode")
def update_recording_pick_mode(
    session_id: int,
    body: UiRecordingPickModeRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if session.status not in {"recording", "paused"}:
        raise HTTPException(status_code=409, detail="当前录制状态不能切换拾取模式")
    duplicated = _lease_or_409(session, body.client_instance_id, body.command_id)
    if not duplicated:
        try:
            set_agent_pick_mode(session.id, body.enabled)
        except RecorderAgentError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    session.capabilities = {
        **(session.capabilities or {}),
        "pick_mode": body.enabled,
    }
    db.session.flush()
    return {"status": "success", "data": serialize_session(db.session, session)}


@router.post("/ui-recordings/{session_id}/mobile-actions")
def perform_recording_mobile_action(
    session_id: int,
    body: UiRecordingMobileActionRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """在模拟器远程画面上执行动作，并立即拉取动作、元素和新快照。"""
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if session.platform not in {"android", "ios"}:
        raise HTTPException(status_code=422, detail="移动动作只适用于 Android/iOS 录制")
    if session.status not in {"recording", "paused"}:
        raise HTTPException(status_code=409, detail="当前录制状态不能执行移动动作")
    duplicated = _lease_or_409(session, body.client_instance_id, body.command_id)
    if duplicated:
        return {"status": "success", "data": serialize_session(db.session, session)}
    try:
        perform_mobile_agent_action(
            session.id,
            body.model_dump(exclude={"client_instance_id", "command_id"}),
        )
    except RecorderAgentError as exc:
        session.error = str(exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _pull_agent_events(db, session, strict=True)
    session.error = None
    db.session.flush()
    return {"status": "success", "data": serialize_session(db.session, session)}


@router.post("/ui-recordings/{session_id}/web-actions")
def perform_recording_web_action(
    session_id: int,
    body: UiRecordingWebActionRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """在 Web 快照上执行浏览/拾取动作，并立即同步新页面状态。"""
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if session.platform != "web":
        raise HTTPException(status_code=422, detail="Web 动作只适用于 Web 录制")
    if session.status not in {"recording", "paused"}:
        raise HTTPException(status_code=409, detail="当前录制状态不能执行 Web 动作")
    duplicated = _lease_or_409(session, body.client_instance_id, body.command_id)
    if duplicated:
        return {"status": "success", "data": serialize_session(db.session, session)}
    try:
        perform_web_agent_action(
            session.id,
            body.model_dump(exclude={"client_instance_id", "command_id"}),
        )
    except RecorderAgentError as exc:
        session.error = str(exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _pull_agent_events(db, session, strict=True)
    session.error = None
    db.session.flush()
    return {"status": "success", "data": serialize_session(db.session, session)}


@router.post("/ui-recordings/{session_id}/replay")
def start_recording_offline_replay(
    session_id: int,
    body: UiRecordingReplayRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if session.platform != "web":
        raise HTTPException(status_code=422, detail="离线网页回放只适用于 Web 录制")
    offline = dict((session.capabilities or {}).get("offline_replay") or {})
    if session.status != "completed" or not offline.get("ready"):
        raise HTTPException(status_code=409, detail="当前会话尚未生成可用的离线回放包")
    if body.entry_url or body.page_fingerprint:
        matched = any(
            (body.entry_url is None or snapshot.url == body.entry_url)
            and (body.page_fingerprint is None or snapshot.fingerprint == body.page_fingerprint)
            for snapshot in session.snapshots
        )
        if not matched:
            raise HTTPException(status_code=422, detail="指定页面状态不属于当前录制会话")
    try:
        replay = start_web_replay(
            session.id,
            browser=body.browser,
            headless=body.headless,
            entry_url=body.entry_url,
            page_fingerprint=body.page_fingerprint,
            viewport=body.viewport,
        )
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "success", "data": replay}


@router.get("/ui-recordings/{session_id}/replays/{replay_id}")
def get_recording_offline_replay(
    session_id: int,
    replay_id: str,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    return {"status": "success", "data": _assert_replay_session(replay_id, session.id)}


@router.post("/ui-recordings/{session_id}/replays/{replay_id}/actions")
def perform_recording_offline_replay_action(
    session_id: int,
    replay_id: str,
    body: UiRecordingReplayActionRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if session.platform != "web" or session.status != "completed":
        raise HTTPException(status_code=409, detail="当前会话不能执行离线画布动作")
    _assert_replay_session(replay_id, session.id)
    try:
        data = perform_web_replay_action(replay_id, body.model_dump())
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "success", "data": data}


@router.get("/ui-recordings/{session_id}/replays/{replay_id}/screenshot")
def get_recording_offline_replay_screenshot(
    session_id: int,
    replay_id: str,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    _assert_replay_session(replay_id, session.id)
    try:
        content = get_web_replay_screenshot(replay_id)
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=content, media_type="image/png")


@router.post("/ui-recordings/{session_id}/replays/{replay_id}/stop")
def stop_recording_offline_replay(
    session_id: int,
    replay_id: str,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    _assert_replay_session(replay_id, session.id)
    try:
        stop_web_replay(replay_id)
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "success", "data": {"status": "stopped"}}


@router.get("/ui-recordings/{session_id}/step-draft")
def get_recording_step_draft(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """把已完成录制编译为现有 v2 TestStep 草稿，不自动生成断言。"""
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if session.status != "completed":
        raise HTTPException(status_code=409, detail="停止录制后才能生成用例草稿")
    return {
        "status": "success",
        "data": build_recording_step_draft(db.session, session),
    }


@router.get("/ui-recordings/{session_id}/events")
def list_recording_events(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    after_sequence: int = Query(0, ge=0),
    to_sequence: int | None = Query(None, ge=1),
    source: str | None = Query(None, max_length=40),
    event_type: str | None = Query(None, max_length=80),
    severity: str | None = Query(None, max_length=20),
    keyword: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=1000),
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if session.status in {"recording", "paused"}:
        _pull_agent_events(db, session, strict=False)
    query = db.session.query(UiRecordingEvent).filter(
        UiRecordingEvent.session_id == session.id,
        UiRecordingEvent.sequence_no > after_sequence,
    )
    if to_sequence is not None:
        query = query.filter(UiRecordingEvent.sequence_no <= to_sequence)
    if source:
        query = query.filter(UiRecordingEvent.source == source)
    if event_type:
        query = query.filter(UiRecordingEvent.event_type == event_type)
    if severity:
        query = query.filter(UiRecordingEvent.severity == severity)
    if keyword and keyword.strip():
        query = query.filter(
            UiRecordingEvent.payload.cast(String).ilike(f"%{keyword.strip()}%")
        )
    events = query.order_by(UiRecordingEvent.sequence_no).limit(limit).all()
    return {
        "status": "success",
        "data": [serialize_event(event) for event in events],
    }


@router.get("/ui-recordings/{session_id}/actions")
def list_recording_actions(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    include_ignored: bool = Query(True),
):
    """返回可编辑动作时间线。"""
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    query = db.session.query(UiRecordedAction).filter(
        UiRecordedAction.session_id == session.id,
    )
    if not include_ignored:
        query = query.filter(UiRecordedAction.status != "ignored")
    actions = query.order_by(UiRecordedAction.sequence_no, UiRecordedAction.id).all()
    return {
        "status": "success",
        "data": [serialize_recorded_action(item) for item in actions],
    }


@router.patch("/ui-recordings/{session_id}/actions/{action_id}")
def update_recording_action(
    session_id: int,
    action_id: int,
    body: UiRecordedActionUpdate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """重命名、排序或忽略噪声动作。"""
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    action = (
        db.session.query(UiRecordedAction)
        .filter(
            UiRecordedAction.id == action_id,
            UiRecordedAction.session_id == session.id,
        )
        .first()
    )
    if action is None:
        raise HTTPException(status_code=404, detail="录制动作不存在")
    if body.name is not None:
        action.name = body.name.strip()
    if body.status is not None:
        action.status = body.status
    if body.sequence_no is not None:
        action.sequence_no = body.sequence_no
    if body.payload is not None:
        action.payload = {**(action.payload or {}), **body.payload}
    db.session.flush()
    db.session.refresh(action)
    return {"status": "success", "data": serialize_recorded_action(action)}


@router.get("/ui-recordings/{session_id}/context")
def get_recording_context(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """返回结果页首屏所需的上下文、动作、制品和页面跳转摘要。"""
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    context = ensure_recording_context_materialized(db.session, session)
    actions = (
        db.session.query(UiRecordedAction)
        .filter(UiRecordedAction.session_id == session.id)
        .order_by(UiRecordedAction.sequence_no, UiRecordedAction.id)
        .all()
    )
    artifacts = (
        db.session.query(UiContextArtifact)
        .filter(UiContextArtifact.context_session_id == context.id)
        .order_by(UiContextArtifact.created_at, UiContextArtifact.id)
        .all()
    )
    transitions = (
        db.session.query(UiPageTransition)
        .filter(UiPageTransition.session_id == session.id)
        .order_by(UiPageTransition.occurred_at, UiPageTransition.id)
        .all()
    )
    counts = dict(
        db.session.query(UiRecordingEvent.source, func.count(UiRecordingEvent.id))
        .filter(UiRecordingEvent.session_id == session.id)
        .group_by(UiRecordingEvent.source)
        .all()
    )
    return {
        "status": "success",
        "data": {
            "context": serialize_context_session(context),
            "actions": [serialize_recorded_action(item) for item in actions],
            "artifacts": [serialize_context_artifact(item) for item in artifacts],
            "transitions": [serialize_page_transition(item) for item in transitions],
            "event_counts": {str(key): int(value) for key, value in counts.items()},
        },
    }


@router.get("/ui-context-artifacts/{artifact_id}/content")
def get_ui_context_artifact_content(
    artifact_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """按项目权限下载上下文制品，且只允许访问 UI 录制制品目录。"""
    artifact = db.session.get(UiContextArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="上下文制品不存在")
    context = db.session.get(UiContextSession, artifact.context_session_id)
    if context is None:
        raise HTTPException(status_code=404, detail="上下文会话不存在")
    assert_project_access(db, current_user, context.project_id)
    raw_path = Path(artifact.uri)
    artifact_path = (
        raw_path.resolve()
        if raw_path.is_absolute()
        else (_PROJECT_ROOT / raw_path).resolve()
    )
    if artifact_path != _UI_ARTIFACT_ROOT and _UI_ARTIFACT_ROOT not in artifact_path.parents:
        raise HTTPException(status_code=403, detail="制品路径不在 UI 录制目录")
    if not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="上下文制品文件不存在")
    return FileResponse(
        artifact_path,
        media_type=artifact.mime_type or "application/octet-stream",
        filename=artifact_path.name,
    )


@router.get("/ui-context-sessions/{context_session_id}")
def get_ui_context_session(
    context_session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    after_sequence: int = Query(0, ge=0),
    to_sequence: int | None = Query(None, ge=1),
    source: str | None = Query(None, max_length=40),
    limit: int = Query(500, ge=1, le=1000),
):
    """录制态和正式执行态共用的上下文结果查询。"""
    context = db.session.get(UiContextSession, context_session_id)
    if context is None:
        raise HTTPException(status_code=404, detail="上下文会话不存在")
    assert_project_access(db, current_user, context.project_id)
    event_query = db.session.query(UiContextEvent).filter(
        UiContextEvent.context_session_id == context.id,
        UiContextEvent.sequence_no > after_sequence,
    )
    if to_sequence is not None:
        event_query = event_query.filter(UiContextEvent.sequence_no <= to_sequence)
    if source:
        event_query = event_query.filter(UiContextEvent.source == source)
    events = event_query.order_by(UiContextEvent.sequence_no).limit(limit).all()
    links = (
        db.session.query(UiStepContextLink, TestStepReport)
        .outerjoin(TestStepReport, TestStepReport.id == UiStepContextLink.test_step_report_id)
        .filter(UiStepContextLink.context_session_id == context.id)
        .order_by(UiStepContextLink.id)
        .all()
    )
    artifacts = (
        db.session.query(UiContextArtifact)
        .filter(UiContextArtifact.context_session_id == context.id)
        .order_by(UiContextArtifact.id)
        .all()
    )
    return {
        "status": "success",
        "data": {
            "context": serialize_context_session(context),
            "events": [
                {
                    "id": event.id,
                    "sequence_no": event.sequence_no,
                    "event_type": event.event_type,
                    "source": event.source,
                    "severity": event.severity,
                    "step_id": event.step_id,
                    "occurred_at": event.occurred_at,
                    "monotonic_ms": event.monotonic_ms,
                    "payload": event.payload or {},
                }
                for event in events
            ],
            "steps": [
                {
                    "link_id": link.id,
                    "test_step_report_id": link.test_step_report_id,
                    "step_name": step.step_name if step else None,
                    "step_type": step.step_type if step else None,
                    "status": step.status if step else None,
                    "error_message": step.error_message if step else None,
                    "event_from_seq": link.event_from_seq,
                    "event_to_seq": link.event_to_seq,
                    "screenshot_before_id": link.screenshot_before_id,
                    "screenshot_after_id": link.screenshot_after_id,
                    "summary": link.summary or {},
                }
                for link, step in links
            ],
            "artifacts": [serialize_context_artifact(item) for item in artifacts],
        },
    }


@router.post("/ui-recordings/{session_id}/events:batch")
def append_recording_events(
    session_id: int,
    body: UiRecordingEventBatchCreate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    try:
        created, skipped = append_events(db.session, session, body.events)
    except UiRecordingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "status": "success",
        "data": {
            "accepted": len(created),
            "skipped": skipped,
            "events": [serialize_event(event) for event in created],
        },
    }


@router.get("/ui-elements")
def list_ui_elements(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Query(..., gt=0),
    platform: str | None = Query(None),
    page_key: str | None = Query(None),
    status: str | None = Query(None),
    keyword: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=1000),
):
    assert_project_access(db, current_user, project_id)
    if platform is not None and platform not in ALL_UI_PLATFORMS:
        raise HTTPException(status_code=422, detail="platform 必须是 web/android/ios")
    if status is not None and status not in ALL_UI_ELEMENT_STATUSES:
        raise HTTPException(status_code=422, detail="非法元素状态")

    query = (
        db.session.query(UiElement)
        .options(selectinload(UiElement.locators))
        .filter(UiElement.project_id == project_id)
    )
    if platform:
        query = query.filter(UiElement.platform == platform)
    if page_key:
        query = query.filter(UiElement.page_key == page_key)
    if status:
        query = query.filter(UiElement.status == status)
    if keyword and keyword.strip():
        token = f"%{keyword.strip()}%"
        query = query.filter(
            UiElement.semantic_name.ilike(token) | UiElement.page_name.ilike(token)
        )
    elements = (
        query.order_by(UiElement.page_name, UiElement.semantic_name).limit(limit).all()
    )
    return {
        "status": "success",
        "data": [serialize_element(element) for element in elements],
    }


@router.get("/ui-page-snapshots")
def list_ui_page_snapshots(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Query(..., gt=0),
    platform: str | None = Query(None),
    page_key: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    assert_project_access(db, current_user, project_id)
    if platform is not None and platform not in ALL_UI_PLATFORMS:
        raise HTTPException(status_code=422, detail="platform 必须是 web/android/ios")
    query = db.session.query(UiPageSnapshot).filter(
        UiPageSnapshot.project_id == project_id,
    )
    if platform:
        query = query.filter(UiPageSnapshot.platform == platform)
    if page_key:
        query = query.filter(UiPageSnapshot.page_key == page_key)
    snapshots = (
        query.order_by(UiPageSnapshot.created_at.desc()).limit(limit).all()
    )
    return {
        "status": "success",
        "data": [serialize_snapshot(snapshot) for snapshot in snapshots],
    }


@router.get("/ui-page-snapshots/{snapshot_id}/screenshot")
def get_ui_page_snapshot_screenshot(
    snapshot_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    snapshot = db.session.query(UiPageSnapshot).filter(UiPageSnapshot.id == snapshot_id).first()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="页面快照不存在")
    assert_project_access(db, current_user, snapshot.project_id)
    if not snapshot.screenshot_uri:
        raise HTTPException(status_code=404, detail="页面快照没有截图")
    raw_path = Path(snapshot.screenshot_uri)
    artifact_path = raw_path.resolve() if raw_path.is_absolute() else (_PROJECT_ROOT / raw_path).resolve()
    if artifact_path != _UI_ARTIFACT_ROOT and _UI_ARTIFACT_ROOT not in artifact_path.parents:
        raise HTTPException(status_code=403, detail="快照路径不在 UI 录制制品目录")
    if not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="页面截图文件不存在")
    return FileResponse(artifact_path, media_type="image/png", filename=f"snapshot-{snapshot.id}.png")


@router.patch("/ui-page-snapshots/{snapshot_id}")
def update_ui_page_snapshot(
    snapshot_id: int,
    body: UiPageSnapshotUpdate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    snapshot = db.session.get(UiPageSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="页面快照不存在")
    assert_project_access(db, current_user, snapshot.project_id)
    if body.page_name is not None:
        page_name = body.page_name.strip()
        if body.apply_page_name_to_group:
            (
                db.session.query(UiPageSnapshot)
                .filter(
                    UiPageSnapshot.project_id == snapshot.project_id,
                    UiPageSnapshot.platform == snapshot.platform,
                    UiPageSnapshot.page_key == snapshot.page_key,
                )
                .update({UiPageSnapshot.page_name: page_name}, synchronize_session=False)
            )
            (
                db.session.query(UiElement)
                .filter(
                    UiElement.project_id == snapshot.project_id,
                    UiElement.platform == snapshot.platform,
                    UiElement.page_key == snapshot.page_key,
                )
                .update({UiElement.page_name: page_name}, synchronize_session=False)
            )
        else:
            snapshot.page_name = page_name
    if body.state_name is not None:
        snapshot.state_name = body.state_name.strip()
    db.session.flush()
    db.session.refresh(snapshot)
    return {"status": "success", "data": serialize_snapshot(snapshot)}


@router.get("/ui-elements/{element_id}")
def get_ui_element(
    element_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    element = _get_element_or_404(db, element_id)
    assert_project_access(db, current_user, element.project_id)
    return {"status": "success", "data": serialize_element(element)}


@router.patch("/ui-elements/{element_id}")
def update_ui_element(
    element_id: int,
    body: UiElementUpdate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    element = _get_element_or_404(db, element_id)
    assert_project_access(db, current_user, element.project_id)
    if body.semantic_name is not None:
        element.semantic_name = body.semantic_name.strip()
    if body.aliases is not None:
        aliases = list(dict.fromkeys(item.strip() for item in body.aliases if item.strip()))[:20]
        element.attributes = {**(element.attributes or {}), "aliases": aliases}
    if body.status is not None:
        element.status = body.status
    db.session.flush()
    return {"status": "success", "data": serialize_element(element)}


@router.post("/ui-elements/{element_id}/locators")
def create_ui_element_locator(
    element_id: int,
    body: UiElementLocatorCreate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    element = _get_element_or_404(db, element_id)
    assert_project_access(db, current_user, element.project_id)
    strategy = body.strategy.strip().lower()
    locator_value = body.locator.strip()
    existing = (
        db.session.query(UiElementLocator)
        .filter(
            UiElementLocator.element_id == element.id,
            UiElementLocator.strategy == strategy,
            UiElementLocator.locator == locator_value,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="相同定位器已经存在")
    if body.is_primary:
        for locator in element.locators:
            locator.is_primary = False
    locator = UiElementLocator(
        element_id=element.id,
        strategy=strategy,
        locator=locator_value,
        score=body.score,
        is_primary=body.is_primary or not element.locators,
        source="manual",
    )
    db.session.add(locator)
    db.session.flush()
    db.session.expire(element, ["locators"])
    return {"status": "success", "data": serialize_element(element)}


@router.patch("/ui-elements/{element_id}/locators/{locator_id}")
def update_ui_element_locator(
    element_id: int,
    locator_id: int,
    body: UiElementLocatorUpdate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    element = _get_element_or_404(db, element_id)
    assert_project_access(db, current_user, element.project_id)
    locator = _get_locator_or_404(db, element.id, locator_id)
    next_strategy = body.strategy.strip().lower() if body.strategy is not None else locator.strategy
    next_value = body.locator.strip() if body.locator is not None else locator.locator
    duplicate = (
        db.session.query(UiElementLocator.id)
        .filter(
            UiElementLocator.element_id == element.id,
            UiElementLocator.strategy == next_strategy,
            UiElementLocator.locator == next_value,
            UiElementLocator.id != locator.id,
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="相同定位器已经存在")
    if body.strategy is not None:
        locator.strategy = next_strategy
    if body.locator is not None:
        locator.locator = next_value
        locator.is_unique = None
        locator.match_count = None
        locator.last_verified_at = None
    if body.score is not None:
        locator.score = body.score
    if body.is_primary is True:
        for candidate in element.locators:
            candidate.is_primary = candidate.id == locator.id
    elif body.is_primary is False:
        locator.is_primary = False
    db.session.flush()
    db.session.expire(element, ["locators"])
    return {"status": "success", "data": serialize_element(element)}


@router.post("/ui-elements/{element_id}/locators/{locator_id}/validate")
def validate_ui_element_locator(
    element_id: int,
    locator_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    snapshot_id: int | None = Query(None, gt=0),
):
    """在元素最后一次页面状态中验证定位器，并保存唯一性证据。"""
    element = _get_element_or_404(db, element_id)
    assert_project_access(db, current_user, element.project_id)
    locator = _get_locator_or_404(db, element.id, locator_id)
    target_snapshot_id = snapshot_id or element.last_snapshot_id or element.first_snapshot_id
    snapshot = db.session.get(UiPageSnapshot, target_snapshot_id) if target_snapshot_id else None
    if snapshot is None or snapshot.project_id != element.project_id:
        raise HTTPException(status_code=422, detail="元素没有可用于验证的页面快照")
    if snapshot.platform == "web":
        recording = db.session.get(UiRecordingSession, snapshot.session_id)
        if recording is None:
            raise HTTPException(status_code=404, detail="页面快照所属录制会话不存在")
        replay_id: str | None = None
        try:
            replay = start_web_replay(
                recording.id,
                browser="chromium",
                headless=True,
                entry_url=snapshot.url,
                page_fingerprint=snapshot.fingerprint,
                viewport={"width": 1440, "height": 900},
            )
            replay_id = str(replay.get("replay_id") or "")
            result = validate_web_replay_locator(replay_id, locator.strategy, locator.locator)
            match_count = int(result.get("match_count") or 0)
        except RecorderAgentError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            if replay_id:
                try:
                    stop_web_replay(replay_id)
                except RecorderAgentError:
                    pass
    else:
        match_count = _mobile_locator_match_count(snapshot, locator)
    locator.match_count = match_count
    locator.is_unique = match_count == 1
    locator.last_verified_at = datetime.now()
    locator.last_verified_snapshot_id = snapshot.id
    if locator.is_unique:
        element.status = "verified"
        element.last_verified_at = locator.last_verified_at
    elif not any(item.is_unique is True for item in element.locators if item.id != locator.id):
        element.status = "stale"
    db.session.flush()
    db.session.expire(element, ["locators"])
    return {"status": "success", "data": serialize_element(element)}


@router.delete("/ui-elements/{element_id}/locators/{locator_id}")
def delete_ui_element_locator(
    element_id: int,
    locator_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    element = _get_element_or_404(db, element_id)
    assert_project_access(db, current_user, element.project_id)
    locator = _get_locator_or_404(db, element.id, locator_id)
    was_primary = locator.is_primary
    db.session.delete(locator)
    db.session.flush()
    if was_primary:
        replacement = (
            db.session.query(UiElementLocator)
            .filter(UiElementLocator.element_id == element.id)
            .order_by(UiElementLocator.score.desc(), UiElementLocator.id)
            .first()
        )
        if replacement is not None:
            replacement.is_primary = True
    db.session.flush()
    db.session.expire(element, ["locators"])
    return {"status": "success", "data": serialize_element(element)}
