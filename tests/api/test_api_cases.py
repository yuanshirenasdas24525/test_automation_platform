from __future__ import annotations

import pytest
from fastapi import HTTPException

from database.models import TestCase as ORMTestCase
from database.schemas import test_case_create
from server.api.api_cases import validate_automation_case_type
from server.api.cases import _serialize_case


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
