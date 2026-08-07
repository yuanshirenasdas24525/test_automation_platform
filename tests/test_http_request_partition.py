"""HTTP 请求参数位置回归：query/json/form 不能再混用。"""
from __future__ import annotations

from typing import Any

import pytest

from runners.context.execution_context import ExecutionContext
from runners.steps.http_request import HttpRequestStepRunner


class _Response:
    status_code = 200
    text = "ok"

    def json(self) -> dict[str, Any]:
        return {"status": "success"}


class _Session:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def request(self, **kwargs: Any) -> _Response:
        self.kwargs = kwargs
        return _Response()


def test_get_uses_query_params_without_json_body() -> None:
    session = _Session()
    runner = HttpRequestStepRunner(session=session)

    runner._send(
        "GET",
        "http://example.test/items",
        {},
        {"page": 2},
        None,
        None,
        "application/json",
        5,
    )

    assert session.kwargs["params"] == {"page": 2}
    assert session.kwargs["json"] is None


def test_form_uses_data_and_keeps_query_separate() -> None:
    session = _Session()
    runner = HttpRequestStepRunner(session=session)

    runner._send(
        "POST",
        "http://example.test/login",
        {},
        {"source": "api"},
        {"username": "tester", "password": "secret"},
        None,
        "application/x-www-form-urlencoded",
        5,
    )

    assert session.kwargs["params"] == {"source": "api"}
    assert session.kwargs["data"] == {"username": "tester", "password": "secret"}
    assert "json" not in session.kwargs


def test_type_assertion_checks_json_value_type() -> None:
    runner = HttpRequestStepRunner(session=_Session())
    ctx = ExecutionContext()
    runner._apply_assertions(
        [{"type": "type", "target": "$.expires_in", "expected": "number"}],
        response_body={"expires_in": 3600},
        status_code=200,
        ctx=ctx,
    )

    with pytest.raises(AssertionError, match="不是 number"):
        runner._apply_assertions(
            [{"type": "type", "target": "$.expires_in", "expected": "number"}],
            response_body={"expires_in": "3600"},
            status_code=200,
            ctx=ExecutionContext(),
        )
