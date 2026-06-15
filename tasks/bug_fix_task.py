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


@celery_app.task(
    name="tasks.run_bug_fix_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
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

        # ── 5. 确保仓库已 clone（Agent 需要读取代码文件） ──────
        if has_git and gops:
            try:
                gops.ensure_clone()
            except Exception:
                LOGGER.warning(
                    "[bug_fix_task] clone 失败，降级为无 Git 模式", exc_info=True
                )
                has_git = False
                gops = None
                repo_dir = None

        # ── 6. 执行 Agent ─────────────────────────────────────────
        LOGGER.info(
            f"[bug_fix_task] executing agent={agent_name} has_git={has_git}"
        )

        result = agent.fix(bug=bug, repo_dir=repo_dir)

        # ── 7. 落地结果 ───────────────────────────────────────────
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

        # ── 8. 回写 AiRun ─────────────────────────────────────────
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

        # ValueError 是参数/校验错误，不重试
        if isinstance(exc, ValueError):
            retryable = False
        else:
            retryable = True

        if retryable and self.request.retries < self.max_retries:
            LOGGER.info(
                f"[bug_fix_task] 第 {self.request.retries + 1} 次重试 ai_run {ai_run_id}"
            )
            try:
                run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
                if run is not None:
                    run.status = AI_RUN_STATUS_RUNNING
                    run.error = f"重试 {self.request.retries + 1}/{self.max_retries}: {type(exc).__name__}"
                    db.commit()
            except Exception:
                pass
            raise self.retry(exc=exc)

        try:
            run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
            if run is not None:
                run.status = AI_RUN_STATUS_FAILED
                run.error = f"{type(exc).__name__}: {exc}"[:2000]
                run.ended_at = datetime.now()
                db.commit()
            # 把错误信息写到 Bug 的 fix_suggestion，前端详情页可见
            try:
                b = session.query(Task).filter(Task.id == bug_id).first()
                if b is not None and not b.fix_description:
                    b.fix_suggestion = f"AI 修复失败：{type(exc).__name__}: {exc}"[:2000]
                    b.fix_agent_used = agent_name
                    b.fix_ai_run_id = ai_run_id
                    db.commit()
            except Exception:
                pass
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

        # ── 清理 patch 残留 ───────────────────────────────────────
        _cleanup_patch_artifacts(gops.repo_dir)

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
    """把 unified diff 文本通过 patch -p1 应用到仓库。

    LLM 生成的 diff 常有格式瑕疵，先做清理再 apply。
    """
    import subprocess as sp

    if not diff_text.strip():
        return

    diff_text = _sanitize_diff(diff_text)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    ) as f:
        f.write(diff_text)
        diff_path = f.name

    try:
        proc = sp.run(
            [
                "patch", "-p1", "--no-backup-if-mismatch",
                "--fuzz=2", "--ignore-whitespace",
                "--reject-format=unified", "-i", diff_path,
            ],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        logger.info(
            f"[bug_fix_task] patch stdout: {proc.stdout[:500]}"
        )

        if proc.returncode != 0:
            stderr_summary = proc.stderr[:500]
            logger.error(
                f"[bug_fix_task] patch 失败 (code={proc.returncode}): {stderr_summary}"
            )
            _cleanup_patch_artifacts(repo_dir)
            raise RuntimeError(
                f"AI 生成的 diff 应用失败 (exit={proc.returncode}): {stderr_summary}"
            )

        rej_files = list(repo_dir.rglob("*.rej"))
        if rej_files:
            rej_paths = [str(f.relative_to(repo_dir)) for f in rej_files[:5]]
            _cleanup_patch_artifacts(repo_dir)
            raise RuntimeError(
                f"AI 生成的 diff 部分应用失败，以下文件有 rejected hunks: {', '.join(rej_paths)}"
            )
    finally:
        Path(diff_path).unlink(missing_ok=True)


def _sanitize_diff(diff_text: str) -> str:
    """修正 LLM 生成的 diff 中的常见格式问题。"""
    lines = diff_text.splitlines(keepends=True)
    cleaned: list[str] = []

    in_hunk = False
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n").rstrip("\r")

        # 跳过 diff 之前的解释文字（LLM 有时会先写一段说明）
        if not in_hunk and not stripped:
            continue

        # 检测 hunk header（@@ -x,y +a,b @@）
        if stripped.startswith("@@ ") and "@@" in stripped[3:]:
            in_hunk = True
            cleaned.append(line)
            continue

        # 检测文件头
        if stripped.startswith("--- ") or stripped.startswith("+++ "):
            cleaned.append(line)
            continue
        if stripped.startswith("diff --git "):
            cleaned.append(line)
            continue

        # 在 hunk 内的行：必须以 '+' / '-' / ' ' 开头
        if in_hunk and stripped and stripped[0] not in ("+", "-", " "):
            stripped = " " + stripped  # 缺前导空格的上下文行
            line = stripped + "\n"

        cleaned.append(line)

    result = "".join(cleaned)
    if not result.strip():
        return diff_text  # 清理失败，用原始文本
    return result


def _cleanup_patch_artifacts(repo_dir: Path) -> None:
    """删除 patch 产生的 .orig / .rej 文件。"""
    for pattern in ["*.orig", "*.rej"]:
        for f in repo_dir.rglob(pattern):
            try:
                f.unlink(missing_ok=True)
                logger.info(f"[bug_fix_task] 清理 patch 残留: {f}")
            except Exception:
                pass
