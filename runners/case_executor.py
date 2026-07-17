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

from runners.context.execution_context import ExecutionContext
from runners.dispatcher import StepDispatcher
from runners.protocol import CaseResult, StepResult, StepStatus

logger = logging.getLogger(__name__)


class CaseExecutor:
    def __init__(
        self,
        dispatcher: StepDispatcher | None = None,
        app_session_factory=None,
        web_session_factory=None,
        app_session_registry=None,
    ):
        """
        :param dispatcher: Step 派发器，默认用 StepDispatcher.default()
        :param app_session_factory: 可调用对象 (case_dict) -> AppSession。
            测试里可以塞 FakeDriver 工厂，避免真的去连 Appium/DB。
            **注意**：传了这个会绕开 AppSessionRegistry（即不跨 case 复用），
            保持老单测"每 case 自建 + finally 关"的行为不变。
            默认 None：走 AppSessionRegistry。
        :param web_session_factory: 可调用对象 (case_dict) -> WebSession。
            测试里可以塞 FakeAdapter 工厂，避免真的拉浏览器。
            默认 None：命中 web/mixed case 或含 web_* step 时才用
            `web.session.acquire_session_for_case`。
        :param app_session_registry: 可选 AppSessionRegistry 实例。默认用 singleton。
            用单测想隔离 registry 状态时传一个新实例进来。
        """
        self.dispatcher = dispatcher or StepDispatcher.default()
        self._app_session_factory = app_session_factory
        self._web_session_factory = web_session_factory
        self._app_session_registry = app_session_registry

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

        # App session 所有权：
        #   - owned=True：本 case 自己建的（走了 app_session_factory 路径），
        #     finally 里要自己 close；
        #   - owned=False：是从 AppSessionRegistry 借来的，跨 case 持久化，
        #     finally 里只解绑 ctx，不关 driver —— 关 driver 由 pytest_sessionfinish
        #     里的 registry.close_all() 负责。
        app_session = None
        app_session_owned = False
        web_session = None
        try:
            self._inject_variables(case_dict, ctx)
            self._run_hooks(case_dict.get("pre_hook"), ctx, label="pre_hook")

            steps = case_dict.get("steps") or []
            if not steps:
                # v1 兼容路径已删。老 case 没 steps 必须先跑数据迁移：
                # python -m database.migrations.data_migrations.v2_cases_to_steps
                # 把 method/path/headers 等老字段拆成一条 http_request step。
                raise ValueError(
                    f"case={cr.case_name} 没有 steps —— v1 兼容路径已移除，"
                    f"请先运行 v2_cases_to_steps 数据迁移把老字段转成 step。"
                )

            # App / Web 会话生命周期（按需懒建）
            app_session, app_session_owned = self._maybe_acquire_app_session(
                case_dict, steps, ctx,
            )
            web_session = self._maybe_acquire_web_session(case_dict, steps, ctx)

            cr.steps = self._run_steps(steps, ctx)

            # 任一 step 失败则整条 case 失败
            for sr in cr.steps:
                if sr.status in (StepStatus.FAILED, StepStatus.ERROR):
                    cr.status = sr.status
                    cr.error_message = sr.error_message
                    break
        except Exception as exc:  # noqa: BLE001
            import traceback as tb
            cr.status = StepStatus.ERROR
            cr.error_message = f"{type(exc).__name__}: {exc}"
            logger.error("case=%s 执行被中断：%s\n%s", cr.case_name, exc, tb.format_exc())
        finally:
            # teardown（数据治理#2）：无论用例通过/失败/抛异常，都执行 post_hook 清理
            # 刚建的测试数据，避免脏数据堆积。post_hook 失败只 warn、不影响用例结论。
            # 放在会话关闭之前，确保 sql:/http DELETE 清理仍能用到 ctx 里已提取的变量。
            self._run_hooks(case_dict.get("post_hook"), ctx, label="post_hook")
            self._close_target_dbs(ctx)
            if app_session is not None:
                if app_session_owned:
                    try:
                        app_session.close()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("AppSession.close 失败（已忽略）：%s", exc)
                else:
                    # registry-owned：不关 driver，只把 ctx 上的引用清掉，避免
                    # 下一条 case 的 ExecutionContext 串进来前还能看到旧 session
                    try:
                        ctx.vars.pop("_app_session", None)
                        ctx.vars.pop("_device", None)
                    except Exception:  # noqa: BLE001
                        pass
            if web_session is not None:
                try:
                    web_session.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("WebSession.close 失败（已忽略）：%s", exc)

        cr.ended_at = time.time()
        cr.duration_ms = int((cr.ended_at - cr.started_at) * 1000)
        logger.info("■ CaseExecutor.run case=%s 耗时 %sms 状态 %s",
                    cr.case_name, cr.duration_ms, cr.status.value)
        return cr

    # ------------------------------------------------------------
    # App 会话：按需获取 + 绑定到 ctx
    # ------------------------------------------------------------
    def _maybe_acquire_app_session(
        self, case_dict: dict, steps: list[dict], ctx: ExecutionContext,
    ) -> tuple["AppSession | None", bool]:
        """命中以下任一条件才获取 AppSession：
          - case_type ∈ APP_CASE_TYPES ∪ {'mixed'}（即 app/android/ios/mixed）；
          - steps 里出现任意 step_type 以 'app_' 开头。

        返回 (session, owned)：
          - owned=True 表示由本 CaseExecutor 负责 close（走了 app_session_factory 的测试路径）；
          - owned=False 表示 registry 持有所有权，case 结束不关，pytest_sessionfinish 收尾。
        """
        from database.models import APP_CASE_TYPES
        case_type = (case_dict.get("case_type") or "").lower()
        needs_app = case_type in (APP_CASE_TYPES | {"mixed"}) or any(
            isinstance(s, dict) and str(s.get("step_type") or "").startswith("app_")
            for s in steps
        )
        if not needs_app:
            return None, False

        try:
            if self._app_session_factory is not None:
                # 单测 / 离线 fake 路径：保持老语义（每 case 自建 + finally 关）
                session = self._app_session_factory(case_dict)
                owned = True
            else:
                # 生产路径：走 AppSessionRegistry，跨 case 复用
                from runners.app.session_registry import AppSessionRegistry
                registry = self._app_session_registry or AppSessionRegistry.default()
                session = registry.get_or_create(case_dict)
                owned = False
        except Exception as exc:  # noqa: BLE001
            # 拿不到设备/建不起 session，记成 error 让后面的 app_* step 都拿不到 driver 立刻失败
            logger.error("无法获取 App 会话：%s", exc)
            raise

        # 绑到 ctx，供 AppStepRunner.require(ctx) 使用
        from runners.app.session import AppSession  # noqa: WPS433 局部 import
        AppSession.bind(ctx, session)
        logger.info(
            "已绑定 AppSession：device=%s owned=%s",
            session.device.get("udid"), owned,
        )
        return session, owned

    # ------------------------------------------------------------
    # Web 会话：按需获取 + 绑定到 ctx
    # ------------------------------------------------------------
    def _maybe_acquire_web_session(self, case_dict: dict, steps: list[dict], ctx: ExecutionContext):
        """命中以下任一条件才构造 WebSession：
          - case_type ∈ {'web', 'mixed'}；
          - steps 里出现任意 step_type 以 'web_' 开头。

        和 app 那侧不一样的是：web 没有"设备池"，只要 adapter 起得来就能跑，
        所以这里构造出 WebSession 后**不会立刻拉起浏览器**（adapter 是懒启动的）。
        """
        case_type = (case_dict.get("case_type") or "").lower()
        needs_web = case_type in ("web", "mixed") or any(
            isinstance(s, dict) and str(s.get("step_type") or "").startswith("web_")
            for s in steps
        )
        if not needs_web:
            return None

        try:
            if self._web_session_factory is not None:
                session = self._web_session_factory(case_dict)
            else:
                from runners.web.session import acquire_session_for_case
                session = acquire_session_for_case(case_dict)
        except Exception as exc:  # noqa: BLE001
            logger.error("无法构造 WebSession：%s", exc)
            raise

        from runners.web.session import WebSession  # noqa: WPS433 局部 import
        WebSession.bind(ctx, session)
        logger.info("已绑定 WebSession：engine=%s", session.engine)
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
        """把"全局默认参数 / env / case"三层变量塞进 ctx，后续的 ${var} 才能取到。

        优先级（窄者覆盖宽者）：
          case.variables  >  env.variables  >  config_center.default_parameters

        历史坑：v1 的 ApiClient 直接读 config_center.get('default_parameters') 当
        extra_pool；v2 step runner 只看 ctx.vars，导致用户在"配置中心"里配的
        ${code} 这类全局参数对 web/app/mixed 步骤完全失效（${code} 始终不解析）。
        现在在 case 启动时把 default_parameters 也铺到 ctx.vars 最底层，env / case
        覆盖优先级保持不变。
        """
        project_id = case_dict.get("project_id")
        if project_id is not None:
            ctx.set_var("_project_id", project_id)
        # 0a) 项目级：default_parameters（兜底层）
        self._inject_default_parameters(ctx)
        # 0b) sql: 前缀需要的 target DB 连接，从配置中心拿；拿不到不阻塞
        self._inject_target_db(ctx)
        # 1) env.variables
        env = case_dict.get("environment") or {}
        env_vars = env.get("variables") or {}
        if isinstance(env_vars, dict):
            for k, v in env_vars.items():
                ctx.set_var(k, v)
        # 2) case.variables（最优先）
        case_vars = case_dict.get("variables") or {}
        if isinstance(case_vars, dict):
            for k, v in case_vars.items():
                ctx.set_var(k, v)
        # 3) 同一轮执行内由前序 case 提取出的变量，优先级最高。
        # 这样“登录用例提取 token → 后续接口用例使用 ${token}”这类链式执行可以成立。
        run_shared_vars = ctx.vars.get("_run_shared_vars") or {}
        if isinstance(run_shared_vars, dict):
            for k, v in run_shared_vars.items():
                if not str(k).startswith("_"):
                    ctx.set_var(k, v)

    @staticmethod
    def _inject_default_parameters(ctx: ExecutionContext) -> None:
        """从配置中心读 `default_parameters` 组的所有 key/value 注入 ctx.vars。

        - 优先用 singleton 缓存（FastAPI 改完 config 时已经 reload 过）；
        - 拿不到缓存时尝试自己开 DB 做一次精准 reload（cheap：只刷一组）；
        - 任何异常都吞掉，只 warn —— 配置读不到不能阻塞用例执行。
        """
        try:
            from utils.reload_config import config_center
        except Exception as exc:  # noqa: BLE001
            logger.warning("default_parameters 注入失败（import）：%s", exc)
            return

        project_id = ctx.vars.get("_project_id")
        defaults = config_center.get("default_parameters", project_id=project_id) or {}
        # 缓存里没有就尝试触发一次 reload（pytest worker 进程冷启动场景）
        if not defaults:
            db = None
            try:
                from database.db import DB  # 延迟 import：避免在没有 DB 的单测里炸
                db = DB()
                config_center.reload(db.sql, project_id=project_id, category="api")
                defaults = config_center.get("default_parameters", project_id=project_id) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("default_parameters 主动 reload 失败（已忽略）：%s", exc)
            finally:
                try:
                    if db is not None:
                        db.close()
                except Exception:  # noqa: BLE001
                    pass

        if not isinstance(defaults, dict) or not defaults:
            return
        for k, v in defaults.items():
            ctx.set_var(k, v)
        logger.info(
            "已注入 default_parameters：%d 个变量 (%s)",
            len(defaults),
            ", ".join(list(defaults.keys())[:8]),
        )

    @staticmethod
    def _inject_target_db(ctx: ExecutionContext) -> None:
        """从配置中心读 `target_db` 配置，开一个 DB 连接塞到 ctx.vars['_db']。

        sql: 前缀的 value 需要 ctx.vars['_db']（duck-type：实现 fetchone(query)），
        没注入的话 value_resolver 会显式抛错。这里跟 v1 ApiClient.factory 行为对齐，
        但配置只从当前项目读取，不再回退全局模板。

        - target_db 没配 → 跳过；后续 sql: 步骤会报"未注入 _db"，提示用户去配置中心配
        - 已经注入过 → 跳过（不重复开连接）
        - 任何异常都 warn 后吞掉 —— sql: 是少数路径，不能阻塞主流程
        """
        # 已经有人显式注入过（pre_hook / 测试代码），不覆盖
        if ctx.vars.get("_db") is not None:
            return
        try:
            from utils.reload_config import config_center
            project_id = ctx.vars.get("_project_id")
            groups = config_center.database_groups(project_id)
            group = groups[0] if groups else "target_db"
            target = config_center.get(group, project_id=project_id) or {}
            if not target:
                return
            from database.db import DB
            connection = DB({**target, "password": target.get("password", "")})
            ctx.set_var("_db", connection)
            ctx.set_var("_db_group", group)
            ctx.set_var("_db_connections", {group: connection})
            logger.info("已注入数据库配置组 %s 到 ctx._db (sql: 前缀可用)", group)
        except Exception as exc:  # noqa: BLE001
            logger.warning("target_db 注入失败（已忽略）：%s", exc)

    @staticmethod
    def _close_target_dbs(ctx: ExecutionContext) -> None:
        """关闭本用例按需创建的所有目标数据库会话。"""
        connections = ctx.vars.get("_db_connections")
        if not isinstance(connections, dict):
            return
        ctx.vars.pop("_db_connections", None)
        seen: set[int] = set()
        for connection in connections.values():
            if connection is None or id(connection) in seen:
                continue
            seen.add(id(connection))
            try:
                connection.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("目标数据库连接关闭失败（已忽略）：%s", exc)
        ctx.vars.pop("_db", None)
        ctx.vars.pop("_db_group", None)

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

            transient_record_keys = ("status_code", "assertion_results", "extract_values", "page_info")
            for key in transient_record_keys:
                getattr(ctx, "records", {}).pop(key, None)
            result = self.dispatcher.dispatch(step, ctx)
            results.append(result)

            # 暴露上一条响应给后续 assert step 用
            if result.output_data is not None:
                ctx.set_var("_last_response_body", result.output_data)

            # 把 extract 到的变量沉淀进 ctx（Runner 内部已经做了一遍，这里是兜底）
            for k, v in (result.extracted or {}).items():
                ctx.set_var(k, v)

            input_data = result.input_data
            if isinstance(input_data, dict):
                input_data = dict(input_data)
                input_data["variable_pool"] = {
                    k: v for k, v in (ctx.vars or {}).items()
                    if not str(k).startswith("_")
                }

            # 记录到 record_property，平台 tasks 层会读
            records_after = getattr(ctx, "records", {})
            step_record = {
                "step_id": result.step_id,
                "step_order": result.step_order,
                "step_name": result.step_name,
                "step_type": result.step_type,
                "status": result.status.value,
                "action": result.action,
                "target": result.target,
                "duration_ms": result.duration_ms,
                "error": result.error_message,
                "input_data": input_data,
                "output_data": result.output_data,
                "extract_values": result.extracted,
                "attachments": result.attachments,
            }
            for key in transient_record_keys:
                if key in records_after:
                    step_record[key] = records_after.get(key)
            ctx.record(f"step_{result.step_order}", step_record)

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
