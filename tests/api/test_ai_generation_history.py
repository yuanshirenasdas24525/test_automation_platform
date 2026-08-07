from __future__ import annotations

from datetime import datetime

from database.models import AI_FEATURE_API_CASE_GEN, AI_RUN_STATUS_SUCCESS, AiRun
from server.api.functional_cases import _generation_history_draft, _generation_history_summary


def test_generation_history_compatibly_reads_outline_only_run() -> None:
    """旧的大纲主记录没有 draft，也必须能作为一条可查看历史返回。"""
    run = AiRun(
        id=21,
        feature=AI_FEATURE_API_CASE_GEN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=3,
        input_payload={
            "module_id": 8,
            "mode": "interface",
            "stage": "outline",
            "coverage": "full",
        },
        output_payload={
            "digest": "登录接口测试",
            "points": [
                {"title": "正常登录", "category": "正常"},
                {"title": "密码错误", "category": "参数校验"},
            ],
            "api_contract": {"hash": "contract-1", "operations": []},
        },
        model="deepseek-v4-pro",
        created_at=datetime(2026, 8, 6, 12, 0, 0),
    )

    draft = _generation_history_draft(run)
    summary = _generation_history_summary(run)

    assert draft["generationRunId"] == 21
    assert draft["stage"] == "outline"
    assert draft["pickedPoints"] == [0, 1]
    assert summary["module_id"] == 8
    assert summary["point_count"] == 2
    assert summary["case_count"] == 0


def test_generation_history_prefers_persisted_review_snapshot() -> None:
    """有审阅快照时，列表统计必须使用详细用例和实际写入状态。"""
    run = AiRun(
        id=22,
        feature=AI_FEATURE_API_CASE_GEN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=3,
        input_payload={"module_id": 8, "mode": "interface", "stage": "outline"},
        output_payload={
            "draft": {
                "version": 1,
                "mode": "interface",
                "stage": "cases",
                "digest": "登录接口测试",
                "points": [{"title": "正常登录", "category": "正常"}],
                "cases": [{"name": "登录成功"}, {"name": "密码错误"}],
                "writtenNames": ["登录成功"],
            },
        },
        created_at=datetime(2026, 8, 6, 12, 5, 0),
    )

    draft = _generation_history_draft(run)
    summary = _generation_history_summary(run)

    assert draft["generationRunId"] == 22
    assert summary["stage"] == "cases"
    assert summary["case_count"] == 2
    assert summary["written_count"] == 1
