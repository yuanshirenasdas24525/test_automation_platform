"""Runner 协议层

一条测试用例（TestCase）= N 条有序步骤（TestStep）。
每种 step_type（http_request / app_tap / web_click / sleep / assert ...）都
对应一个实现了 `StepRunner` 协议的类。

设计要点：
  - 协议用 `typing.Protocol`，Runner 实现类**不需要显式继承**，duck typing 即可；
  - Runner 只关心**一个 step**怎么跑，用例级编排在 `CaseExecutor`；
  - Runner 拿到的是纯字典 + ExecutionContext，**不依赖 SQLAlchemy ORM 实例**，
    这样将来可以脱离平台单独跑（CLI / 本地调试）；
  - Runner 的返回统一成 `StepResult`，由上层决定怎么聚合 / 写 report / 记 allure。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from core.context.execution_context import ExecutionContext


# =============================================================================
# 枚举 & 数据类
# =============================================================================
class StepStatus(str, Enum):
    """一条 step 执行完后的四种状态。"""
    PASSED = "passed"
    FAILED = "failed"            # 断言失败 / 业务失败
    ERROR = "error"              # 系统异常（连不上、NullPointer 之类）
    SKIPPED = "skipped"          # step.skip=True 或上游决定跳过


@dataclass
class StepResult:
    """一条 step 执行完的统一返回体。

    规则：
      - status=PASSED：走到最后断言全过
      - status=FAILED：业务断言失败，traceback 为 None，error_message 填断言描述
      - status=ERROR：非预期异常，traceback 必填
      - status=SKIPPED：skip 原因填 error_message
    """
    step_id: int | None
    step_order: int
    step_name: str
    step_type: str
    status: StepStatus

    # 执行详情
    started_at: float = 0.0              # time.time()
    ended_at: float = 0.0
    duration_ms: int = 0                 # 便于报告层直接展示

    # 载荷快照（给 report / debug 用）
    action: str = ""                     # 人类可读："POST /login"、"tap 登录按钮"
    target: str = ""                     # URL / 元素 locator
    input_data: Any = None               # 请求体 / 输入参数
    output_data: Any = None              # 响应体 / 元素返回值

    # 异常 & 附件
    error_message: str | None = None
    traceback: str | None = None
    attachments: list[dict] = field(default_factory=list)  # [{name, path, type}]

    # 提取出来的变量（供下游 step 使用）
    extracted: dict[str, Any] = field(default_factory=dict)

    def mark_done(self) -> "StepResult":
        """结束后补一下时间字段。"""
        self.ended_at = time.time()
        if self.started_at:
            self.duration_ms = int((self.ended_at - self.started_at) * 1000)
        return self


@dataclass
class CaseResult:
    """一条 Case 跑完的聚合结果。"""
    case_id: int | None
    case_name: str
    case_type: str
    status: StepStatus                   # 所有 step 全过才是 PASSED
    started_at: float = 0.0
    ended_at: float = 0.0
    duration_ms: int = 0
    steps: list[StepResult] = field(default_factory=list)
    error_message: str | None = None


# =============================================================================
# 协议
# =============================================================================
@runtime_checkable
class StepRunner(Protocol):
    """所有 step-level runner 的鸭子协议。

    实现类需要声明：
      - `step_types`：支持哪些 step_type（用 tuple，一个 runner 可注册多种）
      - `execute(step, ctx)`：跑这一步，返回 StepResult

    execute 内部**不要 raise**，所有异常都应包装成 StepResult(status=ERROR)，
    由 CaseExecutor 根据 on_failure 策略决定后续动作。
    """
    step_types: tuple[str, ...]

    def execute(self, step: dict, ctx: ExecutionContext) -> StepResult:
        ...


# =============================================================================
# 基类：实现一些共享工具，派生类继承它可以少写重复代码
# =============================================================================
class BaseStepRunner:
    """协议的默认实现骨架。派生类通常只需要改写 `_run`。"""

    step_types: tuple[str, ...] = ()     # 子类必填

    # ------------ 子类需要实现的部分 ------------
    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        """真正的业务逻辑。

        约定：往 result 上写 action/target/input_data/output_data/extracted，
        断言失败就直接 raise AssertionError，系统异常也直接 raise 其他 Exception。
        execute 会兜底把它们变成合适的 StepStatus。
        """
        raise NotImplementedError

    # ------------ 模板方法：统一异常处理 + 时间统计 ------------
    def execute(self, step: dict, ctx: ExecutionContext) -> StepResult:
        import traceback as tb

        result = StepResult(
            step_id=step.get("id"),
            step_order=step.get("step_order", 0),
            step_name=step.get("step_name") or f"step#{step.get('id')}",
            step_type=step.get("step_type", ""),
            status=StepStatus.PASSED,
            started_at=time.time(),
        )

        if step.get("skip"):
            result.status = StepStatus.SKIPPED
            result.error_message = "step marked skip=true"
            return result.mark_done()

        try:
            self._run(step, ctx, result)
        except AssertionError as exc:
            result.status = StepStatus.FAILED
            result.error_message = str(exc) or "AssertionError"
            result.traceback = tb.format_exc()
        except Exception as exc:  # noqa: BLE001
            result.status = StepStatus.ERROR
            result.error_message = f"{type(exc).__name__}: {exc}"
            result.traceback = tb.format_exc()

        return result.mark_done()
