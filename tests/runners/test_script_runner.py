from __future__ import annotations

from runners.context.execution_context import ExecutionContext
from runners.dispatcher import StepDispatcher
from runners.protocol import StepStatus
from runners.steps import script as script_step
from runners.steps.script import ScriptStepRunner


def _step(**config):
    return {
        "id": 1,
        "step_order": 0,
        "step_name": "准备项目数据",
        "step_type": "script",
        "config": {
            "script_name": "prepare_account",
            "input": '{"prefix": "${prefix}"}',
            "script_config": "{}",
            "export_variables": True,
            **config,
        },
        "timeout": 5,
    }


def test_script_runner_exports_variables_and_result(monkeypatch) -> None:
    observed = {}

    def fake_run(name, **kwargs):
        observed["name"] = name
        observed.update(kwargs)
        return True, {
            "result": {"id": 9},
            "variables": {"username": "AUTO_1", "password": "Secret#1"},
            "logs": ["账号已创建"],
        }

    monkeypatch.setattr(script_step, "run_named_script", fake_run)
    ctx = ExecutionContext()
    ctx.set_var("_project_id", 7)
    ctx.set_var("prefix", "AUTO")
    result = ScriptStepRunner().execute(_step(save_result_as="account"), ctx)

    assert result.status == StepStatus.PASSED
    assert observed["name"] == "prepare_account"
    assert observed["project_id"] == 7
    assert observed["body"] == {"prefix": "AUTO"}
    assert ctx.get_var("username") == "AUTO_1"
    assert ctx.get_var("account") == {"id": 9}
    assert result.extracted["password"] == "Secret#1"
    assert "账号已创建" in ctx.logs[-1]
    assert result.input_data == {
        "script_name": "prepare_account",
        "project_id": 7,
        "timeout_seconds": 5,
        "export_variables": True,
        "save_result_as": "account",
        "script_config": {},
        "input": {"prefix": "AUTO"},
    }


def test_script_runner_reports_missing_script() -> None:
    runner = ScriptStepRunner()
    result = runner.execute(_step(script_name=""), ExecutionContext())
    assert result.status == StepStatus.ERROR
    assert "script_name" in str(result.error_message)


def test_default_dispatcher_registers_script_runner() -> None:
    assert isinstance(StepDispatcher.default().get("script"), ScriptStepRunner)
