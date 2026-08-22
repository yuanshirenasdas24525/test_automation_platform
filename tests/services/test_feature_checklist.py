"""feature_checklist 纯函数单测。

跑：../../../venv/bin/python -m pytest tests/services/test_feature_checklist.py -q
"""
from server.services.feature_checklist import (
    build_checklist,
    checklist_summary,
)


def test_coverage_thresholds_none_thin_covered():
    existing = ["c1", "c2", "c3", "c4", "c5", "c6"]
    aspects = [
        {"aspect": "能否登录", "what_to_test": "登进去", "covered_cases": ["c1", "c2", "c3"]},
        {"aspect": "失败锁定", "what_to_test": "锁定", "covered_cases": ["c4", "c5"]},
        {"aspect": "界面反馈", "what_to_test": "loading", "covered_cases": []},
    ]
    res = build_checklist(aspects, existing)
    by = {a["aspect"]: a for a in res}
    assert by["能否登录"]["coverage"] == "covered" and by["能否登录"]["covered_count"] == 3
    assert by["失败锁定"]["coverage"] == "thin" and by["失败锁定"]["covered_count"] == 2
    assert by["界面反馈"]["coverage"] == "none" and by["界面反馈"]["covered_count"] == 0


def test_drops_hallucinated_case_names():
    # AI 编了不存在的用例名 → 不计入覆盖
    aspects = [{"aspect": "A", "what_to_test": "x", "covered_cases": ["真实用例", "AI编的假名"]}]
    res = build_checklist(aspects, ["真实用例"])
    assert res[0]["covered_cases"] == ["真实用例"]
    assert res[0]["covered_count"] == 1


def test_one_case_counted_once_across_aspects():
    # 同一条被两个要点认领，只算给第一个，避免覆盖数灌水
    aspects = [
        {"aspect": "A", "what_to_test": "x", "covered_cases": ["c1"]},
        {"aspect": "B", "what_to_test": "y", "covered_cases": ["c1"]},
    ]
    res = build_checklist(aspects, ["c1"])
    assert res[0]["covered_count"] == 1
    assert res[1]["covered_count"] == 0


def test_ignores_malformed_and_empty_aspect():
    aspects = ["notadict", {"what_to_test": "无aspect名"}, {"aspect": "", "covered_cases": []}]
    assert build_checklist(aspects, []) == []
    assert build_checklist("notalist", []) == []


def test_summary_counts_gaps():
    aspects = build_checklist(
        [
            {"aspect": "A", "what_to_test": "", "covered_cases": ["a", "b", "c"]},
            {"aspect": "B", "what_to_test": "", "covered_cases": ["d"]},
            {"aspect": "C", "what_to_test": "", "covered_cases": []},
        ],
        ["a", "b", "c", "d"],
    )
    s = checklist_summary(aspects)
    assert s == {"total": 3, "covered": 1, "gaps": 2}
