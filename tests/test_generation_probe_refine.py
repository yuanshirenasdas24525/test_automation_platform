"""生成执行接地精修闭环——纯逻辑单测（样本格式化 + 精修合并）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.generation_probe_refine import format_sample, apply_refinements


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
