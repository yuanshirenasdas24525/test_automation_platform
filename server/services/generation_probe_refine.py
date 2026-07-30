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


_DESTRUCTIVE_HINTS = ("change-password", "reset-password", "/password", "logout", "signout")


def _step_is_destructive(cfg: dict) -> bool:
    """步骤是否破坏性操作（改密码/登出/删除账号）。"""
    path = str(cfg.get("path") or "").lower()
    method = str(cfg.get("method") or "").upper()
    if any(h in path for h in _DESTRUCTIVE_HINTS):
        return True
    return method == "DELETE" and "user" in path


def validate_isolation(draft: dict) -> list[str]:
    """草稿对共享账号做破坏性操作却没先建一次性账号 → 返回违规说明；否则空列表。"""
    import json

    steps = draft.get("steps") or []
    if not any(_step_is_destructive(s.get("config") or {}) for s in steps):
        return []
    blob = json.dumps([s.get("config") or {} for s in steps], ensure_ascii=False)
    if "function:unique" in blob:
        return []
    return [
        "破坏性操作（改密码/删除/登出）未用一次性账号："
        "应先 function:unique 建号→登录→对它操作，不能直接用共享账号"
    ]


def probe_drafts(drafts: list[dict], project_id: int) -> list[dict]:
    """真跑每条草稿（不落库），返回 SAMPLES 列表。

    隔离保护：对"破坏性操作打共享账号"的违规草稿**跳过真跑**（给空样本），
    避免 probe 真跑污染共享账号；执行异常的草稿也给空样本，不中断。
    """
    from runners.case_executor import CaseExecutor
    from runners.context.execution_context import ExecutionContext

    samples: list[dict] = []
    for draft in drafts:
        if validate_isolation(draft):
            samples.append(format_sample(draft, {"input_data": None, "output_data": None, "status_code": None}))
            continue
        try:
            ctx = ExecutionContext()
            ctx.set_var("_project_id", project_id)
            result = CaseExecutor().run(dict(draft), ctx)
            first = next(
                (s for s in (result.steps or []) if getattr(s, "step_type", "") == "http_request"),
                None,
            )
            rec = {
                "input_data": getattr(first, "input_data", None) if first else None,
                "output_data": getattr(first, "output_data", None) if first else None,
                "status_code": (getattr(ctx, "records", {}) or {}).get("status_code"),
            }
        except Exception:  # noqa: BLE001
            rec = {"input_data": None, "output_data": None, "status_code": None}
        samples.append(format_sample(draft, rec))
    return samples


def refine_from_samples(samples: list[dict], cfg: Any) -> list[dict]:
    """把样本喂给 api_probe_refine.md，返回精修后的 [{name, extract, assertion, note}]。"""
    import json

    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown

    template = _load_prompt("api_probe_refine")
    prompt = _render_prompt(template, {"SAMPLES": json.dumps(samples, ensure_ascii=False)})
    raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=180)
    from server.api.functional_cases import _extract_json_list

    return _extract_json_list(raw) or []


def probe_and_refine(drafts: list[dict], project_id: int, cfg: Any) -> list[dict]:
    """闭环编排：真跑收集样本 → 精修 → 合并回草稿。任何一步失败都回退原草稿（不阻断生成）。"""
    try:
        samples = probe_drafts(drafts, project_id)
        refinements = refine_from_samples(samples, cfg)
        return apply_refinements(drafts, refinements)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("[gen-probe] 精修闭环失败，返回原草稿", exc_info=True)
        return drafts
