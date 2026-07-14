"""AI 用例草稿服务层（M7）。

负责：
  - list / get / update / 逻辑删
  - batch_commit：勾选的 pending 草稿一并写入 test_cases（functional 用例）

batch_commit 是 M7 的核心写路径，事务边界要严格：
  - 任一草稿非法（已 accepted / module 解析失败）→ 整批不写
  - 写完后回写 draft.committed_case_id + status='accepted'
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import (
    AiCaseDraft,
    AI_CASE_DRAFT_STATUS_ACCEPTED,
    AI_CASE_DRAFT_STATUS_PENDING,
    AI_CASE_DRAFT_STATUS_REJECTED,
    CASE_TYPE_FUNCTIONAL,
    Module,
    Requirement,
    TestCase,
)


logger = logging.getLogger(__name__)

# 解析 steps_text 编号行：把 "1. xxx" / "2. yyy" 抽出来变成 list[str]
_STEPS_LINE_PATTERN = re.compile(r"^\s*(\d+)[\.\)、]\s*(.+)$", re.MULTILINE)


def list_drafts(
    session: Session,
    requirement_id: Optional[int] = None,
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[AiCaseDraft]:
    q = session.query(AiCaseDraft)
    if requirement_id is not None:
        q = q.filter(AiCaseDraft.requirement_id == requirement_id)
    if batch_id:
        q = q.filter(AiCaseDraft.batch_id == batch_id)
    if status:
        q = q.filter(AiCaseDraft.status == status)
    return q.order_by(AiCaseDraft.created_at.desc(), AiCaseDraft.id.desc()).all()


def get_draft(session: Session, draft_id: int) -> Optional[AiCaseDraft]:
    return session.query(AiCaseDraft).filter(AiCaseDraft.id == draft_id).one_or_none()


def update_draft(
    session: Session,
    draft_id: int,
    patch: dict[str, Any],
) -> AiCaseDraft:
    """部分更新；只动 patch 里有的字段。失败抛 ValueError。"""
    draft = get_draft(session, draft_id)
    if draft is None:
        raise ValueError(f"草稿 #{draft_id} 不存在")
    if draft.status != AI_CASE_DRAFT_STATUS_PENDING:
        raise ValueError(
            f"草稿 #{draft_id} 状态 {draft.status}，不可再编辑"
        )

    allowed = {
        "title", "preconditions", "steps_text", "expected",
        "priority", "tags", "step_template", "needs_ui_detail",
    }
    for k, v in patch.items():
        if k in allowed and v is not None:
            setattr(draft, k, v)
    session.flush()
    return draft


def reject_draft(
    session: Session, draft_id: int, reason: Optional[str] = None
) -> AiCaseDraft:
    """逻辑删 = status -> rejected。已 accepted 的不允许 reject。

    reason 是数据飞轮的关键信号：回填 prompt 反例 + 统计 top 拒因。
    """
    draft = get_draft(session, draft_id)
    if draft is None:
        raise ValueError(f"草稿 #{draft_id} 不存在")
    if draft.status == AI_CASE_DRAFT_STATUS_ACCEPTED:
        raise ValueError("草稿已入库，不能 reject")
    draft.status = AI_CASE_DRAFT_STATUS_REJECTED
    if reason and reason.strip():
        draft.reject_reason = reason.strip()[:500]
    session.flush()
    return draft


def _edit_ratio(draft: AiCaseDraft) -> Optional[float]:
    """编辑相似度 0..1：生成时原始快照 vs 当前字段。1.0 = 原样采纳。

    用 difflib 对拼接文本做序列相似度——不追求学术精确,追求"能横向对比"。
    没有快照的老草稿返回 None。
    """
    import difflib

    orig = draft.original_payload
    if not isinstance(orig, dict):
        return None

    def _concat(title, pre, steps, exp) -> str:
        return "\n".join(str(x or "") for x in (title, pre, steps, exp))

    before = _concat(
        orig.get("title"), orig.get("preconditions"),
        orig.get("steps_text"), orig.get("expected"),
    )
    after = _concat(
        draft.title, draft.preconditions, draft.steps_text, draft.expected,
    )
    if not before.strip() and not after.strip():
        return 1.0
    return round(difflib.SequenceMatcher(None, before, after).ratio(), 4)


# ---------------------------------------------------------------------------
# 评审信号统计（数据飞轮看板）
# ---------------------------------------------------------------------------
def draft_review_stats(
    session: Session,
    project_id: Optional[int] = None,
    days: int = 90,
) -> dict[str, Any]:
    """采纳率 / 编辑相似度 / top 拒因,按 model_label 与 prompt_version 分组。

    这是"AI 生成质量是否在变好"的唯一事实来源:
      - adoption_rate = accepted / (accepted + rejected)  （pending 不计入分母）
      - avg_edit_ratio 只统计 accepted 且有快照的
    """
    from datetime import datetime, timedelta

    from database.models import AiRun

    since = datetime.now() - timedelta(days=max(1, days))
    q = (
        session.query(AiCaseDraft, AiRun.prompt_version)
        .outerjoin(AiRun, AiCaseDraft.ai_run_id == AiRun.id)
        .filter(AiCaseDraft.created_at >= since)
    )
    if project_id is not None:
        q = q.join(
            Requirement, AiCaseDraft.requirement_id == Requirement.id
        ).filter(Requirement.project_id == project_id)

    rows = q.all()

    def _bucket() -> dict[str, Any]:
        return {"total": 0, "accepted": 0, "rejected": 0, "pending": 0,
                "edit_ratios": []}

    overall = _bucket()
    by_model: dict[str, dict[str, Any]] = {}
    by_version: dict[str, dict[str, Any]] = {}
    reject_reasons: dict[str, int] = {}

    for draft, prompt_version in rows:
        for bucket in (
            overall,
            by_model.setdefault(draft.model_label or "unknown", _bucket()),
            by_version.setdefault(prompt_version or "unknown", _bucket()),
        ):
            bucket["total"] += 1
            if draft.status == AI_CASE_DRAFT_STATUS_ACCEPTED:
                bucket["accepted"] += 1
                if draft.edit_ratio is not None:
                    bucket["edit_ratios"].append(draft.edit_ratio)
            elif draft.status == AI_CASE_DRAFT_STATUS_REJECTED:
                bucket["rejected"] += 1
            else:
                bucket["pending"] += 1
        if draft.status == AI_CASE_DRAFT_STATUS_REJECTED and draft.reject_reason:
            key = draft.reject_reason.strip()[:100]
            reject_reasons[key] = reject_reasons.get(key, 0) + 1

    def _finish(b: dict[str, Any]) -> dict[str, Any]:
        reviewed = b["accepted"] + b["rejected"]
        ratios = b.pop("edit_ratios")
        b["adoption_rate"] = round(b["accepted"] / reviewed, 4) if reviewed else None
        b["avg_edit_ratio"] = round(sum(ratios) / len(ratios), 4) if ratios else None
        return b

    return {
        "days": days,
        "overall": _finish(overall),
        "by_model": {k: _finish(v) for k, v in by_model.items()},
        "by_prompt_version": {k: _finish(v) for k, v in by_version.items()},
        "top_reject_reasons": sorted(
            ({"reason": k, "count": v} for k, v in reject_reasons.items()),
            key=lambda x: -x["count"],
        )[:10],
    }


# ---------------------------------------------------------------------------
# 业务步骤合成（commit 时落到 TestCase.business_steps）
# ---------------------------------------------------------------------------
def build_business_steps(
    steps_text: Optional[str],
    step_template: Optional[list[Any]],
) -> list[dict[str, Any]]:
    """从 steps_text + step_template 合成 [{order, action_text, step_type_hint, needs_ui_detail}]。

    - steps_text 用 `^\s*(\d+)\.\s*(.+)$` 多行匹配抽编号行
    - step_template 按下标对齐；缺位时填默认 hint
    - 没有 steps_text 时，回退用 step_template 自己的 order/hint
    """
    template = step_template or []

    parsed_lines: list[str] = []
    if steps_text:
        matches = _STEPS_LINE_PATTERN.findall(steps_text)
        # findall 返回 [(num, text), ...]，按出现顺序保留
        parsed_lines = [t.strip() for _, t in matches if t and t.strip()]

    out: list[dict[str, Any]] = []
    total = max(len(parsed_lines), len(template))
    for i in range(total):
        action = parsed_lines[i] if i < len(parsed_lines) else ""
        tpl = template[i] if i < len(template) else {}
        if not isinstance(tpl, dict):
            tpl = {}
        out.append(
            {
                "order": i + 1,
                "action_text": action,
                "step_type_hint": str(tpl.get("step_type_hint") or "other"),
                "needs_ui_detail": bool(tpl.get("needs_ui_detail")),
            }
        )
    return out


# ---------------------------------------------------------------------------
# batch_commit
# ---------------------------------------------------------------------------
def batch_commit(
    session: Session,
    draft_ids: list[int],
    target_module_id: Optional[int] = None,
) -> tuple[list[int], list[dict[str, Any]]]:
    """批量入库：勾选的草稿 → test_cases。

    模块归属：
      - 显式传 target_module_id：所有草稿一律用这个 module
      - 不传：每条草稿走自己 requirement.module_id
      - 都没有：把那条草稿放入 skipped[]，不阻塞其它

    返回：(created_case_ids, skipped[])  skipped 元素 = {draft_id, reason}
    """
    if not draft_ids:
        return [], []

    drafts = (
        session.query(AiCaseDraft)
        .filter(AiCaseDraft.id.in_(draft_ids))
        .all()
    )
    by_id = {d.id: d for d in drafts}

    # 模块归属池：req_id → module_id
    requirement_ids = {d.requirement_id for d in drafts}
    req_module_map: dict[int, Optional[int]] = {}
    if requirement_ids:
        rows = (
            session.query(Requirement.id, Requirement.module_id)
            .filter(Requirement.id.in_(requirement_ids))
            .all()
        )
        req_module_map = {rid: mid for rid, mid in rows}

    # 显式 target_module_id 校验
    if target_module_id is not None:
        exists = (
            session.query(Module.id).filter(Module.id == target_module_id).one_or_none()
        )
        if exists is None:
            raise ValueError(f"target_module_id={target_module_id} 不存在")

    created_ids: list[int] = []
    skipped: list[dict[str, Any]] = []

    for draft_id in draft_ids:
        d = by_id.get(draft_id)
        if d is None:
            skipped.append({"draft_id": draft_id, "reason": "not_found"})
            continue
        if d.status != AI_CASE_DRAFT_STATUS_PENDING:
            skipped.append(
                {"draft_id": draft_id, "reason": f"status_{d.status}"}
            )
            continue

        module_id = (
            target_module_id
            if target_module_id is not None
            else req_module_map.get(d.requirement_id)
        )
        if module_id is None:
            skipped.append(
                {"draft_id": draft_id, "reason": "no_module"}
            )
            continue

        business_steps = build_business_steps(d.steps_text, d.step_template)

        # functional_spec —— 兼容 M5 现状（前端 CasesTab 在用）
        functional_spec = {
            "preconditions": d.preconditions or "",
            "steps_text": d.steps_text or "",
            "expected": d.expected or "",
        }

        case = TestCase(
            module_id=module_id,
            name=d.title,
            description=d.preconditions or None,
            case_type=CASE_TYPE_FUNCTIONAL,
            tags=d.tags or [],
            priority=d.priority,
            source="ai_m7",
            draft_id=d.id,
            requirement_id=d.requirement_id,
            business_steps=business_steps,
            functional_spec=functional_spec,
        )
        session.add(case)
        session.flush()
        created_ids.append(case.id)

        # 回写草稿 + 评审信号（编辑相似度）
        d.status = AI_CASE_DRAFT_STATUS_ACCEPTED
        d.committed_case_id = case.id
        d.edit_ratio = _edit_ratio(d)

    session.flush()
    logger.info(
        "[ai_case_draft_service] batch_commit drafts=%d created=%d skipped=%d",
        len(draft_ids), len(created_ids), len(skipped),
    )
    return created_ids, skipped
