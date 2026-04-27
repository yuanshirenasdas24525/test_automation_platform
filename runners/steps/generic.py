"""通用 StepRunner：sleep / assert，跟协议 / 数据源无关。"""
from __future__ import annotations

import json
import time
from typing import Any

from runners.context.execution_context import ExecutionContext
from runners.protocol import BaseStepRunner, StepResult
from utils.platform_utils import extractor, rep_expr


class SleepStepRunner(BaseStepRunner):
    """显式等待：config = {"seconds": 3} 或 {"ms": 500}。"""
    step_types = ("sleep",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        config = step.get("config") or {}
        seconds = float(config.get("seconds") or 0)
        ms = float(config.get("ms") or 0)
        total = seconds + ms / 1000.0
        if total <= 0:
            total = 1.0
        result.action = f"sleep {total:.3f}s"
        time.sleep(total)


class AssertStepRunner(BaseStepRunner):
    """独立断言步骤（一般对上一步 extract 出来的变量做二次校验）。

    config = {
        "target": "${token}",          # 或 "$.something" jsonpath
        "type":   "is_not_null",       # equal / contains / is_not_null / ...
        "expected": "abc"
    }
    """
    step_types = ("assert",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        config = step.get("config") or {}
        t = (config.get("type") or "is_not_null").lower()
        target_expr = config.get("target") or ""
        expected = config.get("expected")

        actual = self._resolve_target(target_expr, ctx)
        result.action = f"assert {t}({target_expr})"
        result.input_data = {"type": t, "target": target_expr, "expected": expected}
        result.output_data = actual

        if t == "equal":
            assert actual == expected, f"[equal] {target_expr}: {actual!r} != {expected!r}"
        elif t in ("not_equal", "ne"):
            assert actual != expected, f"[ne] {target_expr}: {actual!r} == {expected!r}"
        elif t == "contains":
            assert expected in (actual or ""), \
                f"[contains] {target_expr}: {expected!r} not in {actual!r}"
        elif t == "is_null":
            assert actual is None, f"[is_null] {target_expr}: {actual!r}"
        elif t == "is_not_null":
            assert actual is not None, f"[is_not_null] {target_expr}: None"
        else:
            raise ValueError(f"不支持的断言类型: {t!r}")

    @staticmethod
    def _resolve_target(expr: Any, ctx: ExecutionContext) -> Any:
        if not isinstance(expr, str):
            return expr
        # 1) ${var} 占位替换（优先取 ctx.vars）
        pool = dict(ctx.vars or {})
        replaced = rep_expr(expr, pool)
        # 2) 如果替换之后是 jsonpath，再用 jsonpath-ng 解析（需要有上下文数据，否则直接返回字符串）
        if replaced.startswith("$"):
            # 找一个最近的响应体；没有就原样返回
            latest_body = ctx.vars.get("_last_response_body")
            if latest_body is not None:
                try:
                    return extractor(latest_body, replaced)
                except Exception:
                    return replaced
        # 3) 如果结果是 JSON 字符串，尝试转对象
        if replaced and replaced[0] in "{[\"":
            try:
                return json.loads(replaced)
            except Exception:
                return replaced
        return replaced
