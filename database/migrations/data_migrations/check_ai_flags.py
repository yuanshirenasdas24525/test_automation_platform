"""AI 标记链路只读诊断：逐环检查，定位"看不到标记"卡在哪一环。

跑法：
    python -m database.migrations.data_migrations.check_ai_flags
    python -m database.migrations.data_migrations.check_ai_flags --module-id 12   # 加上正在看的模块

输出 5 个检查点：
  [1] alembic 版本 / ai_case_flags 表是否存在
  [2] 历史诊断（ai_runs.feature=api_report_fix）有多少、items 里的分类分布
  [3] ai_case_flags 行数（按 status / flag_type 分布）+ 样例
  [4] active 标记落在哪些模块（对照你正在看的模块）
  [5] 模拟列表接口查询：指定模块下有多少用例带 active 标记
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-id", type=int, default=None, help="正在查看的模块 id（第 5 步用）")
    args = parser.parse_args()

    from sqlalchemy import text
    from database.db import DB
    from database.models import AiRun, AI_FEATURE_API_REPORT_FIX, Module, TestCase

    db = DB()
    s = db.session

    # ── [1] 迁移 / 表 ────────────────────────────────────────
    print("=" * 60)
    try:
        ver = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print(f"[1] alembic 当前版本: {ver}   （应为 ai_case_flags_001）")
    except Exception as exc:  # noqa: BLE001
        print(f"[1] 读 alembic_version 失败：{exc}")
        s.rollback()
    try:
        n = s.execute(text("SELECT COUNT(*) FROM ai_case_flags")).scalar()
        print(f"[1] ai_case_flags 表存在，共 {n} 行")
    except Exception as exc:  # noqa: BLE001
        print(f"[1] ❌ ai_case_flags 表不存在或查询失败：{exc}")
        print("    → 说明 alembic upgrade head 没成功，或连的不是这个库；到此为止")
        return

    # ── [2] 历史诊断 ─────────────────────────────────────────
    print("=" * 60)
    runs = (
        s.query(AiRun)
        .filter(AiRun.feature == AI_FEATURE_API_REPORT_FIX)
        .order_by(AiRun.id.desc())
        .limit(10)
        .all()
    )
    total_runs = s.query(AiRun).filter(AiRun.feature == AI_FEATURE_API_REPORT_FIX).count()
    print(f"[2] ai_runs 里 feature=api_report_fix 共 {total_runs} 条（最近 10 条）：")
    if total_runs == 0:
        print("    ❌ 一条都没有 → 历史上从没跑过「AI 修复参数并应用」，回填自然是 0。")
        print("    → 对一份报告点「AI 全面分析 → AI 修复参数并应用」跑一次即可产生标记")
    for r in runs:
        items = (r.output_payload or {}).get("items") or []
        cls = Counter(str(i.get("classification") or "?") for i in items if isinstance(i, dict))
        print(f"    run#{r.id} status={r.status} created={r.created_at} "
              f"items={len(items)} 分类={dict(cls) or '无'}")

    # ── [3] 标记分布 ─────────────────────────────────────────
    print("=" * 60)
    from database.models import AiCaseFlag
    rows = s.query(AiCaseFlag).all()
    by_status = Counter(f.status for f in rows)
    by_type = Counter(f.flag_type for f in rows if f.status == "active")
    print(f"[3] ai_case_flags：按 status = {dict(by_status) or '空'}")
    print(f"    active 按类型 = {dict(by_type) or '无 active 标记'}")
    for f in rows[:8]:
        print(f"    flag#{f.id} case={f.case_id} module={f.module_id} "
              f"type={f.flag_type} status={f.status} run={f.source_ai_run_id}")

    # ── [4] active 标记在哪些模块 ─────────────────────────────
    print("=" * 60)
    actives = [f for f in rows if f.status == "active"]
    mod_ids = sorted({f.module_id for f in actives if f.module_id is not None})
    if mod_ids:
        names = dict(s.query(Module.id, Module.name).filter(Module.id.in_(mod_ids)).all())
        per_mod = Counter(f.module_id for f in actives)
        print("[4] active 标记分布在这些模块：")
        for mid in mod_ids:
            print(f"    module {mid}（{names.get(mid, '?')}）: {per_mod[mid]} 个")
        print("    → 如果你正在看的模块不在上面，说明标记在别的模块下")
    else:
        print("[4] 没有任何 active 标记")

    # ── [5] 模拟列表接口 ─────────────────────────────────────
    if args.module_id is not None:
        print("=" * 60)
        from server.services.ai_flag_service import get_active_flags
        case_ids = [cid for (cid,) in s.query(TestCase.id).filter(
            TestCase.module_id == args.module_id, TestCase.case_type == "api",
        ).all()]
        flags = get_active_flags(s, case_ids)
        print(f"[5] 模块 {args.module_id}: api 用例 {len(case_ids)} 条，其中 {len(flags)} 条带 active 标记")
        for cid, fl in list(flags.items())[:8]:
            print(f"    case {cid}: {fl['flag_type']}  findings={fl['findings'][:1]}")
        if case_ids and not flags:
            print("    → 该模块用例都没标记：要么回填时这些用例的诊断分类全是'正常'，"
                  "要么历史诊断根本不是这个模块的报告")
    db.close()


if __name__ == "__main__":
    main()
