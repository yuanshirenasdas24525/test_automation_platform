"""Celery 任务：run_test_task

v2 唯一路径：所有 case（API/Web/Android/iOS/Mixed）都走
`tests/service_run_executor.py::TestService::test_case_runner`，
该入口走 CaseExecutor → StepDispatcher → 各 StepRunner。
v1 的 `test_api_runner` + 兼容选 runner 的逻辑已删。

可靠性：
  - 以前外层 except 只 print，报告就卡在 "running"。现在：
    * try：正常跑完 pytest + sync + finalize；
    * except：捕获任何异常，立刻用 `force_error_status` 把报告刷成 "fail"，带错误摘要；
    * finally：关 session；
  - `allure generate` 失败（未装 allure CLI）不再让整个任务失败；
  - `sync_allure_to_db` / `finalize_report` 自己也都有兜底。
"""
from __future__ import annotations

import json
import os
import shutil
import traceback
from utils.logger import LOGGER
from celery_app import celery_app

# v2 唯一 pytest 入口
_PYTEST_TARGET = "tests/service_run_executor.py::TestService::test_case_runner"


def _run_allure_generate(result_path: str, report_path: str) -> None:
    """尽力跑一下 allure generate；binary 没装或命令挂了不要影响主流程。"""
    allure_bin = shutil.which("allure")
    if not allure_bin:
        LOGGER.info("[run_test_task] allure CLI 未安装，跳过 HTML 报告生成")
        return
    try:
        rc = os.system(f"{allure_bin} generate {result_path} -o {report_path} --clean")
        if rc != 0:
            LOGGER.warning(f"[run_test_task] allure generate 退出码 {rc}")
    except Exception as exc:  # pragma: no cover
        LOGGER.warning(f"[run_test_task] allure generate 异常: {exc}")


@celery_app.task(name="tasks.run_test_task")
def run_test_task(t_id, r_id, cases, category):
    """后台执行入口。无论走哪条路径，结束时 TestReport.status 必须是终态。"""
    # --- 入口 trace：这条打印出来就说明 celery worker（或 EAGER 模式）已经接到了任务 ---
    # 如果提交了 /api/run_test 但 worker 终端 / uvicorn 终端一条这样的日志都没有，
    # 说明任务根本没被消费——99% 是 celery worker 没启动或连不上 Redis。
    LOGGER.info(
        f"[run_test_task] ENTERED t_id={t_id} r_id={r_id} "
        f"category={category} cases_count={len(cases) if cases else 0}"
    )

    import pytest
    from database.db import DB
    from database.data_sync import (
        finalize_report,
        force_error_status,
        sync_allure_to_db,
    )

    db_session = DB().session
    try:
        result_path = f"data/results/{t_id}"
        report_path = f"data/reports/{t_id}"
        os.makedirs(result_path, exist_ok=True)

        pytest_args = [
            "-s", "-v",
            "-p", "config.pytest_config",
            "--report_id", str(r_id),
            "--category", category,
            "--alluredir", result_path,
            _PYTEST_TARGET,
            f"--cases_data={json.dumps(cases)}",
        ]

        # pytest.main 本身不抛异常；退出码通过返回值拿。异常在收集 / conftest 阶段才抛。
        try:
            exit_code = pytest.main(pytest_args)
        except SystemExit as se:  # pytest 极端情况下会 sys.exit
            exit_code = se.code
        except Exception as exc:
            traceback.print_exc()
            force_error_status(r_id, db_session, f"pytest 启动失败: {exc}")
            return

        # exit_code 含义：0=全通过，1=有 fail，2=中断，3=内部错，4=用法错，5=没用例
        # 不直接用它判 success —— sync_allure_to_db + finalize_report 会按 step 结果落盘
        LOGGER.info(f"[run_test_task] pytest exit_code={exit_code}")

        # HTML 报告：失败不要传染给主流程
        _run_allure_generate(result_path, report_path)

        # 把 allure 结果落进 step 表；这一步失败也不要卡住报告
        try:
            sync_allure_to_db(r_id, result_path, db_session)
        except Exception as exc:
            traceback.print_exc()
            LOGGER.warning(f"[run_test_task] sync_allure_to_db 失败: {exc}")
            db_session.rollback()

        # 统一 finalize，它自己兜底把 status 写成终态
        finalize_report(r_id, db_session, t_id)

        # 用例通过 → 自动清掉它身上的 AI 标记（manual_fix / ai_fixed）。
        # 标记只是提示层，这步失败不许传染主流程。
        try:
            _auto_clear_ai_flags(r_id, db_session)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(f"[run_test_task] 自动清 AI 标记失败（忽略）: {exc}")
            try:
                db_session.rollback()
            except Exception:
                pass

        # api 报告：异步学习「响应结构约定」回流记忆层（带节流，纯增强，失败不传染）。
        # 让新项目首次跑完后自动"开窍"，下次生成用例直接写对 JSONPath。
        if str(category or "").strip().lower() == "api":
            try:
                from tasks.learn_convention_task import learn_response_convention_task
                learn_response_convention_task.delay(r_id)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(f"[run_test_task] 派发响应约定学习失败（忽略）: {exc}")

    except Exception as exc:
        # 任何没被内层捕获的异常都在这里兜底
        traceback.print_exc()
        try:
            force_error_status(r_id, db_session, f"任务执行失败: {exc}")
        except Exception as inner:
            LOGGER.warning(f"[run_test_task] 兜底也失败: {inner}")

    finally:
        try:
            db_session.close()
        except Exception:
            pass


def _auto_clear_ai_flags(report_id: int, db_session) -> None:
    """本次报告里聚合状态为 passed 的用例 → 自动清 AI 标记（manual_fix/ai_fixed）。"""
    from database.models import TestStepReport
    from server.services.ai_flag_service import auto_clear_on_pass

    rows = (
        db_session.query(TestStepReport.case_id, TestStepReport.status)
        .filter(TestStepReport.report_id == report_id, TestStepReport.case_id.isnot(None))
        .all()
    )
    statuses: dict[int, list[str]] = {}
    for cid, st in rows:
        statuses.setdefault(cid, []).append(str(st or "").lower())
    passed = [
        cid for cid, sts in statuses.items()
        if sts and all(s in ("passed", "skipped") for s in sts) and "passed" in sts
    ]
    if passed:
        n = auto_clear_on_pass(db_session, passed)
        if n:
            db_session.commit()
            LOGGER.info(f"[run_test_task] 自动清除 {n} 个 AI 标记（用例已通过）")


# ---------------------------------------------------------------------------
# 注册到全局任务看板（Task Registry）
# ---------------------------------------------------------------------------
def _query_test_reports(categories: list[str]):
    """返回查询某类测试执行的进行中 query_fn。"""
    def _query(db_session, project_id: int | None, limit: int):
        from database.models import TestReport, Project

        q = db_session.query(
            TestReport.id,
            TestReport.category,
            TestReport.status,
            TestReport.project_id,
            TestReport.scene_name,
            TestReport.start_time,
            Project.name.label("project_name"),
        ).outerjoin(Project, Project.id == TestReport.project_id).filter(
            TestReport.status == "running",
            TestReport.category.in_(categories),
        )
        if project_id is not None:
            q = q.filter(TestReport.project_id == project_id)
        rows = q.order_by(TestReport.start_time.desc().nullslast()).limit(limit).all()
        return [
            {
                "id": r.id,
                "name": r.scene_name or f"#{r.id}",
                "status": r.status,
                "project_id": r.project_id,
                "project_name": r.project_name,
                "started_at": r.start_time,
                "detail_url": f"/runs?report_id={r.id}",
            }
            for r in rows
        ]
    return _query


from server.services.task_registry import task_registry, TaskTypeInfo  # noqa: E402

_EXECUTION_ENTRIES = [
    ("test_run_api", "API 自动化执行", "Globe", ["api", "mixed"]),
    ("test_run_web", "Web 自动化执行", "Monitor", ["web"]),
    ("test_run_app", "App 自动化执行", "Smartphone", ["android", "ios"]),
]

for _key, _label, _icon, _cats in _EXECUTION_ENTRIES:
    task_registry.register(TaskTypeInfo(
        key=_key,
        label=_label,
        category="execution",
        icon=_icon,
        query_fn=_query_test_reports(_cats),
        detail_url_tpl="/runs",
    ))
