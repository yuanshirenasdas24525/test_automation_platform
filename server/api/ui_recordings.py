"""UI 录制会话、统一事件流和项目元素库 API。"""
from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunsplit

from fastapi import APIRouter, Body, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import String, func
from sqlalchemy.orm import selectinload

from database.models import (
    ALL_UI_ELEMENT_STATUSES,
    ALL_UI_PLATFORMS,
    ALL_UI_RECORDING_STATUSES,
    UI_RECORDING_COMPLETED,
    UI_RECORDING_ROLE_HISTORY,
    UI_RECORDING_ROLE_PRIMARY,
    UI_RECORDING_ROLE_SUPPLEMENT,
    AppPackage,
    Module,
    DEVICE_STATUS_BUSY,
    DEVICE_STATUS_IDLE,
    Device,
    Project,
    TestEnvironment,
    UiDeletionAudit,
    UiElement,
    UiElementOccurrence,
    UiElementLocator,
    UiContextArtifact,
    UiContextEvent,
    UiContextSession,
    UiPageSnapshot,
    UiPageTransition,
    UiRecordedAction,
    UiMockExchange,
    UiRecordingEvent,
    UiRecordingSession,
    UiStepContextLink,
    TestStepReport,
)
from database.schemas.ui_recording import (
    UiElementLocatorCreate,
    UiElementLocatorUpdate,
    UiElementUpdate,
    UiPageSnapshotPickRequest,
    UiPageSnapshotUpdate,
    UiRecordingControlRequest,
    UiRecordingBaselineUpdate,
    UiRecordingCreate,
    UiRecordingEventCreate,
    UiRecordingEventBatchCreate,
    UiRecordingExplorationRequest,
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
    materialize_snapshot_element,
    serialize_context_artifact,
    serialize_context_session,
    serialize_element,
    serialize_event,
    serialize_session,
    serialize_snapshot,
    serialize_page_transition,
    serialize_recorded_action,
    suggest_snapshot_modules,
    update_control_lease,
    validate_control_action,
)
from server.services.ui_recorder_agent_client import (
    RecorderAgentError,
    control_agent_session,
    mobile_preflight as get_mobile_preflight,
    get_web_replay,
    get_web_exploration,
    get_web_replay_screenshot,
    get_mobile_live_screenshot,
    perform_mobile_action as perform_mobile_agent_action,
    perform_web_action as perform_web_agent_action,
    perform_web_replay_action,
    pull_agent_events,
    set_agent_pick_mode,
    start_web_replay,
    start_web_exploration,
    stop_web_exploration,
    stop_web_replay,
    validate_web_replay_locator,
    start_mobile_session,
    start_web_session,
)

router = APIRouter(tags=["ui-recordings"])
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_UI_ARTIFACT_ROOT = (_PROJECT_ROOT / "data" / "ui_recordings").resolve()
_AI_EXPLORATION_TERMINAL_STATUSES = {"completed", "cancelled", "failed"}
_AGENT_EVENT_PULL_BATCH_SIZE = 500
_AGENT_EVENT_PULL_MAX_BATCHES = 200
_AI_EXPLORATION_SEED_LIMIT = 200
_AI_EXPLORATION_IGNORED_QUERY_KEYS = {
    "_", "cache", "cachebuster", "nonce", "timestamp", "ts",
}
_AI_EXPLORATION_SENSITIVE_QUERY_KEY = re.compile(
    r"password|passwd|secret|credential|authorization|cookie|token|signature|card|cvv|cvc",
    re.IGNORECASE,
)


def _merge_ai_exploration_seed_urls(
    *,
    base_url: str,
    requested_urls: list[str],
    known_urls: list[str],
    allowed_hosts: list[str],
) -> list[str]:
    """合并人工种子与项目已知路由，并移除历史 URL 中的敏感查询参数。"""
    parsed_base = urlparse(base_url)
    allowed = {
        item.strip().lower()
        for item in allowed_hosts
        if item.strip()
    }
    if parsed_base.netloc:
        allowed.add(parsed_base.netloc.lower())

    merged: list[str] = []
    seen: set[str] = set()

    def append(value: str) -> None:
        seed = value.strip()
        if not seed:
            return
        absolute = urljoin(base_url, seed)
        key = absolute.split("#", 1)[0]
        if key in seen or len(merged) >= _AI_EXPLORATION_SEED_LIMIT:
            return
        seen.add(key)
        merged.append(seed)

    for value in requested_urls:
        append(str(value))

    for value in known_urls:
        parsed = urlparse(str(value).strip())
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in allowed:
            continue
        query = urlencode([
            (key, item_value)
            for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in _AI_EXPLORATION_IGNORED_QUERY_KEYS
            and not key.lower().startswith("utm_")
            and not _AI_EXPLORATION_SENSITIVE_QUERY_KEY.search(key)
        ])
        append(urlunsplit(("", "", parsed.path or "/", query, "")))
    return merged


def _project_ai_exploration_seed_urls(
    db: DBDep,
    session: UiRecordingSession,
    *,
    requested_urls: list[str],
    allowed_hosts: list[str],
) -> list[str]:
    """读取项目已完成录制中的页面清单，作为 AI 探索的自动路由种子。"""
    rows = (
        db.session.query(UiPageSnapshot.url)
        .join(UiRecordingSession, UiRecordingSession.id == UiPageSnapshot.session_id)
        .filter(
            UiPageSnapshot.project_id == session.project_id,
            UiPageSnapshot.platform == "web",
            UiPageSnapshot.url.isnot(None),
            UiRecordingSession.status == UI_RECORDING_COMPLETED,
        )
        .order_by(UiPageSnapshot.created_at.desc(), UiPageSnapshot.id.desc())
        .limit(2000)
        .all()
    )
    known_urls = [str(row[0]) for row in rows if row and row[0]]
    return _merge_ai_exploration_seed_urls(
        base_url=str(session.source_url or ""),
        requested_urls=requested_urls,
        known_urls=known_urls,
        allowed_hosts=allowed_hosts,
    )


def _get_primary_recording(
    db: DBDep,
    project_id: int,
    platform: str,
    *,
    for_update: bool = False,
) -> UiRecordingSession | None:
    """读取项目平台当前唯一的主录制基线。"""
    query = db.session.query(UiRecordingSession).filter(
        UiRecordingSession.project_id == project_id,
        UiRecordingSession.platform == platform,
        UiRecordingSession.recording_role == UI_RECORDING_ROLE_PRIMARY,
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def _get_recording_baseline(
    db: DBDep,
    session: UiRecordingSession,
    *,
    for_update: bool = False,
    allow_excluded: bool = False,
) -> UiRecordingSession:
    """把主会话或已合并补充会话解析到同一个主基线。"""
    if session.recording_role == UI_RECORDING_ROLE_PRIMARY:
        return session
    if (
        session.recording_role == UI_RECORDING_ROLE_SUPPLEMENT
        and session.baseline_session_id
        and (session.baseline_included or allow_excluded)
    ):
        query = db.session.query(UiRecordingSession).filter(
            UiRecordingSession.id == session.baseline_session_id,
            UiRecordingSession.recording_role == UI_RECORDING_ROLE_PRIMARY,
        )
        if for_update:
            query = query.with_for_update()
        baseline = query.first()
        if baseline is not None:
            return baseline
    return session


def _baseline_source_sessions(
    db: DBDep,
    baseline: UiRecordingSession,
) -> list[UiRecordingSession]:
    """返回主基线离线包所包含的原始录制，原始证据本身保持不可变。"""
    if baseline.recording_role != UI_RECORDING_ROLE_PRIMARY:
        return [baseline]
    supplements = (
        db.session.query(UiRecordingSession)
        .filter(
            UiRecordingSession.baseline_session_id == baseline.id,
            UiRecordingSession.recording_role == UI_RECORDING_ROLE_SUPPLEMENT,
            UiRecordingSession.baseline_included.is_(True),
            UiRecordingSession.status == "completed",
        )
        .order_by(UiRecordingSession.merged_at.desc(), UiRecordingSession.id.desc())
        .all()
    )
    return [baseline, *supplements]


def _start_snapshot_pick_replay(
    snapshot: UiPageSnapshot,
    session: UiRecordingSession,
) -> dict:
    """启动或复用完成态页面的只读分析回放。"""
    viewport = dict((snapshot.environment or {}).get("viewport") or {})
    return start_web_replay(
        session.id,
        browser="chromium",
        headless=True,
        entry_url=snapshot.url,
        page_fingerprint=snapshot.fingerprint,
        viewport={
            "width": int(viewport.get("width") or 1440),
            "height": int(viewport.get("height") or 900),
        },
        reuse_key=f"snapshot-pick:frozen:{snapshot.id}:{snapshot.fingerprint}",
        freeze_dom=True,
    )


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


def _record_deletion_audit(
    db: DBDep,
    current_user,
    *,
    project_id: int,
    object_type: str,
    object_id: str,
    object_name: str | None,
    cascade_scope: dict,
) -> UiDeletionAudit:
    """删除事实数据前，在同一事务写入不可变审计。"""
    audit = UiDeletionAudit(
        project_id=project_id,
        operator_id=current_user.id,
        operator_name=str(current_user.username or f"user#{current_user.id}")[:128],
        object_type=object_type,
        object_id=object_id[:255],
        object_name=(object_name or "")[:255] or None,
        cascade_scope=cascade_scope,
        deleted_at=datetime.now(),
    )
    db.session.add(audit)
    return audit


def _delete_recording_artifacts(session_id: int) -> bool:
    """只删除已解析到 UI 录制根目录下的精确会话目录。"""
    session_root = (_UI_ARTIFACT_ROOT / f"session_{session_id}").resolve()
    if session_root.parent != _UI_ARTIFACT_ROOT:
        raise RuntimeError("录制制品目录越界，拒绝删除")
    if not session_root.exists():
        return False
    if not session_root.is_dir() or session_root.is_symlink():
        raise RuntimeError("录制制品路径不是安全目录，拒绝删除")
    shutil.rmtree(session_root)
    return True


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
    """分批排空宿主机 Agent 的增量事件并落库。

    串行化并发拉取：前端会高频并发轮询 GET /events，每次都会走到这里往
    ui_recording_events 落库。多个 pull 同时 append 同一批 Agent 事件会造成唯一键
    冲突 / 死锁（psycopg2 DeadlockDetected → 500）。这里先用 `FOR UPDATE SKIP LOCKED`
    抢会话行：抢不到说明已有一个 pull 在落库，本次直接跳过（事件会由那个 pull 或
    下一次轮询取回），既避免死锁也不排队堆积。
    """
    locked = (
        db.session.query(UiRecordingSession.id)
        .filter(UiRecordingSession.id == session.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if locked is None:
        return 0
    after_sequence = int(
        db.session.query(func.coalesce(func.max(UiRecordingEvent.sequence_no), 0))
        .filter(UiRecordingEvent.session_id == session.id)
        .scalar()
        or 0
    )
    total_created = 0
    for _batch_index in range(_AGENT_EVENT_PULL_MAX_BATCHES):
        try:
            payloads = pull_agent_events(
                session.id,
                after_sequence,
                limit=_AGENT_EVENT_PULL_BATCH_SIZE,
            )
        except RecorderAgentError as exc:
            session.capabilities = {
                **(session.capabilities or {}),
                "recorder_agent_connected": False,
            }
            session.error = str(exc)
            if strict:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return total_created
        if not payloads:
            break
        try:
            items = [UiRecordingEventCreate.model_validate(item) for item in payloads]
            created, _skipped = append_events(db.session, session, items)
        except (ValueError, UiRecordingTransitionError) as exc:
            if strict:
                raise HTTPException(status_code=422, detail=f"Agent 事件格式错误：{exc}") from exc
            session.error = f"Agent 事件格式错误：{exc}"
            return total_created
        total_created += len(created)
        batch_max_sequence = max(
            (
                int(item.sequence_no)
                for item in items
                if item.sequence_no is not None
            ),
            default=max(
                (int(item.sequence_no) for item in created),
                default=after_sequence,
            ),
        )
        if batch_max_sequence <= after_sequence:
            session.error = "Recorder Agent 返回了无法推进的事件批次"
            if strict:
                raise HTTPException(status_code=422, detail=session.error)
            return total_created
        after_sequence = batch_max_sequence
        if len(payloads) < _AGENT_EVENT_PULL_BATCH_SIZE:
            break
    else:
        session.error = "Recorder Agent 待同步事件过多，请稍后重试"
        if strict:
            raise HTTPException(status_code=503, detail=session.error)
        return total_created
    session.capabilities = {
        **(session.capabilities or {}),
        "recorder_agent_connected": True,
    }
    session.error = None
    return total_created


@router.post("/ui-recordings")
def create_recording(
    body: UiRecordingCreate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    project = (
        db.session.query(Project)
        .filter(Project.id == body.project_id)
        .with_for_update()
        .first()
    )
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

    primary = _get_primary_recording(db, project.id, body.platform, for_update=True)
    recording_role = body.recording_role
    if recording_role == "auto":
        recording_role = (
            UI_RECORDING_ROLE_PRIMARY
            if primary is None
            else UI_RECORDING_ROLE_SUPPLEMENT
        )
    if recording_role == UI_RECORDING_ROLE_PRIMARY and primary is not None:
        raise HTTPException(status_code=409, detail="当前平台已有主录制，请使用补充录制或提升历史会话")
    baseline = None
    if recording_role == UI_RECORDING_ROLE_SUPPLEMENT:
        baseline = primary
        if body.baseline_session_id is not None:
            baseline = db.session.get(UiRecordingSession, body.baseline_session_id)
        if (
            baseline is None
            or baseline.project_id != project.id
            or baseline.platform != body.platform
            or baseline.recording_role != UI_RECORDING_ROLE_PRIMARY
        ):
            raise HTTPException(status_code=422, detail="补充录制必须绑定当前项目平台的主录制")

    session = UiRecordingSession(
        project_id=project.id,
        platform=body.platform,
        name=body.name.strip(),
        recording_role=recording_role,
        baseline_session_id=baseline.id if baseline is not None else None,
        # 单一主线：补充录制默认即合入主线，无需人工"合并"（历史仍可在主线卡片里回看）
        baseline_included=recording_role in (UI_RECORDING_ROLE_PRIMARY, UI_RECORDING_ROLE_SUPPLEMENT),
        baseline_version=baseline.baseline_version if baseline is not None else 1,
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


@router.post("/ui-recordings/{session_id}/baseline")
def update_recording_baseline(
    session_id: int,
    body: UiRecordingBaselineUpdate,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """合并、移出补充录制，或把历史录制提升为新的主基线。"""
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if session.status != "completed":
        raise HTTPException(status_code=409, detail="只有已完成录制才能维护主基线")
    offline = dict((session.capabilities or {}).get("offline_replay") or {})
    if session.platform == "web" and not offline.get("ready"):
        raise HTTPException(status_code=409, detail="当前录制没有可用的离线包")

    if body.action in {"include", "exclude"}:
        if session.recording_role != UI_RECORDING_ROLE_SUPPLEMENT:
            raise HTTPException(status_code=409, detail="只有补充录制才能加入或移出主基线")
        baseline = _get_recording_baseline(
            db,
            session,
            for_update=True,
            allow_excluded=True,
        )
        if baseline.id == session.id:
            raise HTTPException(status_code=409, detail="补充录制绑定的主基线不存在")
        included = body.action == "include"
        if session.baseline_included != included:
            session.baseline_included = included
            baseline.baseline_version = max(1, int(baseline.baseline_version or 1)) + 1
            session.baseline_version = baseline.baseline_version
            session.merged_at = datetime.now() if included else None
    else:
        if session.recording_role == UI_RECORDING_ROLE_PRIMARY:
            raise HTTPException(status_code=409, detail="当前录制已经是主基线")
        old_primary = _get_primary_recording(
            db,
            session.project_id,
            session.platform,
            for_update=True,
        )
        if old_primary is not None and old_primary.id != session.id:
            children = (
                db.session.query(UiRecordingSession)
                .filter(
                    UiRecordingSession.baseline_session_id == old_primary.id,
                    UiRecordingSession.id != session.id,
                )
                .with_for_update()
                .all()
            )
            old_primary.recording_role = UI_RECORDING_ROLE_SUPPLEMENT
            old_primary.baseline_session_id = session.id
            old_primary.baseline_included = True
            old_primary.merged_at = datetime.now()
            db.session.flush()
            for child in children:
                child.baseline_session_id = session.id
        session.recording_role = UI_RECORDING_ROLE_PRIMARY
        session.baseline_session_id = None
        session.baseline_included = True
        session.merged_at = None
        session.baseline_version = max(
            int(session.baseline_version or 1),
            int(old_primary.baseline_version or 1) + 1 if old_primary else 1,
        )

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


@router.delete("/ui-recordings/{session_id}")
def delete_recording(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    confirm: bool = Query(False),
):
    """手动删除终态录制、上下文与物理制品，并保留审计。"""
    if not confirm:
        raise HTTPException(status_code=422, detail="删除录制需要显式 confirm=true")
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if session.status in {"starting", "recording", "paused", "stopping", "processing"}:
        raise HTTPException(status_code=409, detail="录制仍在运行，请先停止或取消后再删除")
    if session.recording_role == UI_RECORDING_ROLE_PRIMARY:
        raise HTTPException(status_code=409, detail="主录制不能直接删除，请先把其他录制设为主录制")
    scope = {
        "events": db.session.query(UiRecordingEvent).filter(UiRecordingEvent.session_id == session.id).count(),
        "snapshots": db.session.query(UiPageSnapshot).filter(UiPageSnapshot.session_id == session.id).count(),
        "mock_exchanges": db.session.query(UiMockExchange).filter(UiMockExchange.session_id == session.id).count(),
        "actions": db.session.query(UiRecordedAction).filter(UiRecordedAction.session_id == session.id).count(),
        "page_transitions": db.session.query(UiPageTransition).filter(UiPageTransition.session_id == session.id).count(),
        "elements_retained": True,
    }
    audit = _record_deletion_audit(
        db,
        current_user,
        project_id=session.project_id,
        object_type="recording_session",
        object_id=str(session.id),
        object_name=session.name,
        cascade_scope=scope,
    )
    db.session.delete(session)
    db.session.flush()
    scope["artifact_directory_deleted"] = _delete_recording_artifacts(session.id)
    audit.cascade_scope = dict(scope)
    db.session.flush()
    return {"status": "success", "data": {"deleted_id": session_id, "cascade_scope": scope}}


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


@router.post("/ui-recordings/{session_id}/exploration/start")
def start_recording_ai_exploration(
    session_id: int,
    body: UiRecordingExplorationRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """在当前已登录的 Web 录制上下文中启动安全探索。"""
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if session.platform != "web":
        raise HTTPException(status_code=422, detail="AI 探索首期只支持 Web")
    if session.status != "recording":
        raise HTTPException(status_code=409, detail="请先启动 Web 录制，再开始 AI 探索")
    duplicated = _lease_or_409(
        session,
        body.client_instance_id,
        body.command_id,
    )
    if duplicated:
        current = dict((session.capabilities or {}).get("ai_exploration") or {})
        return {"status": "success", "data": current}
    exploration_config = body.model_dump(
        exclude={"client_instance_id", "command_id"},
    )
    exploration_config["seed_urls"] = _project_ai_exploration_seed_urls(
        db,
        session,
        requested_urls=list(body.seed_urls),
        allowed_hosts=list(body.allowed_hosts),
    )
    try:
        status = start_web_exploration(
            session.id,
            exploration_config,
        )
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    session.capture_config = {
        **(session.capture_config or {}),
        "ai_exploration": True,
        "ai_exploration_config": exploration_config,
        "ai_exploration_context": {
            "screen": bool((session.capture_config or {}).get("screen", True)),
            "console": bool((session.capture_config or {}).get("console", True)),
            "network": bool((session.capture_config or {}).get("network", True)),
            "user_events": bool((session.capture_config or {}).get("user_events", True)),
            "environment": bool((session.capture_config or {}).get("environment", True)),
            "elements": True,
            "offline_business_replay": True,
        },
    }
    session.capabilities = {
        **(session.capabilities or {}),
        "ai_exploration": status,
    }
    db.session.flush()
    return {"status": "success", "data": status}


@router.get("/ui-recordings/{session_id}/exploration")
def get_recording_ai_exploration(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """同步 AI 探索进度；终态时自动完成录制并生成离线包。"""
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if session.platform != "web":
        raise HTTPException(status_code=422, detail="AI 探索首期只支持 Web")
    try:
        status = get_web_exploration(session.id)
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _pull_agent_events(db, session, strict=False)
    session.capabilities = {
        **(session.capabilities or {}),
        "ai_exploration": status,
    }
    recording_session = serialize_session(db.session, session)
    if (
        status.get("status") in _AI_EXPLORATION_TERMINAL_STATUSES
        and session.status in {"recording", "paused"}
    ):
        stopped = _control(db, current_user, session.id, "stop")
        recording_session = stopped["data"]
    db.session.flush()
    return {
        "status": "success",
        "data": {
            **status,
            "recording_session": recording_session,
        },
    }


@router.post("/ui-recordings/{session_id}/exploration/stop")
def stop_recording_ai_exploration(
    session_id: int,
    body: UiRecordingControlRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """停止 AI 探索，已发现页面仍会进入本次补充录制。"""
    session = _get_session_or_404(db, session_id, for_update=True)
    assert_project_access(db, current_user, session.project_id)
    if session.platform != "web" or session.status not in {"recording", "paused"}:
        raise HTTPException(status_code=409, detail="当前会话没有可停止的 AI 探索")
    duplicated = _lease_or_409(
        session,
        body.client_instance_id,
        body.command_id,
    )
    if duplicated:
        return {
            "status": "success",
            "data": dict((session.capabilities or {}).get("ai_exploration") or {}),
        }
    try:
        status = stop_web_exploration(session.id)
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    session.capabilities = {
        **(session.capabilities or {}),
        "ai_exploration": status,
    }
    db.session.flush()
    return {"status": "success", "data": status}


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
    baseline = _get_recording_baseline(db, session)
    sources = _baseline_source_sessions(db, baseline)
    source_ids = [item.id for item in sources]
    offline = dict((baseline.capabilities or {}).get("offline_replay") or {})
    if baseline.status != "completed" or not offline.get("ready"):
        raise HTTPException(status_code=409, detail="当前会话尚未生成可用的离线回放包")
    if body.entry_url or body.page_fingerprint:
        matched = any(
            (body.entry_url is None or snapshot.url == body.entry_url)
            and (body.page_fingerprint is None or snapshot.fingerprint == body.page_fingerprint)
            and (
                body.page_source_session_id is None
                or snapshot.session_id == body.page_source_session_id
            )
            for source in sources
            for snapshot in source.snapshots
        )
        if not matched:
            raise HTTPException(status_code=422, detail="指定页面状态不属于当前主录制基线")
    try:
        replay = start_web_replay(
            baseline.id,
            browser=body.browser,
            headless=body.headless,
            entry_url=body.entry_url,
            page_fingerprint=body.page_fingerprint,
            page_source_session_id=body.page_source_session_id,
            viewport=body.viewport,
            source_session_ids=source_ids,
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
    replay_state = _assert_replay_session(replay_id, session.id)
    try:
        data = perform_web_replay_action(
            replay_id,
            body.model_dump(exclude={"snapshot_id"}),
        )
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if body.action == "pick" and body.snapshot_id is not None:
        allowed_source_ids = {
            int(item)
            for item in (
                replay_state.get("source_session_ids")
                or [replay_state.get("session_id")]
            )
            if item is not None
        }
        snapshot = db.session.get(UiPageSnapshot, body.snapshot_id)
        if (
            snapshot is None
            or snapshot.project_id != session.project_id
            or snapshot.platform != "web"
            or snapshot.session_id not in allowed_source_ids
        ):
            raise HTTPException(status_code=422, detail="元素定位对应的页面状态无效")
        current_url = str(data.get("url") or "") if isinstance(data, dict) else ""
        if current_url and snapshot.url != current_url:
            matched_snapshot = (
                db.session.query(UiPageSnapshot)
                .filter(
                    UiPageSnapshot.project_id == session.project_id,
                    UiPageSnapshot.platform == "web",
                    UiPageSnapshot.session_id.in_(allowed_source_ids),
                    UiPageSnapshot.url == current_url,
                )
                .order_by(UiPageSnapshot.created_at.desc(), UiPageSnapshot.id.desc())
                .first()
            )
            if matched_snapshot is not None:
                snapshot = matched_snapshot
        source_session = db.session.get(UiRecordingSession, snapshot.session_id)
        raw_element = data.get("element") if isinstance(data, dict) else None
        if source_session is None or not isinstance(raw_element, dict):
            raise HTTPException(status_code=422, detail="该坐标没有可定位元素")
        try:
            element = materialize_snapshot_element(
                db.session,
                source_session,
                snapshot,
                raw_element,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        data["element"] = serialize_element(element)
        data["element_snapshot_id"] = snapshot.id
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


@router.get("/ui-recordings/{session_id}/screenshot")
def get_recording_live_screenshot(
    session_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """移动录制会话的实时截图（镜像实时刷新用）——绕过去重存档，直接取当前画面。"""
    session = _get_session_or_404(db, session_id)
    assert_project_access(db, current_user, session.project_id)
    if session.platform not in {"android", "ios"}:
        raise HTTPException(status_code=422, detail="仅移动端录制支持实时截图")
    try:
        content = get_mobile_live_screenshot(session.id)
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ui-recordings/snapshots/{snapshot_id}/document")
def get_snapshot_document(
    snapshot_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """返回该快照采集的 UI Tree 原始文档（移动=Appium page_source XML）。供前端渲染成可点选的元素树。"""
    snapshot = db.session.get(UiPageSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    assert_project_access(db, current_user, snapshot.project_id)
    if not snapshot.document_uri:
        raise HTTPException(status_code=404, detail="该快照没有采集到 UI Tree")
    raw_path = Path(snapshot.document_uri)
    path = raw_path if raw_path.is_absolute() else _PROJECT_ROOT / raw_path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="UI Tree 文档已丢失") from exc
    return Response(content=text, media_type="application/xml", headers={"Cache-Control": "no-store"})


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
    offset: int = Query(0, ge=0),
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
        query.order_by(UiElement.page_name, UiElement.semantic_name, UiElement.id)
        .offset(offset)
        .limit(limit)
        .all()
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


@router.post("/ui-page-snapshots/{snapshot_id}/prepare")
def prepare_ui_page_snapshot_pick(
    snapshot_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """后台预热完成态页面的只读分析环境，避免首次点击等待。"""
    snapshot = db.session.get(UiPageSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="页面快照不存在")
    assert_project_access(db, current_user, snapshot.project_id)
    if snapshot.platform != "web" or not snapshot.url:
        raise HTTPException(status_code=409, detail="当前页面快照不支持 Web 只读拾取")
    session = db.session.get(UiRecordingSession, snapshot.session_id)
    if session is None or session.status != "completed":
        raise HTTPException(status_code=409, detail="录制完成后才能预热只读拾取")
    try:
        replay = _start_snapshot_pick_replay(snapshot, session)
    except RecorderAgentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "success",
        "data": {
            "ready": True,
            "reused": bool(replay.get("reused")),
        },
    }


@router.post("/ui-page-snapshots/{snapshot_id}/pick")
def pick_ui_page_snapshot_element(
    snapshot_id: int,
    body: UiPageSnapshotPickRequest,
    db: DBDep,
    current_user: CurrentUserDep,
):
    """在完成态截图上只读拾取元素，不执行页面点击。"""
    snapshot = db.session.get(UiPageSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="页面快照不存在")
    assert_project_access(db, current_user, snapshot.project_id)
    if snapshot.platform != "web" or not snapshot.url:
        raise HTTPException(status_code=409, detail="当前页面快照不支持 Web 只读拾取")
    session = db.session.get(UiRecordingSession, snapshot.session_id)
    if session is None or session.status != "completed":
        raise HTTPException(status_code=409, detail="录制完成后才能在快照中只读拾取")

    replay_id = ""
    result: dict = {}
    for attempt in range(2):
        try:
            replay = _start_snapshot_pick_replay(snapshot, session)
            replay_id = str(replay.get("replay_id") or "")
            result = perform_web_replay_action(
                replay_id,
                {"action": "pick", "x": body.x, "y": body.y},
            )
            break
        except RecorderAgentError as exc:
            # Agent 重启或缓存页异常时丢弃旧实例并自动重建一次。
            if replay_id:
                try:
                    stop_web_replay(replay_id)
                except RecorderAgentError:
                    pass
                replay_id = ""
            if attempt == 1:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not result:
        if replay_id:
            try:
                stop_web_replay(replay_id)
            except RecorderAgentError:
                pass
        raise HTTPException(status_code=503, detail="页面只读分析没有返回结果")

    raw_element = result.get("element") if isinstance(result, dict) else None
    if not isinstance(raw_element, dict):
        raise HTTPException(status_code=422, detail="该坐标没有可拾取元素")
    try:
        element = materialize_snapshot_element(
            db.session,
            session,
            snapshot,
            raw_element,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "success", "data": serialize_element(element)}


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


@router.delete("/ui-page-groups")
def delete_ui_page_group(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Query(..., gt=0),
    platform: str = Query(...),
    page_key: str = Query(..., min_length=1, max_length=255),
    confirm: bool = Query(False),
):
    """删除一个逻辑页面的全部状态和元素事实。"""
    if not confirm:
        raise HTTPException(status_code=422, detail="删除页面需要显式 confirm=true")
    assert_project_access(db, current_user, project_id)
    if platform not in ALL_UI_PLATFORMS:
        raise HTTPException(status_code=422, detail="platform 必须是 web/android/ios")
    snapshots = db.session.query(UiPageSnapshot).filter(
        UiPageSnapshot.project_id == project_id,
        UiPageSnapshot.platform == platform,
        UiPageSnapshot.page_key == page_key,
    ).all()
    elements = db.session.query(UiElement).filter(
        UiElement.project_id == project_id,
        UiElement.platform == platform,
        UiElement.page_key == page_key,
    ).all()
    if not snapshots and not elements:
        raise HTTPException(status_code=404, detail="页面事实不存在")
    element_ids = [item.id for item in elements]
    occurrence_count = (
        db.session.query(UiElementOccurrence)
        .filter(UiElementOccurrence.element_id.in_(element_ids))
        .count()
        if element_ids else 0
    )
    scope = {
        "snapshots": len(snapshots),
        "elements": len(elements),
        "occurrences": occurrence_count,
        "shared_session_resources_retained": True,
    }
    _record_deletion_audit(
        db,
        current_user,
        project_id=project_id,
        object_type="page_group",
        object_id=f"{platform}:{page_key}",
        object_name=(snapshots[0].page_name if snapshots else elements[0].page_name),
        cascade_scope=scope,
    )
    for element in elements:
        db.session.delete(element)
    for snapshot in snapshots:
        db.session.delete(snapshot)
    db.session.flush()
    return {"status": "success", "data": {"page_key": page_key, "cascade_scope": scope}}


def _get_snapshot_or_404(db: DBDep, snapshot_id: int) -> UiPageSnapshot:
    snapshot = db.session.get(UiPageSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="画面不存在")
    return snapshot


@router.patch("/ui-page-snapshots/{snapshot_id}/module")
def set_snapshot_module(
    snapshot_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    module_id: int | None = Body(None, embed=True),
):
    """手动指派/清除画面的归属模块（module_id=null 即"未分类"）。"""
    snapshot = _get_snapshot_or_404(db, snapshot_id)
    assert_project_access(db, current_user, snapshot.project_id)
    if module_id is not None:
        module = db.session.get(Module, module_id)
        if module is None or module.project_id != snapshot.project_id:
            raise HTTPException(status_code=422, detail="模块不存在或不属于当前项目")
    snapshot.module_id = module_id
    db.session.flush()
    return {"status": "success", "data": serialize_snapshot(snapshot)}


@router.post("/ui-page-snapshots/auto-classify")
def auto_classify_snapshots(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Query(..., gt=0),
    platform: str = Query(...),
    only_unclassified: bool = Query(True),
):
    """按"用例引用 + 名称关键词"给画面自动建议归属模块。
    only_unclassified=true 只填未分类的画面，不覆盖用户手工指派。"""
    assert_project_access(db, current_user, project_id)
    if platform not in ALL_UI_PLATFORMS:
        raise HTTPException(status_code=422, detail="platform 必须是 web/android/ios")
    suggestions = suggest_snapshot_modules(db.session, project_id=project_id, platform=platform)
    snapshots = db.session.query(UiPageSnapshot).filter(
        UiPageSnapshot.project_id == project_id,
        UiPageSnapshot.platform == platform,
    ).all()
    applied = 0
    for snapshot in snapshots:
        mid = suggestions.get(snapshot.id)
        if mid is None:
            continue
        if only_unclassified and snapshot.module_id is not None:
            continue
        if snapshot.module_id != mid:
            snapshot.module_id = mid
            applied += 1
    db.session.flush()
    return {"status": "success", "data": {"suggested": len(suggestions), "applied": applied}}


@router.delete("/ui-page-snapshots/{snapshot_id}")
def delete_snapshot(
    snapshot_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    confirm: bool = Query(False),
):
    """删除单个画面（快照）及其"独有元素"——只在这一屏出现过的元素才删，跨屏共享的保留
    （仅移除其在本屏的出现记录）。"""
    if not confirm:
        raise HTTPException(status_code=422, detail="删除画面需要显式 confirm=true")
    snapshot = _get_snapshot_or_404(db, snapshot_id)
    assert_project_access(db, current_user, snapshot.project_id)
    # 本屏出现的元素里，"所有出现都在本屏"的即独有元素
    element_ids = [
        row[0] for row in db.session.query(UiElementOccurrence.element_id)
        .filter(UiElementOccurrence.snapshot_id == snapshot_id).distinct().all()
    ]
    exclusive_ids: list[int] = []
    if element_ids:
        rows = (
            db.session.query(UiElementOccurrence.element_id)
            .filter(UiElementOccurrence.element_id.in_(element_ids))
            .group_by(UiElementOccurrence.element_id)
            .having(func.count(func.distinct(UiElementOccurrence.snapshot_id)) == 1)
            .all()
        )
        exclusive_ids = [row[0] for row in rows]
    scope = {
        "snapshot_id": snapshot_id,
        "exclusive_elements_deleted": len(exclusive_ids),
        "shared_elements_retained": len(element_ids) - len(exclusive_ids),
    }
    _record_deletion_audit(
        db,
        current_user,
        project_id=snapshot.project_id,
        object_type="page_snapshot",
        object_id=str(snapshot_id),
        object_name=snapshot.page_name,
        cascade_scope=scope,
    )
    if exclusive_ids:
        for element in db.session.query(UiElement).filter(UiElement.id.in_(exclusive_ids)).all():
            db.session.delete(element)
    db.session.delete(snapshot)
    db.session.flush()
    return {"status": "success", "data": {"snapshot_id": snapshot_id, "cascade_scope": scope}}


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


@router.delete("/ui-elements/{element_id}")
def delete_ui_element(
    element_id: int,
    db: DBDep,
    current_user: CurrentUserDep,
    confirm: bool = Query(False),
):
    """彻底删除元素、定位器与出现证据；录制动作保留但解除元素引用。"""
    if not confirm:
        raise HTTPException(status_code=422, detail="删除元素需要显式 confirm=true")
    element = _get_element_or_404(db, element_id)
    assert_project_access(db, current_user, element.project_id)
    scope = {
        "locators": len(element.locators),
        "occurrences": db.session.query(UiElementOccurrence).filter(
            UiElementOccurrence.element_id == element.id,
        ).count(),
        "recording_events_released": db.session.query(UiRecordingEvent).filter(
            UiRecordingEvent.element_id == element.id,
        ).count(),
        "recorded_actions_released": db.session.query(UiRecordedAction).filter(
            UiRecordedAction.target_element_id == element.id,
        ).count(),
        "copied_step_locators_retained": True,
    }
    _record_deletion_audit(
        db,
        current_user,
        project_id=element.project_id,
        object_type="ui_element",
        object_id=str(element.id),
        object_name=element.semantic_name,
        cascade_scope=scope,
    )
    db.session.delete(element)
    db.session.flush()
    return {"status": "success", "data": {"deleted_id": element_id, "cascade_scope": scope}}


@router.get("/ui-deletion-audits")
def list_ui_deletion_audits(
    db: DBDep,
    current_user: CurrentUserDep,
    project_id: int = Query(..., gt=0),
    limit: int = Query(50, ge=1, le=200),
):
    """查询项目 UI 录制数据删除审计。"""
    assert_project_access(db, current_user, project_id)
    rows = (
        db.session.query(UiDeletionAudit)
        .filter(UiDeletionAudit.project_id == project_id)
        .order_by(UiDeletionAudit.deleted_at.desc(), UiDeletionAudit.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "status": "success",
        "data": [
            {
                "id": row.id,
                "operator_name": row.operator_name,
                "object_type": row.object_type,
                "object_id": row.object_id,
                "object_name": row.object_name,
                "cascade_scope": row.cascade_scope or {},
                "deleted_at": row.deleted_at,
            }
            for row in rows
        ],
    }


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
            viewport = dict((snapshot.environment or {}).get("viewport") or {})
            replay = start_web_replay(
                recording.id,
                browser="chromium",
                headless=True,
                entry_url=snapshot.url,
                page_fingerprint=snapshot.fingerprint,
                viewport={
                    "width": int(viewport.get("width") or 1440),
                    "height": int(viewport.get("height") or 900),
                },
                freeze_dom=True,
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
