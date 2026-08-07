"""生成执行接地精修闭环——纯逻辑单测（样本格式化 + 精修合并）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.generation_probe_refine import (
    _cross_case_probe_refs,
    format_sample,
    apply_refinements,
    validate_isolation,
)


def test_format_sample_extracts_request_and_response():
    """草稿 + 真跑记录 → api_probe_refine 的 SAMPLES 元素。"""
    draft = {"name": "登录成功", "steps": [
        {"config": {"method": "POST", "path": "/api/auth/login",
                    "params": {"username": "u", "password": "p"}}}]}
    step_record = {
        "input_data": {"method": "POST", "url": "/api/auth/login",
                       "body": {"username": "u", "password": "p"}},
        "output_data": {"status": "success", "data": {"access_token": "T"}},
        "status_code": 200,
    }
    s = format_sample(draft, step_record)
    assert s["name"] == "登录成功"
    assert s["status"] == 200
    assert s["response"] == {"status": "success", "data": {"access_token": "T"}}
    assert s["request"]["method"] == "POST"


def test_format_sample_tolerates_empty_record():
    """真跑失败（无记录）→ 样本响应为 None，不报错。"""
    s = format_sample({"name": "x"}, {"input_data": None, "output_data": None, "status_code": None})
    assert s["name"] == "x"
    assert s["response"] is None
    assert s["status"] is None


def test_apply_refinements_overwrites_extract_assertion():
    """按 name 把精修后的 extract/assertion 覆盖回草稿第一个 http step。"""
    drafts = [{"name": "登录成功", "steps": [
        {"config": {"method": "POST", "path": "/api/auth/login",
                    "extract_data": {"token": "$.wrong.path"},
                    "assertion": {"$.code": 0}}}]}]
    refinements = [{"name": "登录成功",
                    "extract": {"token": "$.data.access_token"},
                    "assertion": {"status_code": 200}}]
    out = apply_refinements(drafts, refinements)
    cfg = out[0]["steps"][0]["config"]
    assert cfg["extract_data"] == {"token": "$.data.access_token"}
    assert cfg["assertion"] == {"status_code": 200}


def test_apply_refinements_skips_unmatched_name():
    """精修结果里没有的用例，草稿保持不变。"""
    drafts = [{"name": "A", "steps": [{"config": {"extract_data": {"k": "$.old"}}}]}]
    out = apply_refinements(drafts, [{"name": "B", "extract": {"k": "$.new"}}])
    assert out[0]["steps"][0]["config"]["extract_data"] == {"k": "$.old"}


# ---------- 支柱 3：隔离校验 ----------

def test_isolation_flags_destructive_without_ephemeral():
    """直接改密码、没建一次性账号 → 违规（动了共享账号）。"""
    draft = {"name": "改密码", "steps": [
        {"config": {"method": "POST", "path": "/api/auth/login",
                    "params": {"username": "${user_admin}"}}},
        {"config": {"method": "PUT", "path": "/api/auth/password",
                    "params": {"old_password": "x", "new_password": "y"}}}]}
    assert validate_isolation(draft)  # 非空 = 违规


def test_isolation_passes_with_ephemeral_account():
    """先 function:unique 建一次性账号再改密码 → 合规。"""
    draft = {"name": "改密码", "steps": [
        {"config": {"method": "POST", "path": "/api/users",
                    "params": {"username": "function:unique(AUTO_TEST_x)",
                               "password": "p", "role_codes": ["test"]}}},
        {"config": {"method": "POST", "path": "/api/auth/login",
                    "params": {"username": "${test_username}"}}},
        {"config": {"method": "PUT", "path": "/api/auth/password", "params": {}}}]}
    assert validate_isolation(draft) == []


def test_isolation_ignores_non_destructive():
    """只登录、不做破坏性操作 → 不检查（登录共享账号本身没问题）。"""
    draft = {"name": "登录成功", "steps": [
        {"config": {"method": "POST", "path": "/api/auth/login", "params": {}}}]}
    assert validate_isolation(draft) == []


def test_isolation_ignores_human_readable_text_steps():
    """重新校验收到评审展示用的字符串 steps 时不能 500。"""
    draft = {
        "name": "登录成功",
        "steps": ["发送登录请求", "校验响应并提取 token"],
    }

    assert validate_isolation(draft) == []


def test_isolation_ignores_negative_validation_of_destructive_path():
    """改密/登出的 4xx 参数校验用例不会进入成功变更分支，不要求建一次性账号。"""
    draft = {
        "name": "缺少 new_password 修改密码失败",
        "compiled_case": {
            "steps": [
                {
                    "config": {"method": "PUT", "path": "/api/auth/password", "json": {"old_password": "x"}},
                    "assertion": [{"target": "status_code", "type": "equals", "expected": 422}],
                }
            ]
        },
    }
    assert validate_isolation(draft) == []


def test_probe_detects_cross_case_variable_that_cannot_be_seeded_alone():
    """前序用例产出的 token 在单条 probe 中没有真实值，应跳过而非制造假 401。"""
    compiled = {
        "generation_metadata": {"carried_variables": ["access_token"]},
        "pre_hook": [],
        "steps": [
            {
                "config": {
                    "method": "GET",
                    "path": "/api/auth/me",
                    "headers": {"Authorization": "Bearer ${access_token}"},
                },
                "extract": None,
            }
        ],
    }

    assert _cross_case_probe_refs(compiled) == {"access_token"}
