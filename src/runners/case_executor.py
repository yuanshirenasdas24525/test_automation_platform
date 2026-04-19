"""CaseExecutor：一条 TestCase 的**用例级**编排。

职责划分：
  - Runner（step-level）：只管一个 step 怎么跑。
  - Dispatcher：按 step_type 找到对应 Runner，处理 retry / wait_before / on_failure。
  - Executor（case-level）← 本文件：
      * 把 TestCase + 关联 steps + 关联 env/variables 取出来 → dict 化；
      * 执行前后的 pre_hook / post_hook；
      * 变量注入（env / case / step）到 ExecutionContext；
      * 按顺序过所有 step，聚合成 CaseResult；
      * 按 on_failure=stop/continue 决定是否中断后续 step。

使用示例（平台 Worker 里）：

    session = DB().session
    case = session.query(TestCase).options(
        selectinload(TestCase.steps),
        joinedload(TestCase.environment),
    ).get(case_id)
    ctx = ExecutionContext(record_property)
    result = CaseExecutor().run(case, ctx)
    # result.status == StepStatus.PASSED 就是通过
"""
from __future__ import annotations

import logging
import time
from typing import Any

from src.core.context.execution_context import ExecutionContext
from src.runners.dispatcher import StepDispatcher
from src.runners.protocol import CaseResult, StepResult, StepStatus

logger = logging.getLogger(__name__)


class CaseExecutor:
    def __init__(
        self,
        dispatcher: StepDispatcher | None = None,
        app_session_factory=None,
    ):
        """
        :param dispatcher: Step 派发器，默认用 StepDispatcher.default()
        :param app_session_factory: 可调用对象 (case_dict) -> AppSession。
            测试里可以塞 FakeDriver 工厂，避免真的去连 Appium/DB。
            默认 None：命中 app/mixed case 时才用 `app.session.acquire_session_for_case`。
        """
        self.dispatcher = dispatcher or StepDispatcher.default()
        self._app_session_factory = app_session_factory

    # ------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------
    def run(self, case: Any, ctx: ExecutionContext | None = None) -> CaseResult:
        """case 支持两种输入：
          - SQLAlchemy 的 TestCase ORM 实例（会自动取 steps / environment）；
          - 纯字典（离线单测 / 平台序列化后的 payload）。
        """
        ctx = ctx or ExecutionContext()
        case_dict = self._coerce_to_dict(case)
        started = time.time()

        cr = CaseResult(
            case_id=case_dict.get("id"),
            case_name=case_dict.get("name") or f"case#{case_dict.get('id')}",
            case_type=case_dict.get("case_type") or "api",
            status=StepStatus.PASSED,
            started_at=started,
        )

        logger.info("▶ CaseExecutor.run case=%s type=%s", cr.case_name, cr.case_type)

        app_session = None
        try:
            self._inject_variables(case_dict, ctx)
            self._run_hooks(case_dict.get("pre_hook"), ctx, label="pre_hook")

            steps = case_dict.get("steps") or []
            if not steps:
                # v1 老用例兼容：case 本身就是一条 http_request
                logger.info("case=%s 没有 steps，按 v1 兼容模式合成一条 http_request step", cr.case_name)
                steps = [self._synthesize_v1_step(case_dict)]

            # App 会话生命周期（只有命中 app 类 case 才起）
            app_session = self._maybe_acquire_app_session(case_dict, steps, ctx)

            cr.steps = self._run_steps(steps, ctx)

            # 任一 step 失败则整条 case 失败
            for sr in cr.steps:
                if sr.status in (StepStatus.FAILED, StepStatus.ERROR):
                    cr.status = sr.status
                    cr.error_message = sr.error_message
                    break

            self._run_hooks(case_dict.get("post_hook"), ctx, label="post_hook")
        except Exception as exc:  # noqa: BLE001
            import traceback as tb
            cr.status = StepStatus.ERROR
            cr.error_message = f"{type(exc).__name__}: {exc}"
            logger.error("case=%s 执行被中断：%s\n%s", cr.case_name, exc, tb.format_exc())
        finally:
            if app_session is not None:
                try:
                    app_session.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("AppSession.close 失败（已忽略）：%s", exc)

        cr.ended_at = time.time()
        cr.duration_ms = int((cr.ended_at - cr.started_at) * 1000)
        logger.info("■ CaseExecutor.run case=%s 耗时 %sms 状态 %s",
                    cr.case_name, cr.duration_ms, cr.status.value)
        return cr

    # ------------------------------------------------------------
    # App 会话：按需获取 + 绑定到 ctx
    # ------------------------------------------------------------
    def _maybe_acquire_app_session(self, case_dict: dict, steps: list[dict], ctx: ExecutionContext):
        """命中以下任一条件才获取设备 & 建立 AppSession：
          - case_type ∈ {'app', 'mixed'}；
          - steps 里出现任意 step_type 以 'app_' 开头。
        """
        case_type = (case_dict.get("case_type") or "").lower()
        needs_app = case_type in ("app", "mixed") or any(
            isinstance(s, dict) and str(s.get("step_type") or "").startswith("app_")
            for s in steps
        )
        if not needs_app:
            return None

        try:
            if self._app_session_factory is not None:
                session = self._app_session_factory(case_dict)
            else:
                # 惰性 import，避免没装 Appium 的 API-only 环境被牵连
                from src.runners.app.session import acquire_session_for_case
                session = acquire_session_for_case(case_dict)
        except Exception as exc:  # noqa: BLE001
            # 拿不到设备/建不起 session，记成 error 让后面的 app_* step 都拿不到 driver 立刻失败
            logger.error("无法获取 App 会话：%s", exc)
            raise

        # 绑到 ctx，供 AppStepRunner.require(ctx) 使用
        from src.runners.app.session import AppSession  # noqa: WPS433 局部 import
        AppSession.bind(ctx, session)
        logger.info("已绑定 AppSession：device=%s", session.device.get("udid"))
        return session

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------
    def _coerce_to_dict(self, case: Any) -> dict:
        """把 ORM 实例拍平成 dict（不用 pydantic 是为了避免加载 schema 模块）"""
        if isinstance(case, dict):
            # 调用方可能已经 dump 好了；我们尽量兼容它没给 steps 的情况
            return case

        # SQLAlchemy 实例 duck-type
        d: dict = {
            "id": getattr(case, "id", None),
            "name": getattr(case, "name", None),
            "case_type": getattr(case, "case_type", None) or "api",
            "tags": getattr(case, "tags", None),
            "priority": getattr(case, "priority", None),
            "timeout": getattr(case, "timeout", None),
            "retry": getattr(case, "retry", None),
            "pre_hook": getattr(case, "pre_hook", None),
            "post_hook": getattr(case, "post_hook", None),
            "variables": getattr(case, "variables", None),
            # v1 遗留字段（用于兼容）
            "method": getattr(case, "method", None),
            "path": getattr(case, "path", None),
            "headers": getattr(case, "headers", None),
            "data_type": getattr(case, "data_type", None),
            "params": getattr(case, "params", None),
            "file_path": getattr(case, "file_path", None),
            "extract_data": getattr(case, "extract_data", None),
            "sql_query": getattr(case, "sql_query", None),
            "assertion": getattr(case, "assertion", None),
            "wait_time": getattr(case, "wait_time", None),
        }

        # steps：若是 ORM 实例则每个都转 dict
        steps_attr = getattr(case, "steps", None) or []
        steps: list[dict] = []
        for s in steps_attr:
            if hasattr(s, "to_dict"):
                steps.append(s.to_dict())
            elif isinstance(s, dict):
                steps.append(s)
        d["steps"] = steps

        # environment
        env = getattr(case, "environment", None)
        if env is not None:
            d["environment"] = {
                "id": getattr(env, "id", None),
                "name": getattr(env, "name", None),
                "host": getattr(env, "host", None),
                "variables": getattr(env, "variables", None),
                "secrets": getattr(env, "secrets", None),
            }
        return d

    def _inject_variables(self, case_dict: dict, ctx: ExecutionContext) -> None:
        """把 env / case 的 variables 塞进 ctx。后续的 ${var} 才能取到。

        优先级：case.variables > env.variables（case 级覆盖 env 级）
        """
        env = case_dict.get("environment") or {}
        env_vars = env.get("variables") or {}
        if isinstance(env_vars, dict):
            for k, v in env_vars.items():
                ctx.set_var(k, v)
        case_vars = case_dict.get("variables") or {}
        if isinstance(case_vars, dict):
            for k, v in case_vars.items():
                ctx.set_var(k, v)

    def _run_hooks(self, hooks: Any, ctx: ExecutionContext, label: str) -> None:
        """hooks = [{type:'sql'|'http'|'script', ...}, ...]；失败只 log，不中断主流程。"""
        if not hooks or not isinstance(hooks, list):
            return
        for i, hk in enumerate(hooks):
            if not isinstance(hk, dict):
                continue
            try:
                # 这里最小实现，复杂逻辑后续迭代：
                # 统一用 dispatcher 跑，但 hook 没有 step_id / 不计入 case.steps
                hook_step = {
                    "id": None,
                    "step_order": -1,
                    "step_name": f"{label}#{i}",
                    "step_type": hk.get("type", "http_request"),
                    "config": hk.get("config") or hk,
                    "skip": False,
                    "retry": 0,
                    "on_failure": "continue",
                }
                self.dispatcher.dispatch(hook_step, ctx)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 执行失败（已忽略）: %s", label, exc)

    def _run_steps(self, steps: list[dict], ctx: ExecutionContext) -> list[StepResult]:
        """按顺序跑所有 step，遇到 FAILED/ERROR 时按 on_failure 决定是否继续。"""
        results: list[StepResult] = []
        for idx, step in enumerate(steps):
            # 给没有 step_order 的 step 补一个合理序号
            if step.get("step_order") is None:
                step["step_order"] = idx

            result = self.dispatcher.dispatch(step, ctx)
            results.append(result)

            # 暴露上一条响应给后续 assert step 用
            if result.output_data is not None:
                ctx.set_var("_last_response_body", result.output_data)

            # 把 extract 到的变量沉淀进 ctx（Runner 内部已经做了一遍，这里是兜底）
            for k, v in (result.extracted or {}).items():
                ctx.set_var(k, v)

            # 记录到 record_property，平台 tasks 层会读
            ctx.record(f"step_{result.step_order}", {
                "status": result.status.value,
                "action": result.action,
                "target": result.target,
                "duration_ms": result.duration_ms,
                "error": result.error_message,
            })

            # 失败策略：stop 就中断；continue 就跳过；其他（包括 retry，dispatcher 内部已处理）继续下一条
            if result.status in (StepStatus.FAILED, StepStatus.ERROR):
                strategy = (step.get("on_failure") or "stop").lower()
                if strategy == "continue":
                    logger.warning("step_order=%s 失败但 on_failure=continue，继续下一条",
                                   result.step_order)
                    continue
                # stop / retry 走到这里都已经失败了，不再继续
                logger.error("step_order=%s 失败，on_failure=%s，中断剩余步骤",
                             result.step_order, strategy)
                break

        return results

    @staticmethod
    def _synthesize_v1_step(case_dict: dict) -> dict:
        """v1 兼容：老 case 没 steps，就用 case 本身的 method/path 合成一条 http_request。"""
        return {
            "id": None,
            "step_order": 0,
            "step_name": case_dict.get("name") or "v1-compat",
            "step_type": "http_request",
            "skip": False,
            "retry": 0,
            "on_failure": "stop",
            "wait_before": case_dict.get("wait_time") or 0,
            "timeout": 60,
            "config": {
                "method": case_dict.get("method") or "GET",
                "path": case_dict.get("path") or "",
                "headers": _maybe_json(case_dict.get("headers")),
                "data_type": case_dict.get("data_type") or "application/json",
                "params": _maybe_json(case_dict.get("params")),
                "file_path": case_dict.get("file_path"),
                "sql_query": case_dict.get("sql_query"),
            },
            # 这里不转 extract_data / assertion，因为 v1 格式结构多变；由老 ApiClient 走兼容分支
            "extract": [],
            "assertion": [],
        }


def _maybe_json(raw: Any) -> Any:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        import json
        return json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return {}
