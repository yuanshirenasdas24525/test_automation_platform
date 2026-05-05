"""Version 测试汇总服务。

  generate(version_id, session): 聚合 Requirement / Task / TestCase /
    TestStepReport → upsert 到 version_test_summaries 一行。

聚合口径：
  - total_requirements:  Requirement.version_id == v
  - total_tasks:         Task.requirement.version_id == v 且 type != bug
  - total_bugs / pX:     同上 join 但 type == bug
  - total_test_cases:    TestCase.version_id == v
  - passed/failed/blocked: TestStepReport.status 按 case_id ∈ TestCase(v) 分桶
                          (broken / error 归 failed；其它非 passed/skipped 归 blocked)
  - first_pass_rate:    1 - bug_count / dev_task_count；dev_task=0 → None
  - avg_fix_time_hours: bug Task 中 closed_at 非空的 (closed_at - created_at) 平均
                        SQLite 用 julianday；其它后端用 EXTRACT(EPOCH ...)/3600 风格
                        这里用 Python 端取出 datetime 自己算，避免方言依赖
  - test_coverage:      min(1, total_test_cases / total_requirements)
                        没有 requirements → None
  - payload:            {requirement_ids, task_ids, bug_ids, test_case_ids}

不 commit；只 flush + refresh。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from database.models import (
    Requirement,
    Task,
    TestCase,
    TestStepReport,
    VersionTestSummary,
    TASK_TYPE_BUG,
    TASK_TYPE_DEV,
    BUG_SEVERITY_P0,
    BUG_SEVERITY_P1,
    BUG_SEVERITY_P2,
    BUG_SEVERITY_P3,
)


def generate(version_id: int, session: Session) -> dict:
    # ---- 需求 ----
    req_ids = [
        rid for (rid,) in session.query(Requirement.id)
        .filter(Requirement.version_id == version_id).all()
    ]
    total_requirements = len(req_ids)

    # ---- Task / Bug ----
    task_rows = (
        session.query(Task.id, Task.type, Task.severity, Task.created_at, Task.closed_at)
        .join(Requirement, Requirement.id == Task.requirement_id)
        .filter(Requirement.version_id == version_id)
        .all()
    )
    task_ids = [r.id for r in task_rows if r.type != TASK_TYPE_BUG]
    bug_rows = [r for r in task_rows if r.type == TASK_TYPE_BUG]
    bug_ids = [r.id for r in bug_rows]
    dev_task_count = sum(1 for r in task_rows if r.type == TASK_TYPE_DEV)

    total_tasks = len(task_ids)
    total_bugs = len(bug_ids)

    p_count = {BUG_SEVERITY_P0: 0, BUG_SEVERITY_P1: 0, BUG_SEVERITY_P2: 0, BUG_SEVERITY_P3: 0}
    fix_durations_hours = []
    for r in bug_rows:
        if r.severity in p_count:
            p_count[r.severity] += 1
        if r.closed_at and r.created_at:
            delta = r.closed_at - r.created_at
            fix_durations_hours.append(delta.total_seconds() / 3600.0)

    avg_fix_time_hours = (
        round(sum(fix_durations_hours) / len(fix_durations_hours), 2)
        if fix_durations_hours else None
    )

    first_pass_rate = (
        round(1 - (total_bugs / dev_task_count), 4)
        if dev_task_count > 0 else None
    )
    if first_pass_rate is not None and first_pass_rate < 0:
        first_pass_rate = 0.0

    # ---- TestCase ----
    test_case_ids = [
        cid for (cid,) in session.query(TestCase.id)
        .filter(TestCase.version_id == version_id).all()
    ]
    total_test_cases = len(test_case_ids)

    # ---- TestStepReport buckets (only for cases in this version) ----
    passed = failed = blocked = 0
    if test_case_ids:
        bucket_rows = (
            session.query(
                func.sum(case((TestStepReport.status == "passed", 1), else_=0)).label("p"),
                func.sum(
                    case(
                        ((TestStepReport.status == "failed")
                         | (TestStepReport.status == "broken")
                         | (TestStepReport.status == "error"), 1),
                        else_=0,
                    )
                ).label("f"),
                func.sum(
                    case(
                        ((TestStepReport.status != "passed")
                         & (TestStepReport.status != "failed")
                         & (TestStepReport.status != "broken")
                         & (TestStepReport.status != "error")
                         & (TestStepReport.status != "skipped"), 1),
                        else_=0,
                    )
                ).label("b"),
            )
            .filter(TestStepReport.case_id.in_(test_case_ids))
            .one()
        )
        passed = int(bucket_rows.p or 0)
        failed = int(bucket_rows.f or 0)
        blocked = int(bucket_rows.b or 0)

    # ---- test_coverage ----
    test_coverage = (
        round(min(1.0, total_test_cases / total_requirements), 4)
        if total_requirements > 0 else None
    )

    payload = {
        "requirement_ids": req_ids,
        "task_ids": task_ids,
        "bug_ids": bug_ids,
        "test_case_ids": test_case_ids,
    }

    stats = dict(
        total_requirements=total_requirements,
        total_tasks=total_tasks,
        total_test_cases=total_test_cases,
        passed=passed,
        failed=failed,
        blocked=blocked,
        total_bugs=total_bugs,
        p0_bugs=p_count[BUG_SEVERITY_P0],
        p1_bugs=p_count[BUG_SEVERITY_P1],
        p2_bugs=p_count[BUG_SEVERITY_P2],
        p3_bugs=p_count[BUG_SEVERITY_P3],
        first_pass_rate=first_pass_rate,
        avg_fix_time_hours=avg_fix_time_hours,
        test_coverage=test_coverage,
        payload=payload,
    )

    summary = (
        session.query(VersionTestSummary)
        .filter(VersionTestSummary.version_id == version_id)
        .first()
    )
    if summary is None:
        summary = VersionTestSummary(version_id=version_id, **stats)
        summary.generated_at = datetime.utcnow()
        session.add(summary)
    else:
        for k, v in stats.items():
            setattr(summary, k, v)
        summary.generated_at = datetime.utcnow()
    session.flush()
    session.refresh(summary)
    return summary.to_dict()
