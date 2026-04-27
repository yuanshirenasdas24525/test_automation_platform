"""数据迁移：app → android（默认）。

背景：v2.1 把 PROJECT_STACK_APP / CASE_TYPE_APP 从枚举里移除了，但 DB 里
可能还有 enabled_stacks 含 'app' 的项目、case_type='app' 的用例。直接读这些
旧值代码层面没问题（字段是 String / JSON 列），但前端 Tab 不会再渲染 App，
跑用例时 case_type='app' 也不在自动化集合里 —— 实际表现就是"用例消失了"。

这个脚本一次性把：
  1. 所有 projects.enabled_stacks 数组里的 'app' 改成 'android'（去重 + 保持其它栈不变）
  2. 所有 test_cases.case_type='app' 改成 'android'

如果你的项目里实际上是 iOS 用例，用 --target ios 跑；或者跑完后手动
UPDATE test_cases SET case_type='ios' WHERE id IN (...) 修正几条。

跑法：
    # 默认转成 android
    python -m database.migrations.data_migrations.app_to_android

    # 转成 ios
    python -m database.migrations.data_migrations.app_to_android --target ios

    # dry-run（只打印不写库）
    python -m database.migrations.data_migrations.app_to_android --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把项目根塞进 sys.path（python -m 执行时已自动加，python 直跑也兼容）
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="把 DB 里残留的 'app' 字面量改成 android / ios"
    )
    parser.add_argument(
        "--target",
        choices=["android", "ios"],
        default="android",
        help="把 app 改成哪个值（默认 android）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要改动的项，不真写库",
    )
    args = parser.parse_args()
    target = args.target
    dry = args.dry_run

    from database.db import DB
    from database.models import Project, TestCase

    db = DB()
    session = db.session
    try:
        # ----- 1. 项目 enabled_stacks 替换 -----
        projects = (
            session.query(Project)
            .filter(Project.enabled_stacks.is_not(None))
            .all()
        )
        proj_changed = 0
        for p in projects:
            stacks = list(p.enabled_stacks or [])
            if "app" not in stacks:
                continue
            new_stacks = []
            for s in stacks:
                v = target if s == "app" else s
                if v not in new_stacks:
                    new_stacks.append(v)
            print(
                f"  project[{p.id}] {p.name!r}: {stacks} → {new_stacks}"
            )
            if not dry:
                p.enabled_stacks = new_stacks
            proj_changed += 1
        print(f"projects affected: {proj_changed}")

        # ----- 2. 用例 case_type 替换 -----
        case_q = session.query(TestCase).filter(TestCase.case_type == "app")
        cases = case_q.all()
        for c in cases:
            print(f"  case[{c.id}] {c.name!r}: case_type=app → {target}")
        if not dry:
            case_q.update({TestCase.case_type: target}, synchronize_session=False)
        print(f"cases affected: {len(cases)}")

        if dry:
            print("\n[DRY RUN] 没有写库。去掉 --dry-run 真正执行。")
            session.rollback()
        else:
            db.commit()
            print("\n✅ 提交完成。前端 Tab 会立刻看到 android/ios（如果项目原本只启用了 app，现在改成了选定 target）。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
