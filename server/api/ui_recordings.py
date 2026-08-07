"""UI 录制会话、统一事件流和项目元素库 API。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
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
    UiPageSnapshot,
    UiRecordingEvent,
    UiRecordingSession,
)
from database.schemas.ui_recording import (
    UiRecordingControlRequest,
    UiRecordingCreate,
    UiRecordingEventCreate,
    UiRecordingEventBatchCreate,
    UiRecordingLeaseRequest,
    UiRecordingMobileActionRequest,
    UiRecordingPickModeRequest,
    UiRecordingReplayRequest,
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
    is_control_command_processed,
    serialize_element,
    serialize_event,
    serialize_session,
    serialize_snapshot,
    update_control_lease,
    validate_control_action,
)
from server.services.ui_recorder_agent_client import (
    RecorderAgentError,
    control_agent_session,
    mobile_preflight as get_mobile_preflight,
    perform_mobile_action as perform_mobile_agent_action,
    pull_agent_events,
    set_agent_pick_mode,
    start_web_replay,
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
    try:
        replay = start_web_replay(
            session.id,
            browser=body.browser,
            headless=body.headless,
        )
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "success", "data": replay}


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
    limit: int = Query(200, ge=1, le=1000),
):
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if session.status in {"recording", "paused"}:
        _pull_agent_events(db, session, strict=False)
    events = (
        db.session.query(UiRecordingEvent)
        .filter(
            UiRecordingEvent.session_id == session.id,
            UiRecordingEvent.sequence_no > after_sequence,
        )
        .order_by(UiRecordingEvent.sequence_no)
        .limit(limit)
        .all()
    )
    return {
        "status": "success",
        "data": [serialize_event(event) for event in events],
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


@router.get("/ui-elements/{element_id}")
def get_ui_element(
    element_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    element = (
        db.session.query(UiElement)
        .options(selectinload(UiElement.locators))
        .filter(UiElement.id == element_id)
        .first()
    )
    if element is None:
        raise HTTPException(status_code=404, detail="元素不存在")
    assert_project_access(db, current_user, element.project_id)
    return {"status": "success", "data": serialize_element(element)}
