"""功能测试要点 Checklist —— 覆盖归类（纯函数）。

AI（prompt `feature_checklist`）产出「该测什么」的要点 + 把已有用例映射到各要点；
本模块把 AI 的原始输出规整成前端可用的结构，并按阈值判定每个要点的覆盖状态：

  covered_count == 0        -> "none"  （缺）
  1 <= covered_count <= 2   -> "thin"  （偏薄）
  covered_count >= 3        -> "covered"（已覆盖）

不碰 DB、不调 LLM，便于单测。阈值先硬编码，后续要可配置只改这一处。
"""
from __future__ import annotations

from typing import Any

THIN_MAX = 2  # 1..THIN_MAX 条算“偏薄”，超过算“已覆盖”


def _coverage_status(n: int) -> str:
    if n <= 0:
        return "none"
    if n <= THIN_MAX:
        return "thin"
    return "covered"


def build_checklist(
    aspects_raw: Any,
    existing_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把 AI 的 aspects 规整成 [{aspect, what_to_test, covered_cases, covered_case_ids, covered_count, coverage}]。

    - existing_cases：本模块用例 [{id, name}, ...]；用于把 AI 给的用例名**解析成真实 id**，
      前端直接按 id 筛选（避免名称匹配的各种坑：编号前缀 / 重命名 / 大小写空白）。
    - 防御性：covered 只保留**确实存在于本模块**的用例（AI 偶尔会编名字）；
    - 一条用例只归一个要点，去重避免两个要点都认领同一条把覆盖数灌水。
    """
    name_to_id: dict[str, int] = {}
    for c in existing_cases or []:
        nm = str((c or {}).get("name") or "").strip()
        cid = (c or {}).get("id")
        if nm and isinstance(cid, int) and nm not in name_to_id:
            name_to_id[nm] = cid
    out: list[dict[str, Any]] = []
    claimed: set[int] = set()
    if not isinstance(aspects_raw, list):
        return out
    for a in aspects_raw:
        if not isinstance(a, dict):
            continue
        aspect = str(a.get("aspect") or "").strip()
        if not aspect:
            continue
        covered: list[str] = []
        covered_ids: list[int] = []
        for name in a.get("covered_cases") or []:
            nm = str(name or "").strip()
            cid = name_to_id.get(nm)
            if cid is not None and cid not in claimed:
                covered.append(nm)
                covered_ids.append(cid)
                claimed.add(cid)
        out.append(
            {
                "aspect": aspect[:80],
                "what_to_test": str(a.get("what_to_test") or "").strip(),
                "gap_hint": str(a.get("gap_hint") or "").strip(),
                "covered_cases": covered,
                "covered_case_ids": covered_ids,
                "covered_count": len(covered_ids),
                "coverage": _coverage_status(len(covered_ids)),
            }
        )
    return out


def checklist_summary(aspects: list[dict[str, Any]]) -> dict[str, int]:
    """给前端顶部用的汇总：总要点数 / 已覆盖数 / 有缺口数(thin+none)。"""
    total = len(aspects)
    covered = sum(1 for a in aspects if a.get("coverage") == "covered")
    gaps = sum(1 for a in aspects if a.get("coverage") in ("thin", "none"))
    return {"total": total, "covered": covered, "gaps": gaps}
