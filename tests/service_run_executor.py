# tests/service_run_executor.py
"""平台侧 pytest 唯一入口：`TestService.test_case_runner`。

走 CaseExecutor → StepDispatcher → 各 StepRunner，适用于所有 case_type：
api / web / android / ios / mixed / functional。

v1 的 `test_api_runner`（一条 case = 一次 HTTP 请求）已删。如果某条 case
没有 steps，CaseExecutor 会直接抛错提示先跑 v2_cases_to_steps 数据迁移
（database/migrations/data_migrations/v2_cases_to_steps.py）。
"""
from __future__ import annotations

import pytest

from runners.case_executor import CaseExecutor
from runners.context.auth_cache import RunAuthCache
from runners.context.execution_context import ExecutionContext
from runners.protocol import StepStatus


_RUN_SHARED_VARS: dict[str, object] = {}
_RUN_AUTH_CACHE = RunAuthCache()


def reset_run_shared_vars() -> None:
    """清空单轮 pytest 运行内的跨用例变量池。"""
    _RUN_SHARED_VARS.clear()
    _RUN_AUTH_CACHE.clear()


def _heal_enabled(request) -> bool:
    try:
        return bool(request.config.getoption("--ai_heal"))
    except Exception:  # noqa: BLE001
        return False


def _try_heal_and_retry(case, failed_result, record_property):
    """就地修复并重试一次。返回重试后的 CaseResult；没修或修不动返回 None。

    重试用**新的 ExecutionContext**（带上最新的共享变量池），避免上一轮的
    半截状态串进来。仍失败就如实返回失败结果，绝不粉饰。
    """
    from database.db import DB

    db = None
    try:
        from server.services.inline_heal import heal_case_inline

        db = DB()
        healed = heal_case_inline(case, failed_result, _LAST_CTX[0], session=db.session)
        if not healed:
            return None

        print(
            f"[inline_heal] 用例「{case.get('name')}」自愈："
            f"{healed['subtype']} → 改了 {healed['parts']}；重试中…"
        )
        record_property("inline_heal", healed)

        retry_ctx = ExecutionContext(record_property)
        retry_ctx.set_var("_run_shared_vars", dict(_RUN_SHARED_VARS))
        retry_ctx.set_var("_run_auth_cache", _RUN_AUTH_CACHE)
        retried = CaseExecutor().run(case, retry_ctx)
        _LAST_CTX[0] = retry_ctx
        for key, value in retry_ctx.vars.items():
            if not str(key).startswith("_"):
                _RUN_SHARED_VARS[str(key)] = value
        record_property(
            "inline_heal_result",
            "修复后通过" if retried.status == StepStatus.PASSED else "修复后仍失败",
        )
        return retried
    except Exception as exc:  # noqa: BLE001
        # 自愈本身出错绝不能改变用例结论
        print(f"[inline_heal] 自愈失败（已忽略）：{exc}")
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


# 当前用例的 ctx（自愈时要读 ctx.records 里的 status_code）
_LAST_CTX: list = [None]


class TestService:
    # v2 唯一入口：所有 case（API/Web/App/Android/iOS/Mixed）都走 CaseExecutor。
    # 老的 v1 `test_api_runner`（一条 case = 一次 HTTP 请求）已删；
    # 没有 steps 的 API 老用例需要先跑 database/migrations/data_migrations/v2_cases_to_steps.py
    # 把字段拆成 step。
    def test_case_runner(self, case, record_property, request):
        if case is None:
            pytest.skip("没有接收到待执行的用例数据")

        # ----- Allure 三层层级（项目 > 模块 > 用例）-----
        # web / app 用例之前在 Allure 报告里全部挤在 "Default Suite"，没法按项目 /
        # 模块归类；现在统一在 v2 入口打三层标记，覆盖 Behaviors（epic/feature/story）
        # 和 Suites（parent_suite/suite/sub_suite）两个面板。
        # case 字典由 read_test_cases.get_cases_v2_from_db 提供 project_name / module_name；
        # 缺字段时跳过对应级别（不要写空串，否则 Allure 还会建一个空节点）。
        try:
            from utils.allure_utils import (
                set_allure_case,
                set_allure_case_id,
                set_allure_module,
                set_allure_project,
                set_allure_suites,
                set_allure_title,
            )

            project_name = (case.get("project_name") if isinstance(case, dict) else None) or ""
            module_name = (case.get("module_name") if isinstance(case, dict) else None) or ""
            case_name = (case.get("name") if isinstance(case, dict) else None) or ""

            # 单用例重复执行：pytest_generate_tests 展开时打的 _iteration 标记。
            # 给报告标题加“(第 i/N 次)”后缀，让 N 次各成可区分的条目。
            if isinstance(case, dict) and case.get("_iteration"):
                _it = case.get("_iteration")
                _total = case.get("_iteration_total") or _it
                _suffix = f"(第 {_it}/{_total} 次)"
                if case_name:
                    case_name = f"{case_name} {_suffix}"

            if project_name:
                set_allure_project(project_name)
            if module_name:
                set_allure_module(module_name)
            if case_name:
                set_allure_case(case_name)
                set_allure_title(case_name)
            if isinstance(case, dict) and case.get("id") is not None:
                set_allure_case_id(int(case["id"]))
            # Suites 面板：同样三层；任意为空时该层跳过
            set_allure_suites(
                parent=project_name or None,
                suite=module_name or None,
                sub=case_name or None,
            )
        except Exception:
            # 任何 allure 注入失败都不能影响正常执行（比如 allure 没装、case 不是 dict）
            pass

        ctx = ExecutionContext(record_property)
        ctx.set_var("_run_shared_vars", dict(_RUN_SHARED_VARS))
        ctx.set_var("_run_auth_cache", _RUN_AUTH_CACHE)
        result = CaseExecutor().run(case, ctx)
        _LAST_CTX[0] = ctx

        # ---- 逐条即时自愈：挂了就当场修 + 重试一次，再跑下一条 ----
        # 关键价值是阻断连锁污染：上游用例拿不到变量会让后面几十条全挂，
        # 事后批量修还得再跑一整轮才见效（实测修 58 条只多通过 9 条）。
        if _heal_enabled(request) and result.status in (StepStatus.FAILED, StepStatus.ERROR):
            healed = _try_heal_and_retry(case, result, record_property)
            if healed is not None:
                result = healed

        for key, value in ctx.vars.items():
            if not str(key).startswith("_"):
                _RUN_SHARED_VARS[str(key)] = value
        record_property("variable_pool", dict(_RUN_SHARED_VARS))

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
