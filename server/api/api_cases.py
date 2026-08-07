"""API 用例工作台所需的分页列表、测试记录和编辑记录接口。"""
from __future__ import annotations

import json
from typing import Annotated, Optional

import pydantic
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_

from server.api.auth import _get_optional_user
from server.api.deps import DBDep
from database.models import (
    ApiCaseEditHistory,
    AUTOMATED_CASE_TYPES,
    CASE_TYPE_API,
    EditOperationEvent,
    TestStep,
    TestCase,
    TestReport,
    TestStepReport,
    User,
)
from database.models.edit_operation import ENTITY_TYPE_TEST_CASE
from server.services.edit_history_service import merge_test_case_edit_history
from server.services.api_case_admission import needs_manual_adjustment

# 可选当前用户：带 token 解出 User，否则 None（记录清除标记的 operator 用）
OptionalUserDep = Annotated[Optional[User], Depends(_get_optional_user)]

router = APIRouter(prefix="/api_cases", tags=["api_cases"])


def validate_automation_case_type(case_type: str) -> str:
    """校验并规范化自动化用例类型。"""
    normalized_case_type = case_type.strip().lower()
    if normalized_case_type not in AUTOMATED_CASE_TYPES:
        raise HTTPException(status_code=422, detail="case_type 只支持 api/web/android/ios/mixed")
    return normalized_case_type


def _aggregate_status(statuses: list[str | None]) -> str:
    normalized = [str(status or "").lower() for status in statuses]
    if "error" in normalized or "broken" in normalized:
        return "error"
    if "failed" in normalized:
        return "failed"
    if normalized and all(status == "skipped" for status in normalized):
        return "skipped"
    if "passed" in normalized:
        return "passed"
    return "pending"


def _latest_runs(db, case_ids: list[int]) -> dict[int, dict]:
    if not case_ids:
        return {}
    latest_sq = (
        db.session.query(
            TestStepReport.case_id.label("case_id"),
            func.max(TestStepReport.report_id).label("report_id"),
        )
        .filter(TestStepReport.case_id.in_(case_ids))
        .group_by(TestStepReport.case_id)
        .subquery()
    )
    rows = (
        db.session.query(TestStepReport, TestReport)
        .join(
            latest_sq,
            (TestStepReport.case_id == latest_sq.c.case_id)
            & (TestStepReport.report_id == latest_sq.c.report_id),
        )
        .outerjoin(TestReport, TestReport.id == TestStepReport.report_id)
        .all()
    )
    grouped: dict[int, list[tuple[TestStepReport, TestReport | None]]] = {}
    for step, report in rows:
        grouped.setdefault(step.case_id, []).append((step, report))
    result: dict[int, dict] = {}
    for case_id, values in grouped.items():
        steps = [item[0] for item in values]
        report = values[0][1]
        executed_at = max(
            (step.create_time for step in steps if step.create_time),
            default=report.end_time if report else None,
        )
        result[case_id] = {
            "status": _aggregate_status([step.status for step in steps]),
            "report_id": steps[0].report_id,
            "executed_at": executed_at.isoformat() if executed_at else None,
            "duration": sum(float(step.duration or 0) for step in steps),
        }
    return result


def _serialize_case(
    case: TestCase,
    latest_run: dict | None,
    step_count: int = 0,
    ai_flag: dict | None = None,
    first_http_config: dict | None = None,
) -> dict:
    http_config = first_http_config or {}
    return {
        "ai_flag": ai_flag,
        "id": case.id,
        "module_id": case.module_id,
        "name": case.name,
        "description": case.description,
        "skip": bool(case.skip),
        "case_type": case.case_type,
        "tags": case.tags or [],
        "priority": case.priority,
        "sort_order": case.sort_order,
        "method": http_config.get("method"),
        "path": http_config.get("path"),
        "headers": http_config.get("headers"),
        "data_type": http_config.get("data_type"),
        "params": http_config.get("params"),
        "extract_data": None,
        "sql_query": http_config.get("sql_query"),
        "assertion": None,
        "wait_time": None,
        "repeat_count": getattr(case, "repeat_count", 1) or 1,
        "source": getattr(case, "source", "manual") or "manual",
        "generation_metadata": getattr(case, "generation_metadata", None),
        # 步骤数：>1 视为"多步骤用例"，前端换图标
        "step_count": step_count,
        "latest_run": latest_run,
    }


def _step_counts(db, case_ids: list[int]) -> dict[int, int]:
    """一次 group by 查回每条用例的步骤数，避免 N+1。"""
    if not case_ids:
        return {}
    from database.models.test_step import TestStep
    from sqlalchemy import func
    rows = (
        db.session.query(TestStep.case_id, func.count(TestStep.id))
        .filter(TestStep.case_id.in_(case_ids))
        .group_by(TestStep.case_id)
        .all()
    )
    return {cid: int(n) for cid, n in rows}


def _first_http_configs(db, case_ids: list[int]) -> dict[int, dict]:
    """取每条用例第一条 http_request step 的 config，列表展示用。"""
    if not case_ids:
        return {}
    rows = (
        db.session.query(TestStep)
        .filter(TestStep.case_id.in_(case_ids), TestStep.step_type == "http_request")
        .order_by(TestStep.case_id.asc(), TestStep.step_order.asc(), TestStep.id.asc())
        .all()
    )
    result: dict[int, dict] = {}
    for step in rows:
        if step.case_id not in result and isinstance(step.config, dict):
            result[step.case_id] = step.config
    return result


def _jsonish(value):
    """尽量把 JSON 字符串还原，失败时返回原文本，便于前端展示。"""
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


def _configured_extract(step_def: TestStep | None, config: dict) -> list[dict] | None:
    if step_def is None:
        return None
    return step_def.extract


def _configured_assertion(step_def: TestStep | None, config: dict) -> list[dict] | None:
    if step_def is None:
        return None
    return step_def.assertion


def _serialize_run_step(step: TestStepReport, step_def: TestStep | None = None) -> dict:
    input_data = _jsonish(step.input_data)
    request_body = input_data.get("body") if isinstance(input_data, dict) else None
    request_body_template = input_data.get("body_template") if isinstance(input_data, dict) else None
    request_headers = input_data.get("headers") if isinstance(input_data, dict) else None
    request_params = input_data.get("params") if isinstance(input_data, dict) else None
    request_method = input_data.get("method") if isinstance(input_data, dict) else _jsonish(step.action)
    request_url = input_data.get("url") if isinstance(input_data, dict) else _jsonish(step.target)
    variable_pool = input_data.get("variable_pool") if isinstance(input_data, dict) else None
    config = step_def.config if step_def is not None and isinstance(step_def.config, dict) else {}
    return {
        "step_report_id": step.id,
        "step_id": step.step_id,
        "step_name": step.step_name,
        "step_type": step.step_type,
        "status": step.status,
        "status_code": step.status_code,
        "duration": step.duration,
        "request": {
            "method": request_method,
            "url": request_url,
            "headers": request_headers,
            "params": request_params or config.get("params"),
            "body_template": request_body_template,
            "body": request_body,
            "variable_pool": variable_pool,
        },
        "response": _jsonish(step.output_data),
        "assertion": {
            "configured": _configured_assertion(step_def, config),
            "results": _jsonish(step.assertion_results),
        },
        "extract": {
            "configured": _configured_extract(step_def, config),
            "values": _jsonish(step.extract_values),
        },
        "error_message": step.error_message,
        "create_time": step.create_time.isoformat() if step.create_time else None,
    }


@router.get("")
def list_api_cases(
    db: DBDep,
    module_id: int = Query(...),
    case_type: str = Query(CASE_TYPE_API, description="用例类型：api/web/android/ios/mixed"),
    status: str | None = Query(None, description="多值逗号分隔，可包含 pending"),
    keyword: str | None = Query(None),
    flag_type: str | None = Query(None, description="按 AI 标记筛选：manual_fix/interface_defect/environment/ai_fixed"),
    manual_adjustment: bool = Query(False, description="只看生成后等待人工调整的用例"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=0, le=500, description="0 表示不分页"),
):
    """列出模块内自动化用例，并附最近一次自动执行结果 + AI 诊断标记。"""
    from server.services.ai_flag_service import get_active_flags

    normalized_case_type = validate_automation_case_type(case_type)
    query = db.session.query(TestCase).filter(
        TestCase.module_id == module_id,
        TestCase.case_type == normalized_case_type,
    )
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.filter(or_(TestCase.name.ilike(pattern), TestCase.path.ilike(pattern)))
    cases = query.order_by(TestCase.sort_order.asc(), TestCase.id.asc()).all()
    latest_map = _latest_runs(db, [case.id for case in cases])
    try:
        flag_map = get_active_flags(db.session, [case.id for case in cases])
    except Exception:  # noqa: BLE001 —— ai_case_flags 表未迁移时不拖垮整个列表
        db.session.rollback()
        flag_map = {}
    wanted = {item.strip().lower() for item in status.split(",") if item.strip()} if status else None
    filtered = [
        case for case in cases
        if wanted is None or (latest_map.get(case.id, {}).get("status") or "pending") in wanted
    ]
    if flag_type and flag_type.strip():
        want_flag = flag_type.strip()
        filtered = [
            case for case in filtered
            if (flag_map.get(case.id) or {}).get("flag_type") == want_flag
        ]
    if manual_adjustment:
        filtered = [
            case for case in filtered
            if needs_manual_adjustment(getattr(case, "generation_metadata", None))
        ]
    total = len(filtered)
    if page_size == 0:
        page = 1
        page_cases = filtered
    else:
        start = (page - 1) * page_size
        page_cases = filtered[start:start + page_size]
    step_count_map = _step_counts(db, [case.id for case in page_cases])
    first_config_map = _first_http_configs(db, [case.id for case in page_cases])
    items = [
        _serialize_case(
            case,
            latest_map.get(case.id),
            step_count_map.get(case.id, 0),
            ai_flag=flag_map.get(case.id),
            first_http_config=first_config_map.get(case.id),
        )
        for case in page_cases
    ]
    return {
        "status": "success",
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }


# ---------------------------------------------------------------------------
# AI 诊断标记：清除（即反馈）/ 历史 / 模块树计数
# ---------------------------------------------------------------------------
class AiFlagClearRequest(pydantic.BaseModel):
    reason: str                                  # manually_fixed | misjudged | external_fixed | wont_fix
    corrected_classification: str | None = None  # misjudged 时必填：正常/用例问题/接口问题/环境/其他
    note: str | None = None


@router.post("/{case_id}/ai_flag/clear")
def clear_ai_flag(case_id: int, payload: AiFlagClearRequest, db: DBDep, user: OptionalUserDep = None):
    """清除用例的 AI 标记。清除原因会作为反馈回流给下次 AI 诊断：
    - misjudged（判断有误）+ 更正分类 → 下次诊断注入该用例的 user_feedback；
    - wont_fix / 更正为「正常」 → 预检层直接跳过该用例的自动修复；
    - manually_fixed 的备注（改了什么）→ 作为经验注入。"""
    from server.services.ai_flag_service import clear_flag, serialize_flag

    case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="用例不存在")
    try:
        flag = clear_flag(
            db.session,
            case_id,
            reason=payload.reason,
            corrected_classification=payload.corrected_classification,
            note=payload.note,
            operator_id=user.id if user else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if flag is None:
        raise HTTPException(status_code=404, detail="该用例没有待清除的 AI 标记")
    return {"status": "success", "data": serialize_flag(flag)}


@router.get("/{case_id}/ai_flags")
def list_ai_flags(case_id: int, db: DBDep, limit: int = Query(20, ge=1, le=100)):
    """用例的 AI 标记历史（含已清除的反馈记录）。"""
    from server.services.ai_flag_service import flag_history

    return {"status": "success", "data": flag_history(db.session, case_id, limit=limit)}


@router.get("/flag_counts")
def ai_flag_counts(db: DBDep, project_id: int = Query(...)):
    """项目内各模块 active 标记计数（含子树聚合），模块卡片角标用。"""
    from server.services.ai_flag_service import module_flag_counts

    counts = module_flag_counts(db.session, project_id)
    return {"status": "success", "data": {str(k): v for k, v in counts.items()}}


@router.get("/edit_history")
def list_edit_history(
    db: DBDep,
    module_id: int = Query(...),
    case_type: str = Query(CASE_TYPE_API),
    limit: int = Query(200, ge=1, le=500),
):
    normalized_case_type = validate_automation_case_type(case_type)
    rows = (
        db.session.query(ApiCaseEditHistory)
        .filter(ApiCaseEditHistory.module_id == module_id)
        .order_by(ApiCaseEditHistory.created_at.desc(), ApiCaseEditHistory.id.desc())
        .limit(limit)
        .all()
    )
    case_ids = [
        case_id for (case_id,) in db.session.query(TestCase.id).filter(
            TestCase.module_id == module_id,
            TestCase.case_type == normalized_case_type,
        ).all()
    ]
    event_rows = []
    if case_ids:
        event_rows = (
            db.session.query(EditOperationEvent)
            .filter(
                EditOperationEvent.entity_type == ENTITY_TYPE_TEST_CASE,
                EditOperationEvent.entity_id.in_(case_ids),
            )
            .order_by(EditOperationEvent.created_at.desc(), EditOperationEvent.id.desc())
            .limit(limit)
            .all()
        )
    data = merge_test_case_edit_history(event_rows, rows, limit=limit)
    return {"status": "success", "data": data}


@router.get("/test_history")
def list_test_history(
    db: DBDep,
    module_id: int = Query(...),
    case_type: str = Query(CASE_TYPE_API),
    limit: int = Query(100, ge=1, le=500),
):
    """按 TestReport 聚合当前模块自动化用例的执行记录。"""
    normalized_case_type = validate_automation_case_type(case_type)
    case_rows = (
        db.session.query(TestCase.id, TestCase.name)
        .filter(TestCase.module_id == module_id, TestCase.case_type == normalized_case_type)
        .all()
    )
    case_names = {case_id: name for case_id, name in case_rows}
    if not case_names:
        return {"status": "success", "data": []}
    report_ids = (
        db.session.query(TestStepReport.report_id)
        .filter(TestStepReport.case_id.in_(case_names))
        .distinct()
    )
    reports = (
        db.session.query(TestReport)
        .filter(TestReport.id.in_(report_ids))
        .order_by(TestReport.start_time.desc(), TestReport.id.desc())
        .limit(limit)
        .all()
    )
    if not reports:
        return {"status": "success", "data": []}
    steps = (
        db.session.query(TestStepReport)
        .filter(
            TestStepReport.report_id.in_([report.id for report in reports]),
            TestStepReport.case_id.in_(case_names),
        )
        .order_by(TestStepReport.create_time.asc(), TestStepReport.id.asc())
        .all()
    )
    grouped: dict[int, dict[int, list[TestStepReport]]] = {}
    for step in steps:
        grouped.setdefault(step.report_id, {}).setdefault(step.case_id, []).append(step)
    data = []
    for report in reports:
        case_results = []
        for case_id, case_steps in grouped.get(report.id, {}).items():
            case_results.append({
                "case_id": case_id,
                "case_name": case_names.get(case_id, ""),
                "status": _aggregate_status([step.status for step in case_steps]),
                "duration": sum(float(step.duration or 0) for step in case_steps),
                "error_message": next(
                    (step.error_message for step in case_steps if step.error_message),
                    None,
                ),
            })
        counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "pending": 0}
        for item in case_results:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        data.append({
            "report_id": report.id,
            "status": report.status,
            "started_at": report.start_time.isoformat() if report.start_time else None,
            "finished_at": report.end_time.isoformat() if report.end_time else None,
            "duration": report.duration,
            "executor": report.executor,
            "allure_url": report.allure_url,
            "counts": counts,
            "cases": case_results,
        })
    return {"status": "success", "data": data}


@router.get("/{case_id}/latest_run_detail")
def get_latest_run_detail(case_id: int, db: DBDep):
    """读取自动化用例最近一次执行详情，用于点击状态查看步骤明细。"""
    case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None or case.case_type not in AUTOMATED_CASE_TYPES:
        raise HTTPException(status_code=404, detail="自动化用例不存在")

    latest = (
        db.session.query(TestStepReport.report_id)
        .filter(TestStepReport.case_id == case_id)
        .order_by(TestStepReport.report_id.desc())
        .first()
    )
    if not latest:
        raise HTTPException(status_code=404, detail="该用例还没有执行记录")

    report_id = latest[0]
    report = db.session.query(TestReport).filter(TestReport.id == report_id).first()
    steps = (
        db.session.query(TestStepReport)
        .filter(TestStepReport.case_id == case_id, TestStepReport.report_id == report_id)
        .order_by(TestStepReport.create_time.asc(), TestStepReport.id.asc())
        .all()
    )
    step_defs = {
        step.id: step
        for step in db.session.query(TestStep).filter(TestStep.case_id == case_id).all()
    }
    fallback_http_step = (
        db.session.query(TestStep)
        .filter(TestStep.case_id == case_id, TestStep.step_type == "http_request")
        .order_by(TestStep.step_order.asc(), TestStep.id.asc())
        .first()
    )
    variable_pool = {}
    for step in steps:
        input_data = _jsonish(step.input_data)
        if isinstance(input_data, dict) and isinstance(input_data.get("variable_pool"), dict):
            variable_pool.update(input_data["variable_pool"])
        values = _jsonish(step.extract_values)
        if isinstance(values, dict):
            variable_pool.update(values)

    return {
        "status": "success",
        "data": {
            "case_id": case.id,
            "case_name": case.name,
            "report_id": report_id,
            "status": _aggregate_status([step.status for step in steps]),
            "executed_at": report.start_time.isoformat() if report and report.start_time else None,
            "duration": sum(float(step.duration or 0) for step in steps),
            "variable_pool": variable_pool,
            "steps": [
                _serialize_run_step(step, step_defs.get(step.step_id) or fallback_http_step)
                for step in steps
            ],
        },
    }


@router.get("/{case_id}/runs")
def list_case_runs(case_id: int, db: DBDep, limit: int = Query(20, ge=1, le=200)):
    case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None or case.case_type not in AUTOMATED_CASE_TYPES:
        raise HTTPException(status_code=404, detail="自动化用例不存在")
    report_ids = (
        db.session.query(TestStepReport.report_id)
        .filter(TestStepReport.case_id == case_id)
        .distinct()
        .order_by(TestStepReport.report_id.desc())
        .limit(limit)
        .all()
    )
    ids = [row[0] for row in report_ids]
    if not ids:
        return {"status": "success", "data": []}
    reports = {
        report.id: report
        for report in db.session.query(TestReport).filter(TestReport.id.in_(ids)).all()
    }
    steps = (
        db.session.query(TestStepReport)
        .filter(TestStepReport.case_id == case_id, TestStepReport.report_id.in_(ids))
        .all()
    )
    grouped: dict[int, list[TestStepReport]] = {}
    for step in steps:
        grouped.setdefault(step.report_id, []).append(step)
    data = []
    for report_id in ids:
        report = reports.get(report_id)
        case_steps = grouped.get(report_id, [])
        data.append({
            "report_id": report_id,
            "status": _aggregate_status([step.status for step in case_steps]),
            "executed_at": (
                report.start_time.isoformat()
                if report and report.start_time
                else None
            ),
            "duration": sum(float(step.duration or 0) for step in case_steps),
            "error_message": next((step.error_message for step in case_steps if step.error_message), None),
        })
    return {"status": "success", "data": data}
