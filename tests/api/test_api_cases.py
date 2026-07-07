from __future__ import annotations

import pytest
from fastapi import HTTPException

from server.api.api_cases import validate_automation_case_type


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
