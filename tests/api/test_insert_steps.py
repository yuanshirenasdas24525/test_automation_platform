"""fix.insert_steps 白名单单测。

背景：AI 修复原本只能改现有步骤的字段，遇到"变量全局无人产出"（典型：用例要
admin_token 却没人登录）就无能为力——实测 103 条失败属于这一类。pre_hook 能补，
但它是用户看不见的隐藏逻辑，已被禁用。insert_steps 是折中：落成真正的 TestStep，
用户在编辑器里看得见、可改可删。

既然放开了"AI 可以往用例里加步骤"，白名单就是唯一的闸门，必须逐条锁死。

跑法：pytest tests/api/test_insert_steps.py -v
"""
from __future__ import annotations

import pytest

from server.services.ai_fix_service import _sanitize_insert_steps


def _login_step(**over):
    cfg = {
        "method": "POST",
        "path": "/api/auth/login",
        "params": {"username": "${user_admin}", "password": "${password_admin}"},
        "extract_data": {"admin_token": "$.data.access_token"},
    }
    cfg.update(over.pop("config", {}))
    return {"step_name": over.pop("step_name", "管理员登录"), "config": cfg, **over}


def test_valid_login_step_is_accepted():
    steps, produced = _sanitize_insert_steps([_login_step()])
    assert len(steps) == 1
    assert produced == ["admin_token"]
    assert steps[0]["step_type"] == "http_request"
    assert steps[0]["config"]["extract_data"] == {"admin_token": "$.data.access_token"}


@pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH"])
def test_mutating_methods_are_rejected(method):
    """补前置不该改动或删除数据 —— 非 GET/POST 一律拒绝。"""
    assert _sanitize_insert_steps([_login_step(config={"method": method})]) == ([], [])


def test_plaintext_credential_is_rejected():
    """密码写明文字面量 → 整体拒绝，防止模型把猜的凭据硬编码进用例库。"""
    bad = _login_step(config={
        "params": {"username": "${user_admin}", "password": "Test@123"},
    })
    assert _sanitize_insert_steps([bad]) == ([], [])


def test_credential_referencing_variable_is_allowed():
    ok = _login_step(config={
        "params": {"username": "${user_admin}", "password": "${password_admin}"},
    })
    steps, produced = _sanitize_insert_steps([ok])
    assert steps and produced == ["admin_token"]


def test_step_without_extract_is_rejected():
    """不产出变量的前置步骤没有意义 —— 拒绝。"""
    assert _sanitize_insert_steps([_login_step(config={"extract_data": {}})]) == ([], [])


def test_missing_path_is_rejected():
    assert _sanitize_insert_steps([_login_step(config={"path": ""})]) == ([], [])


def test_more_than_three_steps_are_truncated():
    """最多补 3 步，防止模型塞一整条用例进来。"""
    steps, _ = _sanitize_insert_steps([_login_step() for _ in range(6)])
    assert len(steps) == 3


@pytest.mark.parametrize("raw", [None, [], {}, "login", [123]])
def test_malformed_input_is_rejected(raw):
    assert _sanitize_insert_steps(raw) == ([], [])


def test_one_bad_step_rejects_the_whole_batch():
    """混入一条违规的就整批丢弃 —— 宁可不修，不冒险半修。"""
    assert _sanitize_insert_steps([
        _login_step(),
        _login_step(config={"method": "DELETE"}),
    ]) == ([], [])
