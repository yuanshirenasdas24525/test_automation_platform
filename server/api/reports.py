"""
/api/reports/* 执行记录。

三个接口：
  - GET    /reports                 列表（可按 project_id / category / status 过滤 + 分页）
  - GET    /reports/{report_id}     详情（含步骤级结果）
  - DELETE /reports/{report_id}     删除单条

数据源是 `test_reports` + `test_step_reports`。列表用 outerjoin 带上 project name，
避免前端再发 N 个请求。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query

from database.models import (
    AiRun,
    AI_FEATURE_TEST_RESULT_ANALYSIS,
    AI_RUN_STATUS_PENDING,
    Project,
    TestReport,
    TestStepReport,
)
from server.api.deps import DBDep

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportAiAnalysisRequest(pydantic.BaseModel):
    """提交执行结果 AI 分析任务。"""
    model_name: str | None = None
    operator: str | None = None


# ---------------------------------------------------------------------------
# Stale "running" 扫除
# ---------------------------------------------------------------------------
# 背景：老版本 run_test_task 的异常路径不会更新 TestReport.status，导致任务挂了之后
# 报告永远卡在 "running"。现在 task 层已经修了，但库里可能还留着一批死记录。这里在
# 列表接口里顺手做一次兜底：超过阈值还没收尾的 running 报告，强制刷成 fail。
STALE_RUNNING_THRESHOLD = timedelta(minutes=30)


def _sweep_stale_running(db) -> int:
    """把卡了太久的 running 报告刷成 fail。返回影响条数。"""
    cutoff = datetime.now() - STALE_RUNNING_THRESHOLD
    stale = (
        db.session.query(TestReport)
        .filter(TestReport.status == "running")
        .filter(TestReport.start_time < cutoff)
        .all()
    )
    for r in stale:
        r.status = "fail"
        r.end_time = r.end_time or datetime.now()
        r.summary = (r.summary or "任务异常结束，状态被自动兜底")[:500]
    if stale:
        db.session.flush()
    return len(stale)


def _serialize_report(report: TestReport, project_name: Optional[str]) -> dict:
    """把 ORM 对象捋成前端用的平铺字典，时间字段都走 isoformat。"""
    return {
        "id": report.id,
        "project_id": report.project_id,
        "project_name": project_name,
        "category": report.category,
        "scene_name": report.scene_name,
        "executor": report.executor,
        "status": report.status,
        "total_count": report.total_count or 0,
        "pass_count": report.pass_count or 0,
        "fail_count": report.fail_count or 0,
        "error_count": report.error_count or 0,
        "skip_count": report.skip_count or 0,
        "duration": report.duration,
        "summary": report.summary,
        "allure_url": report.allure_url,
        "start_time": report.start_time.isoformat() if report.start_time else None,
        "end_time": report.end_time.isoformat() if report.end_time else None,
        "create_time": report.create_time.isoformat() if report.create_time else None,
    }


def _serialize_step(step: TestStepReport) -> dict:
    return {
        "id": step.id,
        "report_id": step.report_id,
        "case_id": step.case_id,
        "step_name": step.step_name,
        "step_type": step.step_type,
        "action": step.action,
        "target": step.target,
        "status": step.status,
        "status_code": step.status_code,
        "duration": step.duration,
        "error_message": step.error_message,
        "create_time": step.create_time.isoformat() if step.create_time else None,
    }


@router.get("")
def list_reports(
    db: DBDep,
    project_id: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    分页列出执行记录。默认按 start_time 倒序（最新在前）。
    返回 {data, total} 方便前端做分页。
    """
    # 顺手把卡太久的 running 报告刷成 fail，避免 UI 一直转圈
    try:
        _sweep_stale_running(db)
    except Exception as exc:  # 清洗出错不能影响列表读取
        print(f"[reports.list] sweep stale running 失败: {exc}")

    query = db.session.query(TestReport, Project.name).outerjoin(
        Project, Project.id == TestReport.project_id
    )

    if project_id is not None:
        query = query.filter(TestReport.project_id == project_id)
    if category:
        query = query.filter(TestReport.category == category)
    if status:
        query = query.filter(TestReport.status == status)

    total = query.count()

    rows = (
        query.order_by(TestReport.start_time.desc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )

    data = [_serialize_report(r, pname) for r, pname in rows]
    return {"status": "success", "data": data, "total": total}


@router.get("/{report_id}")
def get_report(report_id: int, db: DBDep):
    """单条报告 + 步骤级结果（按 create_time 升序）。"""
    row = (
        db.session.query(TestReport, Project.name)
        .outerjoin(Project, Project.id == TestReport.project_id)
        .filter(TestReport.id == report_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")

    report, project_name = row
    steps = (
        db.session.query(TestStepReport)
        .filter(TestStepReport.report_id == report_id)
        .order_by(TestStepReport.create_time.asc().nullslast())
        .all()
    )

    return {
        "status": "success",
        "data": {
            **_serialize_report(report, project_name),
            "steps": [_serialize_step(s) for s in steps],
        },
    }


@router.get("/{report_id}/analysis-preview")
def get_report_analysis_preview(report_id: int, db: DBDep):
    """快速返回规则诊断结果，不调 AI，让前端先展示可用结论。"""
    from server.services.test_result_analysis_service import analyze_report

    report = db.session.query(TestReport).filter(TestReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.status == "running":
        raise HTTPException(status_code=400, detail="报告还在运行中，请结束后再分析")
    try:
        output = analyze_report(db.session, report_id, model_name=None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success", "data": output}


@router.post("/{report_id}/ai-analysis")
def submit_report_ai_analysis(report_id: int, payload: ReportAiAnalysisRequest, db: DBDep):
    """提交测试报告体检任务：规则诊断优先，AI 只做汇总增强。"""
    from tasks.ai_tasks import dispatch_ai_task

    report = db.session.query(TestReport).filter(TestReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.status == "running":
        raise HTTPException(status_code=400, detail="报告还在运行中，请结束后再分析")

    run = AiRun(
        feature=AI_FEATURE_TEST_RESULT_ANALYSIS,
        status=AI_RUN_STATUS_PENDING,
        project_id=report.project_id,
        input_payload={
            "report_id": report_id,
            "model_name": (payload.model_name or "").strip() or None,
        },
        operator=payload.operator,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    async_result = dispatch_ai_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "ai_run_id": run.id,
            "celery_task_id": async_result.id,
            "feature": run.feature,
        },
    }


@router.delete("/{report_id}")
def delete_report(report_id: int, db: DBDep):
    report = db.session.query(TestReport).filter(TestReport.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    # 顺手把 step_reports 也清掉（没设外键级联）
    db.session.query(TestStepReport).filter(
        TestStepReport.report_id == report_id
    ).delete(synchronize_session=False)
    db.session.delete(report)
    return {"status": "success", "message": "已删除"}
