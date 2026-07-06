"""AI 诊断标记服务：打标 / 清除 / 自动清 / 反馈查询。

标记生命周期（docs/ai_case_flags_design.md §3-4）：
  - 诊断闭环结束 → upsert_flags_from_outcomes：新标记 supersede 同用例旧 active，
    判"正常"则 auto_clear 旧标记；
  - 后续任意报告该用例通过 → auto_clear_on_pass（只清 manual_fix / ai_fixed；
    interface_defect / environment 通过≠修好，不自动清）；
  - 人工清除 → clear_flag（必须给 reason，即反馈数据）。

反馈读取（§6）：
  - get_case_feedback：诊断时按用例注入 user_feedback；
  - get_no_touch_case_ids：预检层直接跳过 wont_fix / 更正为"正常"的用例。
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    AiCaseFlag,
    ALL_AI_FLAG_TYPES,
    ALL_AI_FLAG_REASONS,
    AI_FLAG_MANUAL_FIX,
    AI_FLAG_INTERFACE_DEFECT,
    AI_FLAG_ENVIRONMENT,
    AI_FLAG_AI_FIXED,
    AI_FLAG_STATUS_ACTIVE,
    AI_FLAG_STATUS_AUTO_CLEARED,
    AI_FLAG_STATUS_CLEARED,
    AI_FLAG_STATUS_SUPERSEDED,
    AI_FLAG_REASON_MANUALLY_FIXED,
    AI_FLAG_REASON_MISJUDGED,
    AI_FLAG_REASON_WONT_FIX,
    Module,
    TestCase,
)
from utils.logger import LOGGER

# 后续执行通过时可自动消失的标记类型
_AUTO_CLEAR_ON_PASS_TYPES = (AI_FLAG_MANUAL_FIX, AI_FLAG_AI_FIXED)


# ---------------------------------------------------------------------------
# 打标 / 覆盖
# ---------------------------------------------------------------------------
def upsert_flags_from_outcomes(
    session: Session,
    outcomes: list[dict],
    *,
    ai_run_id: int | None = None,
    report_id: int | None = None,
) -> dict:
    """按诊断结局批量打标。

    outcome 结构：
      {case_id, flag_type|None, classification, findings: [str], fix_rounds: int}
      flag_type=None 表示"本次判正常"→ 只清旧 active 标记。
    """
    case_ids = [o["case_id"] for o in outcomes if o.get("case_id") is not None]
    if not case_ids:
        return {"created": 0, "cleared": 0}
    module_by_case = dict(
        session.query(TestCase.id, TestCase.module_id)
        .filter(TestCase.id.in_(case_ids))
        .all()
    )
    actives = {
        f.case_id: f
        for f in session.query(AiCaseFlag).filter(
            AiCaseFlag.case_id.in_(case_ids),
            AiCaseFlag.status == AI_FLAG_STATUS_ACTIVE,
        )
    }
    created = 0
    cleared = 0
    now = datetime.now()
    for o in outcomes:
        cid = o.get("case_id")
        if cid is None or cid not in module_by_case:
            continue
        old = actives.get(cid)
        ft = o.get("flag_type")
        if ft is None:
            if old is not None:
                old.status = AI_FLAG_STATUS_AUTO_CLEARED
                old.cleared_at = now
                old.cleared_note = "新一轮 AI 诊断判定为正常"
                cleared += 1
            continue
        if ft not in ALL_AI_FLAG_TYPES:
            LOGGER.warning("[ai_flag] 未知 flag_type=%r，跳过 case=%s", ft, cid)
            continue
        if old is not None:
            old.status = AI_FLAG_STATUS_SUPERSEDED
            old.cleared_at = now
        flag = AiCaseFlag(
            case_id=cid,
            module_id=module_by_case.get(cid),
            flag_type=ft,
            classification=str(o.get("classification") or "")[:20] or None,
            findings=[str(f)[:300] for f in (o.get("findings") or [])][:6],
            fix_rounds=int(o.get("fix_rounds") or 0),
            source_ai_run_id=ai_run_id,
            source_report_id=report_id,
            status=AI_FLAG_STATUS_ACTIVE,
        )
        session.add(flag)
        actives[cid] = flag
        created += 1
    session.flush()
    LOGGER.info("[ai_flag] upsert ai_run=%s report=%s created=%d cleared=%d",
                ai_run_id, report_id, created, cleared)
    return {"created": created, "cleared": cleared}


def derive_outcomes_from_items(
    items: list[dict],
    *,
    fixed_ids: set[int] | None = None,
    kept_green_ids: set[int] | None = None,
    regressed_ids: set[int] | None = None,
    fix_rounds_by_case: dict[int, int] | None = None,
    skip_reasons_by_case: dict[int, list[str]] | None = None,
    unverified: bool = False,
    applied_ids: set[int] | None = None,
) -> list[dict]:
    """诊断 items（+ 闭环结局）→ 打标 outcome 列表。

    分类映射：
      接口问题 → interface_defect；环境/其他 → environment；正常 → None（清旧标）；
      用例问题 → 看修复结局：
        - 验证红→绿 / 假通过补断言后仍绿 → ai_fixed
        - 修复引发回归被回滚 / 仍失败 / 修不动 → manual_fix
        - unverified（未跑验证）→ 一律 manual_fix（没有验证背书，提示人工复核）
    """
    fixed_ids = fixed_ids or set()
    kept_green_ids = kept_green_ids or set()
    regressed_ids = regressed_ids or set()
    fix_rounds_by_case = fix_rounds_by_case or {}
    skip_reasons_by_case = skip_reasons_by_case or {}
    applied_ids = applied_ids or set()

    outcomes: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("case_id"))
        except (TypeError, ValueError):
            continue
        cls = str(item.get("classification") or "").strip()
        findings = [str(f) for f in (item.get("findings") or []) if str(f).strip()]
        rounds = int(fix_rounds_by_case.get(cid) or 0)

        if cls == "接口问题":
            ft = AI_FLAG_INTERFACE_DEFECT
        elif cls == "环境/其他":
            ft = AI_FLAG_ENVIRONMENT
        elif cls == "正常":
            ft = None
        elif cls == "用例问题":
            if unverified:
                ft = AI_FLAG_MANUAL_FIX
                findings.insert(
                    0,
                    "AI 修复已应用但未重跑验证，请人工核对" if cid in applied_ids
                    else "AI 未能给出可自动应用的修复",
                )
            elif cid in fixed_ids:
                ft = AI_FLAG_AI_FIXED
                findings.insert(0, f"AI 修复并重跑验证通过（{max(rounds, 1)} 轮）")
            elif cid in kept_green_ids:
                ft = AI_FLAG_AI_FIXED
                findings.insert(0, "AI 补充断言/提取后重跑仍通过（假通过治理）")
            elif cid in regressed_ids:
                ft = AI_FLAG_MANUAL_FIX
                findings.insert(0, "AI 修复导致该用例回归，已自动回滚——需人工处理")
            else:
                ft = AI_FLAG_MANUAL_FIX
                reasons = skip_reasons_by_case.get(cid) or []
                if rounds > 0:
                    findings.insert(0, f"AI 尝试修复 {rounds} 轮后仍失败")
                elif reasons:
                    findings.insert(0, f"AI 的修复建议未通过预检：{'；'.join(reasons[:2])}")
                else:
                    findings.insert(0, "AI 未能自动修复")
        else:
            continue   # 未知分类不打标

        outcomes.append({
            "case_id": cid,
            "flag_type": ft,
            "classification": cls,
            "findings": findings,
            "fix_rounds": rounds,
        })
    return outcomes


# ---------------------------------------------------------------------------
# 清除
# ---------------------------------------------------------------------------
def clear_flag(
    session: Session,
    case_id: int,
    *,
    reason: str,
    corrected_classification: str | None = None,
    note: str | None = None,
    operator_id: int | None = None,
) -> Optional[AiCaseFlag]:
    """人工清除某用例的 active 标记（清除动作本身就是反馈）。无 active 标记返回 None。"""
    if reason not in ALL_AI_FLAG_REASONS:
        raise ValueError(f"无效的清除原因：{reason!r}，可选 {sorted(ALL_AI_FLAG_REASONS)}")
    if reason == AI_FLAG_REASON_MISJUDGED and not (corrected_classification or "").strip():
        raise ValueError("选择「AI 判断有误」时必须给出正确分类（corrected_classification）")
    flag = (
        session.query(AiCaseFlag)
        .filter(AiCaseFlag.case_id == case_id, AiCaseFlag.status == AI_FLAG_STATUS_ACTIVE)
        .order_by(AiCaseFlag.id.desc())
        .first()
    )
    if flag is None:
        return None
    flag.status = AI_FLAG_STATUS_CLEARED
    flag.cleared_at = datetime.now()
    flag.cleared_by_id = operator_id
    flag.cleared_reason = reason
    flag.corrected_classification = (corrected_classification or "").strip()[:20] or None
    flag.cleared_note = (note or "").strip()[:2000] or None
    session.flush()
    return flag


def auto_clear_on_pass(session: Session, passed_case_ids: Iterable[int]) -> int:
    """用例在新报告里通过 → 自动清 manual_fix / ai_fixed（问题已不存在/修复已坐实）。"""
    ids = [int(c) for c in passed_case_ids]
    if not ids:
        return 0
    rows = (
        session.query(AiCaseFlag)
        .filter(
            AiCaseFlag.case_id.in_(ids),
            AiCaseFlag.status == AI_FLAG_STATUS_ACTIVE,
            AiCaseFlag.flag_type.in_(_AUTO_CLEAR_ON_PASS_TYPES),
        )
        .all()
    )
    now = datetime.now()
    for f in rows:
        f.status = AI_FLAG_STATUS_AUTO_CLEARED
        f.cleared_at = now
        f.cleared_note = "后续执行已通过，自动清除"
    session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# 查询（列表 / 树 / 历史）
# ---------------------------------------------------------------------------
def get_active_flags(session: Session, case_ids: Iterable[int]) -> dict[int, dict]:
    """批量取 active 标记（列表 embed 用，防 N+1）。"""
    ids = [int(c) for c in case_ids]
    if not ids:
        return {}
    rows = (
        session.query(AiCaseFlag)
        .filter(AiCaseFlag.case_id.in_(ids), AiCaseFlag.status == AI_FLAG_STATUS_ACTIVE)
        .order_by(AiCaseFlag.id.asc())
        .all()
    )
    return {r.case_id: serialize_flag(r) for r in rows}


def serialize_flag(f: AiCaseFlag) -> dict:
    return {
        "id": f.id,
        "case_id": f.case_id,
        "flag_type": f.flag_type,
        "classification": f.classification,
        "findings": f.findings or [],
        "fix_rounds": f.fix_rounds or 0,
        "source_ai_run_id": f.source_ai_run_id,
        "source_report_id": f.source_report_id,
        "status": f.status,
        "cleared_reason": f.cleared_reason,
        "corrected_classification": f.corrected_classification,
        "cleared_note": f.cleared_note,
        "cleared_at": f.cleared_at.isoformat() if f.cleared_at else None,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def flag_history(session: Session, case_id: int, limit: int = 20) -> list[dict]:
    rows = (
        session.query(AiCaseFlag)
        .filter(AiCaseFlag.case_id == case_id)
        .order_by(AiCaseFlag.id.desc())
        .limit(limit)
        .all()
    )
    return [serialize_flag(r) for r in rows]


def module_flag_counts(session: Session, project_id: int) -> dict[int, dict]:
    """项目内各模块的 active 标记计数，**含子树聚合**（模块卡片红点用）。"""
    modules = (
        session.query(Module.id, Module.parent_id)
        .filter(Module.project_id == project_id)
        .all()
    )
    if not modules:
        return {}
    parent_of = {m.id: m.parent_id for m in modules}
    rows = (
        session.query(AiCaseFlag.module_id, AiCaseFlag.flag_type, func.count(AiCaseFlag.id))
        .filter(
            AiCaseFlag.module_id.in_(list(parent_of.keys())),
            AiCaseFlag.status == AI_FLAG_STATUS_ACTIVE,
        )
        .group_by(AiCaseFlag.module_id, AiCaseFlag.flag_type)
        .all()
    )
    counts: dict[int, dict] = {}

    def _bump(mid: int, ft: str, n: int) -> None:
        entry = counts.setdefault(mid, {"total": 0})
        entry["total"] += n
        entry[ft] = entry.get(ft, 0) + n

    for module_id, flag_type, n in rows:
        # 沿 parent 链向上传播（含自身），带环保护
        mid, hops = module_id, 0
        seen: set[int] = set()
        while mid is not None and mid in parent_of and mid not in seen and hops < 50:
            _bump(mid, flag_type, int(n))
            seen.add(mid)
            mid = parent_of.get(mid)
            hops += 1
    return counts


# ---------------------------------------------------------------------------
# 反馈读取（回流 AI）
# ---------------------------------------------------------------------------
_REASON_LABELS = {
    AI_FLAG_REASON_MANUALLY_FIXED: "用户已人工修复",
    AI_FLAG_REASON_MISJUDGED: "用户更正了 AI 的分类",
    AI_FLAG_REASON_WONT_FIX: "用户标记为无需处理（预期行为）",
}


def get_case_feedback(
    session: Session,
    case_ids: Iterable[int],
    per_case: int = 3,
) -> dict[int, list[str]]:
    """按用例取最近的人工清除反馈，渲染成可注入 prompt 的中文句子。

    只取有学习价值的 reason（manually_fixed / misjudged / wont_fix）；
    external_fixed（接口修好了/环境恢复了）对下次诊断没有指导意义，不注入。
    """
    ids = [int(c) for c in case_ids]
    if not ids:
        return {}
    rows = (
        session.query(AiCaseFlag)
        .filter(
            AiCaseFlag.case_id.in_(ids),
            AiCaseFlag.status == AI_FLAG_STATUS_CLEARED,
            AiCaseFlag.cleared_reason.in_(list(_REASON_LABELS.keys())),
        )
        .order_by(AiCaseFlag.cleared_at.desc().nullslast(), AiCaseFlag.id.desc())
        .all()
    )
    out: dict[int, list[str]] = {}
    for f in rows:
        bucket = out.setdefault(f.case_id, [])
        if len(bucket) >= per_case:
            continue
        date = f.cleared_at.strftime("%Y-%m-%d") if f.cleared_at else ""
        text = f"{date} {_REASON_LABELS.get(f.cleared_reason, f.cleared_reason)}"
        if f.cleared_reason == AI_FLAG_REASON_MISJUDGED:
            text += f"：AI 曾判『{f.classification or '?'}』，实际是『{f.corrected_classification or '?'}』"
        elif f.classification:
            text += f"（当时 AI 判『{f.classification}』）"
        if f.cleared_note:
            text += f"。备注：{f.cleared_note[:200]}"
        bucket.append(text.strip())
    return out


def get_no_touch_case_ids(session: Session, case_ids: Iterable[int]) -> set[int]:
    """预检硬约束：用户曾标记 wont_fix，或更正分类为「正常」的用例——自动修复直接跳过。"""
    ids = [int(c) for c in case_ids]
    if not ids:
        return set()
    rows = (
        session.query(AiCaseFlag.case_id, AiCaseFlag.cleared_reason, AiCaseFlag.corrected_classification)
        .filter(
            AiCaseFlag.case_id.in_(ids),
            AiCaseFlag.status == AI_FLAG_STATUS_CLEARED,
        )
        .all()
    )
    out: set[int] = set()
    for cid, reason, corrected in rows:
        if reason == AI_FLAG_REASON_WONT_FIX:
            out.add(cid)
        elif reason == AI_FLAG_REASON_MISJUDGED and (corrected or "").strip() == "正常":
            out.add(cid)
    return out
