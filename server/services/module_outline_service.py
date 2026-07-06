"""模块大纲的读取 / 对齐 / 应用逻辑。

「对齐」= 把大纲和当前模块用例比对，产出 diff（不落库）；「应用」= 按最新用例
重新计算并写库（幂等）。设计见 docs/module_outline_design.md。

diff 里每条 change 的 op：
    added     新增点（模块里有用例，但大纲里没有对应点）→ 绿 +
    linked    已有 gap 点按标题匹配到用例 → 绿（转 covered）
    renamed   关联用例改名 → 黄 ~（old_title → title）
    orphaned  关联用例被删 → 红 −（点转 gap）
    unchanged 无变化（一般不下发给前端 diff）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.models import (
    CASE_TYPE_API,
    CASE_TYPE_FUNCTIONAL,
    ModuleOutline,
    ModuleOutlinePoint,
    TestCase,
    OUTLINE_POINT_COVERED,
    OUTLINE_POINT_GAP,
    OUTLINE_POINT_SOURCE_MANUAL,
)


def _case_type_for_mode(mode: str) -> str:
    return CASE_TYPE_API if (mode or "").lower() == "interface" else CASE_TYPE_FUNCTIONAL


def get_outline(session, module_id: int) -> ModuleOutline | None:
    return (
        session.query(ModuleOutline)
        .filter(ModuleOutline.module_id == module_id)
        .first()
    )


def _current_cases(session, module_id: int, mode: str) -> list[TestCase]:
    case_type = _case_type_for_mode(mode)
    return (
        session.query(TestCase)
        .filter(TestCase.module_id == module_id, TestCase.case_type == case_type)
        .order_by(TestCase.sort_order.asc(), TestCase.id.asc())
        .all()
    )


def compute_align_changes(session, module_id: int, mode: str) -> dict[str, Any]:
    """比对大纲 ↔ 当前用例，返回 changes（不落库）。

    mode 优先取已存在大纲的 mode，其次用入参。
    """
    outline = get_outline(session, module_id)
    effective_mode = (outline.mode if outline else None) or mode or "functional"
    points: list[ModuleOutlinePoint] = list(outline.points) if outline else []
    cases = _current_cases(session, module_id, effective_mode)

    cases_by_id = {c.id: c for c in cases}
    linked_case_ids = {p.linked_case_id for p in points if p.linked_case_id}
    # 未关联用例的 gap 点，按标题建索引，供“已有用例”回连
    gap_by_title: dict[str, ModuleOutlinePoint] = {}
    for p in points:
        if not p.linked_case_id:
            gap_by_title.setdefault((p.title or "").strip(), p)

    changes: list[dict] = []

    # 1) 遍历已有点
    for p in points:
        if p.linked_case_id:
            case = cases_by_id.get(p.linked_case_id)
            if case is None:
                changes.append({
                    "op": "orphaned",
                    "point_id": p.id,
                    "title": p.title,
                    "category": p.category,
                    "linked_case_id": None,
                    "source": p.source,
                    "next_status": OUTLINE_POINT_GAP,
                })
            elif (case.name or "").strip() != (p.title or "").strip():
                changes.append({
                    "op": "renamed",
                    "point_id": p.id,
                    "old_title": p.title,
                    "title": case.name,
                    "category": p.category,
                    "linked_case_id": case.id,
                    "source": p.source,
                    "next_status": OUTLINE_POINT_COVERED,
                })
        # 未关联的 gap 点：保持不变（可能在步骤 2 里被用例按标题回连）

    # 2) 遍历当前用例，找没被任何点关联的
    matched_gap_ids: set[int] = set()
    for c in cases:
        if c.id in linked_case_ids:
            continue
        title = (c.name or "").strip()
        gap = gap_by_title.get(title)
        if gap is not None and gap.id not in matched_gap_ids:
            matched_gap_ids.add(gap.id)
            changes.append({
                "op": "linked",
                "point_id": gap.id,
                "title": title,
                "category": gap.category,
                "linked_case_id": c.id,
                "source": gap.source,
                "next_status": OUTLINE_POINT_COVERED,
            })
        else:
            changes.append({
                "op": "added",
                "point_id": None,
                "title": title,
                "category": None,
                "linked_case_id": c.id,
                "source": OUTLINE_POINT_SOURCE_MANUAL,
                "next_status": OUTLINE_POINT_COVERED,
            })

    summary = {
        "added": sum(1 for c in changes if c["op"] == "added"),
        "linked": sum(1 for c in changes if c["op"] == "linked"),
        "renamed": sum(1 for c in changes if c["op"] == "renamed"),
        "orphaned": sum(1 for c in changes if c["op"] == "orphaned"),
    }
    return {
        "module_id": module_id,
        "mode": effective_mode,
        "has_outline": outline is not None,
        "changes": changes,
        "summary": summary,
    }


def apply_align(session, module_id: int, mode: str) -> dict[str, Any]:
    """按最新用例重新计算并写库（幂等）。返回应用后的大纲 dict。

    不信任前端传来的陈旧 diff —— 服务端重算后直接应用，避免并发下用例又变了。
    """
    result = compute_align_changes(session, module_id, mode)
    effective_mode = result["mode"]

    outline = get_outline(session, module_id)
    if outline is None:
        outline = ModuleOutline(module_id=module_id, mode=effective_mode)
        session.add(outline)
        session.flush()

    points_by_id = {p.id: p for p in outline.points}
    max_order = max([p.sort_order or 0 for p in outline.points], default=0)

    for ch in result["changes"]:
        op = ch["op"]
        if op == "added":
            max_order += 1
            session.add(ModuleOutlinePoint(
                outline_id=outline.id,
                title=ch["title"],
                category=ch.get("category"),
                sort_order=max_order,
                linked_case_id=ch["linked_case_id"],
                status=OUTLINE_POINT_COVERED,
                source=OUTLINE_POINT_SOURCE_MANUAL,
            ))
        elif op == "linked":
            p = points_by_id.get(ch["point_id"])
            if p is not None:
                p.linked_case_id = ch["linked_case_id"]
                p.status = OUTLINE_POINT_COVERED
        elif op == "renamed":
            p = points_by_id.get(ch["point_id"])
            if p is not None:
                p.title = ch["title"]
                p.status = OUTLINE_POINT_COVERED
        elif op == "orphaned":
            p = points_by_id.get(ch["point_id"])
            if p is not None:
                p.linked_case_id = None
                p.status = OUTLINE_POINT_GAP

    outline.last_aligned_at = datetime.now(timezone.utc)
    session.flush()
    session.refresh(outline)
    return outline.to_dict()


def purge_unlinked_points(session, module_id: int) -> dict[str, Any]:
    """清理垃圾：删掉没有关联用例的测试点（gap / 未 link），只保留"同步自真实用例"的点。

    用于清掉历史上"生成大纲自动落库"灌进来的、没有对应用例的 gap 点。
    返回 {removed, outline}。
    """
    outline = get_outline(session, module_id)
    if outline is None:
        return {"removed": 0, "outline": None}
    removed = 0
    for p in list(outline.points):
        if not p.linked_case_id:
            session.delete(p)
            removed += 1
    session.flush()
    session.refresh(outline)
    return {"removed": removed, "outline": outline.to_dict()}


def diff_ai_points(session, module_id: int, ai_points: list[dict]) -> dict[str, Any]:
    """把 AI 增量重规划产出的测试点和现有大纲比对，返回 diff（不落库）。

    只产出 `added`（标题在现有大纲里不存在的新点）。obsolete 不自动判定（见设计文档 §9），
    交由人工在大纲里删除。用于「AI 重新规划」的预览。
    """
    outline = get_outline(session, module_id)
    existing_titles = {
        (p.title or "").strip() for p in (outline.points if outline else [])
    }
    changes: list[dict] = []
    seen: set[str] = set()
    for pt in ai_points or []:
        title = str((pt or {}).get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        if title in existing_titles:
            continue  # 已有点，不重复
        changes.append({
            "op": "added",
            "point_id": None,
            "title": title[:200],
            "category": str((pt or {}).get("category") or "").strip() or None,
            "linked_case_id": None,
            "source": "ai",
            "next_status": OUTLINE_POINT_GAP,
        })
    return {
        "module_id": module_id,
        "has_outline": outline is not None,
        "changes": changes,
        "summary": {"added": len(changes)},
    }


def upsert_outline_from_ai(
    session,
    module_id: int,
    mode: str,
    digest: str,
    points: list[dict],
    model_name: str | None = None,
) -> ModuleOutline:
    """初次 AI 规划落库：写 digest + 测试点（source=ai，status=gap，暂无关联用例）。

    points: [{"title": ..., "category": ...}, ...]
    已存在大纲则更新 digest/mode/model 并追加尚不存在的点（按标题去重），不清空已有点。
    """
    outline = get_outline(session, module_id)
    if outline is None:
        outline = ModuleOutline(module_id=module_id, mode=mode or "functional")
        session.add(outline)
        session.flush()
    outline.digest = digest or outline.digest
    outline.mode = mode or outline.mode
    if model_name:
        outline.model_name = model_name

    existing_titles = {(p.title or "").strip() for p in outline.points}
    max_order = max([p.sort_order or 0 for p in outline.points], default=0)
    from database.models import OUTLINE_POINT_SOURCE_AI
    for pt in points or []:
        title = str((pt or {}).get("title") or "").strip()
        if not title or title in existing_titles:
            continue
        existing_titles.add(title)
        max_order += 1
        session.add(ModuleOutlinePoint(
            outline_id=outline.id,
            title=title[:200],
            category=str((pt or {}).get("category") or "").strip() or None,
            sort_order=max_order,
            linked_case_id=None,
            status=OUTLINE_POINT_GAP,
            source=OUTLINE_POINT_SOURCE_AI,
        ))
    session.flush()
    return outline
