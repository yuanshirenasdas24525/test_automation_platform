"""用例覆盖率视图：哪些需求/模块还没有用例，把"补全面"变成可见可点的动作。

覆盖口径：
  - 需求维度：每个需求关联多少条 test_cases（按 requirement_id）+ 多少条待评审草稿
  - 模块维度：每个模块下多少条 test_cases（按 module_id，区分 case_type）
"哪些需求还是 0 用例"就是覆盖缺口 —— 是最该优先补的地方。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import (
    AI_CASE_DRAFT_STATUS_PENDING,
    AiCaseDraft,
    Module,
    Requirement,
    TestCase,
)


def requirement_coverage(session: Session, project_id: int) -> dict[str, Any]:
    """按需求统计覆盖：每个需求的用例数 / 待评审草稿数 / 是否有缺口。"""
    reqs = (
        session.query(Requirement.id, Requirement.title, Requirement.priority,
                      Requirement.module_id, Requirement.status)
        .filter(Requirement.project_id == project_id)
        .all()
    )
    if not reqs:
        return {"project_id": project_id, "total": 0, "covered": 0,
                "uncovered": 0, "coverage_rate": None, "requirements": []}

    req_ids = [r.id for r in reqs]

    # 每需求的用例数
    case_counts = dict(
        session.query(TestCase.requirement_id, func.count(TestCase.id))
        .filter(TestCase.requirement_id.in_(req_ids))
        .group_by(TestCase.requirement_id)
        .all()
    )
    # 每需求的待评审草稿数（pending）
    draft_counts = dict(
        session.query(AiCaseDraft.requirement_id, func.count(AiCaseDraft.id))
        .filter(
            AiCaseDraft.requirement_id.in_(req_ids),
            AiCaseDraft.status == AI_CASE_DRAFT_STATUS_PENDING,
        )
        .group_by(AiCaseDraft.requirement_id)
        .all()
    )
    module_names = dict(
        session.query(Module.id, Module.name)
        .filter(Module.project_id == project_id)
        .all()
    )

    rows: list[dict] = []
    covered = 0
    for r in reqs:
        n_cases = int(case_counts.get(r.id, 0))
        n_drafts = int(draft_counts.get(r.id, 0))
        if n_cases > 0:
            covered += 1
        rows.append({
            "requirement_id": r.id,
            "title": r.title,
            "priority": r.priority,
            "module": module_names.get(r.module_id),
            "status": r.status,
            "case_count": n_cases,
            "pending_draft_count": n_drafts,
            # 缺口分级：0 用例=gap；有草稿没入库=pending；已覆盖=covered
            "coverage": "covered" if n_cases > 0
                        else ("has_drafts" if n_drafts > 0 else "gap"),
        })

    # 缺口优先：gap 且高优先级排前面
    rows.sort(key=lambda x: (
        {"gap": 0, "has_drafts": 1, "covered": 2}[x["coverage"]],
        x["priority"] if x["priority"] is not None else 9,
    ))
    total = len(rows)
    return {
        "project_id": project_id,
        "total": total,
        "covered": covered,
        "uncovered": total - covered,
        "coverage_rate": round(covered / total, 4) if total else None,
        "requirements": rows,
    }


def module_coverage(session: Session, project_id: int) -> list[dict]:
    """按模块统计用例数（区分 case_type），供覆盖热区展示。"""
    modules = (
        session.query(Module.id, Module.name)
        .filter(Module.project_id == project_id)
        .all()
    )
    if not modules:
        return []
    mids = [m.id for m in modules]
    # (module_id, case_type) → count
    rows = (
        session.query(TestCase.module_id, TestCase.case_type, func.count(TestCase.id))
        .filter(TestCase.module_id.in_(mids))
        .group_by(TestCase.module_id, TestCase.case_type)
        .all()
    )
    by_module: dict[int, dict] = {m.id: {"module_id": m.id, "name": m.name,
                                         "total": 0, "by_type": {}} for m in modules}
    for mid, ctype, cnt in rows:
        b = by_module.get(mid)
        if b is None:
            continue
        b["by_type"][ctype or "unknown"] = int(cnt)
        b["total"] += int(cnt)
    out = list(by_module.values())
    out.sort(key=lambda x: x["total"])  # 用例少的模块排前面（更可能是缺口）
    return out
