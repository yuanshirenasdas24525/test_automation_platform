# tests/service_run_executor.py
"""平台侧的 pytest 入口类。

两个入口：

- `test_case_runner(case, record_property)`（v2 推荐）
    走新 CaseExecutor + StepDispatcher + 各种 StepRunner。
    适用于：
      * case 字典里带 `steps` 列表
      * 或 `case_type` 不为 'api'
      * 或 `case_type='api'` 但走 v2 流程

- `test_api_runner(case, record_property)`（v1 兼容）
    保留老路径：一条 case 就是一次 HTTP 请求。
    适用于：老用例还没做数据迁移的过渡期。

Celery 侧的 `run_test_task.py` 会根据 case 数据结构选择走哪个入口。
也可以统一指向 `test_case_runner`，它内部能兼容无 steps 的情况（CaseExecutor
会自动合成一条 v1 http_request step）。
"""
from __future__ import annotations

import pytest

from src.core.api.factory import create_api_client
from src.core.context.execution_context import ExecutionContext
from src.runners.case_executor import CaseExecutor
from src.runners.protocol import StepStatus


class TestService:
    # --------------------------------------------------
    # v1 兼容入口：一条 case = 一次请求
    # --------------------------------------------------
    def test_api_runner(self, case, record_property):
        if case is None:
            pytest.skip("没有接收到待执行的用例数据")
        create_api_client(record_property).send_case(case=case)

    # --------------------------------------------------
    # v2 入口：走 CaseExecutor（推荐）
    # --------------------------------------------------
    def test_case_runner(self, case, record_property):
        if case is None:
            pytest.skip("没有接收到待执行的用例数据")

        ctx = ExecutionContext(record_property)
        result = CaseExecutor().run(case, ctx)

        # 把最终聚合结果也写进 record_property，便于平台 tasks 层消费
        record_property("case_id", result.case_id)
        record_property("case_status", result.status.value)
        record_property("duration_ms", result.duration_ms)
        if result.error_message:
            record_property("case_error", result.error_message)

        if result.status == StepStatus.PASSED:
            return
        if result.status == StepStatus.SKIPPED:
            pytest.skip(result.error_message or "case skipped")
        # FAILED / ERROR 都以 AssertionError 形式抛出，pytest 才会标红
        raise AssertionError(
            f"case {result.case_name} 执行失败（{result.status.value}）："
            f"{result.error_message or 'see step logs'}"
        )
