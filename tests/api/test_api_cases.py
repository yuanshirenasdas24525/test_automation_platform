from __future__ import annotations

import pytest
from fastapi import HTTPException

from database.models import TestCase as ORMTestCase
from database.schemas import test_case_create
from server.api.api_cases import validate_automation_case_type
from server.api.cases import _NUM_PREFIX_RE, _serialize_case
from server.services.api_case_admission import validate_ai_interface_admission


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("api", "api"),
        (" web ", "web"),
        ("ANDROID", "android"),
        ("ios", "ios"),
        ("mixed", "mixed"),
    ],
)
def test_validate_automation_case_type_accepts_automation_types(raw: str, expected: str) -> None:
    assert validate_automation_case_type(raw) == expected


@pytest.mark.parametrize("raw", ["functional", "app", "", "unknown"])
def test_validate_automation_case_type_rejects_non_automation_types(raw: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_automation_case_type(raw)
    assert exc_info.value.status_code == 422


def test_case_edit_contract_exposes_and_accepts_pre_hooks() -> None:
    """前置步骤必须能通过详情接口读取，并能在更新请求中显式清空。"""
    hook = [{
        "type": "http_request",
        "step_name": "登录准备",
        "config": {
            "method": "POST",
            "path": "/api/auth/login",
            "extract_data": {"token": "$.data.access_token"},
        },
    }]
    case = ORMTestCase(
        id=10,
        module_id=2,
        name="读取资料",
        case_type="api",
        pre_hook=hook,
    )

    serialized = _serialize_case(case)
    update_payload = test_case_create.TestCaseCreate(module_id=2, name="读取资料", pre_hook=[])

    assert serialized["pre_hook"] == hook
    assert update_payload.pre_hook == []
    assert "pre_hook" in update_payload.model_fields_set


def _ai_payload(*, passed: bool, skip: bool = False) -> dict:
    errors = [] if passed else ["请求方法不在 OpenAPI 契约中"]
    return {
        "source": "ai_interface",
        "skip": skip,
        "generation_metadata": {
            "preflight": {"passed": passed, "errors": errors},
        },
    }


def test_ai_interface_admission_accepts_passed_preflight() -> None:
    validate_ai_interface_admission(_ai_payload(passed=True), steps_provided=True)


def test_ai_interface_admission_rejects_unmarked_failed_preflight() -> None:
    with pytest.raises(ValueError, match="标记为需人工调整"):
        validate_ai_interface_admission(_ai_payload(passed=False), steps_provided=True)


def test_ai_interface_admission_accepts_pending_manual_case_only_when_skipped() -> None:
    payload = _ai_payload(passed=False, skip=True)
    payload["generation_metadata"].update({
        "needs_manual_adjustment": True,
        "manual_adjustment_status": "pending",
        "manual_adjustment_reasons": ["请求方法不在 OpenAPI 契约中"],
    })
    validate_ai_interface_admission(payload, steps_provided=True)

    payload["skip"] = False
    with pytest.raises(ValueError, match="跳过执行"):
        validate_ai_interface_admission(payload, steps_provided=True)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("1. 【正常】登录成功", "【正常】登录成功"),
        ("11. 【鉴权】未带 token", "【鉴权】未带 token"),
        ("0003 【参数校验】缺少密码", "【参数校验】缺少密码"),
    ],
)
def test_case_sequence_prefix_supports_ai_display_numbers(name: str, expected: str) -> None:
    """AI 原始序号从 1 开始，重新编号/去编号必须避免叠加。"""
    assert _NUM_PREFIX_RE.sub("", name).lstrip() == expected


def test_ai_interface_admission_allows_explicit_manual_resolution_after_step_edit() -> None:
    previous = {
        "preflight": {"passed": False, "errors": ["变量缺少来源"]},
        "needs_manual_adjustment": True,
        "manual_adjustment_status": "pending",
        "manual_adjustment_reasons": ["变量缺少来源"],
    }
    resolved = {
        **previous,
        "preflight": {**previous["preflight"], "manual_override": True},
        "needs_manual_adjustment": False,
        "manual_adjustment_status": "resolved",
    }
    validate_ai_interface_admission(
        {
            "source": "ai_interface",
            "skip": False,
            "generation_metadata": resolved,
        },
        previous_metadata=previous,
        steps_provided=True,
    )

    with pytest.raises(ValueError, match="不能通过修改 source"):
        validate_ai_interface_admission(
            {
                "source": "manual",
                "skip": False,
                "generation_metadata": resolved,
            },
            previous_metadata=previous,
            steps_provided=True,
        )
