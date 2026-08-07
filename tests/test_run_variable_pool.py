"""单轮跨用例 token 生命周期回归测试。"""
from __future__ import annotations

import base64
import json

from runners.context.execution_context import ExecutionContext
from runners.context.run_variable_pool import update_run_shared_vars
from runners.protocol import CaseResult, StepResult, StepStatus


def _jwt(sub: str, sid: str, token_type: str = "access") -> str:
    def part(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'sub': sub, 'sid': sid, 'type': token_type})}.sig"


def _case_result(status: StepStatus, *steps: StepResult) -> CaseResult:
    return CaseResult(
        case_id=1,
        case_name="case",
        case_type="api",
        status=status,
        steps=list(steps),
    )


def _login_step(access: str, refresh: str, *, status=StepStatus.PASSED, extracted=None):
    return StepResult(
        step_id=1,
        step_order=0,
        step_name="管理员登录",
        step_type="http_request",
        status=status,
        target="http://host/api/auth/login",
        output_data={
            "status": "success",
            "data": {"access_token": access, "refresh_token": refresh},
        },
        extracted=extracted or {},
    )


def test_failed_case_login_refreshes_same_subject_shared_token():
    """登录已成功但后续断言失败时，新会话仍必须替换共享池旧会话。"""
    old_access = _jwt("admin", "old")
    new_access = _jwt("admin", "new")
    new_refresh = _jwt("admin", "new", "refresh")
    shared = {"admin_token": old_access, "unrelated": "keep"}
    ctx = ExecutionContext()
    ctx.vars = {"admin_token": new_access, "half_product": "do-not-publish"}
    step = _login_step(
        new_access,
        new_refresh,
        status=StepStatus.FAILED,
        extracted={"admin_token": new_access},
    )

    update_run_shared_vars(shared, _case_result(StepStatus.FAILED, step), ctx)

    assert shared["admin_token"] == new_access
    assert shared["unrelated"] == "keep"
    assert "half_product" not in shared


def test_login_without_extract_refreshes_existing_token_name():
    """普通登录用例不提取 token，也不能把共享 admin_token 留成已吊销旧值。"""
    old_access = _jwt("admin", "old")
    new_access = _jwt("admin", "new")
    shared = {"admin_token": old_access}
    ctx = ExecutionContext()
    ctx.vars = {"admin_token": old_access}

    update_run_shared_vars(
        shared,
        _case_result(StepStatus.PASSED, _login_step(new_access, _jwt("admin", "new", "refresh"))),
        ctx,
    )

    assert shared["admin_token"] == new_access


def test_logout_removes_access_and_refresh_tokens_for_same_session():
    access = _jwt("user-1", "sid-1")
    refresh = _jwt("user-1", "sid-1", "refresh")
    other = _jwt("user-1", "sid-2")
    shared = {"access_token": access, "refresh_token": refresh, "other_token": other}
    ctx = ExecutionContext()
    ctx.vars = dict(shared)
    logout = StepResult(
        step_id=2,
        step_order=1,
        step_name="登出",
        step_type="http_request",
        status=StepStatus.PASSED,
        target="http://host/api/auth/logout",
        input_data={"body": {"refresh_token": refresh}},
        output_data={"status": "success"},
    )

    update_run_shared_vars(shared, _case_result(StepStatus.PASSED, logout), ctx)

    assert "access_token" not in shared
    assert "refresh_token" not in shared
    assert shared["other_token"] == other


def test_logout_all_removes_all_tokens_for_subject():
    first = _jwt("user-1", "sid-1")
    second = _jwt("user-1", "sid-2")
    unrelated = _jwt("user-2", "sid-3")
    shared = {"first": first, "second": second, "unrelated": unrelated}
    ctx = ExecutionContext()
    ctx.vars = dict(shared)
    logout_all = StepResult(
        step_id=3,
        step_order=2,
        step_name="登出所有会话",
        step_type="http_request",
        status=StepStatus.PASSED,
        target="http://host/api/auth/logout-all",
        input_data={"headers": {"Authorization": f"Bearer {first}"}},
        output_data={"status": "success"},
    )

    update_run_shared_vars(shared, _case_result(StepStatus.PASSED, logout_all), ctx)

    assert "first" not in shared
    assert "second" not in shared
    assert shared["unrelated"] == unrelated
