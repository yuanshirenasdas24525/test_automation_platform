"""一次性修复：把因为 update_case bug 被清成 NULL / 重复 / 0 的 case.sort_order
按"模块内 id 升序"重新分配 0..N-1。

背景：
  v2 重构后 update_case 用了 model_dump()（不带 exclude_unset），前端编辑时
  没传 sort_order 字段会被 setattr 成 None，导致执行链路（按 sort_order 排）
  把这些 case 排到第一位。

跑法（先 dry-run）：
    python -m database.migrations.data_migrations.repair_case_sort_order

确认无误后真跑：
    python -m database.migrations.data_migrations.repair_case_sort_order --commit

只按"module_id 分组 + id 升序"重号；用户原本的相对顺序如果靠 id 单调，那么
重号后顺序跟创建顺序一致。如果用户曾经显式调过位置（reorder API），重号会丢
那个位置 —— 这是一次性修复，可以接受。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="真的写库；默认 dry-run")
    parser.add_argument("--module-id", type=int, default=None,
                        help="只修某个模块下的用例")
    args = parser.parse_args()

    from database.db import DB
    from database.models import TestCase

    db = DB()
    session = db.session
    try:
        # 按 module 分组
        q = session.query(TestCase)
        if args.module_id is not None:
            q = q.filter(TestCase.module_id == args.module_id)
        cases = q.order_by(TestCase.module_id, TestCase.id).all()

        by_module: dict = {}
        for c in cases:
            by_module.setdefault(c.module_id, []).append(c)

        changed = 0
        for mid, items in by_module.items():
            for new_order, c in enumerate(items):
                if c.sort_order != new_order:
                    print(
                        f"  case[{c.id}] module={mid} {c.sort_order!r} → {new_order}"
                    )
                    if args.commit:
                        c.sort_order = new_order
                    changed += 1

        print(f"\nchanged: {changed}")
        if args.commit:
            db.commit()
            print("✅ 已提交")
        else:
            print("[DRY RUN] 没写库，去掉 dry-run 真跑：加 --commit")
            session.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
