"""AI 参数修复的闭环验证 + 多轮修复循环控制器。

单轮流程（round k）：
  等验证报告跑完 → 与上一轮报告按用例对比 → green→red / red→red 自动按事件回滚
  → 若仍有 red 且未到轮数上限：带着**修复后的新响应**（新证据）只对 still_red
    用例重新诊断（附 previous_attempt 说明上一轮做了什么/为何被拦），预检 + 应用
    → 派下一轮验证执行 → 自我派发 round k+1。

停止条件（满足其一）：
  - 没有 still_red 用例；
  - 到达 loop.max_rounds（默认 2）；
  - 新一轮诊断没有产出任何能通过预检的修复（没有新思路，继续无意义）。

结束时以**最初报告**为基线做总账，写 ai_run.output_payload["verify"]；
前端轮询 /api/ai/runs/{id}，看到 verify 出现即为闭环完成。
过程状态在 output_payload["rounds"] 里逐轮追加，可随时观察。
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from celery_app import celery_app
from utils.logger import LOGGER

# 每轮验证报告最多等 30 分钟；run_test_task 有 force_error_status 兜底，不会永远 running
_VERIFY_TIMEOUT_SEC = 30 * 60
_POLL_INTERVAL_SEC = 5


def _wait_report_done(session, report_id: int) -> str | None:
    """轮询到报告不再 running。返回最终 status；报告不存在返回 None。"""
    from database.models import TestReport

    deadline = time.monotonic() + _VERIFY_TIMEOUT_SEC
    status = None
    while time.monotonic() < deadline:
        # 结束当前读事务再查：SQLite/可重复读隔离下，长事务会一直看到旧快照，
        # 报告状态永远是 running 直到超时。此时任务还没有未提交写入，rollback 安全。
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        status = (
            session.query(TestReport.status)
            .filter(TestReport.id == report_id)
            .scalar()
        )
        if status is None or status != "running":
            return status
        time.sleep(_POLL_INTERVAL_SEC)
    return status  # 可能仍是 "running"（超时）


def _build_attempt_notes(round_no: int, round_entry: dict, still_red_ids: set[int]) -> dict[int, str]:
    """给下一轮诊断构造 previous_attempt：上一轮改了什么 / 为何被拦，避免模型原地打转。"""
    notes: dict[int, str] = {}
    for a in round_entry.get("applied") or []:
        cid = a.get("case_id")
        if cid not in still_red_ids:
            continue
        parts = "、".join(a.get("parts") or []) or "无"
        note = f"第{round_no}轮已应用修复（改动：{parts}），重跑后仍失败；本条 result 是修复后的最新执行结果。"
        dropped = [d.get("reason") for d in (a.get("dropped") or []) if d.get("reason")]
        if dropped:
            note += f" 上一轮部分建议被预检拦截：{'；'.join(dropped[:3])}。"
        notes[cid] = note
    for s in round_entry.get("skipped") or []:
        cid = s.get("case_id")
        if cid not in still_red_ids or cid in notes:
            continue
        reasons = "；".join((s.get("reasons") or [])[:3]) or "未给出可应用修复"
        notes[cid] = f"第{round_no}轮的修复建议未被应用（{reasons}），请修正原因后重新给 fix。"
    return notes


def _finalize(session, db, run, payload: dict, *, orig_report_id: int,
              final_report_id: int, note: str | None = None) -> dict:
    """多轮结束：按最初报告做总账，写 verify + 给用例打 AI 标记。"""
    from server.services.ai_fix_service import compute_final_summary

    rounds = payload.get("rounds") or []
    summary = compute_final_summary(
        session,
        orig_report_id=orig_report_id,
        final_report_id=final_report_id,
        rounds=rounds,
    )

    # ── 打标：诊断分类（首轮 items + 各轮增量诊断）× 修复结局 ──────────
    flags_stat = {}
    try:
        flags_stat = _write_case_flags(session, run, payload, summary, final_report_id)
    except Exception as exc:  # noqa: BLE001 —— 标记是提示层，失败不吞掉闭环结果
        LOGGER.error("[ai_fix_verify] 写 AI 标记失败（忽略）：%s", exc)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass

    payload["verify"] = {
        "status": "done",
        "verify_report_id": final_report_id,
        **summary,
        **({"note": note} if note else {}),
        **({"flags": flags_stat} if flags_stat else {}),
        "finished_at": datetime.now().isoformat(),
    }
    run.output_payload = payload
    db.commit()
    LOGGER.info(
        "[ai_fix_verify] finalize ai_run=%s rounds=%s fixed=%s still_red=%s rolled_back=%s",
        run.id, summary["rounds_used"], summary["fixed_count"],
        summary["still_red_count"], summary["rolled_back_count"],
    )
    return payload["verify"]


def _write_case_flags(session, run, payload: dict, summary: dict, final_report_id: int) -> dict:
    """把诊断结论落成用例标记。分类取"最后一次诊断到该用例"的结论。"""
    from server.services.ai_flag_service import (
        derive_outcomes_from_items,
        upsert_flags_from_outcomes,
    )

    rounds = payload.get("rounds") or []
    # 最新分类：首轮 items 打底，后续轮的增量诊断覆盖
    latest: dict[int, dict] = {}
    for item in payload.get("items") or []:
        try:
            latest[int(item.get("case_id"))] = item
        except (TypeError, ValueError):
            continue
    for rd in rounds:
        for item in rd.get("diagnosis") or []:
            try:
                latest[int(item.get("case_id"))] = item
            except (TypeError, ValueError):
                continue

    details = summary.get("details") or {}
    fixed_ids = {int(e["case_id"]) for e in details.get("fixed") or [] if e.get("case_id") is not None}
    still = {int(e["case_id"]) for e in details.get("still_red") or [] if e.get("case_id") is not None}

    kept_green_ids: set[int] = set()
    regressed_ids: set[int] = set()
    fix_rounds_by_case: dict[int, int] = {}
    skip_reasons_by_case: dict[int, list[str]] = {}
    for rd in rounds:
        v = rd.get("verify") or {}
        for e in (v.get("details") or {}).get("kept_green") or []:
            if e.get("case_id") is not None:
                kept_green_ids.add(int(e["case_id"]))
        for e in (v.get("details") or {}).get("regressed") or []:
            if e.get("case_id") is not None:
                regressed_ids.add(int(e["case_id"]))
        for a in rd.get("applied") or []:
            if a.get("case_id") is not None:
                cid = int(a["case_id"])
                fix_rounds_by_case[cid] = fix_rounds_by_case.get(cid, 0) + 1
        for s in rd.get("skipped") or []:
            if s.get("case_id") is not None:
                skip_reasons_by_case.setdefault(int(s["case_id"]), []).extend(s.get("reasons") or [])
    # 回归后被回滚又在后续轮修好的，以最终结局为准
    regressed_ids -= fixed_ids
    _ = still  # still_red 走 derive 的默认 manual_fix 分支

    outcomes = derive_outcomes_from_items(
        list(latest.values()),
        fixed_ids=fixed_ids,
        kept_green_ids=kept_green_ids,
        regressed_ids=regressed_ids,
        fix_rounds_by_case=fix_rounds_by_case,
        skip_reasons_by_case=skip_reasons_by_case,
    )
    stat = upsert_flags_from_outcomes(
        session, outcomes,
        ai_run_id=run.id,
        report_id=int((run.input_payload or {}).get("report_id") or final_report_id),
    )
    session.commit()
    return stat


@celery_app.task(name="tasks.verify_ai_fix")
def verify_ai_fix_task(
    ai_run_id: int,
    prev_report_id: int,
    verify_report_id: int,
    batch_id: int | None,
    round_no: int = 1,
) -> dict:
    from database.db import DB
    from database.models import AiRun
    from server.services.ai_fix_service import (
        apply_report_fixes,
        compare_and_rollback,
        prepare_verification_run,
        rollback_applied_fixes,
    )

    LOGGER.info(
        "[ai_fix_verify] round=%s ai_run=%s prev_report=%s verify_report=%s batch=%s",
        round_no, ai_run_id, prev_report_id, verify_report_id, batch_id,
    )
    db = DB()
    session = db.session
    try:
        run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
        if run is None:
            return {"status": "error", "message": "ai_run 不存在"}
        payload = dict(run.output_payload or {})
        rounds: list[dict] = list(payload.get("rounds") or [])
        entry = next((r for r in rounds if r.get("round") == round_no), None)
        if entry is None:
            entry = {"round": round_no, "applied": [], "skipped": [],
                     "batch_id": batch_id, "verify_report_id": verify_report_id}
            rounds.append(entry)
        applied = entry.get("applied") or []
        orig_report_id = int((run.input_payload or {}).get("report_id") or prev_report_id)
        max_rounds = int((payload.get("loop") or {}).get("max_rounds") or 2)

        # ── 1. 等本轮验证执行结束 ─────────────────────────────────
        report_status = _wait_report_done(session, verify_report_id)
        if report_status is None or report_status == "running":
            rollback_result = rollback_applied_fixes(
                session,
                batch_id=batch_id,
                applied=applied,
                reason="AI 修复验证超时或报告丢失，候选修改未获验证，自动回滚",
            )
            entry["status"] = "verify_timeout"
            entry["rolled_back_count"] = rollback_result.get("rolled_back", 0)
            entry["rollback_conflicts"] = rollback_result.get("conflicts") or []
            payload["rounds"] = rounds
            payload["verify"] = {
                "status": "timeout" if report_status == "running" else "report_missing",
                "verify_report_id": verify_report_id,
                "rounds_used": round_no,
                "message": "验证执行超时或报告丢失；未验证的候选修复已自动回滚",
                "rolled_back_count": rollback_result.get("rolled_back", 0),
                "rollback_conflicts": rollback_result.get("conflicts") or [],
                "finished_at": datetime.now().isoformat(),
            }
            run.output_payload = payload
            db.commit()
            return {"status": "timeout"}

        # ── 2. 与上一轮对比 + 绿变红自动回滚 ───────────────────────
        result = compare_and_rollback(
            session,
            orig_report_id=prev_report_id,
            verify_report_id=verify_report_id,
            batch_id=batch_id,
            applied=applied,
        )
        entry["status"] = "compared"
        entry["verify"] = {
            "fixed_count": len(result["fixed"]),
            "regressed_count": len(result["regressed"]),
            "still_red_count": len(result["still_red"]),
            "kept_green_count": len(result["kept_green"]),
            "rolled_back_count": result["rolled_back_count"],
            "collateral_regressed": result["collateral_regressed"],
            "rollback_conflicts": result["rollback_conflicts"],
            "details": {
                "fixed": result["fixed"],
                "regressed": result["regressed"],
                "still_red": result["still_red"],
                "kept_green": result["kept_green"],
            },
        }
        payload["rounds"] = rounds
        run.output_payload = payload
        db.commit()

        still_red_ids = {
            int(e["case_id"]) for e in result["still_red"] if e.get("case_id") is not None
        }

        # ── 3. 停止判定 ──────────────────────────────────────────
        if not still_red_ids:
            return {"status": "success",
                    **_finalize(session, db, run, payload,
                                orig_report_id=orig_report_id,
                                final_report_id=verify_report_id)}
        if round_no >= max_rounds:
            return {"status": "success",
                    **_finalize(session, db, run, payload,
                                orig_report_id=orig_report_id,
                                final_report_id=verify_report_id,
                                note=f"已到轮数上限（{max_rounds}），仍失败 {len(still_red_ids)} 条")}

        # ── 4. 下一轮：带新证据只重诊断 still_red 用例 ──────────────
        from server.api.functional_cases import diagnose_report_items
        from server.services.ai_model_service import get_ai_model
        from database.models import TestReport

        model_name = str((run.input_payload or {}).get("model_name") or "").strip()
        model_report = session.query(TestReport).filter(TestReport.id == verify_report_id).first()
        model_project_id = model_report.project_id if model_report else None
        cfg = get_ai_model(session, model_name, project_id=model_project_id) if model_name and model_project_id else None
        if cfg is None or not cfg.enabled:
            return {"status": "success",
                    **_finalize(session, db, run, payload,
                                orig_report_id=orig_report_id,
                                final_report_id=verify_report_id,
                                note=f"模型 {model_name!r} 不可用，停止多轮修复")}

        attempt_notes = _build_attempt_notes(round_no, entry, still_red_ids)
        try:
            diag = diagnose_report_items(
                session, verify_report_id, cfg,
                only_case_ids=still_red_ids,
                attempt_notes=attempt_notes,
            )
        except Exception as exc:  # noqa: BLE001 —— 再诊断失败不吞掉已有成果
            LOGGER.error("[ai_fix_verify] 第%s轮再诊断失败：%s", round_no + 1, exc)
            return {"status": "success",
                    **_finalize(session, db, run, payload,
                                orig_report_id=orig_report_id,
                                final_report_id=verify_report_id,
                                note=f"第{round_no + 1}轮再诊断失败（{exc}），保留前几轮成果")}

        next_apply = apply_report_fixes(session, verify_report_id, diag.get("items") or [])
        if not next_apply["applied"]:
            return {"status": "success",
                    **_finalize(session, db, run, payload,
                                orig_report_id=orig_report_id,
                                final_report_id=verify_report_id,
                                note="新一轮诊断没有产出能通过预检的修复，停止")}

        report = model_report
        prepared = prepare_verification_run(
            session,
            project_id=report.project_id,
            category=report.category,
            base_report_id=verify_report_id,
        ) if report and report.project_id is not None else None
        if prepared is None:
            return {"status": "success",
                    **_finalize(session, db, run, payload,
                                orig_report_id=orig_report_id,
                                final_report_id=verify_report_id,
                                note="无法装配下一轮验证执行，停止")}

        next_round = round_no + 1
        rounds.append({
            "round": next_round,
            "batch_id": next_apply["batch_id"],
            "applied": next_apply["applied"],
            "skipped": next_apply["skipped"],
            "base_report_id": verify_report_id,
            "verify_report_id": prepared["report_id"],
            "status": "verifying",
            # 本轮增量诊断结论（打标时按"最后一次诊断"覆盖首轮分类）
            "diagnosis": [
                {
                    "case_id": it.get("case_id"),
                    "name": it.get("name"),
                    "classification": it.get("classification"),
                    "findings": (it.get("findings") or [])[:6],
                }
                for it in (diag.get("items") or [])
            ],
        })
        payload["rounds"] = rounds
        run.output_payload = payload
        db.commit()   # 先落库再派发，worker 才能看到 report 行和轮次状态

        from tasks import run_test_task
        run_test_task.delay(
            prepared["task_id"], prepared["report_id"], prepared["cases_to_run"], report.category,
        )
        verify_ai_fix_task.delay(
            ai_run_id, verify_report_id, prepared["report_id"], next_apply["batch_id"], next_round,
        )
        LOGGER.info(
            "[ai_fix_verify] 第%s轮已派发：re-applied=%d verify_report=%s",
            next_round, len(next_apply["applied"]), prepared["report_id"],
        )
        return {"status": "next_round", "round": next_round}

    except Exception as exc:  # noqa: BLE001
        LOGGER.error("[ai_fix_verify] failed: %s", exc)
        traceback.print_exc()
        rollback_result = {"rolled_back": 0, "conflicts": []}
        try:
            from server.services.ai_fix_service import rollback_applied_fixes

            rollback_result = rollback_applied_fixes(
                session,
                batch_id=batch_id,
                applied=applied if "applied" in locals() else [],
                reason=f"AI 修复验证任务异常（{type(exc).__name__}），候选修改自动回滚",
            )
        except Exception as rollback_exc:  # noqa: BLE001
            LOGGER.error("[ai_fix_verify] 异常兜底回滚失败：%s", rollback_exc)
            rollback_result = {
                "rolled_back": 0,
                "conflicts": [],
                "error": str(rollback_exc),
            }
        try:
            run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
            if run is not None:
                payload = dict(run.output_payload or {})
                payload["verify"] = {
                    "status": "error",
                    "verify_report_id": verify_report_id,
                    "rounds_used": round_no,
                    "message": f"{type(exc).__name__}: {exc}"[:500],
                    "rolled_back_count": rollback_result.get("rolled_back", 0),
                    "rollback_conflicts": rollback_result.get("conflicts") or [],
                    "rollback_error": rollback_result.get("error"),
                    "finished_at": datetime.now().isoformat(),
                }
                run.output_payload = payload
                db.commit()
        except Exception as inner:  # noqa: BLE001
            LOGGER.error("[ai_fix_verify] 兜底写状态也失败：%s", inner)
        return {"status": "error", "message": str(exc)}
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
