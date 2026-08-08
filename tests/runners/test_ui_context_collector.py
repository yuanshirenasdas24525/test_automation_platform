from __future__ import annotations

from runners.context.execution_context import ExecutionContext
from runners.context.ui_context_collector import UIContextCollector
from runners.protocol import StepResult, StepStatus


def test_execution_collector_redacts_sensitive_context_without_changing_result() -> None:
    ctx = ExecutionContext()
    ctx.set_var("_report_id", 77)
    ctx.records["status_code"] = 200
    collector = UIContextCollector(ctx)
    step = {
        "id": 9,
        "step_order": 1,
        "step_name": "登录",
        "step_type": "http_request",
    }
    collector.step_started(step)
    ctx.log("request Authorization: Bearer live-token")
    result = StepResult(
        step_id=9,
        step_order=1,
        step_name="登录",
        step_type="http_request",
        status=StepStatus.PASSED,
        action="POST /login",
        target="https://example.test/login",
        input_data={"username": "admin", "password": "live-password"},
        output_data={"access_token": "live-jwt", "status": "success"},
        duration_ms=25,
    )

    collected = collector.step_finished(step, result)

    assert result.status == StepStatus.PASSED
    network_event = next(
        event for event in collected["events"] if event["event_type"] == "network.exchange"
    )
    assert network_event["payload"]["input"]["password"] == "***"
    assert network_event["payload"]["output"]["access_token"] == "***"
    console_event = next(
        event for event in collected["events"] if event["event_type"] == "runner.log"
    )
    environment_event = next(
        event for event in collected["events"] if event["event_type"] == "environment.snapshot"
    )
    assert environment_event["payload"]["runtime"]["os"]
    assert "live-token" not in console_event["payload"]["message"]
    assert console_event["payload"]["message"].endswith("Bearer ***")
    assert collected["event_from_local"] == 1
    assert collected["event_to_local"] == 5


def test_execution_collector_failure_is_reported_as_limitation() -> None:
    ctx = ExecutionContext()
    collector = UIContextCollector(ctx)
    step = {"id": 1, "step_order": "invalid", "step_name": "坏步骤", "step_type": "web_click"}

    collector.step_started(step)

    assert collector.limitations
    assert "步骤开始上下文降级" in collector.limitations[0]
