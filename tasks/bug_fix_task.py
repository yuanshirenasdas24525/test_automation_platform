"""AI Bug Fix Celery 异步任务。

流程：
  1. load AiRun + Bug
  2. 检查项目 Git 配置
  3. 有 Git：clone → temp_branch → 执行 Agent → commit → push → 更新 Bug
  4. 无 Git：执行 Agent → 写修复建议到 Bug metadata
  5. 回写 AiRun output_payload
"""
from __future__ import annotations

import logging
import re
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from celery_app import celery_app
from utils.logger import LOGGER

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.run_bug_fix_task", bind=True)
def run_bug_fix_task(self, ai_run_id: int) -> dict:
    """AI 修复 Bug 主任务。"""
    from database.db import DB
    from database.models import (
        AiRun,
        Task,
        AI_RUN_STATUS_RUNNING,
        AI_RUN_STATUS_SUCCESS,
        AI_RUN_STATUS_FAILED,
        TASK_STATUS_DEV_DONE,
    )
    from server.services.bug_fix_service import _find_agent

    LOGGER.info(f"[bug_fix_task] start ai_run_id={ai_run_id}")
    db = DB()
    session = db.session

    try:
        # ── 1. 加载 AiRun ─────────────────────────────────────────
        run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
        if run is None:
            LOGGER.error(f"[bug_fix_task] ai_run {ai_run_id} 不存在")
            return {"status": "error", "message": "ai_run not found"}

        run.status = AI_RUN_STATUS_RUNNING
        run.celery_task_id = self.request.id
        run.started_at = datetime.now()
        db.commit()

        # ── 2. 加载 Bug ───────────────────────────────────────────
        payload = run.input_payload or {}
        bug_id = payload.get("bug_id")
        agent_name = payload.get("agent_name") or "opencode"

        if not bug_id:
            raise ValueError("input_payload.bug_id 缺失")

        bug = session.query(Task).filter(Task.id == bug_id).first()
        if bug is None:
            raise ValueError(f"Bug {bug_id} 不存在")

        # ── 3. 查找 Agent ─────────────────────────────────────────
        agent = _find_agent(agent_name)
        if agent is None:
            raise ValueError(f"智能体 {agent_name!r} 不可用")

        # ── 4. 检查 Git 配置 ──────────────────────────────────────
        from server.services.git_config_service import build_gitops_for_project

        has_git = False
        gops = None
        repo_dir = None
        try:
            if run.project_id:
                gops = build_gitops_for_project(session, run.project_id)
                has_git = True
                repo_dir = gops.repo_dir
        except Exception:
            LOGGER.info("[bug_fix_task] 项目无 Git 配置，走纯文本建议模式")
            has_git = False
            gops = None
            repo_dir = None

        # ── 5. 执行 Agent ─────────────────────────────────────────
        LOGGER.info(
            f"[bug_fix_task] executing agent={agent_name} has_git={has_git}"
        )

        result = agent.fix(bug=bug, repo_dir=repo_dir)

        # ── 6. 落地结果 ───────────────────────────────────────────
        if has_git and gops and result.files_changed:
            # 有 Git 且有文件改动 → 应用 diff + commit + push
            _apply_and_push(bug, result, gops, session)
        elif has_git and gops:
            # 有 Git 但 Agent 没找到需要改的文件 → 写建议
            bug.fix_suggestion = result.fix_description or "AI 分析后未找到需要修改的文件"
            bug.fix_agent_used = agent_name
            bug.fix_ai_run_id = ai_run_id
            session.flush()
        else:
            # 无 Git → 写建议
            bug.fix_suggestion = result.suggestion or result.fix_description or "AI 已生成修复建议"
            bug.fix_agent_used = agent_name
            bug.fix_ai_run_id = ai_run_id
            session.flush()

        # ── 7. 回写 AiRun ─────────────────────────────────────────
        run.output_payload = {
            "bug_id": bug.id,
            "agent_name": agent_name,
            "has_git": has_git,
            "fix_description": result.fix_description,
            "files_changed": result.files_changed,
            "status": "success",
        }
        run.status = AI_RUN_STATUS_SUCCESS
        run.ended_at = datetime.now()
        db.commit()

        LOGGER.info(f"[bug_fix_task] ai_run {ai_run_id} success")
        return {"status": "success", "ai_run_id": ai_run_id}

    except Exception as exc:
        LOGGER.error(f"[bug_fix_task] ai_run {ai_run_id} failed: {exc}")
        traceback.print_exc()
        try:
            run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
            if run is not None:
                run.status = AI_RUN_STATUS_FAILED
                run.error = f"{type(exc).__name__}: {exc}"[:2000]
                run.ended_at = datetime.now()
                db.commit()
        except Exception as inner:
            LOGGER.error(f"[bug_fix_task] 兜底状态更新也失败：{inner}")
        return {"status": "error", "message": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 辅助：应用 diff → commit → push
# ---------------------------------------------------------------------------
def _apply_and_push(
    bug,
    result,
    gops,
    session,
) -> None:
    """把 AgentResult 的 diff 应用到仓库并 commit/push。"""
    from coding_agent.git_ops import _run as _git_run
    from database.models.task import TASK_STATUS_DEV_DONE

    # ── 确保仓库就绪 + 创建临时分支 ────────────────────────────────
    gops.ensure_clone()
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    branch_name = f"fix/bug-{bug.id}-{ts}"

    with gops.temp_branch(branch_name):
        # ── 应用 diff ─────────────────────────────────────────────
        if result.diff:
            _apply_diff(result.diff, gops.repo_dir)

        # ── 提交 ──────────────────────────────────────────────────
        commit_msg = f"fix: {bug.title[:72]}"
        commit_sha = gops.commit_all(
            message=commit_msg,
            author_name="AI Bug Fix",
            author_email="ai-bug-fix@local",
        )

        # ── 推送 ──────────────────────────────────────────────────
        try:
            gops.push(branch_name)
        except Exception:
            logger.warning(f"[bug_fix_task] push 失败，尝试继续", exc_info=True)

    # ── 更新 Bug 状态 ────────────────────────────────────────────
    bug.status = TASK_STATUS_DEV_DONE
    bug.fix_description = result.fix_description
    bug.fix_commit_sha = commit_sha
    bug.fix_commit_branch = branch_name
    bug.fix_suggestion = None
    bug.fix_agent_used = bug.fix_agent_used or "opencode"
    session.flush()


def _apply_diff(diff_text: str, repo_dir: Path) -> None:
    """把 unified diff 文本通过 patch -p1 应用到仓库。"""
    import subprocess as sp

    if not diff_text.strip():
        return

    # 写 diff 到临时文件
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    ) as f:
        f.write(diff_text)
        diff_path = f.name

    try:
        proc = sp.run(
            ["patch", "-p1", "-i", diff_path],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            logger.warning(
                f"[bug_fix_task] patch apply 非零退出 (code={proc.returncode}): "
                f"{proc.stderr[:500]}"
            )
        logger.info(f"[bug_fix_task] patch applied: {proc.stdout[:500]}")
    finally:
        Path(diff_path).unlink(missing_ok=True)
