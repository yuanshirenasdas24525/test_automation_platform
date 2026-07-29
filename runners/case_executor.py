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

import json
import logging
import re
import time
from typing import Any

from runners.context.auth_cache import (
    RunAuthCache,
    build_auth_request_signature,
    extract_hook_values,
)
from runners.context.execution_context import ExecutionContext
from runners.dispatcher import StepDispatcher
from runners.protocol import CaseResult, StepResult, StepStatus

logger = logging.getLogger(__name__)

# ${var} 引用（用于扫描用例里的悬空鉴权变量）
_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _auth_cache_reuse_enabled() -> bool:
    """是否允许用缓存的登录响应顶替真实请求。**默认关闭**。

    关闭的理由见 _run_hooks 里的说明：执行引擎不该谎报"发过请求"。
    只有当被测系统限流无法关闭、且能接受重复登录步骤不真实执行时，
    才设 AUTH_RESPONSE_CACHE=1 打开。
    """
    import os

    return os.getenv("AUTH_RESPONSE_CACHE", "0").strip().lower() in ("1", "true", "yes", "on")


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
            # 悬空鉴权变量自动补齐：必须排在 pre_hook 前面（pre_hook 自己也可能要用 token）
            self._ensure_auth_vars(case_dict, ctx)
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
            "description": getattr(case, "description", None),
            "requirement_id": getattr(case, "requirement_id", None),
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
        requirement = getattr(case, "requirement", None)
        if requirement is not None:
            d["requirement"] = {
                "id": getattr(requirement, "id", None),
                "title": getattr(requirement, "title", None),
                "description": getattr(requirement, "description", None),
                "acceptance_criteria": getattr(requirement, "acceptance_criteria", None) or [],
                "spec_json": getattr(requirement, "spec_json", None) or {},
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

    # ------------------------------------------------------------------
    # 悬空鉴权变量自动补齐（auth_provider）
    # ------------------------------------------------------------------
    def _ensure_auth_vars(self, case_dict: dict, ctx: ExecutionContext) -> None:
        """用例引用了 ${admin_token} 这类鉴权变量、但变量池里没有时，自动登录补齐。

        动机：AI 生成的用例常常引用一个"约定俗成"的 token 变量，却没有任何步骤产出它
        （典型：前置链用例自己就需要 admin 权限建号，形成鸡生蛋）。这类用例 100% 401。
        与其逐条改用例，不如在执行链路上补一次：项目在配置中心配一遍
        `auth_provider`（登录接口 + 凭据 + 提取规则），平台按需自动登录。

        边界（重要）：
        - **只补执行前提，不改验证内容** —— 只产出变量，绝不碰用例的断言/参数；
        - 只补 `auth_provider.extract` 里**显式声明**能产出的变量，不做语义猜测；
        - 没配 `auth_provider` 就完全不生效（默认关闭，零侵入）；
        - 登录失败不中断用例（非 strict），让用例带着自己的真实错误失败，
          避免"自动鉴权失败"这层噪音盖住真实问题。

        为什么不会把接口刷爆：`_RUN_SHARED_VARS` 会把产出的变量带给后续用例，
        所以整轮通常只登录一次；RunAuthCache 是第二层兜底。
        """
        provider = self._load_auth_provider(ctx)
        if not provider:
            return
        providable = self._hook_extract_vars(provider)
        if not providable:
            logger.warning("auth_provider 未声明 extract，无法确定能产出哪些变量，跳过自动补齐")
            return

        referenced = self._referenced_vars(case_dict.get("steps") or [])
        dangling = {name for name in referenced if ctx.vars.get(name) is None}
        needed = dangling & providable
        if not needed:
            return

        names = ", ".join(sorted(needed))
        logger.info("自动鉴权补齐：本用例悬空变量 [%s]，触发 auth_provider 登录", names)
        hook = {"step_name": f"自动鉴权补齐（{names}）", "config": provider}
        self._run_hooks([hook], ctx, label="auto_auth")

        still_missing = sorted(n for n in needed if ctx.vars.get(n) is None)
        if still_missing:
            logger.warning(
                "自动鉴权补齐未能产出 %s —— 请检查配置中心 auth_provider 的 path/params/extract",
                ", ".join(still_missing),
            )
        else:
            ctx.record("auto_auth_vars", sorted(needed))

    @staticmethod
    def _load_auth_provider(ctx: ExecutionContext) -> dict | None:
        """从配置中心读 `auth_provider` 组，拼成一个 http_request step config。

        配置中心的一组配置是扁平 key→str，所以 params / headers / extract 用 JSON 字符串存。
        缺 path 或 extract 视为没配（返回 None，功能整体不生效）。
        """
        try:
            from utils.reload_config import config_center
        except Exception as exc:  # noqa: BLE001
            logger.warning("auth_provider 读取失败（import）：%s", exc)
            return None

        project_id = ctx.vars.get("_project_id")
        if project_id is None:
            return None
        try:
            raw = config_center.get("auth_provider", project_id=project_id) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("auth_provider 读取失败：%s", exc)
            return None
        if not isinstance(raw, dict) or not raw:
            return None

        enabled = str(raw.get("enabled", "true")).strip().lower()
        if enabled in ("0", "false", "no", "off"):
            return None

        path = str(raw.get("path") or "").strip()
        extract = CaseExecutor._loads_dict(raw.get("extract"))
        if not path or not extract:
            return None

        headers = CaseExecutor._loads_dict(raw.get("headers")) or {
            "Content-Type": "application/json"
        }
        return {
            "method": str(raw.get("method") or "POST").upper(),
            "path": path,
            "headers": headers,
            "params": CaseExecutor._loads_dict(raw.get("params")),
            "data_type": str(raw.get("data_type") or "application/json"),
            "extract_data": extract,
        }

    @staticmethod
    def _loads_dict(value: Any) -> dict:
        """配置中心存的是字符串；容忍已经是 dict 的情况（测试直接塞对象）。"""
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
        except Exception:  # noqa: BLE001
            logger.warning("auth_provider 配置不是合法 JSON，已忽略：%.80s", value)
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _referenced_vars(steps: list) -> set[str]:
        """扫出用例所有步骤里引用的 ${var} 变量名。"""
        try:
            blob = json.dumps(steps, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            return set()
        return set(_VAR_REF_RE.findall(blob))

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
        """执行用例前后置 hook。

        ``pre_hook`` 是主步骤的硬依赖，尤其是 AI 参数修复生成的“重新登录并提取
        token”hook：执行失败或没有提取到声明变量时必须中断用例，绝不能继续使用
        ``ctx`` 里从共享参数池带进来的旧 token。``post_hook`` 仍是尽力清理，失败只
        记录日志，不改变用例结论。
        """
        if not hooks or not isinstance(hooks, list):
            return
        strict = label == "pre_hook"
        for i, hk in enumerate(hooks):
            if not isinstance(hk, dict):
                continue
            try:
                # 这里最小实现，复杂逻辑后续迭代：
                # 统一用 dispatcher 跑，但 hook 没有 step_id / 不计入 case.steps
                hook_step = {
                    "id": None,
                    "step_order": -1,
                    "step_name": hk.get("step_name") or f"{label}#{i + 1}",
                    "step_type": hk.get("type", "http_request"),
                    "config": hk.get("config") or hk,
                    "skip": bool(hk.get("skip") or False),
                    "wait_before": float(hk.get("wait_before") or 0),
                    "timeout": int(hk.get("timeout") or 30),
                    "retry": int(hk.get("retry") or 0),
                    "on_failure": hk.get("on_failure") or "stop",
                    "_is_hook": True,
                }
                expected_vars = self._hook_extract_vars(hook_step["config"])
                # 本用例前置步骤声明要产出的变量不能沿用共享池旧值。用户即使选择
                # “跳过”该步骤，也只会得到明确的未解析变量/鉴权失败，不会悄悄复用
                # 已过期 token。
                for var_name in expected_vars:
                    ctx.vars.pop(var_name, None)
                if hook_step["skip"]:
                    self.dispatcher.dispatch(hook_step, ctx)
                    continue
                cache = ctx.get_var("_run_auth_cache")
                signature = build_auth_request_signature(hook_step["config"], ctx)
                # 复用登录响应**默认关闭**：把声明要发的请求偷偷换成缓存响应，会让
                # 报告撒谎——步骤显示"登录"、挂着一份响应，实际什么都没发出去，
                # 对它的断言全是空的；同一账号重复登录也会拿到同一个 token，
                # "多会话/多设备"这类语义在平台里直接失真。
                #
                # 它当初是为了压住被测系统的登录限流，但那是用污染测试结果的方式
                # 解决对方的问题。限流应由被测系统侧配置解决
                #（见 LOGIN_THROTTLE_ENABLED）。
                # 确有需要（如对方限流无法关闭）可设 AUTH_RESPONSE_CACHE=1 恢复旧行为，
                # 但要清楚代价：那一轮里重复的登录步骤不再真实执行。
                if _auth_cache_reuse_enabled() and isinstance(cache, RunAuthCache) and signature:
                    cached_response = cache.get(signature)
                    cached_extracts = extract_hook_values(hook_step["config"], cached_response)
                    if cached_response is not None and expected_vars <= set(cached_extracts):
                        for key, value in cached_extracts.items():
                            ctx.set_var(key, value)
                        logger.info(
                            "%s 命中单轮认证缓存，复用登录响应并映射变量：%s"
                            "（AUTH_RESPONSE_CACHE=1 开启，本步骤未真实发送请求）",
                            hook_step["step_name"],
                            ", ".join(sorted(cached_extracts)),
                        )
                        continue
                result = self.dispatcher.dispatch(hook_step, ctx)

                # AI 可能把 $.data.access_token 猜成 $.access_token。登录已成功时，
                # 根据真实响应中的唯一同名叶子键纠偏，避免 200 响应仍空提取。
                #
                # 纠偏必须排在下面的 strict 判定**之前**：http_request runner 对 hook 的
                # 提取失败会直接把步骤判 FAILED（见 steps/http_request.py 的 _is_hook 分支），
                # 若先判 strict 就永远走不到这里 —— 这段补救对它本该服务的场景是死代码。
                recovered = extract_hook_values(hook_step["config"], result.output_data)
                result.extracted = dict(result.extracted or {})
                for key, value in recovered.items():
                    if key not in result.extracted:
                        result.extracted[key] = value
                        ctx.set_var(key, value)
                if isinstance(cache, RunAuthCache) and signature and result.status == StepStatus.PASSED:
                    cache.put(signature, result.output_data)

                missing_vars = sorted(expected_vars - set(result.extracted or {}))
                hook_failed = result.status in (StepStatus.FAILED, StepStatus.ERROR)
                if strict and hook_failed and missing_vars:
                    raise RuntimeError(
                        f"{hook_step['step_name']} 执行失败："
                        f"{result.error_message or result.status.value}"
                    )
                if strict and missing_vars:
                    raise RuntimeError(
                        f"{hook_step['step_name']} 未提取到声明变量："
                        f"{', '.join(missing_vars)}；已阻止回退使用共享参数池旧值"
                    )
                if hook_failed and not missing_vars:
                    # 步骤被判失败、但声明变量已靠纠偏拿全：放行并留痕，便于用户回头
                    # 把用例里写错的 JSONPath 改对。
                    logger.warning(
                        "%s 步骤判定为 %s，但已通过响应纠偏取到全部声明变量 %s —— "
                        "建议修正该 hook 的 JSONPath",
                        hook_step["step_name"], result.status.value, sorted(expected_vars),
                    )
            except Exception as exc:  # noqa: BLE001
                if strict:
                    raise
                logger.warning("%s 执行失败（已忽略）: %s", label, exc)

    @staticmethod
    def _hook_extract_vars(config: Any) -> set[str]:
        """读取 hook 声明的提取变量名，兼容 v1 字典与 v2 规则列表。"""
        if not isinstance(config, dict):
            return set()
        raw = config.get("extract_data") or config.get("extract") or {}
        if isinstance(raw, str):
            try:
                import json
                raw = json.loads(raw)
            except Exception:  # noqa: BLE001
                return set()
        if isinstance(raw, dict):
            return {str(key) for key in raw if str(key).strip()}
        if isinstance(raw, list):
            return {
                str(rule.get("name"))
                for rule in raw
                if isinstance(rule, dict) and rule.get("name")
            }
        return set()

    def _run_steps(self, steps: list[dict], ctx: ExecutionContext) -> list[StepResult]:
        """按顺序跑所有 step，遇到 FAILED/ERROR 时按 on_failure 决定是否继续。"""
        results: list[StepResult] = []
        for idx, step in enumerate(steps):
            # 给没有 step_order 的 step 补一个合理序号
            if step.get("step_order") is None:
                step["step_order"] = idx

            transient_record_keys = (
                "status_code",
                "assertion_results",
                "extract_values",
                "extract_errors",
                "page_info",
            )
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
                if (
                    step.get("step_type") == "http_request"
                    and bool(ctx.get_var("_ai_heal_enabled"))
                ):
                    # 自愈必须发生在下一个接口请求之前。即使用例原来配置了
                    # on_failure=continue，也先在当前请求形成断点；修好并重跑后，
                    # CaseExecutor 才会按更新后的定义继续后续步骤。
                    logger.warning(
                        "step_order=%s 失败，AI 自愈模式立即中断后续请求",
                        result.step_order,
                    )
                    break
                if strategy == "continue":
                    logger.warning("step_order=%s 失败但 on_failure=continue，继续下一条",
                                   result.step_order)
                    continue
                # stop / retry 走到这里都已经失败了，不再继续
                logger.error("step_order=%s 失败，on_failure=%s，中断剩余步骤",
                             result.step_order, strategy)
                break

        return results
