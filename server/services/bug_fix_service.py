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
import os
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
                            top_k=3,
                            hybrid=True,
                        )
                        if chunks:
                            # 只选最相关的少量 chunk，优先取源码文件
                            code_lines: list[str] = []
                            total_len = 0
                            MAX_CTX = 15000
                            for c in chunks:
                                block = (
                                    f"### {c.file_path}:{c.start_line}-{c.end_line}\n"
                                    f"```\n{c.content}\n```\n"
                                )
                                if total_len + len(block) > MAX_CTX:
                                    break
                                code_lines.append(block)
                                total_len += len(block)
                            user_input["CODE_CONTEXT"] = (
                                "## 相关代码\n\n" + "\n".join(code_lines)
                            )
                    finally:
                        db.close()
            except Exception:
                logger.warning("RAG 检索失败，跳过代码上下文", exc_info=True)

        # RAG 未命中时，通过关键词搜索 repo 文件内容作为备选上下文
        if (
            repo_dir is not None
            and user_input["CODE_CONTEXT"].startswith("（无代码上下文")
        ):
            try:
                context_text = _build_fallback_code_context(
                    repo_dir,
                    bug_title=bug.title,
                    bug_description=bug.description or "",
                )
                if context_text:
                    # 截断过长的备选上下文
                    if len(context_text) > 15000:
                        context_text = context_text[:15000] + "\n...（上下文过长，已截断）"
                    user_input["CODE_CONTEXT"] = context_text
            except Exception:
                logger.warning("构建备选代码上下文失败", exc_info=True)

        # 调 LLM（json_mode=False：避免 DeepSeek 在大 prompt 下返回空内容）
        res = chat_json(
            feature=AI_FEATURE_BUG_FIX,
            user_input=user_input,
            project_id=bug.requirement.project_id if bug.requirement else None,
            timeout=300,
            json_mode=False,
            analysis_mode="deep",
        )
        output = res.get("output") or {}

        fix_description = str(output.get("fix_description") or "")
        files_changed: list[str] = output.get("files_changed") or []
        changes: list[dict] = output.get("changes") or []
        diff_raw = output.get("diff") or ""

        # 新格式：从 changes 数组生成 unified diff
        if changes and repo_dir:
            diff_raw = _changes_to_diff(changes, repo_dir)
        elif not diff_raw and changes and not repo_dir:
            pass  # 无 Git 时 changes 描述在 fix_description 里

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


_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    "vendor", ".idea", ".vscode", "dist", "build", ".next",
    "target", ".gradle", ".mvn", "egg-info",
    ".angular", "coverage", ".nyc_output", "__snapshots__",
}

_CODE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte",
    ".py", ".java", ".kt", ".swift", ".go", ".rs",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
    ".css", ".scss", ".less", ".html", ".htm",
}


def _extract_keywords(text: str) -> list[str]:
    """从中文/英文混合文本中抽取搜索关键词。"""
    import re

    keywords: list[str] = []

    # 中文连续 2+ 字的关键短语
    cn_phrases = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for phrase in cn_phrases:
        # 滑动窗口 2-4 字
        for wlen in range(2, min(5, len(phrase) + 1)):
            for i in range(len(phrase) - wlen + 1):
                keywords.append(phrase[i:i + wlen])

    # 英文单词
    en_words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text)
    keywords.extend(w.lower() for w in en_words)

    # 去重 + 去停用词
    stop = {
        "功能", "问题", "进行", "需要", "可能", "应该", "可以", "没有", "已经",
        "或者", "但是", "因为", "所以", "如果", "这个", "那个", "什么", "怎么",
        "the", "and", "for", "are", "not", "but", "can", "has", "was",
        "from", "with", "that", "this", "will", "have", "been", "when", "its",
    }
    seen: set[str] = set()
    result: list[str] = []
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower not in stop and kw_lower not in seen:
            seen.add(kw_lower)
            result.append(kw)

    return result[:60]


def _build_fallback_code_context(
    repo_dir: Path,
    bug_title: str,
    bug_description: str,
    max_files: int = 8,
    max_file_bytes: int = 8000,
) -> str:
    """RAG 不可用时，从 repo_dir 搜索与 Bug 相关的文件内容。

    策略：
    1. 从 Bug 标题/描述中提取关键词
    2. 遍历 repo_dir 中的代码文件，按关键词命中数排序
    3. 将 top-N 文件的内容（截断）拼入上下文
    4. 附上项目文件树
    """
    keywords = _extract_keywords(f"{bug_title}\n{bug_description}")
    if not keywords:
        keywords = _extract_keywords("路由 页面 导航 返回 按钮 跳转")

    # ── 收集候选文件 ──
    candidate_scores: dict[str, int] = {}
    all_files: list[str] = []

    for root, dirs, files in os.walk(str(repo_dir)):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS)
        rel_root = os.path.relpath(root, str(repo_dir))

        for fname in sorted(files):
            if fname.startswith("."):
                continue
            rel_path = os.path.join(rel_root, fname) if rel_root != "." else fname
            _, ext = os.path.splitext(fname)

            if ext.lower() not in _CODE_EXTS:
                continue

            all_files.append(rel_path)

            # 文件名打分
            fname_lower = fname.lower()
            score = sum(1 for kw in keywords if kw in fname_lower) * 3

            # 文件内容前部搜索关键词（只读前 20KB）
            try:
                file_path = os.path.join(root, fname)
                size = os.path.getsize(file_path)
                if size > 500 * 1024:  # 跳过 >500KB 的文件
                    continue
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(50000)
                head_lower = head.lower()
                content_hits = sum(1 for kw in keywords if kw in head_lower)
                score += content_hits * 3
            except Exception:
                pass

            if score > 0:
                candidate_scores[rel_path] = score

    # ── 排序取 top-N ──
    ranked = sorted(candidate_scores.items(), key=lambda x: -x[1])[:max_files]

    # ── 构建上下文 ──
    parts: list[str] = []

    if ranked:
        parts.append("## 相关代码文件（通过关键词搜索匹配）\n")
        for i, (file_path, score) in enumerate(ranked, 1):
            full_path = repo_dir / file_path
            try:
                raw = full_path.read_text(encoding="utf-8", errors="ignore")
                if len(raw.encode("utf-8")) > max_file_bytes * 3:
                    lines = raw.split("\n")
                    truncated: list[str] = []
                    byte_count = 0
                    for line in lines:
                        lb = len(line.encode("utf-8")) + 1
                        if byte_count + lb > max_file_bytes * 2:
                            truncated.append("...（文件过长，已截断）")
                            break
                        truncated.append(line)
                        byte_count += lb
                    raw = "\n".join(truncated)
                elif len(raw.encode("utf-8")) > max_file_bytes:
                    raw = raw[:max_file_bytes] + "\n...（文件过长，已截断）"

                parts.append(
                    f"### [{i}] `{file_path}` (关键词匹配分={score})\n"
                    f"```{_lang_from_ext(file_path)}\n{raw}\n```\n"
                )
            except Exception:
                parts.append(f"### [{i}] `{file_path}` (无法读取)\n")
    else:
        parts.append("## 未找到匹配关键词的文件，以下是项目结构供参考\n")

    # ── 附上精简文件树 ──
    tree_lines: list[str] = []
    dir_count = 0
    file_count = 0
    for root, dirs, files in os.walk(str(repo_dir)):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS)
        rel = Path(root).relative_to(repo_dir)
        depth = len(rel.parts) if rel != Path(".") else 0
        if depth > 4:
            continue
        indent = "  " * depth
        dn = rel.name if rel != Path(".") else repo_dir.name
        if dn != ".":
            tree_lines.append(f"{indent}{dn}/")
            dir_count += 1
        for f in sorted(files)[:15]:
            if f.startswith("."):
                continue
            if len(tree_lines) >= 80:
                break
            tree_lines.append(f"{indent}  {f}")
            file_count += 1
        if len(tree_lines) >= 80:
            tree_lines.append("...（树已截断）")
            break

    parts.append(
        f"## 项目文件结构（{dir_count} 个目录、{file_count}+ 个文件）\n```\n"
        + "\n".join(tree_lines)
        + "\n```"
    )

    return "\n\n".join(parts)


def _lang_from_ext(file_path: str) -> str:
    """根据文件扩展名返回 markdown 代码块语言标识。"""
    ext = os.path.splitext(file_path)[1].lower()
    lang_map = {
        ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx",
        ".vue": "vue", ".py": "python", ".java": "java", ".kt": "kotlin",
        ".go": "go", ".rs": "rust", ".swift": "swift",
        ".c": "c", ".cpp": "cpp", ".h": "c", ".cs": "csharp",
        ".rb": "ruby", ".php": "php", ".css": "css", ".scss": "scss",
        ".html": "html", ".svelte": "svelte",
    }
    return lang_map.get(ext, "")


def _changes_to_diff(changes: list[dict], repo_dir: Path) -> str:
    """把 LLM 输出的结构化 changes 转换为 unified diff 文本。"""
    import difflib

    file_changes: dict[str, list[dict]] = {}
    for c in changes:
        fp = c.get("file", "")
        if not fp:
            continue
        file_changes.setdefault(fp, []).append(c)

    diff_parts: list[str] = []
    for fp, fp_changes in file_changes.items():
        full_path = repo_dir / fp
        if not full_path.exists():
            logger.warning(f"_changes_to_diff: 文件不存在 {fp}，跳过")
            continue
        try:
            original = full_path.read_text(encoding="utf-8")
        except Exception:
            logger.warning(f"_changes_to_diff: 读取 {fp} 失败，跳过")
            continue

        modified = original
        errors: list[str] = []
        for c in fp_changes:
            try:
                modified = _apply_change_text(c, modified)
            except ValueError as exc:
                errors.append(str(exc))
                continue

        if modified == original:
            if errors:
                logger.warning(f"_changes_to_diff: {fp} 部分改动失败: {'; '.join(errors)}")
            continue
        if errors:
            logger.warning(f"_changes_to_diff: {fp} 部分改动失败但继续: {'; '.join(errors)}")

        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                modified.splitlines(keepends=True),
                fromfile=f"a/{fp}",
                tofile=f"b/{fp}",
            )
        )
        if diff_lines:
            diff_parts.append("".join(diff_lines))

    return "\n".join(diff_parts)


def _apply_change_text(c: dict, text: str) -> str:
    """基于文本锚点的 change 应用。"""
    action = c.get("action", "")
    code = c.get("code", "")

    if action == "append":
        return text.rstrip("\n") + "\n" + code.rstrip("\n") + "\n"

    if action in ("insert_after", "insert_before"):
        anchor_key = "after_text" if action == "insert_after" else "before_text"
        anchor = c.get(anchor_key, "")
        if not anchor:
            raise ValueError(f"{action} 缺少 {anchor_key}")
        index = text.find(anchor)
        if index == -1:
            # 容错：移除末尾换行后重试
            stripped_anchor = anchor.rstrip("\n")
            index = text.find(stripped_anchor)
        if index == -1:
            raise ValueError(f"{action} 未找到匹配文本: {anchor[:50]}...")
        if action == "insert_after":
            insert_pos = index + len(anchor)
            if insert_pos < len(text) and text[insert_pos] == "\n":
                insert_pos += 1
        else:
            insert_pos = index
            if insert_pos > 0 and text[insert_pos - 1] != "\n":
                code = "\n" + code
        code = code.rstrip("\n") + "\n"
        return text[:insert_pos] + code + text[insert_pos:]

    if action == "replace":
        find_text = c.get("find_text", "")
        if not find_text:
            raise ValueError("replace 缺少 find_text")
        index = text.find(find_text)
        if index == -1:
            raise ValueError(f"replace 未找到匹配文本: {find_text[:50]}...")
        code = code.rstrip("\n") + "\n"
        return text[:index] + code + text[index + len(find_text):]

    return text


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
