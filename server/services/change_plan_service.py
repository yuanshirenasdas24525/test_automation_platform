"""变更调整：规划(plan_preview) 与 应用(plan_apply) 编排。

plan_preview：变更文本 + 接口契约/文本 + 现有用例 → AI 产出用例级调整大纲(ops)，
              落一条 AiRun 持久化 ops + 上下文，返回 plan_id + ops。
plan_apply：见 Task 4。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from database.models import CASE_TYPE_API, Module, TestCase
from database.models.ai_run import AiRun, AI_RUN_STATUS_SUCCESS
from server.services.api_case_contract import contract_prompt, contract_hash
from server.services.doc_ingest import IngestResult

AI_FEATURE_CHANGE_PLAN = "change_plan"
_VALID_ACTIONS = {"add", "modify", "delete"}


def _existing_interface_cases(db, module_id: int) -> list[TestCase]:
    return (
        db.session.query(TestCase)
        .filter(TestCase.module_id == module_id, TestCase.case_type == CASE_TYPE_API)
        .order_by(TestCase.sort_order.asc(), TestCase.id.asc())
        .all()
    )


def _normalize_ops(raw: dict[str, Any], existing_ids: set[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (raw or {}).get("ops") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        if action not in _VALID_ACTIONS:
            continue
        title = str(item.get("title") or "").strip()[:200]
        if not title:
            continue
        target = item.get("target_case_id")
        if action in {"modify", "delete"}:
            if not isinstance(target, int) or target not in existing_ids:
                continue
        else:
            target = None
        ep = item.get("endpoint") if isinstance(item.get("endpoint"), dict) else None
        endpoint = None
        if ep:
            endpoint = {
                "method": str(ep.get("method") or "").strip().upper(),
                "path": str(ep.get("path") or "").strip(),
            }
        out.append({
            "id": len(out),
            "action": action,
            "target_case_id": target,
            "title": title,
            "endpoint": endpoint,
            "reason": str(item.get("reason") or "").strip()[:500],
        })
    return out


def _parse_ai_json(raw: str) -> dict[str, Any] | None:
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    s, e = raw.find("{"), raw.rfind("}")
    if 0 <= s < e:
        try:
            obj = json.loads(raw[s:e + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def plan_preview(
    db,
    module: Module,
    model_name: str,
    change_text: str,
    ingest: IngestResult,
    operator: str | None = None,
) -> dict[str, Any]:
    """跑 AI 产出调整大纲，落 AiRun，返回 {plan_id, ops, warnings}。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options
    from server.api.functional_cases import _resolve_model

    cases = _existing_interface_cases(db, module.id)
    existing_ids = {c.id for c in cases}
    existing_block = "\n".join(f"- #{c.id} {c.name}" for c in cases) or "（本模块暂无接口用例）"
    contract_block = contract_prompt(ingest.contract) if (ingest.contract.get("operations") or []) else "（无结构化接口）"
    doc_text = "\n\n".join(ingest.text_blocks)[:20000] or "（无补充文本）"

    cfg = _resolve_model(db, model_name, module.project_id)
    call_options = model_task_options(cfg, "api_outline")
    template = _load_prompt("change_plan")
    prompt = _render_prompt(template, {
        "MODULE_NAME": module.name,
        "CHANGE_TEXT": change_text.strip(),
        "CONTRACT_BLOCK": contract_block,
        "DOC_TEXT": doc_text,
        "EXISTING_CASES": existing_block,
    })
    raw, tin, tout = chat_markdown(
        prompt, cfg,
        timeout=call_options["timeout"],
        system_prompt="你只输出一个合法 JSON 对象，含 ops 数组。不要输出任何其它文字。",
        enable_thinking=call_options["enable_thinking"],
        json_mode=call_options["json_mode"],
        max_tokens=call_options["max_tokens"],
        temperature=call_options["temperature"],
        reasoning_effort=call_options.get("reasoning_effort"),
    )
    obj = _parse_ai_json(raw or "")
    if obj is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="调整大纲解析失败，请重试或更换模型")
    ops = _normalize_ops(obj, existing_ids)

    run = AiRun(
        feature=AI_FEATURE_CHANGE_PLAN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=module.project_id,
        provider=cfg.provider,
        model=cfg.model,
        tokens_in=tin,
        tokens_out=tout,
        input_payload={
            "module_id": module.id,
            "mode": "interface",
            "model_name": model_name,
            "change_text": change_text,
            "warnings": ingest.warnings,
        },
        output_payload={
            "ops": ops,
            "contract": ingest.contract,
            "contract_hash": contract_hash(ingest.contract),
            "doc_text": doc_text,
        },
        operator=operator,
        started_at=datetime.now(),
        ended_at=datetime.now(),
    )
    db.session.add(run)
    db.session.flush()
    return {"plan_id": run.id, "ops": ops, "warnings": ingest.warnings}
