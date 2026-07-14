"""报告跑完后，自动从真实响应学习「响应结构约定」回流记忆层。

由 run_test_task 在 api 类报告 finalize 后异步派发。带节流（项目近 7 天学过就跳过），
失败绝不影响主流程——它是纯增强。
"""
from __future__ import annotations

import logging

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.learn_response_convention")
def learn_response_convention_task(report_id: int) -> dict:
    import sys
    from pathlib import Path
    _root = str(Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from database.db import DB
    from database.models import TestReport
    from server.services.ai_model_service import get_default_ai_model
    from server.services.response_convention import (
        collect_report_samples,
        distill_and_save,
        recently_learned,
    )

    db = DB()
    try:
        report = db.session.query(TestReport).filter(TestReport.id == report_id).first()
        if report is None or not report.project_id:
            return {"skipped": "报告不存在或缺 project_id"}
        project_id = report.project_id

        if recently_learned(db.session, project_id):
            return {"skipped": "近 7 天已学过，节流跳过", "project_id": project_id}

        samples = collect_report_samples(db.session, report_id)
        if not samples:
            return {"skipped": "报告无 http 样本", "project_id": project_id}

        cfg = get_default_ai_model(db.session, project_id)
        if cfg is None:
            return {"skipped": "项目无可用 AI 模型", "project_id": project_id}

        created = distill_and_save(
            db.session, project_id=project_id, samples=samples, cfg=cfg,
            source_file=f"report:{report_id}",
        )
        db.commit()
        return {"project_id": project_id, "learned": len(created)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[learn_convention] 失败（忽略）: %s", exc, exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"error": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass
