"""AI Bug Fix 核心服务。

Agent 抽象 + GitOps 编排 + Bug 状态更新。

设计：
  - LLM Agent（主路径）：ai_gateway 生成 diff → patch apply → git commit/push
  - CLI Agent（可选）：subprocess 执行外部 CLI 工具（Codex / Claude Code / OpenCode）
  - CLI Agent 通过 ``shutil.which`` 自动检测可用性
  - 无 Git 的项目仅生成修复建议文本，不修改代码
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from database.models import (
    AiRun,
    Task,
    AI_FEATURE_BUG_FIX,
    AI_RUN_STATUS_PENDING,
    TASK_TYPE_BUG,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent 抽象
# ---------------------------------------------------------------------------
@dataclass
class AgentResult:
    """Agent 执行结果。"""
    fix_description: str
    files_changed: list[str] = field(default_factory=list)
    diff: str = ""
    suggestion: str = ""  # 无 Git 时的纯文本建议


class BugFixAgent(Protocol):
    """Bug 修复智能体接口。

    实现可以是 LLM 调用（走 ai_gateway）或 CLI 子进程。
    """

    name: str
    agent_type: str  # "llm" | "cli"

    def fix(self, bug: Task, repo_dir: Path | None) -> AgentResult:
        """执行修复。

        bug: Task 对象（type=bug）
        repo_dir: Git 仓库工作目录；为 None 时表示无 Git
        """


# ---------------------------------------------------------------------------
# 内置 LLM Agent（主路径，Docker + 本地都可用）
# ---------------------------------------------------------------------------
@dataclass
class LlmBugFixAgent:
    """通过 ai_gateway.chat_json 调用 LLM 生成 unified diff 修复。"""
    name: str = "opencode"
    agent_type: str = "llm"

    def fix(self, bug: Task, repo_dir: Path | None) -> AgentResult:
        from ai_gateway import chat_json
        from database.models import Requirement

        # 构建 Bug 上下文
        reproduce_steps = ""
        if bug.task_metadata and isinstance(bug.task_metadata, dict):
            steps = bug.task_metadata.get("reproduce_steps", "")
            if steps:
                reproduce_steps = str(steps)

        user_input: dict[str, str] = {
            "BUG_TITLE": bug.title,
            "BUG_SEVERITY": bug.severity or "P2",
            "BUG_DESCRIPTION": bug.description or "（无描述）",
            "BUG_REPRODUCE_STEPS": reproduce_steps or "（无复现步骤）",
            "CODE_CONTEXT": "（无代码上下文 —— 项目未配置 Git 仓库或 RAG 未索引）",
        }

        # 如果有 Git 仓库，尝试 RAG 检索相关代码
        if repo_dir is not None and bug.requirement:
            try:
                from coding_agent.rag.retriever import retrieve_relevant
                from database.db import DB

                project_id = bug.requirement.project_id if bug.requirement else None
                if project_id:
                    db = DB()
                    try:
                        query = f"Bug: {bug.title}\n{bug.description or ''}"
                        chunks = retrieve_relevant(
                            db.session,
                            project_id,
                            query,
                            top_k=8,
                        )
                        if chunks:
                            code_lines: list[str] = []
                            for c in chunks:
                                code_lines.append(
                                    f"### {c.file_path}:{c.start_line}-{c.end_line} "
                                    f"(score={c.score:.3f})\n```\n{c.content}\n```"
                                )
                            user_input["CODE_CONTEXT"] = (
                                "## 相关代码（RAG 检索，仅供参考）\n\n"
                                + "\n\n".join(code_lines)
                            )
                    finally:
                        db.close()
            except Exception:
                logger.warning("RAG 检索失败，跳过代码上下文", exc_info=True)

        # 调 LLM
        res = chat_json(
            feature=AI_FEATURE_BUG_FIX,
            user_input=user_input,
            project_id=bug.requirement.project_id if bug.requirement else None,
            timeout=180,
        )
        output = res.get("output") or {}

        fix_description = str(output.get("fix_description") or "")
        files_changed: list[str] = output.get("files_changed") or []
        diff_raw = output.get("diff") or ""

        return AgentResult(
            fix_description=fix_description,
            files_changed=files_changed,
            diff=diff_raw,
            suggestion=fix_description if not repo_dir else "",
        )


# ---------------------------------------------------------------------------
# CLI Agent（可选，需工具已安装）
# ---------------------------------------------------------------------------
@dataclass
class CliBugFixAgent:
    """通过 subprocess 调用外部 CLI 工具修复 Bug。

    CLI 工具被调到 repo_dir 下运行，直接修改文件；
    平台随后负责 git add / commit / push。
    """
    name: str
    agent_type: str = "cli"
    command: str = ""       # e.g. "claude -p '{{prompt}}'"
    check_cmd: str = ""     # e.g. "which claude"

    @property
    def available(self) -> bool:
        if not self.check_cmd:
            return False
        parts = self.check_cmd.split()
        return shutil.which(parts[0]) is not None

    def fix(self, bug: Task, repo_dir: Path | None) -> AgentResult:
        if repo_dir is None:
            return AgentResult(
                fix_description="CLI Agent 需要 Git 仓库才能运行",
                suggestion="请在项目设置中配置 Git 仓库",
            )

        prompt = self._build_prompt(bug)
        cmd = self.command.replace("{{prompt}}", prompt)
        logger.info(f"[bug_fix] running CLI agent: {cmd[:200]}...")

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                fix_description=f"CLI Agent {self.name} 执行超时",
            )

        description = result.stdout.strip()[:2000] or result.stderr.strip()[:2000] or "CLI Agent 执行完成"

        return AgentResult(
            fix_description=description,
            files_changed=[],  # CLI 改了什么不好追踪
            diff="",
        )

    def _build_prompt(self, bug: Task) -> str:
        lines = [
            f"请修复以下 Bug：{bug.title}",
        ]
        if bug.description:
            lines.append(f"\n描述：{bug.description}")
        if bug.task_metadata and isinstance(bug.task_metadata, dict):
            steps = bug.task_metadata.get("reproduce_steps")
            if steps:
                lines.append(f"\n复现步骤：{steps}")
        lines.append("\n请在当前目录下修改相关文件，完成后说明修改了哪些文件和原因。")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent 注册表
# ---------------------------------------------------------------------------
@dataclass
class AgentConfig:
    """前端可用的智能体配置。"""
    name: str
    agent_type: str
    available: bool
    label: str
    description: str


_BUILTIN_AGENTS: list[BugFixAgent] = [
    LlmBugFixAgent(name="opencode"),
    CliBugFixAgent(
        name="codex",
        command="codex exec '{{prompt}}'",
        check_cmd="which codex",
    ),
    CliBugFixAgent(
        name="claude",
        command="claude -p '{{prompt}}'",
        check_cmd="which claude",
    ),
]


def get_available_agents() -> list[AgentConfig]:
    """返回所有已注册 + 检测可用性的智能体。"""

    def _label(name: str) -> str:
        labels = {
            "opencode": "OpenCode（平台内置 LLM）",
            "codex": "Codex（OpenAI CLI）",
            "claude": "Claude Code（Anthropic CLI）",
        }
        return labels.get(name, name)

    def _desc(name: str, agent_type: str) -> str:
        if agent_type == "llm":
            return "通过 AI Gateway 调用大模型生成统一 diff 修复，Docker 和本地都可用"
        return "通过 subprocess 调用外部 CLI 工具，需要工具已安装在 PATH 上"

    configs: list[AgentConfig] = []
    for agent in _BUILTIN_AGENTS:
        available = True
        if isinstance(agent, CliBugFixAgent):
            available = agent.available
        configs.append(AgentConfig(
            name=agent.name,
            agent_type=agent.agent_type,
            available=available,
            label=_label(agent.name),
            description=_desc(agent.name, agent.agent_type),
        ))
    return configs


def _find_agent(name: str) -> BugFixAgent | None:
    """按名称查找智能体。"""
    for agent in _BUILTIN_AGENTS:
        if agent.name == name:
            if isinstance(agent, CliBugFixAgent) and not agent.available:
                return None
            return agent
    return None


# ---------------------------------------------------------------------------
# 主流程：执行修复
# ---------------------------------------------------------------------------
def execute_bug_fix(
    *,
    bug: Task,
    agent_name: str,
    project_id: int,
    git_url: str | None,
    operator: str | None = None,
) -> int:
    """执行 Bug 修复的同步入口（API 层调用，创建 AiRun 后返回）。

    返回 ai_run_id 供前端轮询。
    """
    from database.db import DB

    db = DB()

    # 创建 AiRun
    run = AiRun(
        feature=AI_FEATURE_BUG_FIX,
        status=AI_RUN_STATUS_PENDING,
        project_id=project_id,
        input_payload={
            "bug_id": bug.id,
            "bug_title": bug.title,
            "agent_name": agent_name,
        },
        operator=operator,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    # 派发 Celery 任务
    from tasks.bug_fix_task import run_bug_fix_task

    async_result = run_bug_fix_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    db.close()
    return run.id


# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------
def rollback_bug_fix(bug: Task, git_url: str | None) -> dict:
    """回滚 AI 修复：删远程分支 + 恢复 Bug 状态。

    返回 {ok, message, ...}
    """
    from database.db import DB
    from coding_agent.git_ops import GitOps, GitCreds
    from server.services.git_config_service import build_gitops_for_project

    fix_branch = bug.fix_commit_branch
    if not fix_branch:
        return {"ok": False, "message": "该 Bug 没有关联的修复分支"}

    db = DB()

    if git_url and bug.fix_ai_run_id:
        # 尝试删远程分支
        try:
            gops = build_gitops_for_project(
                db.session,
                bug.requirement.project_id if bug.requirement else None,
            )
        except Exception:
            db.close()
            return {"ok": False, "message": "无法构造 GitOps，请检查项目 Git 配置"}

        try:
            # 删远程分支
            from coding_agent.git_ops import _auth_env
            import subprocess as sp
            from coding_agent.git_ops import _run as _git_run

            with _auth_env(gops.creds) as env:
                _git_run(
                    ["git", "push", "origin", "--delete", fix_branch],
                    cwd=gops.repo_dir,
                    env=env,
                    op="push-delete-branch",
                    timeout=60,
                    check=False,
                )
        except Exception:
            logger.warning(f"删除远程分支 {fix_branch} 失败", exc_info=True)

        # 删本地分支
        try:
            gops.delete_branch(fix_branch)
        except Exception:
            logger.warning(f"删除本地分支 {fix_branch} 失败", exc_info=True)

    # 恢复 Bug 状态
    from database.models.task import TASK_STATUS_DEV_DOING
    bug.status = TASK_STATUS_DEV_DOING
    bug.fix_description = None
    bug.fix_commit_sha = None
    bug.fix_commit_branch = None
    bug.fix_suggestion = None
    bug.fix_agent_used = None
    bug.fix_ai_run_id = None
    db.session.flush()
    db.commit()
    db.close()

    return {"ok": True, "message": f"已回滚修复，分支 {fix_branch} 已删除"}
