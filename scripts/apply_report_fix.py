"""手动应用一次 AI 报告修复（绕过 HTTP 鉴权），用于诊断 apply 链路 / 抢救已诊断结果。

用法（项目根目录）：
    venv/bin/python scripts/apply_report_fix.py <ai_run_id>

会打印预检明细：哪些用例放行、哪些被拦截及原因。放行的直接落库，
之后你可以重跑原报告看通过率变化。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main(ai_run_id: int) -> None:
    from database.db import DB
    from database.models import AiRun, AI_FEATURE_API_REPORT_FIX
    from server.services.ai_fix_service import apply_report_fixes, preflight_report_fixes

    db = DB()
    try:
        run = db.session.query(AiRun).filter(AiRun.id == ai_run_id).first()
        if run is None:
            print(f"❌ ai_run {ai_run_id} 不存在")
            return
        if run.feature != AI_FEATURE_API_REPORT_FIX:
            print(f"❌ ai_run {ai_run_id} feature={run.feature}，不是报告修复任务")
            return

        out = run.output_payload or {}
        if out.get("apply"):
            print("⚠️  该诊断结果已标记应用过（output_payload.apply 存在）")
        report_id = int((run.input_payload or {}).get("report_id") or 0)
        items = out.get("items") or []
        print(f"report_id={report_id}  诊断条目={len(items)}")

        # 先只跑预检看分布，不落库
        checked = preflight_report_fixes(db.session, report_id, items)
        eligible = [c for c in checked if c["eligible"]]
        print(f"\n== 预检结果：放行 {len(eligible)} / 共 {len(checked)} ==")
        for c in checked:
            if not c["eligible"]:
                reasons = "; ".join(d["reason"] for d in c.get("dropped", [])) or "(无 fix / case_id 缺失)"
                print(f"  拦截 case={c.get('case_id')} 《{(c.get('name') or '')[:40]}》：{reasons}")

        if not eligible:
            print("\n没有可应用的修复，未落库。")
            return

        confirm = input(f"\n确认把 {len(eligible)} 条修复落库？(y/N) ").strip().lower()
        if confirm != "y":
            print("已取消，未落库。")
            return

        result = apply_report_fixes(db.session, report_id, items, operator_id=None)
        db.commit()
        print(f"\n✅ 已落库 {len(result['applied'])} 条，拦截 {len(result['skipped'])} 条")
        print("下一步：到平台重跑该报告的用例，看通过率是否上升。")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: venv/bin/python scripts/apply_report_fix.py <ai_run_id>")
        sys.exit(1)
    main(int(sys.argv[1]))
