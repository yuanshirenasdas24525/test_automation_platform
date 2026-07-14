"""AI 生成用例草稿的静态校验（P0-2）。

原则：人的耐心最贵，结构不合格的草稿不允许进入评审页——
先给模型一次自修机会，仍不合格就丢弃并计数（宁缺毋滥）。

本模块只做**结构校验**（纯函数、不碰 DB、不调 LLM），供：
  - tasks/ai_tasks.py::_handle_functional_case_gen（M7 功能用例草稿）
  - server/api/functional_cases.py::ai_generate_batch（接口用例已有 _harden_generated_cases，
    本模块不重复；repair 回路复用其 warnings）
"""
from __future__ import annotations

import re

# 与 ai_gateway/prompts/case_generation_v1.md 中的约定保持一致
ALLOWED_STEP_HINTS = {
    "navigate", "input", "select", "submit",
    "verify", "wait", "cleanup", "other",
}

_NUMBERED_LINE_RE = re.compile(r"^\s*\d+[.、)]", re.M)


def validate_functional_draft(item: dict) -> list[str]:
    """校验一条 M7 功能用例草稿，返回错误列表（空 = 合格）。"""
    errors: list[str] = []

    title = str(item.get("title") or "").strip()
    if not title:
        errors.append("title 为空")
    elif len(title) > 200:
        errors.append(f"title 超长（{len(title)} > 200 字符）")

    steps_text = str(item.get("steps_text") or "").strip()
    if not steps_text:
        errors.append("steps_text 为空——没有步骤的用例无法执行评审")

    expected = str(item.get("expected") or "").strip()
    if not expected:
        errors.append("expected 为空——没有预期结果的用例无法判定通过与否")

    st = item.get("step_template")
    if st is not None and not isinstance(st, list):
        errors.append(f"step_template 不是数组（{type(st).__name__}）")
    elif isinstance(st, list):
        for i, s in enumerate(st):
            if not isinstance(s, dict):
                errors.append(f"step_template[{i}] 不是对象")
                continue
            hint = str(s.get("step_type_hint") or "").strip()
            if hint and hint not in ALLOWED_STEP_HINTS:
                errors.append(
                    f"step_template[{i}].step_type_hint 非法值 {hint!r}，"
                    f"只允许: {', '.join(sorted(ALLOWED_STEP_HINTS))}"
                )
        # 步骤编号对齐（软校验：steps_text 有编号行且数量差 >1 才算错）
        if steps_text and st:
            n_lines = len(_NUMBERED_LINE_RE.findall(steps_text))
            if n_lines and abs(n_lines - len(st)) > 1:
                errors.append(
                    f"steps_text 有 {n_lines} 个编号步骤但 step_template 有 {len(st)} 项，"
                    "两者必须按 1-based 顺序一一对应"
                )

    pr = item.get("priority")
    if pr is not None:
        try:
            if not (0 <= int(pr) <= 3):
                errors.append(f"priority={pr} 越界，取 0/1/2/3")
        except (TypeError, ValueError):
            errors.append(f"priority={pr!r} 不是整数")

    return errors


def _norm_title(s: str) -> str:
    """标题归一化：去场景前缀标记、标点、空白，转小写，便于比相似度。"""
    import re
    t = str(s or "")
    t = re.sub(r"^[【\[（(]?(正向|异常|边界|安全|参数校验|鉴权|越权|场景|前置链)[】\]）)]?[:：]?", "", t)
    t = re.sub(r"[\s\-_、，,。.；;：:（）()【】\[\]]+", "", t)
    return t.lower()


def dedup_against_existing(
    items: list[dict], existing_titles: list[str], threshold: float = 0.88,
) -> tuple[list[dict], list[dict]]:
    """把与已有用例近乎重复的草稿剔除。返回 (保留, 判为重复)。

    先批内互相去重（同批别产两条几乎一样的），再与已入库用例比。
    用 difflib 序列相似度（词法），不依赖 embedding 配置——先跑起来，
    需要更强语义去重再接 embeddings。
    """
    import difflib

    existing_norm = [_norm_title(t) for t in (existing_titles or []) if t]
    kept: list[dict] = []
    dups: list[dict] = []
    seen_norm: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        nt = _norm_title(item.get("title") or "")
        if not nt:
            kept.append(item)
            continue
        pool = existing_norm + seen_norm
        is_dup = any(
            nt == e or difflib.SequenceMatcher(None, nt, e).ratio() >= threshold
            for e in pool
        )
        if is_dup:
            dups.append(item)
        else:
            kept.append(item)
            seen_norm.append(nt)
    return kept, dups


def partition_drafts(items: list) -> tuple[list[dict], list[tuple[dict, list[str]]]]:
    """把解析出的草稿分成（合格, 不合格+错误原因）两组。非 dict 项直接丢弃。"""
    ok: list[dict] = []
    flawed: list[tuple[dict, list[str]]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        errs = validate_functional_draft(item)
        if errs:
            flawed.append((item, errs))
        else:
            ok.append(item)
    return ok, flawed
