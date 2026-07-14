"""从分析文档 markdown 提取 context_items 回流记忆层。

背景：原本只有 M6 需求解析（requirement_parse）会写 project_contexts；
需求分析文档（requirement_analyze）内容更深却不回流。本模块补上这条回流链路：

    分析文档 markdown → LLM 提取事实条目 → save_contexts 入库（自动去重）

原则：只回流"事实"（业务规则/数据模型/接口契约/术语/流程/约束），
不回流 AI 的观点与建议 —— 防止记忆层被推测性内容污染（见 docs/AI用例质量路线图.md）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 文档过长截断（提取事实不需要全文，头部信息密度最高）
MAX_DOCUMENT_CHARS = 12000


def _parse_context_items(raw: str) -> list[dict[str, Any]]:
    """解析 LLM 输出：```json``` 围栏 / 裸 JSON 对象 / 首尾大括号兜底。"""
    obj = None
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            break
        except Exception:
            obj = None
    if obj is None:
        s, e = raw.find("{"), raw.rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads(raw[s : e + 1])
            except Exception:
                obj = None

    if isinstance(obj, dict):
        items = obj.get("context_items") or []
    elif isinstance(obj, list):
        items = obj
    else:
        return []
    return [i for i in items if isinstance(i, dict)]


def extract_and_save_contexts(
    session,
    *,
    markdown: str,
    project_id: int,
    cfg,
    source_file: str = "",
    ai_run_id: int | None = None,
    timeout: int = 120,
) -> list[int]:
    """对一份文档跑提取并入库，返回新建的 context id 列表。

    - save_contexts 内部按 (project_id, context_type, title) 去重，重复条目静默跳过
    - 本函数不吞异常 —— 调用方决定失败是否阻断（回流场景应 try/except 包住）
    """
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown
    from server.services.context_service import save_contexts

    text = (markdown or "").strip()
    if len(text) < 50:
        return []

    template = _load_prompt("context_extraction")
    prompt = _render_prompt(
        template, {"DOCUMENT_MARKDOWN": text[:MAX_DOCUMENT_CHARS]}
    )
    raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=timeout)
    items = _parse_context_items(raw)
    if not items:
        logger.info("[context_extraction] 未提取到事实条目 source=%s", source_file)
        return []

    created = save_contexts(
        contexts=items,
        project_id=project_id,
        source_type="analysis_doc",
        source_file=source_file[:200],
        ai_run_id=ai_run_id,
        session=session,
    )
    logger.info(
        "[context_extraction] source=%s 提取 %d 条,新入库 %d 条(其余为重复)",
        source_file, len(items), len(created),
    )
    return created
