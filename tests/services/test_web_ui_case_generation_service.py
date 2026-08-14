"""Web UI AI 计划编译器的事实门禁测试。"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from database.schemas.ui_automation_case import WebUiCaseGenerationRequest
from server.services.web_ui_case_generation_service import (
    _module_page_score,
    _safe_url,
    compile_ai_case,
    normalize_auto_source_selection,
    validate_draft_step_edit,
    validate_draft_steps,
)


def test_prompt_url_removes_credentials_fragment_and_sensitive_query_values():
    value = _safe_url(
        "https://alice:secret@example.test/orders?status=open&access_token=abc&code=123#detail",
        redact_query=True,
    )
    assert value == "https://example.test/orders?status=open&access_token=%24%7Bredacted%7D&code=%24%7Bredacted%7D"


def test_auto_request_needs_no_manual_cases_or_pages():
    payload = WebUiCaseGenerationRequest(project_id=1, target_module_id=7, model_name="default")
    assert payload.source_mode == "auto"
    assert payload.functional_case_ids == []
    assert payload.page_keys == []
    assert payload.executable_only is True
    assert "count" not in payload.model_dump()

    with pytest.raises(ValidationError):
        WebUiCaseGenerationRequest(
            project_id=1,
            target_module_id=7,
            model_name="default",
            source_mode="functional_and_elements",
            functional_case_ids=[1],
            page_keys=[],
        )


def test_module_page_score_prefers_exact_business_page_over_global_navigation_match():
    login_page = {
        "page_name": "自动化测试平台",
        "route": "http://127.0.0.1:54351/login",
        "element_samples": ["登录(submit)", "用户名(input)", "密码(password)"],
    }
    project_page = {
        "page_name": "项目管理",
        "route": "http://127.0.0.1:54351/projects",
        "element_samples": ["退出登录(button)", "新建项目(button)"],
    }
    assert _module_page_score("登录", login_page) > _module_page_score("登录", project_page)


def test_auto_selection_rejects_unknown_and_unsuitable_ai_choices():
    catalog = {
        "functional_candidates": [
            {"functional_case_id": 1, "automation_score": 12, "matched_page_keys": ["page-a"]},
            {"functional_case_id": 2, "automation_score": -8, "matched_page_keys": ["page-b"]},
        ],
        "page_candidates": [{"page_key": "page-a"}, {"page_key": "page-b"}],
        "fallback_functional_case_ids": [1],
        "fallback_page_keys": ["page-a"],
        "budget": {
            "functional_total": 200,
            "functional_included": 80,
            "functional_truncated": True,
        },
    }
    selected = normalize_auto_source_selection(
        {"functional_case_ids": [2, 999], "page_keys": ["missing"]},
        catalog,
    )
    assert selected["functional_case_ids"] == [1]
    assert selected["page_keys"] == ["page-a"]
    assert any("裁剪" in item for item in selected["warnings"])


def _locator(strategy: str, value: str, *, primary: bool = False, score: int = 80):
    return SimpleNamespace(
        strategy=strategy,
        locator=value,
        is_primary=primary,
        is_unique=True,
        last_verified_at=datetime.now(),
        score=score,
    )


def _element(element_id: int, name: str, *, element_type: str = "button"):
    return SimpleNamespace(
        id=element_id,
        semantic_name=name,
        element_type=element_type,
        page_key="page-login",
        attributes={"text": name, "tag": "button"},
        locators=[
            _locator("xpath", "//button[1]", score=30),
            _locator("css", f"[data-testid='element-{element_id}']", primary=True, score=95),
        ],
    )


def test_compile_uses_element_library_locator_and_parameterizes_input():
    username = _element(1, "用户名", element_type="input")
    login = _element(2, "登录")
    compiled = compile_ai_case(
        {
            "title": "登录成功",
            "functional_case_id": 7,
            "variables": {"password": "should-not-be-saved"},
            "steps": [
                {"action": "input", "element_id": 1, "value": "admin"},
                # AI 即使额外输出 locator 也不能覆盖元素事实。
                {"action": "click", "element_id": 2, "locator": "#hallucinated"},
                {"action": "assert_visible", "element_id": 2},
            ],
        },
        element_map={1: username, 2: login},
        snapshot_map={},
        include_structure_assertions=True,
        include_visual_assertions=False,
        visual_threshold=0.02,
    )
    assert compiled is not None
    assert compiled["variables"] == {"password": "", "username": "admin"}
    assert compiled["steps"][0]["config"]["value"] == "${username}"
    assert compiled["steps"][1]["config"] == {
        "by": "css",
        "locator": "[data-testid='element-2']",
        "element_id": 2,
    }
    assert compiled["evidence"]["element_ids"] == [1, 2]


def test_compile_marks_unknown_element_and_captcha_for_manual_intervention():
    captcha = _element(3, "滑块验证码")
    compiled = compile_ai_case(
        {
            "title": "安全校验",
            "steps": [
                {"action": "click", "element_id": 999},
                {"action": "click", "element_id": 3},
            ],
        },
        element_map={3: captcha},
        snapshot_map={},
        include_structure_assertions=True,
        include_visual_assertions=False,
        visual_threshold=0.02,
    )
    assert compiled is not None
    assert len(compiled["manual_reasons"]) == 2
    assert all(step["skip"] for step in compiled["steps"])
    assert all(step["config"]["manual_intervention"] for step in compiled["steps"])


def test_compile_does_not_persist_sensitive_input_default():
    token = _element(4, "访问令牌", element_type="input")
    compiled = compile_ai_case(
        {
            "title": "令牌录入",
            "steps": [
                {"action": "input", "element_id": 4, "value": "real-secret-value"},
                {"action": "assert_visible", "element_id": 4},
            ],
        },
        element_map={4: token},
        snapshot_map={},
        include_structure_assertions=True,
        include_visual_assertions=False,
        visual_threshold=0.02,
    )
    assert compiled is not None
    assert list(compiled["variables"].values()) == [""]


def test_compile_visual_assertion_requires_known_snapshot_and_feature_switch():
    snapshot = SimpleNamespace(
        id=10,
        page_key="page-home",
        page_name="工作台",
        screenshot_uri="data/ui_recordings/session_1/screenshots/home.png",
    )
    raw = {"title": "工作台视觉", "steps": [{"action": "visual_assert", "snapshot_id": 10}]}
    disabled = compile_ai_case(
        raw,
        element_map={},
        snapshot_map={10: snapshot},
        include_structure_assertions=True,
        include_visual_assertions=False,
        visual_threshold=0.03,
    )
    enabled = compile_ai_case(
        raw,
        element_map={},
        snapshot_map={10: snapshot},
        include_structure_assertions=True,
        include_visual_assertions=True,
        visual_threshold=0.03,
    )
    assert disabled is None
    assert enabled is not None
    assert enabled["visual_assertion"] is True
    assert enabled["steps"][0]["config"]["threshold"] == 0.03


def test_compile_tolerates_malformed_ai_scalar_fields():
    login = _element(2, "登录")
    compiled = compile_ai_case(
        {
            "title": "异常字段仍可编译",
            "functional_case_id": {"bad": True},
            "priority": "not-an-int",
            "tags": "not-a-list",
            "steps": [
                {"action": "click", "element_id": 2},
                {"action": "assert_visible", "element_id": 2},
                {"action": "visual_assert", "snapshot_id": {"bad": True}},
            ],
        },
        element_map={2: login},
        snapshot_map={},
        include_structure_assertions=True,
        include_visual_assertions=True,
        visual_threshold=0.02,
    )
    assert compiled is not None
    assert compiled["priority"] == 2
    assert compiled["functional_case_id"] is None
    assert compiled["tags"] == ["ai-web-ui"]


def test_draft_validation_rejects_unsafe_step_and_locator_edit():
    safe = [
        {
            "step_order": 1,
            "step_name": "断言登录按钮可见",
            "step_type": "web_wait",
            "skip": False,
            "config": {
                "by": "css",
                "locator": "[data-testid='login']",
                "element_id": 2,
                "state": "visible",
                "assertion_kind": "visible",
            },
        },
    ]
    assert validate_draft_steps(safe, allow_manual=False) == []

    unsafe = [{**safe[0], "step_type": "web_evaluate", "config": {"script": "return document.cookie"}}]
    errors = validate_draft_steps(unsafe, allow_manual=False)
    assert any("不在 AI Web 安全白名单" in item for item in errors)

    edited = [{**safe[0], "config": {**safe[0]["config"], "locator": "#hallucinated"}}]
    edit_errors = validate_draft_step_edit(safe, edited)
    assert any("未经元素库证明" in item for item in edit_errors)
