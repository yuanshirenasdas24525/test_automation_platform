"""编辑历史快照清理任务。"""
from __future__ import annotations

from celery_app import celery_app
from database.db import DB
from server.services.edit_history_service import purge_expired_snapshots


@celery_app.task(name="tasks.edit_history_cleanup")
def edit_history_cleanup_task(limit: int = 1000) -> dict:
    """清理已过期的可回滚快照，保留审计记录。"""
    db = DB()
    try:
        purged = purge_expired_snapshots(db.session, limit=limit)
        db.commit()
        return {"purged": purged}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
