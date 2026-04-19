"""Web（Playwright/Selenium）step runner 的占位实现。

在 Phase 5（Web 自动化）落地前，先用统一的 NotImplementedError 把类型占上，
避免 dispatch 时 KeyError。
"""
from __future__ import annotations

from src.core.context.execution_context import ExecutionContext
from src.runners.protocol import BaseStepRunner, StepResult


_WEB_STEP_TYPES = (
    "web_goto",
    "web_click",
    "web_input",
    "web_select",
    "web_wait",
)


class _WebNotImplementedRunner(BaseStepRunner):
    def __init__(self, step_type: str):
        self.step_types = (step_type,)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        result.action = f"{self.step_types[0]} (not implemented)"
        raise NotImplementedError(
            f"step_type={self.step_types[0]} 尚未接入 Web Runner（计划在 Phase 5 实现）。"
        )


def build_web_runners() -> list[_WebNotImplementedRunner]:
    return [_WebNotImplementedRunner(t) for t in _WEB_STEP_TYPES]
