"""API 用例工作台所需的分页列表、测试记录和编辑记录接口。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_

from server.api.deps import DBDep
from database.models import (
    ApiCaseEditHistory,
    CASE_TYPE_API,
    EditOperationEvent,
    TestStep,
    TestCase,
    TestReport,
    TestStepReport,
)
from database.models.edit_operation import ENTITY_TYPE_TEST_CASE
from server.services.edit_history_service import serialize_test_case_event

router = APIRouter(prefix="/api_cases", tags=["api_cases"])


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


def _serialize_case(case: TestCase, latest_run: dict | None) -> dict:
    return {
        "id": case.id,
        "module_id": case.module_id,
        "name": case.name,
        "description": case.description,
        "skip": bool(case.skip),
        "case_type": case.case_type,
        "tags": case.tags or [],
        "priority": case.priority,
        "sort_order": case.sort_order,
        "method": case.method,
        "path": case.path,
        "headers": case.headers,
        "data_type": case.data_type,
        "params": case.params,
        "extract_data": case.extract_data,
        "sql_query": case.sql_query,
        "assertion": case.assertion,
        "wait_time": case.wait_time,
        "latest_run": latest_run,
    }


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


def _serialize_run_step(step: TestStepReport, step_def: TestStep | None = None) -> dict:
    input_data = _jsonish(step.input_data)
    return {
        "step_report_id": step.id,
        "step_id": step.step_id,
        "step_name": step.step_name,
        "step_type": step.step_type,
        "status": step.status,
        "status_code": step.status_code,
        "duration": step.duration,
        "request": {
            "method": step.action,
            "url": step.target,
            "headers": input_data.get("headers") if isinstance(input_data, dict) else None,
            "params": input_data,
        },
        "response": _jsonish(step.output_data),
        "assertion": {
            "configured": step_def.assertion if step_def is not None else None,
            "results": _jsonish(step.assertion_results),
        },
        "extract": {
            "configured": step_def.extract if step_def is not None else None,
            "values": _jsonish(step.extract_values),
        },
        "error_message": step.error_message,
        "create_time": step.create_time.isoformat() if step.create_time else None,
    }


@router.get("")
def list_api_cases(
    db: DBDep,
    module_id: int = Query(...),
    status: str | None = Query(None, description="多值逗号分隔，可包含 pending"),
    keyword: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=0, le=500, description="0 表示不分页"),
):
    """列出模块内 API 用例，并附最近一次自动执行结果。"""
    query = db.session.query(TestCase).filter(
        TestCase.module_id == module_id,
        TestCase.case_type == CASE_TYPE_API,
    )
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        query = query.filter(or_(TestCase.name.ilike(pattern), TestCase.path.ilike(pattern)))
    cases = query.order_by(TestCase.sort_order.asc(), TestCase.id.asc()).all()
    latest_map = _latest_runs(db, [case.id for case in cases])
    wanted = {item.strip().lower() for item in status.split(",") if item.strip()} if status else None
    filtered = [
        case for case in cases
        if wanted is None or (latest_map.get(case.id, {}).get("status") or "pending") in wanted
    ]
    total = len(filtered)
    if page_size == 0:
        page = 1
        page_cases = filtered
    else:
        start = (page - 1) * page_size
        page_cases = filtered[start:start + page_size]
    items = [_serialize_case(case, latest_map.get(case.id)) for case in page_cases]
    return {
        "status": "success",
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }


@router.get("/edit_history")
def list_edit_history(
    db: DBDep,
    module_id: int = Query(...),
    limit: int = Query(200, ge=1, le=500),
):
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
            TestCase.case_type == CASE_TYPE_API,
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
    data = [serialize_test_case_event(row) for row in event_rows]
    data.extend(row.to_dict() for row in rows)
    data.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"status": "success", "data": data[:limit]}


@router.get("/test_history")
def list_test_history(
    db: DBDep,
    module_id: int = Query(...),
    limit: int = Query(100, ge=1, le=500),
):
    """按 TestReport 聚合当前模块 API 用例的自动执行记录。"""
    case_rows = (
        db.session.query(TestCase.id, TestCase.name)
        .filter(TestCase.module_id == module_id, TestCase.case_type == CASE_TYPE_API)
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
    """读取 API 用例最近一次执行详情，用于点击状态查看请求/响应/断言/提取。"""
    case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None or case.case_type != CASE_TYPE_API:
        raise HTTPException(status_code=404, detail="API 用例不存在")

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
    variable_pool = {}
    for step in steps:
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
            "steps": [_serialize_run_step(step, step_defs.get(step.step_id)) for step in steps],
        },
    }


@router.get("/{case_id}/runs")
def list_case_runs(case_id: int, db: DBDep, limit: int = Query(20, ge=1, le=200)):
    case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None or case.case_type != CASE_TYPE_API:
        raise HTTPException(status_code=404, detail="API 用例不存在")
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
