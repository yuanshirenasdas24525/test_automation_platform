"""Celery 任务：run_test_task

策略：
  - 优先用 v2 的 `test_case_runner`（走 CaseExecutor → StepDispatcher → StepRunner）。
  - 如果传进来的 cases 列表里**所有 case** 都没有 steps 且没有 case_type，说明是老
    调用方（没换 v2 loader），为了不破坏现有流程，退回到旧的 `test_{category}_runner`。

这样就保证：
  - 老 /api/run_test 路径（用 `get_cases_from_db` v1）—— 行为不变。
  - 新 /api/run_test （或同一接口 + v2=true）路径（用 `get_cases_v2_from_db`）—— 自动走新 Runner。
  - 逐条 case 可以混用：有 steps 的走 v2，剩下的走 v1 兼容（CaseExecutor 会自动合成 http_request）。

可靠性（2026-04 修复）：
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
from typing import Iterable
from utils.logger import LOGGER
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
    """根据 cases 决定走哪个 pytest 节点。

    规则（按优先级）：
      1. cases 里有 step / case_type 非 api → v2（`test_case_runner`）
      2. category 不是 'api' → **强制** v2。
         service_run_executor.py 里只定义了 `test_api_runner` 和 `test_case_runner`，
         根本没有 `test_web_runner` / `test_app_runner`。web/app 必须走 v2。
      3. 其它（category == 'api' 且 cases 里没有 steps）→ v1 `test_api_runner`
    """
    if _needs_v2_runner(cases):
        return "tests/service_run_executor.py::TestService::test_case_runner"

    cat = str(category or "").strip().lower()
    if cat != "api":
        # web / app / 其它任何非 api 的类型都没有 v1 入口，必须走 v2
        return "tests/service_run_executor.py::TestService::test_case_runner"

    # v1 回退路径：只对 api 保留老行为
    return "tests/service_run_executor.py::TestService::test_api_runner"


def _run_allure_generate(result_path: str, report_path: str) -> None:
    """尽力跑一下 allure generate；binary 没装或命令挂了不要影响主流程。"""
    allure_bin = shutil.which("allure")
    if not allure_bin:
        print("[run_test_task] allure CLI 未安装，跳过 HTML 报告生成")
        return
    try:
        rc = os.system(f"{allure_bin} generate {result_path} -o {report_path} --clean")
        if rc != 0:
            print(f"[run_test_task] allure generate 退出码 {rc}")
    except Exception as exc:  # pragma: no cover
        print(f"[run_test_task] allure generate 异常: {exc}")


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
        print(f"[run_test_task] pytest exit_code={exit_code}")

        # HTML 报告：失败不要传染给主流程
        _run_allure_generate(result_path, report_path)

        # 把 allure 结果落进 step 表；这一步失败也不要卡住报告
        try:
            sync_allure_to_db(r_id, result_path, db_session)
        except Exception as exc:
            traceback.print_exc()
            print(f"[run_test_task] sync_allure_to_db 失败: {exc}")
            db_session.rollback()

        # 统一 finalize，它自己兜底把 status 写成终态
        finalize_report(r_id, db_session, t_id)

    except Exception as exc:
        # 任何没被内层捕获的异常都在这里兜底
        traceback.print_exc()
        try:
            force_error_status(r_id, db_session, f"任务执行失败: {exc}")
        except Exception as inner:
            print(f"[run_test_task] 兜底也失败: {inner}")

    finally:
        try:
            db_session.close()
        except Exception:
            pass
