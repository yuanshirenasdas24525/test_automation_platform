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
        step_suggestions.extend(_classify_failure(row, body))
        step_suggestions.extend(_detect_function_needs(row, step, request_data))
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
    configured = step.extract if step is not None and isinstance(step.extract, list) else []
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


def _classify_failure(row: TestStepReport, body: Any) -> list[dict[str, Any]]:
    status = (row.status or "").lower()
    if status in ("passed", "pass", "success", "skipped"):
        return []
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

        cfg = get_ai_model(session, model_name)
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
            "2. 按优先级说明：应补变量提取、应补断言、参数错误、SQL 断言、function 补充、环境问题、接口问题。\n"
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
    norm = _norm_key(name)
    best = None
    for item in _leaf_paths(body):
        path = item["path"]
        tail = _norm_key(path.split(".")[-1].strip("[]'"))
        if tail == norm:
            return path
        if norm and norm in tail:
            best = best or path
    return best


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
    if "environment_issue" in cats:
        return "环境问题"
    if "api_defect" in cats:
        return "接口问题"
    if cats & {"missing_extraction", "missing_assertion", "parameter_error", "function_needed"}:
        return "用例需优化"
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
