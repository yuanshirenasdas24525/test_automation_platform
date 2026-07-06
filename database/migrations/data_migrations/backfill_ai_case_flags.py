"""一次性回填：把已有的 AI 诊断结果（ai_runs.feature=api_report_fix）转成用例标记。

背景：
  ai_case_flags 标记功能上线前跑过的「AI 全面分析 / AI 修复参数并应用」结果都
  躺在 ai_runs.output_payload.items 里，列表上看不到。这个脚本把历史诊断结论
  按 unverified 口径回填成标记（接口问题→interface_defect、环境/其他→environment、
  用例问题→manual_fix、正常→清旧标），让列表立刻有标记，不用重新烧一次 AI。

  按 ai_run id 升序逐个回放，新诊断自然 supersede 旧标记——每条用例最终留下
  "最近一次诊断"的结论。之后新的闭环会照常覆盖这些回填标记。

跑法（先 dry-run 看数量）：
    python -m database.migrations.data_migrations.backfill_ai_case_flags

确认无误后真跑：
    python -m database.migrations.data_migrations.backfill_ai_case_flags --commit

可选：--days 30 只回放最近 30 天的诊断（默认 90）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="真的写库；默认 dry-run")
    parser.add_argument("--days", type=int, default=90, help="只回放最近 N 天的诊断（默认 90）")
    args = parser.parse_args()

    from database.db import DB
    from database.models import AiRun, AI_FEATURE_API_REPORT_FIX
    from server.services.ai_flag_service import (
        derive_outcomes_from_items,
        upsert_flags_from_outcomes,
    )

    db = DB()
    session = db.session
    cutoff = datetime.now() - timedelta(days=max(args.days, 1))
    runs = (
        session.query(AiRun)
        .filter(
            AiRun.feature == AI_FEATURE_API_REPORT_FIX,
            AiRun.status == "success",
            AiRun.created_at >= cutoff,
        )
        .order_by(AiRun.id.asc())   # 旧→新回放，新诊断 supersede 旧标记
        .all()
    )
    print(f"找到 {len(runs)} 个历史诊断（近 {args.days} 天，按 id 升序回放）")

    total_created = total_cleared = 0
    for run in runs:
        payload = run.output_payload or {}
        items = payload.get("items") or []
        if not items:
            continue
        applied_ids = {
            a.get("case_id")
            for a in ((payload.get("apply") or {}).get("applied") or [])
            if a.get("case_id") is not None
        }
        outcomes = derive_outcomes_from_items(items, unverified=True, applied_ids=applied_ids)
        flagged = sum(1 for o in outcomes if o["flag_type"])
        cleared = sum(1 for o in outcomes if o["flag_type"] is None)
        print(f"  ai_run #{run.id} report={int((run.input_payload or {}).get('report_id') or 0)} "
              f"→ 打标 {flagged}，清标 {cleared}")
        if args.commit:
            stat = upsert_flags_from_outcomes(
                session, outcomes,
                ai_run_id=run.id,
                report_id=int((run.input_payload or {}).get("report_id") or 0) or None,
            )
            total_created += stat["created"]
            total_cleared += stat["cleared"]

    if args.commit:
        db.commit()
        print(f"完成：实际创建 {total_created} 个标记，清除 {total_cleared} 个旧标记")
    else:
        print("dry-run 结束（未写库）。确认无误后加 --commit 真跑")
    db.close()


if __name__ == "__main__":
    main()
