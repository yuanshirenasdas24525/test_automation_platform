"""生成执行接地精修闭环：草稿真跑 → 按真实响应精修 extract/assertion。

把现成但从未接上的 ai_gateway/prompts/api_probe_refine.md 接进接口用例生成：
生成草稿后真跑一遍（不落库）→ 收集「请求 + 真实响应」样本 → 让模型按真实响应
重写 extract/assertion → 合并回草稿。纯逻辑（本文件的 format_sample /
apply_refinements）单测覆盖；真跑与 LLM 调用（probe_drafts / refine_from_samples /
probe_and_refine）走集成验证。
"""
from __future__ import annotations

from typing import Any


def format_sample(draft: dict, step_record: dict) -> dict:
    """把一条草稿 + 它真跑第一个 http step 的记录，压成 api_probe_refine 的 SAMPLES 元素。"""
    inp = step_record.get("input_data") or {}
    return {
        "name": draft.get("name") or "",
        "request": {
            "method": inp.get("method"),
            "url": inp.get("url"),
            "body": inp.get("body"),
        },
        "response": step_record.get("output_data"),
        "status": step_record.get("status_code"),
    }


def apply_refinements(drafts: list[dict], refinements: list[dict]) -> list[dict]:
    """按 name 把精修后的 extract/assertion 覆盖回草稿第一个 http step 的 config。"""
    by_name = {r.get("name"): r for r in (refinements or []) if r.get("name")}
    for draft in drafts:
        r = by_name.get(draft.get("name"))
        if not r:
            continue
        steps = draft.get("steps") or []
        if not steps:
            continue
        cfg = steps[0].setdefault("config", {})
        if "extract" in r:
            cfg["extract_data"] = r["extract"] or {}
        if "assertion" in r:
            cfg["assertion"] = r["assertion"] or {}
    return drafts
