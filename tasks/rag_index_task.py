"""Celery 任务：对一个项目的 git repo 跑一次 RAG 索引。

链路：
    PUT /api/projects/{id}/git-config 成功 → rag_index_project_task.delay(project_id)
    或：前端"重新索引"按钮 → 同一任务

任务流程（每一步失败都能定位）：
    1. 把 ``projects.rag_index_status`` 改 ``running`` 并立刻 commit —— 让前端看见状态
    2. 拿到带凭证的 ``GitOps``，clone / fetch / checkout 默认分支
    3. ``head_sha()`` 取当前 commit
    4. ``scan_workspace`` 惰性产 chunk
    5. ``embed_and_persist`` 批量算 embedding + 落库（默认 replace_project_index=True，
       把同 project 之前 sha 的旧索引一把删，避免脏数据堆积）
    6. 成功 → status=ready, rag_indexed_at=now；失败 → status=failed

幂等性：
    - 同一 project 并发 .delay() 会拿到独立 session，二者都会去 clone / 改 status，
      最坏结果是两次重复索引；第 1 期 worker 并发限 1-2，实际不太容易撞。
    - 必要时改 SELECT FOR UPDATE 抢占；当前简化，避免引入 advisory lock 复杂度。

不在这里做：
    - 凭证解密（``server.services.git_config_service.build_gitops_for_project`` 干）
    - embedding 配置加载（``ai_gateway.embeddings.load_embedding_config`` 干）
    - chunk 切分规则（``coding_agent.rag.indexer`` 干）
"""
from __future__ import annotations

import logging
from datetime import datetime

from celery_app import celery_app
from coding_agent.rag.embedder import embed_and_persist
from coding_agent.rag.indexer import scan_workspace
from database.db import DB
from database.models.project import (
    PROJECT_RAG_STATUS_FAILED,
    PROJECT_RAG_STATUS_READY,
    PROJECT_RAG_STATUS_RUNNING,
    Project,
)
from server.services.git_config_service import build_gitops_for_project

logger = logging.getLogger(__name__)


def _set_status(project_id: int, status: str, *, mark_done: bool = False) -> None:
    """开一个独立短事务把状态写下去（独立于主索引事务，让 UI 能即时看到 running/failed）。"""
    db = DB()
    try:
        proj = db.session.query(Project).filter(Project.id == project_id).first()
        if proj is None:
            logger.warning("rag_index: 写 status 时项目 %s 已不存在，忽略", project_id)
            return
        proj.rag_index_status = status
        if mark_done:
            proj.rag_indexed_at = datetime.now()
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        raise
    finally:
        db.close()


@celery_app.task(name="tasks.rag_index_project", bind=True)
def rag_index_project_task(self, project_id: int) -> dict:
    """对一个项目的 git repo 跑一次完整 RAG 索引。

    返回小摘要 dict 给 celery flower / 日志看；主要数据写入 ``code_chunks``。
    """
    logger.info("rag_index: start project_id=%s task_id=%s", project_id, self.request.id)
    _set_status(project_id, PROJECT_RAG_STATUS_RUNNING)

    db = DB()
    try:
        # --- 1. 准备 git working tree ---
        gops = build_gitops_for_project(db.session, project_id)
        # ensure_clone / fetch / checkout_default 之间不需要 session
        gops.ensure_clone()
        gops.checkout_default()
        sha = gops.head_sha()
        repo_dir = gops.repo_dir
        logger.info(
            "rag_index: project_id=%s checked out %s @ %s",
            project_id, gops.default_branch, sha[:8],
        )

        # --- 2. 扫文件 → 切 chunk ---
        chunks = list(scan_workspace(repo_dir))
        logger.info(
            "rag_index: project_id=%s sha=%s 扫到 %d 个 chunk",
            project_id, sha[:8], len(chunks),
        )

        # --- 3. 调 embedding 并落库 ---
        # 用一个新 session 跑索引主事务；上面的 gops session 我们已经"用完"了
        # （它只用于读 Project 行解密凭证），不强求复用
        index_session = DB()
        try:
            stats = embed_and_persist(
                index_session.session,
                project_id=project_id,
                git_sha=sha,
                chunks=chunks,
            )
            index_session.session.commit()
        except Exception:  # noqa: BLE001
            index_session.session.rollback()
            raise
        finally:
            index_session.close()

        # --- 4. 标 ready ---
        _set_status(project_id, PROJECT_RAG_STATUS_READY, mark_done=True)

        summary = {
            "project_id": project_id,
            "git_sha": sha,
            "chunks_total": stats.chunks_total,
            "chunks_indexed": stats.chunks_indexed,
            "batches_total": stats.batches_total,
            "batches_failed": stats.batches_failed,
            "tokens_used": stats.tokens_used,
        }
        logger.info("rag_index: done %s", summary)
        return summary

    except Exception as exc:  # noqa: BLE001
        logger.exception("rag_index: project_id=%s 失败 — %s", project_id, exc)
        try:
            _set_status(project_id, PROJECT_RAG_STATUS_FAILED)
        except Exception:  # noqa: BLE001
            logger.exception("rag_index: 标 failed 状态也失败了 project_id=%s", project_id)
        # 不抛回去 —— Celery EAGER 模式下会让调用方 PUT /git-config 整个 500
        # 走异步模式时也没意义重试（凭证 / repo 问题不会自己好），直接结束
        return {
            "project_id": project_id,
            "ok": False,
            "error": str(exc)[:500],
        }
    finally:
        db.close()
