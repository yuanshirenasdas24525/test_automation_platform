"""StepDispatcher：按 step_type 把一条 step 派发到对应的 Runner。

典型用法（CaseExecutor 里）：

    dispatcher = StepDispatcher.default()    # 自动扫描并注册所有内置 Runner
    for step in case.steps:
        result = dispatcher.dispatch(step, ctx)
        ...

高级用法（想换掉某个实现 / 注入 mock）：

    dispatcher = StepDispatcher()
    dispatcher.register(MyCustomHttpRunner())
    dispatcher.register(MyCustomAppTapRunner())

通用控制逻辑（wait_before / retry / on_failure）统一在 dispatcher 这一层实现，
具体 Runner 只管"跑一次"的单趟逻辑，避免每个 Runner 都要抄一遍重试代码。
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from core.context.execution_context import ExecutionContext
from runners.protocol import (
    StepRunner,
    StepResult,
    StepStatus,
)
from utils.allure_step_reporter import (
    allure_step,
    attach_step_details,
    capture_failure_screenshot,
)

logger = logging.getLogger(__name__)


class StepDispatcher:
    def __init__(self) -> None:
        self._registry: dict[str, StepRunner] = {}

    # ---------------- 注册 / 查询 ----------------
    def register(self, runner: StepRunner) -> None:
        """注册一个 Runner。它的 `step_types` 决定了覆盖哪些类型。"""
        types = getattr(runner, "step_types", ())
        if not types:
            raise ValueError(f"{type(runner).__name__} 没有声明 step_types")
        for t in types:
            if t in self._registry:
                logger.warning("step_type=%s 已有 Runner %s，将被 %s 覆盖",
                               t, type(self._registry[t]).__name__, type(runner).__name__)
            self._registry[t] = runner

    def register_all(self, runners: Iterable[StepRunner]) -> None:
        for r in runners:
            self.register(r)

    def get(self, step_type: str) -> StepRunner | None:
        return self._registry.get(step_type)

    @property
    def supported_types(self) -> list[str]:
        return sorted(self._registry.keys())

    # ---------------- 派发 ----------------
    def dispatch(self, step: dict, ctx: ExecutionContext) -> StepResult:
        """根据 step.step_type 找 Runner 并执行，封装 wait_before / retry / on_failure 通用控制。"""
        step_type = step.get("step_type")
        runner = self._registry.get(step_type)
        if runner is None:
            # 没注册 → 返回 ERROR，由 CaseExecutor 决定中断还是跳过
            return StepResult(
                step_id=step.get("id"),
                step_order=step.get("step_order", 0),
                step_name=step.get("step_name") or f"step#{step.get('id')}",
                step_type=step_type or "",
                status=StepStatus.ERROR,
                started_at=time.time(),
                error_message=f"没有注册的 StepRunner 支持 step_type={step_type!r}。"
                              f"已注册的类型：{self.supported_types}",
            ).mark_done()

        # wait_before：step 开始前先 sleep（单位秒，浮点）
        wait_before = float(step.get("wait_before") or 0)
        if wait_before > 0:
            time.sleep(wait_before)

        # retry + on_failure：
        #   retry=N 表示失败后最多再试 N 次
        #   on_failure: stop | continue | retry
        #     - retry：失败时按 retry 次数重试
        #     - stop/continue：失败不再自动重试，由 CaseExecutor 决定是否继续下一条
        retry = int(step.get("retry") or 0)
        on_failure = (step.get("on_failure") or "stop").lower()
        max_attempts = (retry + 1) if on_failure == "retry" else 1

        # Allure step 名字：[order] step_name (step_type)，方便报告里一眼定位。
        allure_name = self._build_allure_step_name(step)

        last_result: StepResult | None = None
        for attempt in range(1, max_attempts + 1):
            with allure_step(allure_name if attempt == 1 else f"{allure_name} #retry{attempt}"):
                result = runner.execute(step, ctx)
                # 失败时先尝试截一张图，再 attach（顺序很重要：截图也要进 attachments）
                capture_failure_screenshot(ctx, step, result)
                attach_step_details(result)
                last_result = result
            if result.status in (StepStatus.PASSED, StepStatus.SKIPPED):
                if attempt > 1:
                    logger.info("step#%s 第 %s 次重试成功", step.get("id"), attempt)
                return result
            if attempt < max_attempts:
                logger.warning("step#%s 第 %s 次失败（%s），准备重试",
                               step.get("id"), attempt, result.error_message)
                time.sleep(min(2 ** (attempt - 1), 5))  # 指数退避，上限 5s

        assert last_result is not None
        # 失败时把 traceback 打出来，别让 CaseExecutor 只拿到一行错误摘要。
        # 这是最常见的排障入口——没有这行，排 step 里 [0]/NPE 得一路翻代码猜。
        if last_result.status in (StepStatus.FAILED, StepStatus.ERROR) and last_result.traceback:
            logger.error(
                "step#%s (type=%s, order=%s) 最终失败：%s\n%s",
                step.get("id"),
                step.get("step_type"),
                last_result.step_order,
                last_result.error_message,
                last_result.traceback,
            )
        return last_result

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _build_allure_step_name(step: dict) -> str:
        """生成 Allure step 节点名：[order] step_name (step_type)"""
        order = step.get("step_order")
        name = step.get("step_name") or f"step#{step.get('id')}"
        type_ = step.get("step_type") or "?"
        prefix = f"[{order}] " if order is not None else ""
        return f"{prefix}{name} ({type_})"

    # ---------------- 便捷工厂 ----------------
    @classmethod
    def default(cls) -> "StepDispatcher":
        """返回一个注册好所有内置 Runner 的 dispatcher。

        惰性 import 防止循环依赖。
        """
        from runners.steps.http_request import HttpRequestStepRunner
        from runners.steps.generic import SleepStepRunner, AssertStepRunner
        from runners.steps.app_actions import build_app_runners
        from runners.steps.app_generic import build_app_generic_runners
        # 注意：web step runner 现在是真实实现（web_actions），老的 web_stubs 仅保留兼容。
        from runners.steps.web_actions import build_web_runners

        d = cls()
        d.register(HttpRequestStepRunner())
        d.register(SleepStepRunner())
        d.register(AssertStepRunner())
        d.register_all(build_app_runners())
        d.register_all(build_app_generic_runners())  # 通用 app_action / app_assert
        d.register_all(build_web_runners())
        return d
