# src/database/data_sync.py
"""Allure 结果落库 + 主报告 finalize。

历史坑（2026-04 修复）：
  1. `sync_allure_to_db` 以前在 finally 里 `db.close()` —— 这个 db 是 run_test_task
     传下来的**共享** session，closed 之后下面 `finalize_report` 用同一把 session 就
     会连不上库。去掉 close，close 的责任交给调用方。
  2. `res.get("stop",0) - res.get("start",0) / 1000.0` 优先级是错的（`/` 先算），
     修成 `(stop-start) / 1000.0` 得到正确的秒。
  3. 状态词统一：主报告只用 "success" / "fail" / "running" / "error"，和前端过滤
     条件对齐。Allure 原始 "passed"/"failed"/"broken"/"skipped" 只留在 step 粒度。
  4. `finalize_report` 没 try/except —— 任何查询异常都会让报告卡 "running"。现在
     统一在 `force_terminal_status` 里兜底，保证每次执行都有终态。
"""
from __future__ import annotations

import json
import os
import re
import traceback
from datetime import datetime
from typing import Any

from sqlalchemy import func, case

from database.models import (
    Module,
    Project,
    TestCase,
    TestReport,
    TestStepReport,
)
from utils.logger import LOGGER


_CASE_FIXTURE_ID_RE = re.compile(r"^\s*\{\s*['\"]id['\"]\s*:\s*(\d+)")


# ---------------------------------------------------------------------------
# Allure 结果目录 → step_reports（FALLBACK 路径）
# ---------------------------------------------------------------------------
# 正常路径上，`config/pytest_config.py::pytest_runtest_makereport` 在每条用例跑完
# 时就把 TestStepReport 写进库了（有 action/target/input/output 等 record_property
# 给的细节）。这个函数是 FALLBACK：
#   - 如果 pytest 钩子因为异常 / worker 崩溃漏写，这里拿 allure 结果按 case 补齐；
#   - 已有该 report_id + case_id 的记录就跳过，避免重复，同时允许修复单条写库失败。
def sync_allure_to_db(report_id: int, result_path: str, db) -> None:
    """从 allure 结果目录兜底补齐 test_step_reports。

    - 遍历 `result_path` 下的 `*-result.json`，按显式 case_id 判断是否缺行；
    - pytest 钩子已写入的 case 不重复，只有漏写的 case 才补一条用例级记录；
    - 老 Allure 没有 case_id label 时，从 pytest 的 case fixture 参数开头安全读取 id。
    - **不**在这里 commit —— 由调用方控制事务（通常是 finalize_report 统一 commit）。
    """
    existing_case_ids = {
        int(case_id)
        for (case_id,) in (
            db.query(TestStepReport.case_id)
            .filter(
                TestStepReport.report_id == report_id,
                TestStepReport.case_id.isnot(None),
            )
            .distinct()
            .all()
        )
        if case_id is not None
    }
    existing_count = (
        db.query(func.count(TestStepReport.id))
        .filter(TestStepReport.report_id == report_id)
        .scalar()
        or 0
    )

    if not result_path or not os.path.isdir(result_path):
        LOGGER.warning(f"[sync_allure_to_db] 结果目录不存在或为空: {result_path}")
        return

    added = 0
    try:
        for file in os.listdir(result_path):
            if not file.endswith("-result.json"):
                continue
            try:
                with open(os.path.join(result_path, file), "r", encoding="utf-8") as f:
                    res = json.load(f)
            except Exception as exc:  # 单个坏文件不能影响整体
                LOGGER.warning(f"[sync_allure_to_db] 跳过坏文件 {file}: {exc}")
                continue

            case_id = _case_id_from_allure_result(res)
            if case_id is not None and case_id in existing_case_ids:
                continue
            # 老结果完全无法识别 case_id 时，仅保留“整份报告一行都没有”的历史兜底；
            # 部分已有记录时加入 case_id=NULL 会污染统计且无法参与后续验证装配。
            if case_id is None and existing_count > 0:
                continue

            status = res.get("status")
            # 修优先级 bug：(stop - start) / 1000 → 毫秒转秒
            start_ms = res.get("start") or 0
            stop_ms = res.get("stop") or 0
            duration_sec = max(0.0, (stop_ms - start_ms) / 1000.0)

            step_record = TestStepReport(
                report_id=report_id,
                case_id=case_id,
                step_name=res.get("name"),
                status=status,
                duration=duration_sec,
                error_message=(res.get("statusDetails") or {}).get("message"),
                action=next(
                    (t["value"] for t in res.get("labels", []) if t.get("name") == "action"),
                    None,
                ),
                create_time=datetime.now(),
            )
            db.add(step_record)
            added += 1
            if case_id is not None:
                existing_case_ids.add(case_id)

        db.flush()
    except Exception as exc:
        LOGGER.warning(f"[sync_allure_to_db] 回填步骤失败: {exc}")
        db.rollback()
        raise
    else:
        LOGGER.info(f"[sync_allure_to_db] 兜底写入 {added} 条 step 记录")


def _case_id_from_allure_result(result: dict[str, Any]) -> int | None:
    """从 Allure 结果提取 case_id，兼容新 label 与历史 fixture 参数。"""
    for label in result.get("labels") or []:
        if isinstance(label, dict) and label.get("name") == "case_id":
            try:
                return int(label.get("value"))
            except (TypeError, ValueError):
                return None
    for parameter in result.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("name")
        value = parameter.get("value")
        if name == "case_id":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if name == "case" and isinstance(value, str):
            match = _CASE_FIXTURE_ID_RE.match(value)
            if match:
                return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# 主报告 finalize：从 step_reports 聚合 → test_reports
# ---------------------------------------------------------------------------
def finalize_report(report_id: int, db_session, task_id: str | None = None) -> None:
    """读 step_reports 聚合统计，落主报告表；失败则兜一个 error 状态。"""
    try:
        _finalize_impl(report_id, db_session, task_id)
    except Exception as exc:
        traceback.print_exc()
        # 兜底：无论如何不能让 status 卡在 running
        _force_terminal(
            report_id,
            db_session,
            status="error",
            summary=f"finalize 失败：{exc}",
        )


def _finalize_impl(report_id: int, db_session, task_id: str | None) -> None:
    # 1. 聚合 step 状态。Allure 输出里 pass/fail/broken/skipped 都算一类，这里归并。
    stats = db_session.query(
        func.count().label("total"),
        func.sum(case((TestStepReport.status == "passed", 1), else_=0)).label("pass_count"),
        func.sum(case((TestStepReport.status == "failed", 1), else_=0)).label("fail_count"),
        func.sum(
            case(
                ((TestStepReport.status == "broken") | (TestStepReport.status == "error"), 1),
                else_=0,
            )
        ).label("error_count"),
        func.sum(case((TestStepReport.status == "skipped", 1), else_=0)).label("skip_count"),
        func.sum(TestStepReport.duration).label("total_duration_sec"),
    ).filter(TestStepReport.report_id == report_id).one()

    total = int(stats.total or 0)
    pass_count = int(stats.pass_count or 0)
    fail_count = int(stats.fail_count or 0)
    error_count = int(stats.error_count or 0)
    skip_count = int(stats.skip_count or 0)
    # duration 已经是秒（sync_allure_to_db 里换算过）
    duration_sec = round(float(stats.total_duration_sec or 0.0), 2)

    # 2. 终态：全部通过 → success；有任何 fail/error → fail；0 条步骤也算 fail
    #    （一个正常跑完的任务至少有一条 step report；0 条通常是 pytest 早夭）
    if total > 0 and pass_count == total:
        final_status = "success"
    elif total == 0:
        final_status = "fail"
    else:
        final_status = "fail"

    summary = (
        f"总:{total} 通过:{pass_count} 失败:{fail_count} "
        f"错误:{error_count} 跳过:{skip_count}"
    )

    # 3. scene_name：project/module/case 三段拼出来给列表用
    scene_rows = (
        db_session.query(
            Project.name.label("project_name"),
            Module.name.label("module_name"),
            TestCase.name.label("case_name"),
        )
        .join(Module, Module.project_id == Project.id)
        .join(TestCase, TestCase.module_id == Module.id)
        .join(TestStepReport, TestStepReport.case_id == TestCase.id)
        .filter(TestStepReport.report_id == report_id)
        .distinct()
        .all()
    )
    scene_name = (
        " | ".join(
            f"{row.project_name}/{row.module_name}/{row.case_name}" for row in scene_rows
        )
        or None
    )

    allure_url = f"/reports/{task_id}/index.html" if task_id else None

    # 4. 写主报告
    report = db_session.query(TestReport).filter(TestReport.id == report_id).first()
    if not report:
        LOGGER.warning(f"[finalize_report] report_id={report_id} 不存在")
        return

    report.scene_name = scene_name or report.scene_name
    report.total_count = total or report.total_count or 0
    report.pass_count = pass_count
    report.fail_count = fail_count
    report.error_count = error_count
    report.skip_count = skip_count
    report.status = final_status
    report.duration = duration_sec
    report.end_time = datetime.now()
    report.summary = summary
    report.allure_url = allure_url

    db_session.commit()
    LOGGER.info(f"[finalize_report] report_id={report_id} → {final_status} ({summary})")


# ---------------------------------------------------------------------------
# 兜底：强制把报告刷成终态（被异常路径使用）
# ---------------------------------------------------------------------------
def _force_terminal(report_id: int, db_session, status: str, summary: str) -> None:
    """把报告直接刷成终态 —— 异常路径上兜底，保证 UI 不会卡 running。"""
    try:
        db_session.rollback()  # 丢弃可能在途的脏事务
    except Exception:
        pass
    try:
        report = (
            db_session.query(TestReport).filter(TestReport.id == report_id).first()
        )
        if not report:
            return
        report.status = status
        report.summary = (summary or "")[:500]  # 避免超长堆栈塞满字段
        report.end_time = datetime.now()
        db_session.commit()
    except Exception as exc:
        LOGGER.warning(f"[_force_terminal] 兜底写入也失败了: {exc}")
        try:
            db_session.rollback()
        except Exception:
            pass


def force_error_status(report_id: int, db_session, message: str) -> None:
    """对外暴露的兜底：run_test_task 异常路径用这个收尾。"""
    _force_terminal(report_id, db_session, status="fail", summary=message)
