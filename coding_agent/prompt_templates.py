"""coding_agent 的 prompt 入口 —— 把"对话写需求 / finalize / 编码"三类调用统一封装。

为啥要这一层（而不是直接调 ``ai_gateway.chat_json``）？
- chat_json 的 user_input 是大字符串 / dict —— 调用方手拼 placeholder 很容易出错
- 这一层把"序列化 turns / 选 analysis_mode / 校验输出 schema"集中处理
- 返回值统一形态：``{output: 业务字段, meta: chat_json 全 meta}``，方便上层落 ai_runs

prompt 模板位于 ``ai_gateway/prompts/``：
- ``ai_studio_dialogue_turn.md`` —— 对话单轮（反问 ≤3）
- ``ai_studio_finalize.md``      —— finalize 整段对话产出 markdown + spec
- ``ai_studio_coding.md``        —— 第 5 批的编码 prompt（含 RAG 检索片段）

不在这里：
- 网络 / 重试 / 多 provider 路由 —— 全归 ai_gateway
- 落库 —— 归 tasks/ai_dialogue_task.py
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ai_gateway import chat_json

logger = logging.getLogger(__name__)


# 对话 turn 默认走 quick：每轮反应要快、单轮 cost 要低
DIALOGUE_DEFAULT_MODE = "quick"
# finalize 走 standard：要稳要全
FINALIZE_DEFAULT_MODE = "standard"


# ---------------------------------------------------------------------------
# 对话单轮
# ---------------------------------------------------------------------------
def run_dialogue_turn(
    *,
    project_name: str,
    turns: list[dict],
    project_id: Optional[int] = None,
    analysis_mode: str = DIALOGUE_DEFAULT_MODE,
) -> dict:
    """跑一轮对话推理。

    Args:
        project_name: 项目名（拼进 prompt 让模型知道上下文）
        turns: 历史 turns（含本轮用户最新一句作为最后一条 user 消息）
        project_id: 仅传给 ai_gateway 做审计；不影响 prompt
        analysis_mode: 默认 quick，PM 想要更细可上 standard

    Returns:
        ``{output: {assistant, asked_fields, coverage_delta, done_hint}, meta: {...}}``
        - ``meta`` 是 chat_json 完整返回（provider / model / tokens / cost / prompt_hash...）
        - 出错时抛 ``ProviderError``（不在这里吞）
    """
    if not turns or turns[-1].get("role") != "user":
        raise ValueError("turns 至少要有一条 user 消息（且最后一条必须是 user）")

    raw = chat_json(
        feature="ai_studio_dialogue_turn",
        user_input={
            "project_name": project_name or "(未命名项目)",
            "turns_json": _serialize_turns(turns),
        },
        project_id=project_id,
        analysis_mode=analysis_mode,
    )

    output = raw.get("output") or {}
    output = _normalize_dialogue_output(output)
    return {"output": output, "meta": _strip_meta(raw)}


def _normalize_dialogue_output(output: dict) -> dict:
    """模型偶尔会少字段；这里补齐默认值，避免下游 KeyError。"""
    return {
        "assistant": (output.get("assistant") or "").strip() or "（模型本轮无输出，请重试）",
        "asked_fields": _ensure_str_list(output.get("asked_fields")),
        "coverage_delta": output.get("coverage_delta") or {},
        "done_hint": bool(output.get("done_hint", False)),
    }


# ---------------------------------------------------------------------------
# finalize：整段对话 → markdown + spec
# ---------------------------------------------------------------------------
def finalize_dialogue(
    *,
    project_name: str,
    turns: list[dict],
    project_id: Optional[int] = None,
    analysis_mode: str = FINALIZE_DEFAULT_MODE,
) -> dict:
    """把对话 turns 收口成需求草稿。

    Returns:
        ``{output: {markdown, spec}, meta: {...}}``
        - ``spec`` 字段缺失时回退到空结构（title="未命名需求" 等），不抛
    """
    if not turns:
        raise ValueError("finalize 要至少一条 turn")

    raw = chat_json(
        feature="ai_studio_finalize",
        user_input={
            "project_name": project_name or "(未命名项目)",
            "turns_json": _serialize_turns(turns),
        },
        project_id=project_id,
        analysis_mode=analysis_mode,
    )

    output = raw.get("output") or {}
    output = _normalize_finalize_output(output)
    return {"output": output, "meta": _strip_meta(raw)}


def _normalize_finalize_output(output: dict) -> dict:
    md = (output.get("markdown") or "").strip()
    if not md:
        md = "# 未命名需求\n\n（模型未生成内容）"
    spec_in = output.get("spec") or {}
    spec = {
        "title": (spec_in.get("title") or "未命名需求").strip()[:200],
        "user_story": (spec_in.get("user_story") or "").strip(),
        "acceptance_criteria": _ensure_str_list(spec_in.get("acceptance_criteria")),
        "nfr": _ensure_str_list(spec_in.get("nfr")),
        "module_deps": _ensure_str_list(spec_in.get("module_deps")),
        "priority": _coerce_priority(spec_in.get("priority")),
    }
    return {"markdown": md, "spec": spec}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _serialize_turns(turns: list[dict]) -> str:
    """把 turns 序列化成模型友好的 JSON 字符串。

    剔除 ``ts`` 之类的元信息以省 token —— 模型只关心 role / content / asked_fields。
    """
    slim = []
    for t in turns:
        role = t.get("role")
        if role not in ("user", "assistant"):
            continue
        item: dict = {"role": role, "content": (t.get("content") or "").strip()}
        if t.get("asked_fields"):
            item["asked_fields"] = t["asked_fields"]
        slim.append(item)
    return json.dumps(slim, ensure_ascii=False, indent=2)


def _ensure_str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


def _coerce_priority(v: Any) -> int:
    """把模型乱七八糟的 priority（"P1" / 1 / "高" / 2.0）压成 0-3 int。"""
    if v is None:
        return 2
    if isinstance(v, bool):
        return 2
    if isinstance(v, (int, float)):
        return max(0, min(3, int(v)))
    s = str(v).strip().lower().lstrip("p")
    try:
        return max(0, min(3, int(s)))
    except ValueError:
        # 中文级别尽力匹配
        if "阻塞" in s or "block" in s:
            return 0
        if "高" in s or "high" in s:
            return 1
        if "低" in s or "low" in s:
            return 3
        return 2


def _strip_meta(raw: dict) -> dict:
    """去掉 ``output`` 字段保留其余 meta —— 让上层落 ai_runs 时直接 ``**meta``。"""
    return {k: v for k, v in (raw or {}).items() if k != "output"}
