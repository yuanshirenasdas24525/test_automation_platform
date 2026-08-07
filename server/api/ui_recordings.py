"""UI 录制会话、统一事件流和项目元素库 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from database.models import (
    ALL_UI_ELEMENT_STATUSES,
    ALL_UI_PLATFORMS,
    ALL_UI_RECORDING_STATUSES,
    AppPackage,
    Device,
    Project,
    TestEnvironment,
    UiElement,
    UiRecordingEvent,
    UiRecordingSession,
)
from database.schemas.ui_recording import (
    UiRecordingCreate,
    UiRecordingEventCreate,
    UiRecordingEventBatchCreate,
)
from server.api.authz import assert_project_access
from server.api.deps import CurrentUserDep, DBDep
from server.services.ui_recording_service import (
    UiRecordingTransitionError,
    append_events,
    apply_control_action,
    serialize_element,
    serialize_event,
    serialize_session,
)
from server.services.ui_recorder_agent_client import (
    RecorderAgentError,
    control_web_session,
    pull_web_events,
    start_web_session,
)

router = APIRouter(tags=["ui-recordings"])


def _get_session_or_404(db: DBDep, session_id: int) -> UiRecordingSession:
    session = (
        db.session.query(UiRecordingSession)
        .options(selectinload(UiRecordingSession.snapshots))
        .filter(UiRecordingSession.id == session_id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="录制会话不存在")
    return session


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
) -> dict:
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if action == "start":
        if session.platform != "web":
            raise HTTPException(
                status_code=501,
                detail="Android/iOS 模拟器 Recorder 尚未开放，请先使用 Web 录制",
            )
        try:
            agent_data = start_web_session(session)
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

    if action in {"pause", "resume"} and session.platform == "web":
        try:
            control_web_session(session.id, action)
        except RecorderAgentError as exc:
            session.capabilities = {
                **(session.capabilities or {}),
                "recorder_agent_connected": False,
            }
            session.error = str(exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if action == "stop" and session.platform == "web":
        _pull_agent_events(db, session, strict=False)
        try:
            control_web_session(session.id, "stop")
            _pull_agent_events(db, session, strict=False)
        except RecorderAgentError as exc:
            session.error = f"停止时 Recorder Agent 不可达：{exc}"
        session.capabilities = {
            **(session.capabilities or {}),
            "recorder_agent_connected": False,
        }

    try:
        apply_control_action(session, action)
    except UiRecordingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    if session.platform != "web":
        return 0
    after_sequence = int(
        db.session.query(func.coalesce(func.max(UiRecordingEvent.sequence_no), 0))
        .filter(UiRecordingEvent.session_id == session.id)
        .scalar()
        or 0
    )
    try:
        payloads = pull_web_events(session.id, after_sequence)
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
def start_recording(session_id: int, db: DBDep, current_user: CurrentUserDep):
    return _control(db, current_user, session_id, "start")


@router.post("/ui-recordings/{session_id}/pause")
def pause_recording(session_id: int, db: DBDep, current_user: CurrentUserDep):
    return _control(db, current_user, session_id, "pause")


@router.post("/ui-recordings/{session_id}/resume")
def resume_recording(session_id: int, db: DBDep, current_user: CurrentUserDep):
    return _control(db, current_user, session_id, "resume")


@router.post("/ui-recordings/{session_id}/stop")
def stop_recording(session_id: int, db: DBDep, current_user: CurrentUserDep):
    return _control(db, current_user, session_id, "stop")


@router.post("/ui-recordings/{session_id}/cancel")
def cancel_recording(session_id: int, db: DBDep, current_user: CurrentUserDep):
    return _control(db, current_user, session_id, "cancel")


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
