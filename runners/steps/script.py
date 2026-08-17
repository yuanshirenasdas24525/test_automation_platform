"""脚本库步骤 Runner。

项目特有逻辑只按脚本名称解析，平台 Runner 不 import 脚本代码。真正执行发生在
``script_runner/worker.py`` 独立进程，输入输出只允许 JSON 数据。
"""
from __future__ import annotations

import json
from typing import Any

from runners.context.execution_context import ExecutionContext
from runners.protocol import BaseStepRunner, StepResult
from utils.script_runtime import run_named_script
from utils.value_resolver import resolve_value_deep


class ScriptStepRunner(BaseStepRunner):
    """执行脚本库中的 workflow 脚本。"""

    step_types = ("script",)

    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        config = dict(step.get("config") or {})
        script_name = str(config.get("script_name") or config.get("name") or "").strip()
        if not script_name:
            raise ValueError("script 步骤缺少 config.script_name")

        script_input = _parse_json_field(config.get("input"), field="input")
        script_config = _parse_json_field(config.get("script_config"), field="script_config")
        script_input = resolve_value_deep(script_input, ctx)
        script_config = resolve_value_deep(script_config, ctx)
        project_id = _as_project_id(ctx.get_var("_project_id"))

        found, output = run_named_script(
            script_name,
            kind="workflow",
            project_id=project_id,
            body=script_input,
            config=script_config,
            vars=ctx.vars,
            ctx=ctx,
            timeout=step.get("timeout") or 30,
        )
        if not found:
            raise ValueError(f"未找到启用的项目逻辑脚本：{script_name}")

        result.action = f"运行项目脚本 {script_name}"
        result.target = script_name
        result.input_data = script_input
        result.output_data = output

        if isinstance(output, dict):
            if output.get("ok") is False:
                raise AssertionError(str(output.get("error") or output.get("message") or "项目脚本返回失败"))
            if bool(config.get("export_variables", True)):
                variables = output.get("variables")
                if variables is not None and not isinstance(variables, dict):
                    raise TypeError("项目脚本返回的 variables 必须是 JSON 对象")
                for key, value in (variables or {}).items():
                    ctx.set_var(str(key), value)
                    result.extracted[str(key)] = value
            logs = output.get("logs")
            if isinstance(logs, list):
                for message in logs:
                    ctx.log(f"[script:{script_name}] {message}")

        save_as = str(config.get("save_result_as") or "").strip()
        if save_as:
            value = output.get("result") if isinstance(output, dict) and "result" in output else output
            ctx.set_var(save_as, value)
            result.extracted[save_as] = value


def _parse_json_field(raw: Any, *, field: str) -> Any:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"script config.{field} 不是有效 JSON：{exc.msg}") from exc


def _as_project_id(raw: Any) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
