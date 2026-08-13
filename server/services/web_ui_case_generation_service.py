"""功能用例与元素事实到 Web UI 自动化草稿的构建、编译和入库。"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from database.models import (
    CASE_TYPE_FUNCTIONAL,
    Module,
    TestCase,
    TestStep,
    UiAutomationCaseDraft,
    UiElement,
    UiMockExchange,
    UiPageSnapshot,
    UiPageTransition,
    UiRecordedAction,
    UiRecordingSession,
    UI_AUTO_DRAFT_ACCEPTED,
    UI_AUTO_DRAFT_PENDING,
    UI_AUTO_DRAFT_REJECTED,
    UI_ELEMENT_ARCHIVED,
    UI_PLATFORM_WEB,
)


_SUPPORTED_LOCATORS = {"css", "xpath", "id", "name", "class", "text", "link"}
_CAPTCHA_MARKERS = {
    "captcha", "slider", "verification", "verify", "puzzle",
    "验证码", "滑块", "人机验证", "安全验证", "拼图",
}
_SENSITIVE_VARIABLE_MARKERS = {"password", "passwd", "pwd", "token", "secret", "cookie", "密码", "令牌", "密钥"}
_SENSITIVE_QUERY_MARKERS = {"password", "passwd", "pwd", "token", "secret", "key", "auth", "session", "code"}
_ALLOWED_ACTIONS = {
    "goto", "click", "input", "select", "wait", "assert_visible",
    "assert_text", "visual_assert", "manual",
}
_SAFE_GENERATED_STEP_TYPES = {
    "web_goto",
    "web_click",
    "web_input",
    "web_select",
    "web_wait",
    "web_assert_text",
    "web_assert_visual",
}
_LOCATOR_STEP_TYPES = {
    "web_click",
    "web_input",
    "web_select",
    "web_wait",
    "web_assert_text",
}


def _as_int(value: Any, default: int = 0) -> int:
    """把不可信的 AI/草稿值安全转成整数。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_url(url: str | None, *, redact_query: bool = False) -> str | None:
    """移除 URL 用户凭据和片段；模型上下文可额外脱敏查询参数。"""
    if not url:
        return None
    parsed = urlsplit(str(url))
    if parsed.scheme not in {"http", "https"}:
        return str(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    query = parsed.query
    if redact_query and query:
        query = urlencode([
            (
                key,
                "${redacted}"
                if any(marker in key.lower() for marker in _SENSITIVE_QUERY_MARKERS)
                else value,
            )
            for key, value in parse_qsl(query, keep_blank_values=True)
        ])
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


def _functional_payload(case: TestCase) -> dict[str, Any]:
    spec = case.functional_spec or {}
    return {
        "id": case.id,
        "module_id": case.module_id,
        "name": case.name,
        "description": case.description or "",
        "priority": case.priority or 2,
        "tags": case.tags or [],
        "preconditions": list(spec.get("preconditions") or []),
        "steps": list(spec.get("steps") or []),
        "expected": spec.get("expected") or "",
    }


def _locator_payload(element: UiElement) -> list[dict[str, Any]]:
    candidates = []
    for locator in element.locators or []:
        strategy = str(locator.strategy or "").lower()
        if strategy not in _SUPPORTED_LOCATORS or not str(locator.locator or "").strip():
            continue
        candidates.append({
            "strategy": strategy,
            "score": int(locator.score or 0),
            "primary": bool(locator.is_primary),
            "unique": locator.is_unique,
            "verified": locator.last_verified_at is not None,
        })
    return candidates[:5]


def build_generation_context(
    db: Session,
    *,
    project_id: int,
    functional_case_ids: list[int],
    page_keys: list[str],
) -> tuple[dict[str, Any], dict[int, UiElement], dict[int, UiPageSnapshot]]:
    """构建脱敏、限量的元素证据图，同时返回编译期 ORM 映射。"""
    functional_cases: list[TestCase] = []
    if functional_case_ids:
        functional_cases = (
            db.query(TestCase)
            .join(Module, TestCase.module_id == Module.id)
            .filter(
                TestCase.id.in_(functional_case_ids),
                TestCase.case_type == CASE_TYPE_FUNCTIONAL,
                Module.project_id == project_id,
            )
            .order_by(TestCase.id)
            .all()
        )
        found = {item.id for item in functional_cases}
        missing = [item for item in functional_case_ids if item not in found]
        if missing:
            raise ValueError(f"功能用例不存在或不属于当前项目：{missing}")

    element_query = (
        db.query(UiElement)
        .options(selectinload(UiElement.locators))
        .filter(
            UiElement.project_id == project_id,
            UiElement.platform == UI_PLATFORM_WEB,
            UiElement.status != UI_ELEMENT_ARCHIVED,
        )
    )
    snapshot_query = db.query(UiPageSnapshot).filter(
        UiPageSnapshot.project_id == project_id,
        UiPageSnapshot.platform == UI_PLATFORM_WEB,
    )
    if page_keys:
        element_query = element_query.filter(UiElement.page_key.in_(page_keys))
        snapshot_query = snapshot_query.filter(UiPageSnapshot.page_key.in_(page_keys))

    elements = element_query.order_by(UiElement.page_key, UiElement.id).limit(600).all()
    if not elements:
        raise ValueError("当前范围没有可用于生成的 Web 元素，请先完成录制或 AI 探索录制")

    snapshots_all = snapshot_query.order_by(
        UiPageSnapshot.page_key,
        UiPageSnapshot.snapshot_version.desc(),
        UiPageSnapshot.id.desc(),
    ).limit(300).all()
    latest_snapshots: dict[str, UiPageSnapshot] = {}
    for snapshot in snapshots_all:
        latest_snapshots.setdefault(snapshot.page_key, snapshot)

    sessions = db.query(UiRecordingSession).filter(
        UiRecordingSession.project_id == project_id,
        UiRecordingSession.platform == UI_PLATFORM_WEB,
        UiRecordingSession.status == "completed",
    ).all()
    source_session_ids = [
        item.id for item in sessions
        if item.recording_role == "primary" or item.baseline_included
    ] or [item.id for item in sessions]

    actions: list[UiRecordedAction] = []
    transitions: list[UiPageTransition] = []
    exchanges: list[UiMockExchange] = []
    if source_session_ids:
        action_query = db.query(UiRecordedAction).filter(
            UiRecordedAction.session_id.in_(source_session_ids),
            UiRecordedAction.status != "ignored",
        )
        transition_query = db.query(UiPageTransition).filter(
            UiPageTransition.project_id == project_id,
            UiPageTransition.platform == UI_PLATFORM_WEB,
            UiPageTransition.session_id.in_(source_session_ids),
        )
        exchange_query = db.query(UiMockExchange).filter(
            UiMockExchange.session_id.in_(source_session_ids),
        )
        if page_keys:
            action_query = action_query.filter(or_(
                UiRecordedAction.page_before_key.in_(page_keys),
                UiRecordedAction.page_after_key.in_(page_keys),
            ))
            transition_query = transition_query.filter(or_(
                UiPageTransition.source_page_key.in_(page_keys),
                UiPageTransition.target_page_key.in_(page_keys),
            ))
            snapshot_ids = [item.id for item in snapshots_all]
            exchange_query = exchange_query.filter(UiMockExchange.snapshot_id.in_(snapshot_ids))
        actions = action_query.order_by(UiRecordedAction.sequence_no).limit(300).all()
        transitions = transition_query.order_by(UiPageTransition.occurred_at).limit(200).all()
        exchanges = exchange_query.order_by(UiMockExchange.sequence_no).limit(150).all()

    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    element_map = {item.id: item for item in elements}
    for element in elements:
        attrs = element.attributes or {}
        by_page[element.page_key].append({
            "element_id": element.id,
            "name": element.semantic_name,
            "page_name": element.page_name,
            "type": element.element_type,
            "status": element.status,
            "text": str(attrs.get("text") or attrs.get("inner_text") or "")[:300],
            "tag": attrs.get("tag"),
            "role": attrs.get("role"),
            "placeholder": attrs.get("placeholder"),
            "input_type": attrs.get("type"),
            "locator_candidates": _locator_payload(element),
        })

    pages = []
    for key, page_elements in by_page.items():
        snapshot = latest_snapshots.get(key)
        pages.append({
            "page_key": key,
            "page_name": snapshot.page_name if snapshot else page_elements[0]["page_name"],
            "url": _safe_url(snapshot.url if snapshot else None, redact_query=True),
            "route": snapshot.route if snapshot else None,
            "snapshot_id": snapshot.id if snapshot else None,
            "visual_baseline_available": bool(snapshot and snapshot.screenshot_uri),
            "is_interactive": bool(snapshot and snapshot.is_interactive),
            "limitations": list(snapshot.limitations or []) if snapshot else [],
            "elements": page_elements,
        })

    context = {
        "project_id": project_id,
        "functional_cases": [_functional_payload(item) for item in functional_cases],
        "pages": pages,
        "recorded_actions": [
            {
                "action": item.action_type,
                "name": item.name,
                "element_id": item.target_element_id,
                "page_before": item.page_before_key,
                "page_after": item.page_after_key,
            }
            for item in actions
        ],
        "page_transitions": [
            {
                "source": item.source_page_key,
                "target": item.target_page_key,
                "action_id": item.action_id,
            }
            for item in transitions
        ],
        "observed_network": [
            {
                "method": item.method,
                "url": _safe_url(item.url, redact_query=True),
                "status": item.response.get("status") if isinstance(item.response, dict) else None,
            }
            for item in exchanges
        ],
    }
    snapshot_map = {item.id: item for item in latest_snapshots.values()}
    return context, element_map, snapshot_map


def _preferred_locator(element: UiElement) -> tuple[str, str] | None:
    candidates = [
        item for item in (element.locators or [])
        if str(item.strategy or "").lower() in _SUPPORTED_LOCATORS
        and str(item.locator or "").strip()
    ]
    if not candidates:
        return None
    selected = max(
        candidates,
        key=lambda item: (
            int(bool(item.is_primary)),
            int(item.is_unique is True),
            int(item.last_verified_at is not None),
            int(item.score or 0),
        ),
    )
    return str(selected.strategy).lower(), str(selected.locator)


def _looks_like_captcha(element: UiElement | None, reason: str = "") -> bool:
    if element is None:
        text = reason.lower()
    else:
        text = " ".join([
            element.semantic_name or "",
            element.element_type or "",
            str((element.attributes or {}).get("text") or ""),
            str((element.attributes or {}).get("role") or ""),
            reason,
        ]).lower()
    return any(marker in text for marker in _CAPTCHA_MARKERS)


def _variable_name(element: UiElement, used: set[str]) -> str:
    aliases = {
        "用户名": "username", "账号": "username", "密码": "password",
        "邮箱": "email", "手机号": "phone", "项目名称": "project_name",
        "验证码": "verification_code",
    }
    raw = element.semantic_name.strip()
    base = next((value for key, value in aliases.items() if key in raw), "")
    if not base:
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", raw).strip("_").lower() or f"input_{element.id}"
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def _is_sensitive_input(element: UiElement, variable: str) -> bool:
    """密码、令牌等字段的默认值永不进入草稿。"""
    attrs = element.attributes or {}
    text = " ".join([
        variable,
        element.semantic_name or "",
        str(attrs.get("type") or ""),
        str(attrs.get("name") or ""),
        str(attrs.get("placeholder") or ""),
    ]).lower()
    return any(marker in text for marker in _SENSITIVE_VARIABLE_MARKERS)


def _step(order: int, name: str, step_type: str, config: dict[str, Any], *, skip: bool = False) -> dict[str, Any]:
    return {
        "step_order": order,
        "step_name": name[:255],
        "step_type": step_type,
        "skip": skip,
        "config": config,
        "extract": [],
        "assertion": [],
        "wait_before": 0,
        "timeout": 30,
        "retry": 0,
        "on_failure": "stop",
    }


def validate_draft_steps(steps: Any, *, allow_manual: bool) -> list[str]:
    """验证待入库步骤仍在 AI 安全白名单内，防止评审 JSON 绕过编译器。"""
    if not isinstance(steps, list) or not steps:
        return ["草稿至少需要一个执行步骤"]
    if len(steps) > 100:
        return ["单条草稿最多允许 100 个步骤"]

    errors: list[str] = []
    has_assertion = False
    for index, item in enumerate(steps, start=1):
        if not isinstance(item, dict):
            errors.append(f"步骤 {index} 必须是对象")
            continue
        step_type = str(item.get("step_type") or "")
        config = item.get("config")
        if step_type not in _SAFE_GENERATED_STEP_TYPES:
            errors.append(f"步骤 {index} 类型 {step_type!r} 不在 AI Web 安全白名单")
            continue
        if not isinstance(config, dict):
            errors.append(f"步骤 {index} 的 config 必须是对象")
            continue

        manual = bool(config.get("manual_intervention"))
        if manual:
            if not allow_manual or step_type != "web_wait" or not bool(item.get("skip")):
                errors.append(f"步骤 {index} 的人工接管标记不合法")
            continue

        if step_type in _LOCATOR_STEP_TYPES:
            by = str(config.get("by") or "").lower()
            locator = str(config.get("locator") or "").strip()
            if by not in _SUPPORTED_LOCATORS or not locator:
                errors.append(f"步骤 {index} 缺少可执行的元素库定位器")
            try:
                element_id = int(config.get("element_id") or 0)
            except (TypeError, ValueError):
                element_id = 0
            if element_id <= 0:
                errors.append(f"步骤 {index} 缺少元素库 element_id 证据")

        if step_type == "web_goto":
            parsed = urlsplit(str(config.get("url") or ""))
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                errors.append(f"步骤 {index} 的页面 URL 非法")
        elif step_type == "web_assert_text":
            if not any(config.get(key) is not None for key in ("equals", "contains", "regex")):
                errors.append(f"步骤 {index} 的文本断言缺少期望值")
            has_assertion = True
        elif step_type == "web_input" and config.get("value") is None:
            errors.append(f"步骤 {index} 的输入值不能为空")
        elif step_type == "web_select" and not any(
            config.get(key) is not None for key in ("value", "label", "index")
        ):
            errors.append(f"步骤 {index} 的选择值不能为空")
        elif step_type == "web_assert_visual":
            baseline = str(config.get("baseline_path") or "")
            if not baseline.startswith("data/ui_recordings/session_"):
                errors.append(f"步骤 {index} 的视觉基线不属于 UI 录制制品")
            has_assertion = True
        elif step_type == "web_wait" and config.get("assertion_kind") == "visible":
            has_assertion = True

    if not has_assertion and not allow_manual:
        errors.append("可执行草稿至少需要一个功能、结构或视觉断言")
    return errors


def validate_draft_step_edit(existing_steps: Any, next_steps: Any) -> list[str]:
    """评审可调业务值和断言，但不能在 JSON 中手改元素定位或视觉证据。"""
    trusted_locators = {
        (
            _as_int(config.get("element_id")),
            str(config.get("by") or ""),
            str(config.get("locator") or ""),
        )
        for item in (existing_steps or [])
        if isinstance(item, dict)
        for config in [item.get("config")]
        if isinstance(config, dict) and config.get("element_id")
    }
    trusted_urls = {
        str((item.get("config") or {}).get("url") or "")
        for item in (existing_steps or [])
        if isinstance(item, dict) and item.get("step_type") == "web_goto"
    }
    trusted_visuals = {
        (
            _as_int(config.get("snapshot_id")),
            str(config.get("baseline_path") or ""),
        )
        for item in (existing_steps or [])
        if isinstance(item, dict) and item.get("step_type") == "web_assert_visual"
        for config in [item.get("config") or {}]
    }

    errors: list[str] = []
    for index, item in enumerate(next_steps or [], start=1):
        if not isinstance(item, dict):
            continue
        config = item.get("config") or {}
        step_type = str(item.get("step_type") or "")
        if step_type in _LOCATOR_STEP_TYPES and not config.get("manual_intervention"):
            try:
                element_id = int(config.get("element_id") or 0)
            except (TypeError, ValueError):
                element_id = 0
            locator_fact = (
                element_id,
                str(config.get("by") or ""),
                str(config.get("locator") or ""),
            )
            if locator_fact not in trusted_locators:
                errors.append(f"步骤 {index} 修改或新增了未经元素库证明的定位器，请返回元素库补录")
        elif step_type == "web_goto" and str(config.get("url") or "") not in trusted_urls:
            errors.append(f"步骤 {index} 修改或新增了未经录制证明的页面 URL")
        elif step_type == "web_assert_visual":
            visual_fact = (
                _as_int(config.get("snapshot_id")),
                str(config.get("baseline_path") or ""),
            )
            if visual_fact not in trusted_visuals:
                errors.append(f"步骤 {index} 修改或新增了未经录制证明的视觉基线")
    return errors


def validate_draft_evidence(db: Session, draft: UiAutomationCaseDraft) -> list[str]:
    """入库前重新确认元素和快照仍属于当前项目且定位器证据仍存在。"""
    steps = list(draft.steps or [])
    element_ids = {
        _as_int((item.get("config") or {}).get("element_id"))
        for item in steps
        if isinstance(item, dict) and (item.get("config") or {}).get("element_id")
    }
    elements = (
        db.query(UiElement)
        .options(selectinload(UiElement.locators))
        .filter(
            UiElement.id.in_(element_ids),
            UiElement.project_id == draft.project_id,
            UiElement.platform == UI_PLATFORM_WEB,
            UiElement.status != UI_ELEMENT_ARCHIVED,
        )
        .all()
        if element_ids else []
    )
    element_map = {item.id: item for item in elements}
    errors: list[str] = []
    for index, item in enumerate(steps, start=1):
        if not isinstance(item, dict):
            continue
        config = item.get("config") or {}
        if item.get("step_type") in _LOCATOR_STEP_TYPES and not config.get("manual_intervention"):
            element_id = _as_int(config.get("element_id"))
            element = element_map.get(element_id)
            if element is None:
                errors.append(f"步骤 {index} 的元素 #{element_id} 已删除、归档或不属于当前项目")
                continue
            locator_fact = (str(config.get("by") or ""), str(config.get("locator") or ""))
            current_facts = {
                (str(locator.strategy or "").lower(), str(locator.locator or ""))
                for locator in element.locators or []
            }
            if locator_fact not in current_facts:
                errors.append(f"步骤 {index} 的元素 #{element_id} 定位器已失效，请重新生成草稿")

        if item.get("step_type") == "web_assert_visual":
            snapshot_id = _as_int(config.get("snapshot_id"))
            snapshot = db.query(UiPageSnapshot).filter(
                UiPageSnapshot.id == snapshot_id,
                UiPageSnapshot.project_id == draft.project_id,
                UiPageSnapshot.platform == UI_PLATFORM_WEB,
            ).one_or_none()
            if snapshot is None or snapshot.screenshot_uri != config.get("baseline_path"):
                errors.append(f"步骤 {index} 的视觉基线证据已失效")
    return errors


def compile_ai_case(
    raw: dict[str, Any],
    *,
    element_map: dict[int, UiElement],
    snapshot_map: dict[int, UiPageSnapshot],
    include_structure_assertions: bool,
    include_visual_assertions: bool,
    visual_threshold: float,
) -> dict[str, Any] | None:
    """把 AI 的 element_id 动作计划编译成 Runner 步骤，拒绝原始定位器。"""
    title = str(raw.get("title") or "").strip()
    raw_steps = raw.get("steps") or []
    if not title or not isinstance(raw_steps, list):
        return None

    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    manual_reasons: list[str] = []
    raw_variables = dict(raw.get("variables") or {}) if isinstance(raw.get("variables"), dict) else {}
    variables = {
        str(key): (
            ""
            if any(marker in str(key).lower() for marker in _SENSITIVE_VARIABLE_MARKERS)
            else value if isinstance(value, (str, int, float, bool)) or value is None else ""
        )
        for key, value in raw_variables.items()
    }
    used_variables = set(variables)
    evidence_elements: set[int] = set()
    evidence_pages: set[str] = set()
    evidence_snapshots: set[int] = set()

    def append(name: str, step_type: str, config: dict[str, Any], *, skip: bool = False) -> None:
        steps.append(_step(len(steps) + 1, name, step_type, config, skip=skip))

    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict):
            warnings.append(f"AI 步骤 {index} 不是对象，已忽略")
            continue
        action = str(item.get("action") or "").lower()
        if action not in _ALLOWED_ACTIONS:
            warnings.append(f"AI 步骤 {index} 使用未知动作 {action!r}，已忽略")
            continue

        if action == "goto":
            page_key = str(item.get("page_key") or "")
            url = next(
                (_safe_url(snapshot.url) for snapshot in snapshot_map.values() if snapshot.page_key == page_key and snapshot.url),
                None,
            )
            if not url:
                warnings.append(f"页面 {page_key or '?'} 缺少已录制 URL，无法生成打开步骤")
                continue
            evidence_pages.add(page_key)
            append(f"打开 {item.get('name') or page_key}", "web_goto", {"url": url})
            continue

        if action == "visual_assert":
            if not include_visual_assertions:
                continue
            snapshot_id = _as_int(item.get("snapshot_id"))
            snapshot = snapshot_map.get(snapshot_id)
            if snapshot is None or not snapshot.screenshot_uri:
                warnings.append(f"视觉断言引用的快照 #{snapshot_id or '?'} 没有基准截图")
                continue
            evidence_snapshots.add(snapshot.id)
            evidence_pages.add(snapshot.page_key)
            append(
                f"视觉回归：{snapshot.page_name}",
                "web_assert_visual",
                {
                    "baseline_path": snapshot.screenshot_uri,
                    "threshold": visual_threshold,
                    "pixel_tolerance": 24,
                    "snapshot_id": snapshot.id,
                },
            )
            continue

        if action == "manual":
            reason = str(item.get("reason") or "需要人工处理").strip()
            manual_reasons.append(reason)
            append(f"人工接管：{reason}", "web_wait", {"seconds": 0, "manual_intervention": True, "reason": reason}, skip=True)
            continue

        try:
            element_id = int(item.get("element_id") or 0)
        except (TypeError, ValueError):
            element_id = 0
        element = element_map.get(element_id)
        if element is None:
            reason = f"AI 步骤 {index} 引用了元素库中不存在的 element_id={element_id or '?'}"
            warnings.append(reason)
            manual_reasons.append(reason)
            append("待补录元素", "web_wait", {"seconds": 0, "manual_intervention": True, "reason": reason}, skip=True)
            continue
        evidence_elements.add(element.id)
        evidence_pages.add(element.page_key)

        if _looks_like_captcha(element, str(item.get("reason") or "")):
            reason = f"{element.semantic_name} 属于验证码/滑块验证，需配置测试绕过或人工接管"
            manual_reasons.append(reason)
            append(f"人工接管：{element.semantic_name}", "web_wait", {"seconds": 0, "manual_intervention": True, "reason": reason}, skip=True)
            continue

        locator = _preferred_locator(element)
        if locator is None:
            reason = f"元素“{element.semantic_name}”没有可执行定位器，需返回元素库补录"
            warnings.append(reason)
            manual_reasons.append(reason)
            append("待补录元素", "web_wait", {"seconds": 0, "manual_intervention": True, "reason": reason}, skip=True)
            continue
        by, locator_value = locator
        config = {"by": by, "locator": locator_value, "element_id": element.id}

        if action == "click":
            append(f"点击 {element.semantic_name}", "web_click", config)
        elif action == "input":
            value = item.get("value")
            if not isinstance(value, str) or not re.fullmatch(r"\$\{[a-zA-Z_][a-zA-Z0-9_.-]*}", value):
                variable = _variable_name(element, used_variables)
                variables[variable] = "" if _is_sensitive_input(element, variable) else (value if value is not None else "")
                value = f"${{{variable}}}"
                warnings.append(f"输入“{element.semantic_name}”已参数化为 {value}，请在执行前确认测试数据")
            else:
                variable = value[2:-1]
                variables.setdefault(variable, "")
            append(f"输入 {element.semantic_name}", "web_input", {**config, "value": value, "clear_first": True})
        elif action == "select":
            value = item.get("value")
            append(f"选择 {element.semantic_name}", "web_select", {**config, "value": value})
        elif action == "wait":
            append(f"等待 {element.semantic_name}", "web_wait", {**config, "state": str(item.get("state") or "visible")})
        elif action == "assert_visible":
            if include_structure_assertions:
                append(
                    f"断言 {element.semantic_name} 可见",
                    "web_wait",
                    {**config, "state": "visible", "assertion_kind": "visible"},
                )
        elif action == "assert_text":
            expected = item.get("equals")
            contains = item.get("contains")
            regex = item.get("regex")
            assertion_config = dict(config)
            if expected is not None:
                assertion_config["equals"] = expected
            elif contains is not None:
                assertion_config["contains"] = contains
            elif regex is not None:
                assertion_config["regex"] = regex
            else:
                warnings.append(f"文本断言“{element.semantic_name}”没有期望值，已忽略")
                continue
            append(f"断言 {element.semantic_name} 文本", "web_assert_text", assertion_config)

    if not steps:
        return None
    has_assertion = any(
        item["step_type"] in {"web_assert_text", "web_assert_visual"}
        or (
            item["step_type"] == "web_wait"
            and (item.get("config") or {}).get("assertion_kind") == "visible"
        )
        for item in steps
    )
    if not has_assertion and not manual_reasons:
        reason = "AI 动作计划缺少可验证预期，请人工补充功能、结构或视觉断言"
        warnings.append(reason)
        manual_reasons.append(reason)
        append(
            "待补充断言",
            "web_wait",
            {"seconds": 0, "manual_intervention": True, "reason": reason},
            skip=True,
        )
    confidence = 0.92
    confidence -= min(0.45, len(manual_reasons) * 0.18)
    confidence -= min(0.2, len(warnings) * 0.03)
    if not raw.get("functional_case_id"):
        confidence -= 0.05
    confidence = max(0.1, min(0.99, confidence))
    raw_tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    priority = _as_int(raw.get("priority"), 2)
    functional_case_id = _as_int(raw.get("functional_case_id")) or None
    return {
        "title": title[:200],
        "description": str(raw.get("description") or "").strip() or None,
        "priority": max(0, min(3, priority)),
        "tags": list(dict.fromkeys(["ai-web-ui", *[str(item)[:50] for item in raw_tags]]))[:20],
        "variables": variables,
        "steps": steps,
        "warnings": list(dict.fromkeys(warnings)),
        "manual_reasons": list(dict.fromkeys(manual_reasons)),
        "confidence": round(confidence, 3),
        "functional_case_id": functional_case_id,
        "visual_assertion": any(item["step_type"] == "web_assert_visual" for item in steps),
        "evidence": {
            "element_ids": sorted(evidence_elements),
            "page_keys": sorted(evidence_pages),
            "snapshot_ids": sorted(evidence_snapshots),
        },
    }


def commit_drafts(db: Session, *, draft_ids: list[int], module_id: int) -> tuple[list[int], list[dict[str, Any]]]:
    """将待评审草稿写入正式 v2 Web 用例；需人工的用例默认停用。"""
    module = db.query(Module).filter(Module.id == module_id).one_or_none()
    if module is None:
        raise ValueError("目标模块不存在")
    drafts = (
        db.query(UiAutomationCaseDraft)
        .filter(UiAutomationCaseDraft.id.in_(draft_ids))
        .order_by(UiAutomationCaseDraft.id)
        .all()
    )
    by_id = {item.id: item for item in drafts}
    created: list[int] = []
    skipped: list[dict[str, Any]] = []
    max_order = db.query(func.max(TestCase.sort_order)).filter(TestCase.module_id == module_id).scalar() or 0

    for draft_id in draft_ids:
        draft = by_id.get(draft_id)
        if draft is None:
            skipped.append({"draft_id": draft_id, "reason": "草稿不存在"})
            continue
        if draft.status != UI_AUTO_DRAFT_PENDING:
            skipped.append({"draft_id": draft_id, "reason": f"草稿状态为 {draft.status}"})
            continue
        if draft.project_id != module.project_id:
            skipped.append({"draft_id": draft_id, "reason": "草稿与目标模块不属于同一项目"})
            continue
        steps = list(draft.steps or [])
        if not steps:
            skipped.append({"draft_id": draft_id, "reason": "草稿没有可执行步骤"})
            continue
        validation_errors = validate_draft_steps(
            steps,
            allow_manual=bool(draft.manual_reasons),
        )
        if validation_errors:
            skipped.append({"draft_id": draft_id, "reason": "；".join(validation_errors[:5])})
            continue
        evidence_errors = validate_draft_evidence(db, draft)
        if evidence_errors:
            skipped.append({"draft_id": draft_id, "reason": "；".join(evidence_errors[:5])})
            continue

        max_order += 1
        manual_reasons = list(draft.manual_reasons or [])
        case = TestCase(
            module_id=module_id,
            name=draft.title,
            description=draft.description,
            sort_order=max_order,
            case_type="web",
            tags=list(draft.tags or []),
            skip=bool(manual_reasons),
            priority=draft.priority,
            variables=dict(draft.variables or {}),
            source="ai_m8_web",
            generation_metadata={
                "source": "web_ui_case_generation",
                "ai_run_id": draft.ai_run_id,
                "draft_id": draft.id,
                "functional_case_id": draft.functional_case_id,
                "confidence": draft.confidence,
                "evidence": draft.evidence or {},
                "warnings": draft.warnings or [],
                "manual_reasons": manual_reasons,
                "visual_assertion": bool(draft.visual_assertion),
            },
        )
        db.add(case)
        db.flush()
        for index, item in enumerate(steps, start=1):
            db.add(TestStep(
                case_id=case.id,
                step_order=int(item.get("step_order") or index),
                step_name=str(item.get("step_name") or f"步骤 {index}")[:255],
                step_type=str(item.get("step_type") or ""),
                skip=bool(item.get("skip")),
                config=dict(item.get("config") or {}),
                extract=list(item.get("extract") or []),
                assertion=list(item.get("assertion") or []),
                wait_before=float(item.get("wait_before") or 0),
                timeout=int(item.get("timeout") or 30),
                retry=int(item.get("retry") or 0),
                on_failure=str(item.get("on_failure") or "stop"),
            ))
        evidence_ids = [int(item) for item in (draft.evidence or {}).get("element_ids") or []]
        if evidence_ids:
            db.query(UiElement).filter(UiElement.id.in_(evidence_ids)).update(
                {UiElement.usage_count: UiElement.usage_count + 1},
                synchronize_session=False,
            )
        draft.status = UI_AUTO_DRAFT_ACCEPTED
        draft.module_id = module_id
        draft.committed_case_id = case.id
        created.append(case.id)
    db.flush()
    return created, skipped


def reject_draft(db: Session, draft: UiAutomationCaseDraft, reason: str | None) -> None:
    if draft.status != UI_AUTO_DRAFT_PENDING:
        raise ValueError("只有待评审草稿可以拒绝")
    draft.status = UI_AUTO_DRAFT_REJECTED
    draft.reject_reason = reason.strip() if reason else None
    db.flush()
