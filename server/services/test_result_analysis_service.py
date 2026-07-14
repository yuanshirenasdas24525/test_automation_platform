from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import selectinload

from database.models import TestCase, TestReport, TestStep, TestStepReport
from utils.platform_utils import extractor


RULES_VERSION = "2026-06-23.v1"
FEATURE_TEST_RESULT_ANALYSIS = "test_result_analysis"

_DYNAMIC_KEYWORDS = (
    "id",
    "token",
    "uuid",
    "key",
    "secret",
    "code",
    "no",
    "number",
    "session",
    "ticket",
    "order",
    "user",
    "task",
)
_FUNCTION_KEYWORDS = (
    "sign",
    "signature",
    "timestamp",
    "nonce",
    "random",
    "captcha",
    "verify",
    "password",
    "encrypt",
    "hash",
)
_ENV_PATTERNS = (
    "connection refused",
    "connect timeout",
    "read timeout",
    "timed out",
    "dns",
    "name or service not known",
    "nodename nor servname",
    "network is unreachable",
    "ssl",
    "certificate",
    "proxy",
)
TEST_DATA_PREFIX = "AUTO_TEST_"
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_READONLY_SEED_NAMES = {"admin", "administrator", "root", "system"}
_DATA_FIELD_KEYS = {
    "username",
    "user_name",
    "account",
    "mobile",
    "phone",
    "tel",
    "email",
    "name",
    "nickname",
    "real_name",
    "title",
    "order_no",
    "orderNo",
}
_DATA_FIELD_KEYS_NORMALIZED = {item.lower() for item in _DATA_FIELD_KEYS}
_NEGATIVE_CASE_KEYWORDS = ("参数校验", "边界", "鉴权", "越权", "响应校验", "安全")
_SEED_MUTATION_WORDS = (
    "修改",
    "更新",
    "删除",
    "禁用",
    "改密",
    "密码",
    "权限",
    "角色",
    "delete",
    "update",
    "disable",
    "password",
    "role",
    "permission",
)


def analyze_report(session, report_id: int, model_name: str | None = None) -> dict[str, Any]:
    """基于测试报告做确定性诊断，并在有模型时追加 AI 汇总。"""
    report = session.query(TestReport).filter(TestReport.id == report_id).first()
    if report is None:
        raise ValueError("报告不存在")

    rows = (
        session.query(TestStepReport)
        .filter(TestStepReport.report_id == report_id)
        .order_by(TestStepReport.case_id.asc().nullslast(), TestStepReport.id.asc())
        .all()
    )
    if not rows:
        return _empty_output(report, model_name, "该报告没有步骤级执行记录")

    case_ids = sorted({r.case_id for r in rows if r.case_id is not None})
    cases = {
        c.id: c
        for c in (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id.in_(case_ids))
            .all()
        )
    }

    by_case: dict[int, list[TestStepReport]] = defaultdict(list)
    for row in rows:
        if row.case_id is not None:
            by_case[row.case_id].append(row)

    case_items = []
    all_suggestions = []
    for case_id, case_rows in by_case.items():
        case = cases.get(case_id)
        if case is None:
            continue
        item = _analyze_case(case, case_rows)
        case_items.append(item)
        all_suggestions.extend(item["suggestions"])

    order_suggestions = _detect_execution_order_and_setup(session, cases, by_case)
    by_item = {item["case_id"]: item for item in case_items}
    for case_id, suggestions in order_suggestions.items():
        item = by_item.get(case_id)
        if item is None:
            continue
        item["suggestions"].extend(suggestions)
        all_suggestions.extend(suggestions)

    duplicate_suggestions = _detect_duplicate_cases(cases, by_case)
    for case_id, suggestions in duplicate_suggestions.items():
        item = by_item.get(case_id)
        if item is None:
            continue
        item["suggestions"].extend(suggestions)
        all_suggestions.extend(suggestions)

    summary = _build_summary(case_items, all_suggestions)
    output = {
        "report_id": report.id,
        "project_id": report.project_id,
        "model_name": model_name,
        "rules_version": RULES_VERSION,
        "summary": summary,
        "cases": case_items,
        "ai_summary": None,
        "ai_error": None,
    }
    if model_name:
        _attach_ai_summary(session, output, model_name)
    return output


def _empty_output(report: TestReport, model_name: str | None, message: str) -> dict[str, Any]:
    return {
        "report_id": report.id,
        "project_id": report.project_id,
        "model_name": model_name,
        "rules_version": RULES_VERSION,
        "summary": {
            "total_cases": 0,
            "total_suggestions": 0,
            "by_category": {},
            "by_severity": {},
            "message": message,
        },
        "cases": [],
        "ai_summary": None,
        "ai_error": None,
    }


def _analyze_case(case: TestCase, rows: list[TestStepReport]) -> dict[str, Any]:
    step_defs = {s.id: s for s in case.steps}
    step_items = []
    suggestions = []
    response_index: list[dict[str, Any]] = []

    for order, row in enumerate(rows, start=1):
        step = step_defs.get(row.step_id)
        body = _load_jsonish(row.output_data)
        request_data = _load_jsonish(row.input_data)
        extract_values = _load_jsonish(row.extract_values)
        assertion_results = _load_jsonish(row.assertion_results)
        response_paths = _leaf_paths(body)
        response_index.append(
            {
                "order": order,
                "row": row,
                "step": step,
                "body": body,
                "paths": response_paths,
            }
        )

        step_suggestions = []
        step_suggestions.extend(_detect_failed_extracts(row, step, extract_values, body))
        step_suggestions.extend(_detect_missing_assertions(row, step, body))
        step_suggestions.extend(_detect_sql_assertion_needs(row, step, body))
        step_suggestions.extend(_detect_function_needs(row, step, request_data))
        step_suggestions.extend(_detect_data_safety_issues(row, step, request_data, case.name))
        if "前置链" not in (case.name or ""):
            # 前置链用例职责就是准备账号/token，断言只校验 200 即可，不提示"断言偏弱/假通过"
            step_suggestions.extend(_detect_false_pass(row, step, body))
        step_suggestions.extend(_classify_failure(row, body, case.name))
        suggestions.extend(step_suggestions)

        step_items.append(
            {
                "step_report_id": row.id,
                "step_id": row.step_id,
                "order": order,
                "name": row.step_name,
                "type": row.step_type,
                "status": row.status,
                "status_code": row.status_code,
                "target": _clip(row.target, 300),
                "error_message": _clip(row.error_message, 500),
                "extract_values": _safe_preview(extract_values),
                "assertion_results": _safe_preview(assertion_results),
                "response_candidates": _candidate_paths(response_paths),
                "suggestion_count": len(step_suggestions),
            }
        )

    dependency_suggestions = _detect_missing_dependencies(response_index)
    suggestions.extend(dependency_suggestions)
    classification = _classify_case(suggestions, rows)

    return {
        "case_id": case.id,
        "module_id": case.module_id,
        "name": case.name,
        "case_type": case.case_type,
        "status": _case_status(rows),
        "classification": classification,
        "steps": step_items,
        "suggestions": suggestions,
    }


def _detect_failed_extracts(
    row: TestStepReport,
    step: TestStep | None,
    extract_values: Any,
    body: Any,
) -> list[dict[str, Any]]:
    suggestions = []
    configured = _normalize_extract_rules(step.extract if step is not None else None)
    extracted = extract_values if isinstance(extract_values, dict) else {}
    for item in configured:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        expr = str(item.get("jsonpath") or item.get("path") or item.get("expr") or "").strip()
        value = extracted.get(name)
        if value not in (None, "", [], {}):
            continue
        alternative = _best_path_for_name(body, name)
        suggestions.append(
            _suggestion(
                "missing_extraction",
                "high",
                0.9 if alternative else 0.72,
                row,
                f"变量 {name or '<未命名>'} 本次没有提取到值",
                f"当前 JSONPath: {expr or '未配置'}；实际响应中"
                f"{'存在相近字段 ' + alternative if alternative else '未找到明显相近字段'}",
                {
                    "type": "update_extract",
                    "variable": name,
                    "current_jsonpath": expr,
                    "suggested_jsonpath": alternative,
                },
                "high_confidence" if alternative else "need_review",
            )
        )
    return suggestions


def _normalize_extract_rules(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [
            {"name": str(name), "jsonpath": path}
            for name, path in value.items()
            if str(name).strip()
        ]
    return []


def _detect_missing_assertions(row: TestStepReport, step: TestStep | None, body: Any) -> list[dict[str, Any]]:
    assertions = step.assertion if step is not None and isinstance(step.assertion, list) else []
    suggestions = []
    has_status = any(str(a.get("target") or "").lower() == "status_code" for a in assertions if isinstance(a, dict))
    if row.step_type == "http_request" and not has_status:
        expected = 200 if row.status_code and 200 <= row.status_code < 300 else row.status_code
        suggestions.append(
            _suggestion(
                "missing_assertion",
                "medium",
                0.86,
                row,
                "缺少 HTTP 状态码断言",
                f"本次返回 status_code={row.status_code}",
                {"type": "add_assertion", "target": "status_code", "operator": "equal", "expected": expected},
                "high_confidence" if expected else "need_review",
            )
        )

    if not isinstance(body, (dict, list)):
        return suggestions
    existing_targets = {
        str(a.get("target") or a.get("jsonpath") or "")
        for a in assertions
        if isinstance(a, dict)
    }
    for path in ("$.code", "$.status", "$.success"):
        val = _try_extract(body, path)
        if val is not None and path not in existing_targets:
            suggestions.append(
                _suggestion(
                    "missing_assertion",
                    "medium",
                    0.82,
                    row,
                    f"缺少业务结果字段断言 {path}",
                    f"本次响应 {path}={val!r}",
                    {"type": "add_assertion", "target": path, "operator": "equal", "expected": val},
                    "need_review",
                )
            )
            break
    return suggestions


_SUCCESS_CODE_FIELDS = ("$.code", "$.errcode", "$.errCode", "$.status", "$.success", "$.ok")
_SUCCESS_VALUES = {0, 200, "0", "200", "success", "ok", "true", True}


def _business_code_failure(body: Any) -> tuple[str, Any] | None:
    """响应体里业务结果字段是否表示失败（用于检测 HTTP 200 但业务失败的"假通过"）。"""
    if not isinstance(body, dict):
        return None
    for path in _SUCCESS_CODE_FIELDS:
        val = _try_extract(body, path)
        if val is None:
            continue
        norm = val.lower() if isinstance(val, str) else val
        if norm in _SUCCESS_VALUES:
            return None  # 明确成功
        # 字段存在但不是成功值 → 业务失败
        return path, val
    return None


def _detect_false_pass(row: TestStepReport, step: TestStep | None, body: Any) -> list[dict[str, Any]]:
    """假通过检测（问题14）：用例被判 passed，但其实没真正通过。
      - HTTP 2xx 但响应体业务码表示失败；
      - 整条用例只断言了 status_code，没有任何业务字段断言（断言形同虚设）。
    """
    status = (row.status or "").lower()
    if status not in ("passed", "pass", "success"):
        return []
    if row.step_type != "http_request":
        return []
    suggestions: list[dict[str, Any]] = []

    biz = _business_code_failure(body)
    if biz is not None:
        path, val = biz
        suggestions.append(
            _suggestion(
                "false_pass",
                "high",
                0.84,
                row,
                "疑似假通过：HTTP 通过但业务码表示失败",
                f"响应 {path}={val!r} 不是成功值，但用例被判通过——多半是断言只校验了状态码、漏了业务码",
                {"type": "add_assertion", "target": path, "operator": "equal", "expected": val},
                "need_review",
            )
        )

    assertions = step.assertion if step is not None and isinstance(step.assertion, list) else []
    only_status = assertions and all(
        str(a.get("target") or a.get("jsonpath") or "").lower() == "status_code"
        for a in assertions
        if isinstance(a, dict)
    )
    if (only_status or not assertions) and biz is None and isinstance(body, dict):
        # 断言太弱：建议补一条业务字段断言
        for path in ("$.code", "$.status", "$.success", "$.data"):
            if _try_extract(body, path) is not None:
                suggestions.append(
                    _suggestion(
                        "false_pass",
                        "medium",
                        0.6,
                        row,
                        "断言偏弱：仅校验状态码，未校验业务字段",
                        f"建议补充对 {path} 的断言，避免接口返回业务失败时仍判通过",
                        {"type": "review_assertion_strength", "candidate": path},
                        "manual_required",
                    )
                )
                break
    return suggestions


def _classify_failure(row: TestStepReport, body: Any, case_name: str = "") -> list[dict[str, Any]]:
    status = (row.status or "").lower()
    if status in ("passed", "pass", "success", "skipped"):
        return []
    # 负向用例（参数校验/边界/鉴权/越权/安全）本就期望 4xx：若断言已通过则不算参数错误
    negative = _is_negative_case(case_name)
    text = " ".join(
        str(x or "")
        for x in (row.error_message, row.output_data, row.target)
    ).lower()
    if any(p in text for p in _ENV_PATTERNS):
        return [
            _suggestion(
                "environment_issue",
                "high",
                0.88,
                row,
                "疑似环境或网络问题",
                _clip(row.error_message or row.output_data, 500),
                {"type": "do_not_modify_case", "reason": "environment"},
                "manual_required",
            )
        ]
    if row.status_code in (401, 403):
        if negative:
            return [
                _suggestion(
                    "expected_negative",
                    "low",
                    0.7,
                    row,
                    "负向用例返回鉴权/权限错误，可能正是预期",
                    f"HTTP {row.status_code}：这是鉴权/越权类负向用例，4xx 多半是预期结果。"
                    "若用例仍被判失败，请核对断言是否按预期错误码/错误信息编写。",
                    {"type": "review_assertion_vs_expected", "status_code": row.status_code},
                    "need_review",
                )
            ]
        return [
            _suggestion(
                "parameter_error",
                "high",
                0.82,
                row,
                "鉴权或权限失败",
                f"HTTP {row.status_code}，优先检查 token、账号权限、环境配置",
                {"type": "check_auth"},
                "need_review",
            )
        ]
    if row.status_code == 400 or row.status_code == 422:
        if negative:
            return [
                _suggestion(
                    "expected_negative",
                    "low",
                    0.7,
                    row,
                    "负向用例返回参数错误，可能正是预期",
                    f"HTTP {row.status_code}：这是参数校验/边界/安全类负向用例，4xx 通常是预期结果，"
                    "不要当成被测接口的参数 Bug。请核对断言是否按预期错误码/错误信息编写。",
                    {"type": "review_assertion_vs_expected", "status_code": row.status_code},
                    "need_review",
                )
            ]
        return [
            _suggestion(
                "parameter_error",
                "high",
                0.84,
                row,
                "请求参数疑似错误或缺失",
                _extract_error_text(body) or _clip(row.error_message or row.output_data, 500),
                {"type": "review_request_params"},
                "need_review",
            )
        ]
    if row.status_code and row.status_code >= 500:
        return [
            _suggestion(
                "api_defect",
                "high",
                0.78,
                row,
                "接口疑似服务端异常",
                f"HTTP {row.status_code}，如果请求参数和前置依赖正确，应转 Bug",
                {"type": "create_bug_candidate"},
                "manual_required",
            )
        ]
    return [
        _suggestion(
            "parameter_error",
            "medium",
            0.62,
            row,
            "执行失败，需要结合请求参数和前置依赖复核",
            _clip(row.error_message or row.output_data, 500),
            {"type": "review_failure"},
            "need_review",
        )
    ]


def _detect_sql_assertion_needs(row: TestStepReport, step: TestStep | None, body: Any) -> list[dict[str, Any]]:
    if row.step_type != "http_request" or row.status_code is None or row.status_code >= 400:
        return []
    config = step.config if step is not None and isinstance(step.config, dict) else {}
    method = str(config.get("method") or row.action or "").upper()
    path = str(config.get("path") or row.target or "").lower()
    mutating = method in {"POST", "PUT", "PATCH", "DELETE"} or any(
        word in path for word in ("create", "add", "update", "modify", "delete", "remove", "submit", "save")
    )
    if not mutating:
        return []
    keys = [
        item["path"]
        for item in _leaf_paths(body)
        if _is_dynamic_candidate(str(item["path"]), item["value"])
    ][:5]
    return [
        _suggestion(
            "sql_assertion_needed",
            "medium",
            0.68,
            row,
            "该接口可能需要补充 SQL 断言",
            "这是写操作接口，建议人工确认数据库是否落库、状态是否流转、关联表是否生成",
            {"type": "add_sql_assertion", "candidate_keys": keys, "method": method, "path": path},
            "manual_required",
        )
    ]


def _detect_function_needs(row: TestStepReport, step: TestStep | None, request_data: Any) -> list[dict[str, Any]]:
    haystack = json.dumps(step.config if step is not None else request_data, ensure_ascii=False, default=str).lower()
    hits = [k for k in _FUNCTION_KEYWORDS if k in haystack]
    if not hits:
        return []
    return [
        _suggestion(
            "function_needed",
            "medium",
            0.74,
            row,
            "请求中存在可能需要动态函数生成的参数",
            f"命中字段关键词：{', '.join(hits[:5])}",
            {"type": "add_function", "function": f"function:build_{hits[0]}", "keywords": hits[:5]},
            "manual_required",
        )
    ]


def _detect_data_safety_issues(
    row: TestStepReport,
    step: TestStep | None,
    request_data: Any,
    case_name: str,
) -> list[dict[str, Any]]:
    """执行后诊断测试数据风险，只给修复建议，不在生成阶段改写用例。"""
    if row.step_type != "http_request":
        return []
    config = step.config if step is not None and isinstance(step.config, dict) else {}
    method = str(config.get("method") or row.action or "").upper()
    if method not in _MUTATING_METHODS:
        return []

    payload = _request_payload(config, request_data)
    path = str(config.get("path") or row.target or "")
    text = json.dumps(
        {"case": case_name, "method": method, "path": path, "payload": payload},
        ensure_ascii=False,
        default=str,
    ).lower()
    suggestions: list[dict[str, Any]] = []
    if any(seed in text for seed in _READONLY_SEED_NAMES) and any(word in text for word in _SEED_MUTATION_WORDS):
        suggestions.append(
            _suggestion(
                "data_safety",
                "high",
                0.82,
                row,
                "写操作疑似会修改固定种子账号或系统数据",
                "admin/root/system 等账号可以用于登录、查询、断言，但不建议用于修改密码、改权限、删除、禁用等写操作",
                {
                    "type": "protect_seed_data",
                    "method": method,
                    "path": path,
                    "recommendation": "AI 修复时应改为先创建 AUTO_TEST 临时数据，再修改/删除该临时数据；固定账号仅保留登录或查询用途",
                },
                "manual_required",
            )
        )

    if _is_negative_case(case_name):
        return suggestions

    replacements = _collect_data_safety_replacements(payload)
    if replacements:
        suggestions.append(
            _suggestion(
                "data_safety",
                "medium",
                0.76,
                row,
                "写操作使用了非测试命名空间数据",
                f"建议写入类数据使用 {TEST_DATA_PREFIX} 前缀或 function 动态生成，避免覆盖现有业务数据",
                {
                    "type": "rewrite_test_data",
                    "method": method,
                    "path": path,
                    "namespace": TEST_DATA_PREFIX,
                    "replacements": replacements[:20],
                    "recommendation": "仅在 AI 修复/人工审核阶段应用，不影响原始生成覆盖点",
                },
                "need_review",
            )
        )
    return suggestions


def _request_payload(config: dict[str, Any], request_data: Any) -> Any:
    for key in ("body", "json", "data", "params"):
        value = config.get(key)
        if value not in (None, "", {}, []):
            return value
    if isinstance(request_data, dict):
        for key in ("body", "json", "data", "params"):
            value = request_data.get(key)
            if value not in (None, "", {}, []):
                return value
    return request_data if request_data not in (None, "", {}, []) else config


def _is_negative_case(case_name: str) -> bool:
    return any(keyword in case_name for keyword in _NEGATIVE_CASE_KEYWORDS)


def _collect_data_safety_replacements(value: Any, path: str = "$") -> list[dict[str, Any]]:
    replacements: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if _plain_key(str(key)) else f"{path}['{key}']"
            replacements.extend(_collect_data_safety_replacements(child, child_path))
            replacement = _suggest_test_data_value(str(key), child)
            if replacement:
                replacements.append(
                    {
                        "path": child_path,
                        "current": _clip(child, 120),
                        "suggested": replacement,
                    }
                )
    elif isinstance(value, list):
        for idx, child in enumerate(value[:20]):
            replacements.extend(_collect_data_safety_replacements(child, f"{path}[{idx}]"))
    return replacements


def _suggest_test_data_value(key: str, value: Any) -> str | None:
    if key not in _DATA_FIELD_KEYS and key.lower() not in _DATA_FIELD_KEYS_NORMALIZED:
        return None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or raw.startswith("${") or raw.startswith("function:") or raw.startswith(TEST_DATA_PREFIX):
        return None
    lowered = key.lower()
    if lowered in {"username", "user_name", "account"}:
        return "function:unique(AUTO_TEST_user)"
    if lowered in {"mobile", "phone", "tel"}:
        return "function:unique_mobile()"
    if lowered == "email":
        return "function:unique_email()"
    return f"{TEST_DATA_PREFIX}{raw}"


def _detect_missing_dependencies(response_index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suggestions = []
    previous_values: list[dict[str, Any]] = []
    for item in response_index:
        row = item["row"]
        step = item["step"]
        config = step.config if step is not None and isinstance(step.config, dict) else {}
        request_text = json.dumps(config, ensure_ascii=False, default=str)
        for prev in previous_values:
            value = prev["value"]
            if value in ("", None) or len(str(value)) < 3:
                continue
            if str(value) in request_text:
                already_extract = _has_extract_for_path(prev["step"], prev["path"])
                if already_extract:
                    continue
                suggestions.append(
                    _suggestion(
                        "missing_extraction",
                        "high",
                        0.91,
                        row,
                        f"步骤请求硬编码了前序响应值，建议改成变量 {prev['name']}",
                        f"步骤 {prev['from_order']} 响应 {prev['path']}={value!r}，后续请求中直接使用了该值",
                        {
                            "type": "extract_and_replace",
                            "extract_from_step_report_id": prev["row"].id,
                            "extract_from_step_id": prev["row"].step_id,
                            "jsonpath": prev["path"],
                            "variable": prev["name"],
                            "replace_in_step_report_id": row.id,
                            "replace_in_step_id": row.step_id,
                            "value": value,
                        },
                        "high_confidence",
                    )
                )
        previous_values.extend(_candidate_values(item))
    return suggestions


def _detect_execution_order_and_setup(
    session,
    cases: dict[int, TestCase],
    by_case: dict[int, list[TestStepReport]],
) -> dict[int, list[dict[str, Any]]]:
    """跨用例检查：找出依赖测试数据但准备用例排在后面/不存在的情况。"""
    module_ids = sorted({c.module_id for c in cases.values() if c.module_id is not None})
    if not module_ids:
        return {}
    module_cases = (
        session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.module_id.in_(module_ids), TestCase.case_type == "api")
        .order_by(TestCase.module_id, TestCase.sort_order, TestCase.id)
        .all()
    )
    by_module: dict[int, list[TestCase]] = defaultdict(list)
    for case in module_cases:
        if case.module_id is not None:
            by_module[case.module_id].append(case)

    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for case_id, case in cases.items():
        first_row = (by_case.get(case_id) or [None])[0]
        if first_row is None or case.module_id is None:
            continue
        if not _case_needs_user_setup(case, by_case.get(case_id) or []):
            continue
        candidates = [
            candidate
            for candidate in by_module.get(case.module_id, [])
            if candidate.id != case.id and _case_is_user_setup(candidate)
        ]
        before_candidates = [
            candidate
            for candidate in candidates
            if int(candidate.sort_order or 0) < int(case.sort_order or 0)
        ]
        if before_candidates:
            continue
        after_candidates = [
            candidate
            for candidate in candidates
            if int(candidate.sort_order or 0) >= int(case.sort_order or 0)
        ]
        if after_candidates:
            setup = sorted(after_candidates, key=lambda c: (int(c.sort_order or 0), c.id))[0]
            result[case_id].append(
                _suggestion(
                    "execution_order",
                    "high",
                    0.88,
                    first_row,
                    "依赖账号数据，但创建/注册账号用例排在后面",
                    f"当前用例疑似需要测试账号；可将「{setup.name}」移动到本用例之前，先准备账号再执行。",
                    {
                        "type": "reorder_case_before",
                        "case_id": setup.id,
                        "case_name": setup.name,
                        "before_case_id": case.id,
                        "before_case_name": case.name,
                        "module_id": case.module_id,
                        "reason": "user_setup_dependency",
                    },
                    "high_confidence",
                )
            )
        else:
            result[case_id].append(
                _suggestion(
                    "execution_order",
                    "high",
                    0.7,
                    first_row,
                    "依赖账号数据，但前面没有账号准备用例",
                    "当前用例疑似需要测试用户/账号存在；建议在它前面新增“创建/注册测试账号并提取用户标识”的接口用例。",
                    {
                        "type": "create_setup_case",
                        "module_id": case.module_id,
                        "before_case_id": case.id,
                        "before_case_name": case.name,
                        "setup_kind": "user_account",
                        "required_fields": _case_user_fields(case),
                        "reason": "missing_user_setup_case",
                    },
                    "need_review",
                )
            )
    return result


def _detect_duplicate_cases(
    cases: dict[int, TestCase],
    by_case: dict[int, list[TestStepReport]],
) -> dict[int, list[dict[str, Any]]]:
    """跨用例检查：同一报告内请求步骤完全相同的用例，提示保留一条、删除重复项。"""
    groups: dict[str, list[TestCase]] = defaultdict(list)
    signatures: dict[int, list[dict[str, Any]]] = {}
    for case in cases.values():
        signature = _case_duplicate_signature(case)
        if not signature:
            continue
        key = json.dumps(signature, ensure_ascii=False, sort_keys=True, default=str)
        groups[key].append(case)
        signatures[case.id] = signature

    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        ordered = sorted(duplicates, key=_duplicate_keep_rank)
        keep = ordered[0]
        for duplicate in ordered[1:]:
            first_row = (by_case.get(duplicate.id) or [None])[0]
            if first_row is None:
                continue
            signature = signatures.get(duplicate.id) or []
            request_summary = _duplicate_signature_summary(signature)
            result[duplicate.id].append(
                _suggestion(
                    "重复用例",
                    "medium",
                    0.95,
                    first_row,
                    f"疑似重复用例：与「{keep.name}」请求相同",
                    (
                        f"{request_summary} 的请求签名一致；建议保留 #{keep.id}「{keep.name}」，"
                        f"删除当前重复用例 #{duplicate.id}「{duplicate.name}」。"
                    ),
                    {
                        "type": "delete_duplicate_case",
                        "case_id": duplicate.id,
                        "case_name": duplicate.name,
                        "duplicate_case_id": duplicate.id,
                        "duplicate_case_name": duplicate.name,
                        "keep_case_id": keep.id,
                        "keep_case_name": keep.name,
                        "signature": signature,
                    },
                    "need_review",
                )
            )
    return result


def _duplicate_keep_rank(case: TestCase) -> tuple[int, int, int]:
    """选择保留项：优先保留正常主流程，其次保留排序靠前、ID 更早的用例。"""
    name = case.name or ""
    response_check_penalty = 2 if "响应校验" in name else 0
    normal_bonus = -1 if "正常" in name else 0
    return (response_check_penalty + normal_bonus, int(case.sort_order or 0), case.id)


def _case_duplicate_signature(case: TestCase) -> list[dict[str, Any]]:
    http_steps = _case_http_request_steps(case)
    if not http_steps:
        return []
    return [
        {
            "intent": _case_intent_category(case.name),
            "method": str(item.get("method") or "").upper(),
            "path": _normalize_request_path(item.get("path")),
            "headers": _canonical_request_value(item.get("headers")),
            "params": _canonical_request_value(item.get("params")),
            "body": _canonical_request_value(
                item.get("json")
                if "json" in item
                else item.get("body")
                if "body" in item
                else item.get("data")
            ),
            "data_type": str(item.get("data_type") or item.get("content_type") or "").lower(),
            "assertions": _case_assertion_signature(case, item.get("step_id")),
            "extracts": _case_extract_signature(case, item.get("step_id")),
        }
        for item in http_steps
    ]


def _case_http_request_steps(case: TestCase) -> list[dict[str, Any]]:
    steps = []
    for step in sorted(case.steps or [], key=lambda item: (int(item.step_order or 0), item.id)):
        if step.step_type != "http_request" or not isinstance(step.config, dict):
            continue
        config = dict(step.config)
        config["step_id"] = step.id
        steps.append(config)
    return steps


def _case_intent_category(name: str | None) -> str:
    text = str(name or "")
    m = re.match(r"^\s*【([^】]+)】", text)
    if m:
        return m.group(1).strip()
    for word in ("前置链", "正常", "参数校验", "边界", "鉴权", "越权", "响应校验", "安全", "场景", "关联"):
        if word in text[:20]:
            return word
    return ""


def _case_assertion_signature(case: TestCase, step_id: Any = None) -> list[dict[str, Any]]:
    """重复检测必须考虑断言目标；同请求但校验不同字段，不直接判重复。"""
    raw_assertions: list[Any] = []
    for step in case.steps or []:
        if step_id is not None and step.id != step_id:
            continue
        if step.step_type != "http_request":
            continue
        if isinstance(step.assertion, list):
            raw_assertions.extend(step.assertion)
        elif isinstance(step.assertion, dict):
            raw_assertions.append(step.assertion)
    shaped = []
    for item in raw_assertions:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or item.get("jsonpath") or item.get("path") or "").strip()
        operator = str(item.get("type") or item.get("operator") or "").strip()
        expected = _canonical_request_value(item.get("expected"))
        if target:
            shaped.append({"target": target, "operator": operator, "expected": expected})
    return sorted(shaped, key=lambda x: (x["target"], x["operator"], str(x["expected"])))


def _case_extract_signature(case: TestCase, step_id: Any = None) -> list[str]:
    """重复检测必须考虑提取产出：同请求但一条 extract 出 ${token} 供下游用、
    另一条不 extract——删掉产出方会把整条依赖链弄断，不能互判重复。"""
    names: set[str] = set()
    for step in case.steps or []:
        if step_id is not None and step.id != step_id:
            continue
        if step.step_type != "http_request":
            continue
        extract = step.extract
        if isinstance(extract, list):
            for rule in extract:
                if isinstance(rule, dict) and rule.get("name"):
                    names.add(str(rule.get("name")))
        elif isinstance(extract, dict):
            names.update(str(k) for k in extract.keys())
    return sorted(names)


def _normalize_request_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"/+", "/", text).rstrip("/") or "/"


# function:unique(order_20260705)、function:unique_mobile() 之类动态函数：
# 参数往往是随机/带时间戳的前缀，两条用例只差这个前缀时语义上是同一条——归一化参数再比较。
_FUNC_CALL_RE = re.compile(r"function:([A-Za-z_][\w]*)\s*\([^)]*\)")


def _canonical_request_value(value: Any) -> Any:
    loaded = _load_jsonish(value)
    if isinstance(loaded, str):
        stripped = loaded.strip()
        if not stripped:
            return None
        return _FUNC_CALL_RE.sub(r"function:\1(<ARG>)", stripped)
    if isinstance(loaded, dict):
        return {str(key): _canonical_request_value(loaded[key]) for key in sorted(loaded)}
    if isinstance(loaded, list):
        return [_canonical_request_value(item) for item in loaded]
    return loaded


def _duplicate_signature_summary(signature: list[dict[str, Any]]) -> str:
    if not signature:
        return "HTTP 请求"
    parts = [
        f"{item.get('method') or 'HTTP'} {item.get('path') or ''}".strip()
        for item in signature[:3]
    ]
    suffix = f" 等 {len(signature)} 个步骤" if len(signature) > 3 else ""
    return "、".join(parts) + suffix


def _case_needs_user_setup(case: TestCase, rows: list[TestStepReport]) -> bool:
    if _case_is_user_setup(case):
        return False
    text = _case_text(case)
    failure_text = " ".join(str(x or "") for row in rows for x in (row.error_message, row.output_data)).lower()
    has_user_payload = any(key in text for key in ("username", "user_name", "account", "用户", "账号"))
    has_password = "password" in text or "密码" in text
    has_missing_signal = any(
        word in failure_text
        for word in ("not found", "not exist", "不存在", "未找到", "no such", "账号不存在", "用户不存在")
    )
    name_hints = any(word in (case.name or "") for word in ("详情", "查询", "修改", "更新", "删除", "登录", "校验"))
    return bool((has_user_payload and (has_password or name_hints)) or (has_user_payload and has_missing_signal))


def _case_is_user_setup(case: TestCase) -> bool:
    text = _case_text(case)
    has_user = any(key in text for key in ("username", "user_name", "account", "用户", "账号", "user"))
    has_create = any(word in text for word in ("create", "register", "signup", "add", "新增", "创建", "注册", "准备"))
    has_delete = any(word in text for word in ("delete", "remove", "删除", "禁用"))
    method = _first_http_method(case)
    return bool(has_user and has_create and not has_delete and method in {"POST", "PUT", "PATCH", ""})


def _first_http_method(case: TestCase) -> str:
    for step in case.steps or []:
        if step.step_type == "http_request" and isinstance(step.config, dict):
            return str(step.config.get("method") or "").upper()
    return ""


def _case_user_fields(case: TestCase) -> list[str]:
    text = _case_text(case)
    fields = []
    for key in ("username", "user_name", "account", "password", "mobile", "phone", "email"):
        if key in text:
            fields.append(key)
    if "用户" in text and "username" not in fields:
        fields.append("username")
    if "密码" in text and "password" not in fields:
        fields.append("password")
    return fields or ["username", "password"]


def _case_text(case: TestCase) -> str:
    parts: list[Any] = [
        case.name,
        case.description,
    ]
    for step in case.steps or []:
        parts.extend([step.step_name, step.step_type, step.config, step.extract, step.assertion])
    return json.dumps(parts, ensure_ascii=False, default=str).lower()


def _candidate_values(item: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for path_item in item["paths"]:
        name = str(path_item["path"]).split(".")[-1].strip("[]'")
        value = path_item["value"]
        if not _is_dynamic_candidate(name, value):
            continue
        out.append(
            {
                "row": item["row"],
                "step": item["step"],
                "from_order": item["order"],
                "name": _variable_name(name),
                "path": path_item["path"],
                "value": value,
            }
        )
    return out[:30]


def _candidate_paths(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [p for p in paths if _is_dynamic_candidate(str(p["path"]), p["value"])]
    return [{"jsonpath": p["path"], "value": _clip(repr(p["value"]), 120)} for p in items[:10]]


def _leaf_paths(obj: Any, base: str = "$") -> list[dict[str, Any]]:
    out = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{base}.{key}" if _plain_key(str(key)) else f"{base}['{key}']"
            out.extend(_leaf_paths(value, path))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj[:5]):
            out.extend(_leaf_paths(value, f"{base}[{idx}]"))
    else:
        out.append({"path": base, "value": obj})
    return out


def _build_summary(cases: list[dict[str, Any]], suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    for s in suggestions:
        by_category[s["category"]] += 1
        by_severity[s["severity"]] += 1
    return {
        "total_cases": len(cases),
        "total_suggestions": len(suggestions),
        "by_category": dict(by_category),
        "by_severity": dict(by_severity),
        "high_confidence": sum(1 for s in suggestions if s["apply_mode"] == "high_confidence"),
        "need_review": sum(1 for s in suggestions if s["apply_mode"] == "need_review"),
        "manual_required": sum(1 for s in suggestions if s["apply_mode"] == "manual_required"),
    }


def _attach_ai_summary(session, output: dict[str, Any], model_name: str) -> None:
    try:
        from ai_gateway.gateway import chat_markdown
        from server.services.ai_model_service import get_ai_model

        cfg = get_ai_model(session, model_name, project_id=output.get("project_id"))
        if cfg is None or not cfg.enabled:
            output["ai_error"] = f"AI 模型 {model_name!r} 未配置或未启用，已返回规则分析结果"
            return
        compact = {
            "summary": output["summary"],
            "cases": [
                {
                    "case_id": c["case_id"],
                    "name": c["name"],
                    "classification": c["classification"],
                    "suggestions": [
                        {
                            "category": s["category"],
                            "severity": s["severity"],
                            "title": s["title"],
                            "evidence": s["evidence"],
                            "action": s["action"],
                            "apply_mode": s["apply_mode"],
                        }
                        for s in c["suggestions"][:12]
                    ],
                }
                for c in output["cases"][:30]
            ],
        }
        prompt = (
            "你是资深接口自动化测试架构师。请基于下面的规则诊断结果，输出给测试人员看的中文 Markdown 总结。\n"
            "要求：\n"
            "1. 不要编造规则结果之外的字段和值。\n"
            "2. 按优先级说明：假通过(HTTP通过但业务失败/断言偏弱)、重复用例、执行顺序/前置数据、应补变量提取、应补断言、参数错误、SQL 断言、function 补充、数据安全、负向用例(确认断言)、环境问题、接口问题。\n"
            "3. 明确哪些建议可高置信一键应用，哪些必须人工审核。\n\n"
            f"规则诊断结果：\n{json.dumps(compact, ensure_ascii=False)[:18000]}"
        )
        md, tokens_in, tokens_out = chat_markdown(prompt, cfg, timeout=180)
        output["ai_summary"] = md
        output["ai_tokens"] = {"tokens_in": tokens_in, "tokens_out": tokens_out}
    except Exception as exc:  # noqa: BLE001
        output["ai_error"] = f"{type(exc).__name__}: {exc}"


def _suggestion(
    category: str,
    severity: str,
    confidence: float,
    row: TestStepReport,
    title: str,
    evidence: str | None,
    action: dict[str, Any],
    apply_mode: str,
) -> dict[str, Any]:
    return {
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "case_id": row.case_id,
        "step_report_id": row.id,
        "step_id": row.step_id,
        "step_name": row.step_name,
        "title": title,
        "evidence": _clip(evidence, 800),
        "action": action,
        "apply_mode": apply_mode,
    }


def _load_jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _try_extract(body: Any, path: str) -> Any:
    try:
        return extractor(body, path)
    except Exception:
        return None


def _best_path_for_name(body: Any, name: str) -> str | None:
    if not name:
        return None
    aliases = _name_aliases(name)
    best = None
    for item in _leaf_paths(body):
        path = item["path"]
        tail = _norm_key(path.split(".")[-1].strip("[]'"))
        if tail in aliases:
            return path
        if aliases & _path_aliases(path):
            best = best or path
        if _looks_like_token_name(name) and _looks_like_token_value(item["value"]):
            best = best or path
    return best


def _name_aliases(name: str) -> set[str]:
    norm = _norm_key(name)
    aliases = {norm}
    if norm in {"accesstoken", "access_token", "token", "jwt", "bearertoken"}:
        aliases |= {"token", "accesstoken", "accessToken".lower(), "jwt", "bearertoken"}
    if norm in {"refreshtoken", "refresh_token"}:
        aliases |= {"refreshtoken", "refreshToken".lower()}
    if norm.endswith("token"):
        aliases.add("token")
    return {_norm_key(item) for item in aliases if item}


def _path_aliases(path: str) -> set[str]:
    parts = re.split(r"[\.\[\]']+", path)
    return {_norm_key(part) for part in parts if part}


def _looks_like_token_name(name: str) -> bool:
    return "token" in _norm_key(name) or _norm_key(name) in {"jwt", "authorization"}


def _looks_like_token_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(re.fullmatch(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", text) or len(text) >= 40 and "." in text)


def _has_extract_for_path(step: TestStep | None, path: str) -> bool:
    if step is None or not isinstance(step.extract, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("jsonpath") or item.get("path") or item.get("expr") or "") == path
        for item in step.extract
    )


def _is_dynamic_candidate(name: str, value: Any) -> bool:
    lowered = name.lower()
    if any(k in lowered for k in _DYNAMIC_KEYWORDS):
        return value not in (None, "", [], {})
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F-]{16,}", value):
        return True
    return isinstance(value, int) and value > 0 and any(k in lowered for k in ("id", "no"))


def _variable_name(name: str) -> str:
    clean = re.sub(r"[^0-9a-zA-Z_]+", "_", name).strip("_").lower()
    return clean or "extracted_value"


def _plain_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key))


def _norm_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _extract_error_text(body: Any) -> str | None:
    if isinstance(body, dict):
        for key in ("message", "msg", "error", "detail", "desc"):
            if key in body and body[key]:
                return _clip(str(body[key]), 500)
    return None


def _case_status(rows: list[TestStepReport]) -> str:
    statuses = {(r.status or "").lower() for r in rows}
    if statuses & {"failed", "fail", "broken", "error"}:
        return "failed"
    if statuses and statuses <= {"passed", "pass", "success", "skipped"}:
        return "passed"
    return "unknown"


def _classify_case(suggestions: list[dict[str, Any]], rows: list[TestStepReport]) -> str:
    cats = {s["category"] for s in suggestions}
    if "false_pass" in cats:
        return "假通过(需复核)"
    if "environment_issue" in cats:
        return "环境问题"
    if "api_defect" in cats:
        return "接口问题"
    if cats & {"missing_extraction", "missing_assertion", "parameter_error", "function_needed", "data_safety", "execution_order", "重复用例"}:
        return "用例需优化"
    if "expected_negative" in cats:
        return "负向用例(确认断言)"
    if _case_status(rows) == "passed":
        return "基本正常"
    return "待复核"


def _safe_preview(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return _load_jsonish(_clip(text, 1000))
    return _clip(str(value), 1000) if value is not None else None


def _clip(value: Any, size: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= size else text[:size] + "..."
