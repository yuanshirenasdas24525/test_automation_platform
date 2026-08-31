"""E｜存量回填：按"本屏唯一性"订正 iOS 元素定位器的 is_unique / match_count / is_primary。

背景：is_primary 落库时纯按 score 排第一，而裸 accessibility_id 分最高——于是撞名元素
（如 Log Out 同时是 Alert/StaticText/Button）的主定位器错误地落在裸 id 上，运行时点错。
本脚本用每个元素"最近出现"的快照 UI Tree 重算本屏同名数，撞名时把裸名定位器标 is_unique=
False 压低分、把类型限定的 class_chain 标 is_unique=True 抬到最高，并据此重选 is_primary。

只处理 iOS（Android 未改评分逻辑，保持原状）。可 --apply 落库，缺省 dry-run。

用法：
  ./venv/bin/python database/migrations/data_migrations/backfill_locator_uniqueness.py          # dry-run
  ./venv/bin/python database/migrations/data_migrations/backfill_locator_uniqueness.py --apply   # 落库
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from database.db import DB  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _counts_for_doc(path: Path) -> tuple[dict[str, int], dict[tuple[str, str], int]] | None:
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError):
        return None
    name_counts: dict[str, int] = defaultdict(int)
    type_name_counts: dict[tuple[str, str], int] = defaultdict(int)
    for node in root.iter():
        a = node.attrib
        acc = a.get("content-desc") or a.get("name") or a.get("label") or ""
        if not acc:
            continue
        etype = a.get("class") or a.get("type") or str(node.tag).split("}")[-1]
        name_counts[acc] += 1
        type_name_counts[(etype, acc)] += 1
    return name_counts, type_name_counts


def _is_scoped(strategy: str, locator: str) -> bool:
    s = strategy.lower()
    if s == "ios_class_chain":
        return "XCUIElementType" in locator
    if s == "xpath":
        return "@" in locator and locator.lstrip().startswith("//")
    return False


def main(apply: bool) -> None:
    d = DB()
    s = d.session

    # 每个 iOS 元素 → 最近一次出现的、且带 document 的快照
    rows = s.execute(text("""
        select o.element_id, max(o.snapshot_id) as snap_id
        from ui_element_occurrences o
        join ui_elements e on e.id = o.element_id
        join ui_page_snapshots sn on sn.id = o.snapshot_id
        where e.platform = 'ios' and sn.document_uri is not null
        group by o.element_id
    """)).fetchall()
    by_snap: dict[int, list[int]] = defaultdict(list)
    for eid, sid in rows:
        by_snap[int(sid)].append(int(eid))

    doc_cache: dict[int, tuple[dict, dict] | None] = {}
    changed_locators = 0
    changed_primary = 0
    touched_elements = 0

    for sid, eids in by_snap.items():
        if sid not in doc_cache:
            uri = s.execute(text("select document_uri from ui_page_snapshots where id=:i"), {"i": sid}).scalar()
            p = Path(uri)
            p = p if p.is_absolute() else _PROJECT_ROOT / p
            doc_cache[sid] = _counts_for_doc(p)
        counts = doc_cache[sid]
        if not counts:
            continue
        name_counts, type_name_counts = counts

        for eid in eids:
            locs = s.execute(text("""
                select id, strategy, locator, score, is_primary, is_unique, match_count
                from ui_element_locators where element_id=:e
            """), {"e": eid}).fetchall()
            if not locs:
                continue
            # 元素的 accessibility 名 = 它的 accessibility_id 定位器值
            name = next((l[2] for l in locs if l[1].lower() == "accessibility_id"), None)
            if not name:
                continue
            etype = s.execute(text("select element_type from ui_elements where id=:i"), {"i": eid}).scalar() or ""
            name_matches = name_counts.get(name, 1)
            type_matches = type_name_counts.get((etype, name), 1)
            ambiguous = name_matches > 1

            new_scores: dict[int, int] = {}
            for lid, strat, loc, score, is_primary, is_unique, match_count in locs:
                scoped = _is_scoped(strat, loc)
                unique = (type_matches <= 1) if scoped else (name_matches <= 1)
                matches = type_matches if scoped else name_matches
                new_score = score
                if ambiguous:
                    new_score = 99 if (scoped and type_matches <= 1) else 30
                new_scores[lid] = new_score
                if (is_unique is not unique) or (match_count != matches) or (new_score != score):
                    changed_locators += 1
                    if apply:
                        s.execute(text("""
                            update ui_element_locators
                            set is_unique=:u, match_count=:m, score=:sc
                            where id=:i
                        """), {"u": unique, "m": matches, "sc": new_score, "i": lid})
            # 重选 primary：最高分（并列取 id 最小）
            best_lid = min(locs, key=lambda l: (-new_scores[l[0]], l[0]))[0]
            for lid, *_ in ((l[0],) for l in locs):
                want = (lid == best_lid)
                cur = next(l[4] for l in locs if l[0] == lid)
                if bool(cur) != want:
                    changed_primary += 1
                    if apply:
                        s.execute(text("update ui_element_locators set is_primary=:p where id=:i"),
                                  {"p": want, "i": lid})
            touched_elements += 1

    if apply:
        d.commit()
    print(f"{'[APPLIED]' if apply else '[DRY-RUN]'} 元素 {touched_elements} 个，"
          f"定位器字段变更 {changed_locators} 条，主定位器改判 {changed_primary} 条")
    d.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
