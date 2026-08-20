from __future__ import annotations

from runners.protocol import StepResult, StepStatus
from utils import allure_step_reporter


def test_script_step_attaches_details_and_keeps_csrf_visible(monkeypatch) -> None:
    """脚本详情分区展示；CSRF 保留原值，其它凭据继续脱敏。"""
    texts: dict[str, str] = {}
    payloads: dict[str, object] = {}
    monkeypatch.setattr(
        allure_step_reporter,
        "_attach_text",
        lambda name, content: texts.__setitem__(name, content),
    )
    monkeypatch.setattr(
        allure_step_reporter,
        "_attach_json",
        lambda name, content: payloads.__setitem__(name, content),
    )

    result = StepResult(
        step_id=8,
        step_order=1,
        step_name="提取 TesterHome CSRF Token",
        step_type="script",
        status=StepStatus.PASSED,
        duration_ms=114,
        action="运行项目脚本 extract_testerhome_csrf",
        target="extract_testerhome_csrf",
        input_data={
            "script_name": "extract_testerhome_csrf",
            "project_id": 3,
            "script_config": {},
            "input": {
                "html": '<meta name="csrf-token" content="live-csrf">',
                "password": "live-password",
            },
        },
        output_data={
            "variables": {"csrf_token": "live-csrf"},
            "logs": ["提取成功", "Authorization: Bearer live-token"],
        },
        extracted={"csrf_token": "live-csrf"},
    )

    allure_step_reporter.attach_step_details(result)

    assert set(payloads) == {"脚本执行配置", "脚本输入参数", "脚本输出结果", "写回变量"}
    assert payloads["脚本输入参数"] == {
        "html": '<meta name="csrf-token" content="live-csrf">',
        "password": "***",
    }
    assert payloads["脚本输出结果"] == {
        "variables": {"csrf_token": "live-csrf"},
        "logs": ["提取成功", "Authorization: Bearer ***"],
    }
    assert payloads["写回变量"] == {"csrf_token": "live-csrf"}
    assert "name    : 提取 TesterHome CSRF Token" in texts["step_summary"]
    assert "[2] Authorization: Bearer ***" in texts["脚本执行日志"]
