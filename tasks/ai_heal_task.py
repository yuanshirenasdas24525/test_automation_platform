"""AI 自愈：执行完自动分诊 → 应用可算出的修复 → 重跑验证。

对应「AI 自愈运行」按钮。与普通运行的区别只在于跑完之后多做几步，
执行本身完全一样（同一条 v2 管道、同一份报告），所以自愈不会改变
"这次跑出来是红是绿"这个事实 —— 修复只对**用例定义**生效，且每一步都可回滚。

流程：
    报告落库
      → L1 规则分诊（零 token）
      → 规则能直接算出修复的：落成 AiRun 后走既有 apply 通道（预检 + 逐条编辑事件）
      → 指定了模型时：把 L1 判不了的送 LLM 深度诊断，再应用一次
      → verify 重跑：绿变红自动回滚（由 apply 链路自带）

安全边界：
  - 只改用例定义，不碰执行结果，也不重写历史报告；
  - 全部走 ai_fix_service 的预检 + batch 编辑事件，任何一条都能精准回滚；
  - 任何一步失败都只记日志，不影响原报告的结论。
"""
from __future__ import annotations

import logging

from celery_app import celery_app
from database.db import DB

LOGGER = logging.getLogger(__name__)


@celery_app.task(name="tasks.ai_heal_report")
def ai_heal_report_task(report_id: int, ai_model: str | None = None) -> dict:
    """对一份刚跑完的报告做自愈。返回摘要 dict（进 celery 结果，便于排查）。"""
    from database.models import (
        AiRun, TestReport, AI_FEATURE_API_REPORT_FIX, AI_RUN_STATUS_SUCCESS,
    )
    from server.services.ai_fix_service import apply_report_fixes
    from server.services.failure_triage import (
        as_diagnosis_items, triage_report, undetermined_case_ids,
    )
    from database.models import TestCase

    summary: dict = {"report_id": report_id, "rule_applied": 0, "llm_applied": 0}
    db = DB()
    try:
        report = db.session.query(TestReport).filter(TestReport.id == report_id).first()
        if report is None:
            return {**summary, "skipped": "报告不存在"}

        triage = triage_report(db.session, report_id)
        summary["total_failed"] = triage["total_failed"]
        summary["l1_triaged"] = triage["triaged"]
        if triage["total_failed"] == 0:
            return {**summary, "skipped": "没有失败用例，无需自愈"}

        # ---------- 第一步：规则能算出的修复（零 token） ----------
        done_ids = [
            c["case_id"] for c in triage["cases"]
            if c["case_id"] is not None and c["classification"] != "待定"
        ]
        module_ids = {
            c.id: c.module_id
            for c in db.session.query(TestCase).filter(TestCase.id.in_(done_ids or [0])).all()
        }
        items = as_diagnosis_items(triage, module_ids)
        fixable = [
            i for i in items
            if any(i["fix"].get(k) for k in ("extract", "assertion", "params"))
        ]
        if fixable:
            run = AiRun(
                feature=AI_FEATURE_API_REPORT_FIX,
                status=AI_RUN_STATUS_SUCCESS,
                project_id=report.project_id,
                input_payload={"report_id": report_id, "source": "ai_heal_rules"},
                output_payload={"items": items, "total": len(items), "source": "L1_triage"},
            )
            db.session.add(run)
            db.session.flush()
            applied = apply_report_fixes(db.session, report_id, items)
            db.session.commit()
            summary["rule_applied"] = len(applied.get("applied") or [])
            summary["rule_ai_run_id"] = run.id
            summary["verify_report_id"] = applied.get("verify_report_id")
            LOGGER.info(
                "[ai_heal] report=%s 规则修复应用 %d 条", report_id, summary["rule_applied"],
            )

        # ---------- 第二步：LLM 深度诊断（仅当显式指定了模型） ----------
        if ai_model:
            pending = undetermined_case_ids(triage)
            if not pending:
                summary["llm_skipped"] = "规则已全部定性，无需调用模型"
            else:
                from server.services.ai_model_service import get_ai_model
                from server.api.functional_cases import diagnose_report_items

                cfg = get_ai_model(db.session, ai_model, project_id=report.project_id)
                if cfg is None or not cfg.enabled:
                    summary["llm_skipped"] = f"模型 {ai_model!r} 未配置或未启用"
                else:
                    result = diagnose_report_items(
                        db.session, report_id, cfg, only_case_ids=pending,
                    )
                    llm_items = result.get("items") or []
                    run2 = AiRun(
                        feature=AI_FEATURE_API_REPORT_FIX,
                        status=AI_RUN_STATUS_SUCCESS,
                        project_id=report.project_id,
                        input_payload={"report_id": report_id, "source": "ai_heal_llm",
                                       "model_name": ai_model},
                        output_payload={"items": llm_items, "total": len(llm_items)},
                    )
                    db.session.add(run2)
                    db.session.flush()
                    applied2 = apply_report_fixes(db.session, report_id, llm_items)
                    db.session.commit()
                    summary["llm_applied"] = len(applied2.get("applied") or [])
                    summary["llm_ai_run_id"] = run2.id
                    summary["sent_to_llm"] = len(pending)
                    LOGGER.info(
                        "[ai_heal] report=%s LLM 诊断 %d 条，应用 %d 条",
                        report_id, len(pending), summary["llm_applied"],
                    )
    except Exception as exc:  # noqa: BLE001
        # 自愈是增强动作，失败绝不能影响原报告的结论
        LOGGER.warning("[ai_heal] report=%s 自愈失败（已忽略）: %s", report_id, exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        summary["error"] = str(exc)[:200]
    finally:
        db.close()
    return summary
