"""Celery 任务：run_test_task

策略：
  - 优先用 v2 的 `test_case_runner`（走 CaseExecutor → StepDispatcher → StepRunner）。
  - 如果传进来的 cases 列表里**所有 case** 都没有 steps 且没有 case_type，说明是老
    调用方（没换 v2 loader），为了不破坏现有流程，退回到旧的 `test_{category}_runner`。

这样就保证：
  - 老 /api/run_test 路径（用 `get_cases_from_db` v1）—— 行为不变。
  - 新 /api/run_test （或同一接口 + v2=true）路径（用 `get_cases_v2_from_db`）—— 自动走新 Runner。
  - 逐条 case 可以混用：有 steps 的走 v2，剩下的走 v1 兼容（CaseExecutor 会自动合成 http_request）。
"""
from __future__ import annotations

import json
import os
from typing import Iterable

from celery_app import celery_app


def _needs_v2_runner(cases: Iterable[dict]) -> bool:
    """只要有一条 case 带 steps 或 case_type 非 'api'，就认为需要 v2。"""
    for c in cases or []:
        if not isinstance(c, dict):
            continue
        if c.get("steps"):
            return True
        ct = c.get("case_type")
        if ct and str(ct).lower() != "api":
            return True
    return False


def _pick_pytest_target(category: str, cases: Iterable[dict]) -> str:
    """根据 cases 决定走哪个 pytest 节点。"""
    if _needs_v2_runner(cases):
        return "tests/service_run_executor.py::TestService::test_case_runner"
    # v1 回退路径，保留老行为
    return f"tests/service_run_executor.py::TestService::test_{str(category).lower()}_runner"


@celery_app.task(name="tasks.run_test_task")
def run_test_task(t_id, r_id, cases, category):
    import pytest
    from src.database.db import DB
    from src.database.data_sync import sync_allure_to_db, finalize_report

    db_session = DB().session

    try:
        result_path = f"data/results/{t_id}"
        report_path = f"data/reports/{t_id}"

        pytest_target = _pick_pytest_target(category, cases)

        pytest_args = [
            "-s", "-v",
            "-p", "config.pytest_config",
            "--report_id", str(r_id),
            "--category", category,
            "--alluredir", result_path,
            pytest_target,
            f"--cases_data={json.dumps(cases)}",
        ]

        pytest.main(pytest_args)

        os.system(f"allure generate {result_path} -o {report_path} --clean")

        sync_allure_to_db(r_id, result_path, db_session)
        finalize_report(r_id, db_session, t_id)

    except Exception as e:
        print(f"任务执行失败: {e}")

    finally:
        db_session.close()
