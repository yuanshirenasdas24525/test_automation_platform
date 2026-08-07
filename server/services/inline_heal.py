"""逐请求即时自愈。

API 用例里的 HTTP 步骤一旦失败，立即执行：

    真实证据 + 用例名称/描述/需求 → AI 诊断 → 安全预检 → 内存候选修复

调用方必须先用候选定义重跑验证；只有失败步骤确实恢复后，才能调用
``persist_verified_heal`` 落库。这样不会为了“变绿”直接篡改断言，也不会把未经
验证的模型输出写进用例资产。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from utils.parameter_flow import (
    extract_rule,
    infer_rebound_extracts,
    is_response_jsonpath,
    merge_rebound_extracts,
)

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.8
_NOT_EMPTY_SENTINELS = {
    "not_empty", "not_null", "notnull", "notempty", "非空", "@notnull", "@notempty",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|authorization|cookie|api[_-]?key)",
    re.IGNORECASE,
)
_STATUS_MISMATCH_RE = re.compile(r"status_code:\s*(\d{3})\s*!=\s*(\d{3})")
_STATUS_INTENT_RE = re.compile(
    r"(?:返回|http(?:\s*状态码)?)[：:\s_-]*(\d{3})",
    re.IGNORECASE,
)


def _loads(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """从模型文本中尽力取出一个 JSON 对象。"""
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, dict):
            return value
    return None


def _redact(value: Any, key: str = "") -> Any:
    """发给模型前隐藏真实凭据；保留 ``${变量}``，因为变量关系是诊断证据。"""
    if _SENSITIVE_KEY_RE.search(str(key)):
        if isinstance(value, str) and "${" in value:
            return value
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _first_failed(result) -> Any | None:
    """取用例里第一个失败的 HTTP 步骤。"""
    from runners.protocol import StepStatus

    for step in result.steps or []:
        if (
            step.step_type == "http_request"
            and step.status in (StepStatus.FAILED, StepStatus.ERROR)
        ):
            return step
    return None


def _step_record(ctx, step) -> dict[str, Any]:
    records = getattr(ctx, "records", {}) or {}
    value = records.get(f"step_{step.step_order}")
    return value if isinstance(value, dict) else {}


class _StepView:
    """把内存 ``StepResult`` 适配为规则分诊和模型诊断共用的视图。"""

    def __init__(self, step, ctx, case_id: int | None):
        record = _step_record(ctx, step)
        self.case_id = case_id
        self.step_id = step.step_id
        self.step_order = step.step_order
        self.step_name = step.step_name
        self.step_type = step.step_type
        self.status = step.status.value if hasattr(step.status, "value") else str(step.status)
        self.error_message = step.error_message or str(record.get("error") or "")
        self.target = step.target or str(record.get("target") or "")
        self.input_data = step.input_data if step.input_data is not None else record.get("input_data")
        self.output_data = step.output_data if step.output_data is not None else record.get("output_data")
        self.extract_values = step.extracted or record.get("extract_values") or {}
        self.extract_errors = record.get("extract_errors") or []
        self.assertion_results = record.get("assertion_results") or []
        code = record.get("status_code")
        try:
            self.status_code = int(code) if code is not None else None
        except (TypeError, ValueError):
            self.status_code = None


def _target_http_step(
    case: dict,
    *,
    target_step_id: int | None = None,
    target_step_order: int | None = None,
    target_step_name: str | None = None,
) -> dict | None:
    steps = [
        step
        for step in (case.get("steps") or [])
        if isinstance(step, dict) and step.get("step_type") == "http_request"
    ]
    if target_step_id is not None:
        found = next((step for step in steps if step.get("id") == target_step_id), None)
        if found is not None:
            return found
    if target_step_order is not None:
        found = next(
            (step for step in steps if int(step.get("step_order") or 0) == target_step_order),
            None,
        )
        if found is not None:
            return found
    if target_step_name:
        found = next((step for step in steps if step.get("step_name") == target_step_name), None)
        if found is not None:
            return found
    return steps[0] if steps else None


def _json_map(raw: Any) -> dict[str, Any]:
    value = _loads(raw)
    return dict(value) if isinstance(value, dict) else {}


def _merge_with_deletes(original: dict, patch: dict) -> dict:
    merged = dict(original or {})
    for key, value in (patch or {}).items():
        if value is None:
            merged.pop(str(key), None)
        else:
            merged[str(key)] = value
    return merged


def _upsert_extract_rules(raw: Any, patch: dict) -> list[dict]:
    rules = [dict(item) for item in (raw or []) if isinstance(item, dict)]
    by_name = {str(item.get("name")): index for index, item in enumerate(rules) if item.get("name")}
    for name, path in patch.items():
        key = str(name)
        if path is None:
            rules = [item for item in rules if str(item.get("name") or "") != key]
            by_name = {
                str(item.get("name")): index
                for index, item in enumerate(rules)
                if item.get("name")
            }
            continue
        rule = extract_rule(key, path)
        if key in by_name:
            rules[by_name[key]] = rule
        else:
            rules.append(rule)
            by_name[key] = len(rules) - 1
    return rules


def _with_rebound_extracts(step: dict, fix: dict[str, Any]) -> dict[str, Any]:
    """把 AI 请求修改确定性转换成同名变量回写。"""
    cfg = step.get("config") if isinstance(step.get("config"), dict) else {}
    inferred_parts: list[dict[str, Any]] = []
    for field in ("params", "headers"):
        patch = fix.get(field)
        if not isinstance(patch, dict) or not patch:
            continue
        before = _json_map(cfg.get(field))
        after = _merge_with_deletes(before, patch)
        inferred_parts.append(infer_rebound_extracts(before, after))

    inferred = merge_rebound_extracts(*inferred_parts)
    if not inferred:
        return fix
    merged = dict(fix)
    explicit = merged.get("extract") if isinstance(merged.get("extract"), dict) else {}
    # 程序从修改前后请求推导出的值优先，避免模型给出冲突的秘密值。
    merged["extract"] = {**explicit, **inferred}
    return merged


def _upsert_assertion_rules(raw: Any, patch: dict) -> list[dict]:
    rules = [dict(item) for item in (raw or []) if isinstance(item, dict)]
    by_target = {
        str(item.get("target")): index
        for index, item in enumerate(rules)
        if item.get("target")
    }
    for target, expected in patch.items():
        key = str(target)
        if isinstance(expected, str) and expected.strip().lower() in _NOT_EMPTY_SENTINELS:
            rule = {
                "type": "is_not_null",
                "target": key,
                "expected": None,
                "description": f"AI 自愈：{key}",
            }
        else:
            rule = {
                "type": "jsonpath" if key.startswith("$") else "equal",
                "target": key,
                "expected": expected,
                "description": f"AI 自愈：{key}",
            }
        if key in by_target:
            rules[by_target[key]] = rule
        else:
            rules.append(rule)
            by_target[key] = len(rules) - 1
    return rules


def _apply_to_case_dict(
    case: dict,
    fix: dict,
    *,
    target_step_id: int | None = None,
    target_step_order: int | None = None,
    target_step_name: str | None = None,
) -> list[str]:
    """把候选修复只应用到指定 HTTP 步骤的内存定义。"""
    step = _target_http_step(
        case,
        target_step_id=target_step_id,
        target_step_order=target_step_order,
        target_step_name=target_step_name,
    )
    if step is None:
        return []

    parts: list[str] = []
    cfg = dict(step.get("config") or {})

    extract_patch = fix.get("extract")
    if isinstance(extract_patch, dict) and extract_patch:
        has_assignment = any(
            value is not None and not is_response_jsonpath(value)
            for value in extract_patch.values()
        )
        if has_assignment:
            before_rules = [
                dict(item)
                for item in (step.get("extract") or [])
                if isinstance(item, dict)
            ]
            for name, jsonpath in _json_map(cfg.get("extract_data")).items():
                before_rules = [
                    item for item in before_rules
                    if str(item.get("name") or "") != str(name)
                ]
                before_rules.append({
                    "name": str(name),
                    "from": "response.body",
                    "jsonpath": str(jsonpath),
                })
            after_rules = _upsert_extract_rules(before_rules, extract_patch)
            if after_rules != before_rules:
                step["extract"] = after_rules
                cfg.pop("extract_data", None)
                parts.append("extract")
        elif cfg.get("extract_data") not in (None, "", {}, []):
            before = _json_map(cfg.get("extract_data"))
            after = _merge_with_deletes(before, extract_patch)
            if after != before:
                cfg["extract_data"] = after
                parts.append("extract")
        else:
            before_rules = step.get("extract") or []
            after_rules = _upsert_extract_rules(before_rules, extract_patch)
            if after_rules != before_rules:
                step["extract"] = after_rules
                parts.append("extract")

    assertion_patch = fix.get("assertion")
    if isinstance(assertion_patch, dict) and assertion_patch:
        if cfg.get("assertion") not in (None, "", {}, []):
            before = _json_map(cfg.get("assertion"))
            after = {**before, **assertion_patch}
            if after != before:
                cfg["assertion"] = after
                parts.append("assertion")
        else:
            before_rules = step.get("assertion") or []
            after_rules = _upsert_assertion_rules(before_rules, assertion_patch)
            if after_rules != before_rules:
                step["assertion"] = after_rules
                parts.append("assertion")

    for field in ("params", "headers"):
        patch = fix.get(field)
        if not isinstance(patch, dict) or not patch:
            continue
        before = _json_map(cfg.get(field))
        after = _merge_with_deletes(before, patch)
        if after != before:
            cfg[field] = after
            parts.append(field)

    step["config"] = cfg

    # 保留既有规则自愈的可见前置步骤能力；需求约束的逐请求模型目前不会自动
    # 生成这一类结构，避免凭据/顺序证据不足时扩大修改范围。
    inserted = fix.get("insert_steps")
    if isinstance(inserted, list) and inserted:
        for existing in case.get("steps") or []:
            existing["step_order"] = int(existing.get("step_order") or 0) + len(inserted)
        new_steps = [
            {
                "id": None,
                "step_order": index,
                "step_name": spec["step_name"],
                "step_type": "http_request",
                "config": spec["config"],
                "on_failure": "stop",
            }
            for index, spec in enumerate(inserted)
            if isinstance(spec, dict) and spec.get("step_name") and isinstance(spec.get("config"), dict)
        ]
        if new_steps:
            case["steps"] = new_steps + list(case.get("steps") or [])
            parts.append("insert_steps")
    return parts


def _case_intent(case: dict) -> dict[str, Any]:
    requirement = case.get("requirement")
    return {
        "case_name": case.get("name"),
        "case_description": case.get("description"),
        "requirement": requirement if isinstance(requirement, dict) else None,
        "rule": (
            "用例名称、描述、需求和验收标准共同定义预期；"
            "真实响应与这些内容冲突时必须保留失败，不能反改断言迎合响应。"
        ),
    }


def _case_steps_for_ai(case: dict) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for step in case.get("steps") or []:
        if not isinstance(step, dict) or step.get("step_type") != "http_request":
            continue
        items.append({
            "step_id": step.get("id"),
            "step_order": step.get("step_order"),
            "step_name": step.get("step_name"),
            "config": _redact(step.get("config") or {}),
            "extract": step.get("extract") or [],
            "assertion": step.get("assertion") or [],
        })
    return items


def _failed_step_for_ai(view: _StepView) -> dict[str, Any]:
    return {
        "step_id": view.step_id,
        "step_order": view.step_order,
        "step_name": view.step_name,
        "target": view.target,
        "status": view.status,
        "status_code": view.status_code,
        "request": _redact(view.input_data),
        "response": _redact(view.output_data),
        "extract_values": _redact(view.extract_values),
        "extract_errors": view.extract_errors,
        "assertion_results": view.assertion_results,
        "error_message": view.error_message,
    }


def _assertion_matches_response(target: str, expected: Any, view: _StepView) -> bool:
    if target == "status_code":
        return view.status_code is not None and str(view.status_code) == str(expected)
    if not target.startswith("$"):
        return False
    body = _loads(view.output_data)
    if not isinstance(body, (dict, list)):
        return False
    from utils.platform_utils import extractor

    actual = extractor(body, target)
    if isinstance(expected, str) and expected.strip().lower() in _NOT_EMPTY_SENTINELS:
        return actual not in (None, "", [], {})
    return actual == expected or str(actual) == str(expected)


def _previous_expected_status(view: _StepView) -> int | None:
    for result in getattr(view, "assertion_results", []) or []:
        if isinstance(result, dict) and result.get("target") == "status_code":
            try:
                return int(result.get("expected"))
            except (TypeError, ValueError):
                pass
    match = _STATUS_MISMATCH_RE.search(str(getattr(view, "error_message", "") or ""))
    return int(match.group(2)) if match else None


def _explicit_statuses(case: dict | None, evidence: list[Any]) -> set[int]:
    """跨状态族修改必须能在用户用例/需求文字中找到目标码；证据文本仅作兼容兜底。"""
    source: Any = _case_intent(case or {}) if case else evidence
    text = json.dumps(source, ensure_ascii=False, default=str)
    return {int(value) for value in _STATUS_INTENT_RE.findall(text)}


def _validate_model_decision(
    decision: dict[str, Any],
    view: _StepView,
    case: dict | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """程序化门禁：语义证据不足或不能被真实响应验证的候选一律不应用。"""
    if str(decision.get("classification") or "").strip() != "用例问题":
        return None, "AI 判定不是用例问题"
    if decision.get("intent_supported") is not True:
        return None, "AI 未确认修复符合用例意图"
    evidence = decision.get("requirement_evidence")
    if not isinstance(evidence, list) or not any(str(item).strip() for item in evidence):
        return None, "缺少名称/描述/需求依据"
    try:
        confidence = float(decision.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < _MIN_CONFIDENCE:
        return None, f"置信度 {confidence:.2f} 低于 {_MIN_CONFIDENCE:.2f}"

    raw_fix = decision.get("fix")
    if not isinstance(raw_fix, dict):
        return None, "AI 没有给出结构化修复"

    fix: dict[str, Any] = {}
    for field in ("extract", "assertion", "params", "headers"):
        value = raw_fix.get(field)
        if isinstance(value, dict) and value:
            fix[field] = dict(value)

    response_body = _loads(view.output_data)
    extract_patch = fix.get("extract") or {}
    if extract_patch:
        from utils.platform_utils import extractor

        verified_extract: dict[str, Any] = {}
        for name, path in extract_patch.items():
            if path is None:
                verified_extract[str(name)] = None
                continue
            if (
                isinstance(path, str)
                and path.startswith("$")
                and isinstance(response_body, (dict, list))
                and extractor(response_body, path) is not None
            ):
                verified_extract[str(name)] = path
        if verified_extract:
            fix["extract"] = verified_extract
        else:
            fix.pop("extract", None)

    request_changed = bool(fix.get("params") or fix.get("headers"))
    assertion_patch = fix.get("assertion") or {}
    if "status_code" in assertion_patch:
        previous_status = _previous_expected_status(view)
        try:
            next_status = int(assertion_patch["status_code"])
        except (TypeError, ValueError):
            return None, "status_code 修复值不是合法整数"
        if (
            previous_status is not None
            and previous_status // 100 != next_status // 100
            and next_status not in _explicit_statuses(case, evidence)
        ):
            return None, (
                f"禁止仅迎合真实响应把状态码从 {previous_status} 跨状态族改为 {next_status}；"
                f"用例名称、描述或需求必须明确写出“返回{next_status}”"
            )
    if assertion_patch and not request_changed:
        verified_assertions = {
            str(target): expected
            for target, expected in assertion_patch.items()
            if _assertion_matches_response(str(target), expected, view)
        }
        if verified_assertions:
            fix["assertion"] = verified_assertions
        else:
            fix.pop("assertion", None)

    if not any(fix.get(field) for field in ("extract", "assertion", "params", "headers")):
        return None, "候选修复未通过真实响应预检"
    return fix, None


def _diagnose_with_model(
    case: dict,
    view: _StepView,
    *,
    session,
    model_name: str,
    deterministic_hint: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """同步调用指定模型；失败只返回 ``None``，不改变测试结论。"""
    from ai_gateway.gateway import (
        _load_prompt,
        _render_prompt,
        chat_markdown,
        model_task_options,
    )
    from server.services.ai_model_service import get_ai_model

    project_id = case.get("project_id")
    cfg = get_ai_model(session, model_name, project_id=project_id)
    if cfg is None or not cfg.enabled:
        logger.warning("[inline_heal] 模型 %r 未配置或未启用，跳过 AI 自愈", model_name)
        return None

    prompt = _render_prompt(_load_prompt("api_inline_heal"), {
        "CASE_INTENT": _redact(_case_intent(case)),
        "CASE_STEPS": _case_steps_for_ai(case),
        "FAILED_STEP": _failed_step_for_ai(view),
        "DETERMINISTIC_HINT": deterministic_hint or {},
    })
    options = model_task_options(cfg, "api_inline_heal")
    raw, _tokens_in, _tokens_out = chat_markdown(
        prompt,
        cfg,
        timeout=options["timeout"],
        system_prompt=(
            "你是严格的接口测试自愈审查器。只输出合法 JSON 对象。"
            "测试通过不是目标，符合用例名称、描述、需求和验收标准才是目标。"
        ),
        enable_thinking=options["enable_thinking"],
        json_mode=options["json_mode"],
        max_tokens=options["max_tokens"],
        temperature=options["temperature"],
        reasoning_effort=options.get("reasoning_effort"),
    )
    return _parse_json_object(raw)


def heal_case_inline(
    case: dict,
    result,
    ctx,
    *,
    session=None,
    model_name: str | None = None,
) -> dict[str, Any] | None:
    """生成并应用一份**仅在内存中**的候选修复；调用方负责重跑验证。"""
    from server.services.failure_triage import triage_step

    step = _first_failed(result)
    if step is None:
        return None

    case_id = case.get("id")
    view = _StepView(step, ctx, case_id)
    producers = {
        str(key): int(case_id or 0)
        for key in (getattr(ctx, "vars", {}) or {})
        if not str(key).startswith("_")
    }
    deterministic = triage_step(view, producers=producers, failed_case_ids=set())

    decision = None
    if model_name and session is not None:
        try:
            decision = _diagnose_with_model(
                case,
                view,
                session=session,
                model_name=model_name,
                deterministic_hint=deterministic,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[inline_heal] case=%s step=%s AI 诊断失败（保留原失败）: %s",
                case_id,
                view.step_id,
                exc,
                exc_info=True,
            )
            return None

    if decision is not None:
        fix, rejected_reason = _validate_model_decision(decision, view, case)
        if fix is None:
            logger.info(
                "[inline_heal] case=%s step=%s 候选未放行：%s",
                case_id,
                view.step_id,
                rejected_reason,
            )
            return None
        subtype = "ai_requirement_guarded"
        summary = str(decision.get("reason") or decision.get("suggestion") or "")
        evidence = [
            str(item)
            for item in (decision.get("requirement_evidence") or [])
            if str(item).strip()
        ]
        confidence = float(decision.get("confidence") or 0)
    else:
        # 没有模型时只允许“响应里已找到确定路径”的结构修复；断言和请求参数
        # 都必须经过语义模型，不能仅凭 actual 值自动改 expected。
        if not deterministic or deterministic.get("subtype") != "wrong_jsonpath":
            return None
        hint = deterministic.get("fix_hint") or {}
        fix = {"extract": hint.get("extract") or {}}
        if not fix["extract"]:
            return None
        subtype = "wrong_jsonpath"
        summary = str(deterministic.get("summary") or "")
        evidence = [str(deterministic.get("evidence") or "真实响应字段路径")]
        confidence = 1.0

    target_step = _target_http_step(
        case,
        target_step_id=view.step_id,
        target_step_order=view.step_order,
        target_step_name=view.step_name,
    )
    if target_step is not None:
        fix = _with_rebound_extracts(target_step, fix)

    parts = _apply_to_case_dict(
        case,
        fix,
        target_step_id=view.step_id,
        target_step_order=view.step_order,
        target_step_name=view.step_name,
    )
    if not parts:
        return None

    return {
        "parts": parts,
        "subtype": subtype,
        "summary": summary,
        "requirement_evidence": evidence,
        "confidence": confidence,
        "fix": fix,
        "target_step_id": view.step_id,
        "target_step_order": view.step_order,
        "target_step_name": view.step_name,
        "model_name": model_name,
    }


def repaired_step_passed(result, ctx, healed: dict[str, Any]) -> bool:
    """候选修复后，目标步骤必须通过且不能再有提取错误。"""
    target_id = healed.get("target_step_id")
    target_order = healed.get("target_step_order")
    target_name = healed.get("target_step_name")
    from runners.protocol import StepStatus

    for step in result.steps or []:
        matches = (
            (target_id is not None and step.step_id == target_id)
            or (target_id is None and step.step_order == target_order)
            or (target_id is None and target_order is None and step.step_name == target_name)
        )
        if not matches:
            continue
        record = _step_record(ctx, step)
        return step.status == StepStatus.PASSED and not (record.get("extract_errors") or [])
    return False


def persist_verified_heal(session, case_id: int, healed: dict[str, Any]) -> bool:
    """把已通过即时验证的候选修复落库，并记录可回滚编辑事件。"""
    from sqlalchemy.orm import selectinload

    from database.models import TestCase
    from database.models.edit_operation import EDIT_ACTION_UPDATE
    from server.services.edit_history_service import (
        create_test_case_batch,
        record_test_case_update,
        snapshot_test_case,
    )

    case = (
        session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == case_id)
        .first()
    )
    if case is None:
        return False

    target = None
    target_id = healed.get("target_step_id")
    if target_id is not None:
        target = next((step for step in case.steps if step.id == target_id), None)
    if target is None:
        target = next(
            (
                step
                for step in case.steps
                if step.step_type == "http_request"
                and int(step.step_order or 0) == int(healed.get("target_step_order") or 0)
            ),
            None,
        )
    if target is None:
        return False

    before = snapshot_test_case(case)
    temp_case = {"steps": [target.to_dict()]}
    parts = _apply_to_case_dict(
        temp_case,
        healed.get("fix") or {},
        target_step_id=target.id,
        target_step_order=target.step_order,
        target_step_name=target.step_name,
    )
    if not parts:
        return False

    updated = temp_case["steps"][0]
    target.config = dict(updated.get("config") or {})
    target.extract = list(updated.get("extract") or [])
    target.assertion = list(updated.get("assertion") or [])

    batch = create_test_case_batch(
        session,
        action=EDIT_ACTION_UPDATE,
        operator_id=None,
        summary=f"AI 即时自愈（用例 #{case_id}，步骤 #{target.id}）",
    )
    record_test_case_update(
        session,
        case,
        before_snapshot=before,
        field_changes=[],
        operator_id=None,
        summary=(
            f"AI 即时自愈验证通过：{target.step_name}；"
            f"依据：{'；'.join(healed.get('requirement_evidence') or [])[:500]}"
        ),
        batch=batch,
    )
    session.commit()
    return True
