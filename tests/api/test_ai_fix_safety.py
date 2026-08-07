"""AI 报告修复的验证安全规则。"""
from __future__ import annotations

from types import SimpleNamespace

from server.services import ai_fix_service


def test_red_to_red_candidate_is_rolled_back(monkeypatch) -> None:
    reports = {
        1: {10: [SimpleNamespace(status="failed")]},
        2: {10: [SimpleNamespace(status="failed")]},
    }
    monkeypatch.setattr(
        ai_fix_service,
        "_load_report_rows",
        lambda _session, report_id: reports[report_id],
    )
    calls: list[dict] = []

    def _rollback(_session, **kwargs):
        calls.append(kwargs)
        return {"rolled_back": len(kwargs["event_ids"]), "conflicts": []}

    monkeypatch.setattr(ai_fix_service, "rollback_test_case_events", _rollback)

    result = ai_fix_service.compare_and_rollback(
        object(),
        orig_report_id=1,
        verify_report_id=2,
        batch_id=7,
        applied=[{"case_id": 10, "name": "失败用例", "event_id": 99}],
    )

    assert result["still_red"][0]["case_id"] == 10
    assert result["rolled_back_count"] == 1
    assert calls[0]["event_ids"] == [99]


def test_timeout_rollback_reverts_every_applied_event(monkeypatch) -> None:
    calls: list[dict] = []

    def _rollback(_session, **kwargs):
        calls.append(kwargs)
        return {"rolled_back": 2, "conflicts": []}

    monkeypatch.setattr(ai_fix_service, "rollback_test_case_events", _rollback)
    result = ai_fix_service.rollback_applied_fixes(
        object(),
        batch_id=8,
        applied=[
            {"case_id": 1, "event_id": 101},
            {"case_id": 2, "event_id": 102},
        ],
        reason="验证超时",
    )
    assert result["rolled_back"] == 2
    assert calls[0]["event_ids"] == [101, 102]
