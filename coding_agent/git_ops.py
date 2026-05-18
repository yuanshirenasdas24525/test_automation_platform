"""Git 操作封装 —— 给 AI 编码任务用。

设计原则：
1. 用 subprocess 直接调 ``git`` 二进制，不引 GitPython（轻量 / 错误清晰 /
   不依赖 PyGit2 编译）。
2. 凭证（PAT / SSH key）**全程明文不落盘**：
   - PAT 仅写入临时 ``GIT_ASKPASS`` 脚本，函数返回前删
   - SSH key 写到 ``NamedTemporaryFile(mode=0o600)``，try/finally 删
3. ``.git/config`` 里的 remote URL **永不含 token**：clone 用 askpass，clone 完
   再单独 push 也走 askpass —— 这样 ``git remote -v`` 不会泄露
4. 工作目录约定：``<project_root>/data/coding_workspaces/<project_id>/``，
   每个项目独占一份 working tree；多个 coding_task 共享，靠临时分支隔离
5. 所有错误统一抛 ``CodingGitError``，message 只含 stderr 摘要、**不含凭证**

不在本模块管的事：
- ``project.git_auth_secret_encrypted`` 的解密（由 ``server.services.git_config_service`` 干）
- 临时分支的命名规则（由 ``coding_task`` 流程决定）
- diff apply（由 ``coding_agent.diff.applier`` 干）
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# 异常 & 配置常量
# ---------------------------------------------------------------------------
class CodingGitError(RuntimeError):
    """Git 操作失败。

    ``stderr`` 会被截断为 1KB；调用方可直接落 ``coding_tasks.error_message``。
    """

    def __init__(self, op: str, returncode: int, stderr: str):
        truncated = (stderr or "").strip()[:1024]
        super().__init__(f"git {op} 失败 (rc={returncode}): {truncated}")
        self.op = op
        self.returncode = returncode
        self.stderr = truncated


# 工作目录根：相对项目根 ``data/coding_workspaces/``
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACES_ROOT = _PROJECT_ROOT / "data" / "coding_workspaces"


# ---------------------------------------------------------------------------
# 凭证类型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GitCreds:
    """已解密的 Git 凭证。

    - ``auth_type`` 为 ``"pat"``：``secret`` 是 token 明文，username 默认 ``x-access-token``
      （GitHub/GitLab 都支持这种凭证用户名）
    - ``auth_type`` 为 ``"ssh_key"``：``secret`` 是 SSH 私钥 PEM 内容（含换行）
    - ``auth_type`` 为 None：匿名（仅公共 repo / 只读操作可用）
    """
    auth_type: str | None
    secret: str | None
    username: str = "x-access-token"


# ---------------------------------------------------------------------------
# subprocess 包装
# ---------------------------------------------------------------------------
def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    op: str = "",
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """跑一条 git 命令；失败时抛 ``CodingGitError``（stderr 已脱敏长度）。"""
    full_env = os.environ.copy()
    # 永远关掉交互式 prompt —— 没有 askpass 时直接快速失败，而不是 hang
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if env:
        full_env.update(env)
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        raise CodingGitError(op or args[1] if len(args) > 1 else "?", proc.returncode, proc.stderr)
    return proc


# ---------------------------------------------------------------------------
# 凭证注入：askpass / SSH 私钥
# ---------------------------------------------------------------------------
@contextmanager
def _askpass_env(creds: GitCreds) -> Iterator[dict[str, str]]:
    """给 PAT 凭证准备 GIT_ASKPASS。

    git 在 https clone 时遇到要密码会调 ``GIT_ASKPASS`` 程序，把 prompt
    传 stdin、读 stdout 当答案。我们写一个一行 shell：把 token 直接 echo 出去。
    脚本只在 with 块内存在，块结束立刻删。

    生成 None 表示不需要 askpass（auth_type=None / ssh_key）—— 调用方
    直接用原 env 跑命令。
    """
    if creds.auth_type != "pat" or not creds.secret:
        yield {}
        return

    fd, script_path = tempfile.mkstemp(prefix="askpass_", suffix=".sh")
    try:
        # 脚本根据 prompt 关键字区分返用户名还是密码
        content = (
            "#!/bin/sh\n"
            'case "$1" in\n'
            f'  Username*) echo "{creds.username}" ;;\n'
            # 用 cat-secret-file 避免 token 出现在 ``ps`` 输出里
            "  *) cat $TOKEN_FILE ;;\n"
            "esac\n"
        )
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.chmod(script_path, stat.S_IRWXU)  # 0700

        # token 单独放一个临时文件，脚本里 ``cat $TOKEN_FILE``
        token_fd, token_path = tempfile.mkstemp(prefix="askpass_tok_")
        try:
            os.write(token_fd, creds.secret.encode("utf-8"))
            os.close(token_fd)
            os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            yield {
                "GIT_ASKPASS": script_path,
                "TOKEN_FILE": token_path,
                # 保险：禁用 macOS keychain / windows credential helper，不要让
                # 平台凭证被持久化
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        finally:
            try:
                os.unlink(token_path)
            except OSError:
                pass
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


@contextmanager
def _ssh_key_env(creds: GitCreds) -> Iterator[dict[str, str]]:
    """给 SSH key 凭证准备 GIT_SSH_COMMAND。"""
    if creds.auth_type != "ssh_key" or not creds.secret:
        yield {}
        return

    fd, key_path = tempfile.mkstemp(prefix="git_ssh_key_")
    try:
        os.write(fd, creds.secret.encode("utf-8"))
        # SSH 严格要求私钥换行结尾
        if not creds.secret.endswith("\n"):
            os.write(fd, b"\n")
        os.close(fd)
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600

        # StrictHostKeyChecking=accept-new：第一次连陌生主机不阻塞，但记下来
        # IdentitiesOnly=yes：只用我们提供的 key，忽略 agent
        ssh_command = (
            "ssh -i {key} "
            "-o StrictHostKeyChecking=accept-new "
            "-o IdentitiesOnly=yes "
            "-o UserKnownHostsFile=/dev/null "
            "-o LogLevel=ERROR"
        ).format(key=key_path)
        yield {"GIT_SSH_COMMAND": ssh_command}
    finally:
        try:
            os.unlink(key_path)
        except OSError:
            pass


@contextmanager
def _auth_env(creds: GitCreds) -> Iterator[dict[str, str]]:
    """根据 auth_type 选 askpass 或 ssh，返回需要注入的 env dict。"""
    if creds.auth_type == "pat":
        with _askpass_env(creds) as e:
            yield e
    elif creds.auth_type == "ssh_key":
        with _ssh_key_env(creds) as e:
            yield e
    else:
        yield {}


# ---------------------------------------------------------------------------
# 主 API：GitOps
# ---------------------------------------------------------------------------
@dataclass
class GitOps:
    """一个项目对应一个 GitOps 实例。

    生命周期：编码任务开始时构造、跑完销毁；不跨任务复用 —— 凭证 secret 留在
    内存最短时间。

    用法：
        creds = GitCreds(auth_type="pat", secret=decrypt_secret(p.git_auth_secret_encrypted))
        gops = GitOps(project_id=p.id, git_url=p.git_url,
                      default_branch=p.git_default_branch, creds=creds)
        gops.ensure_clone()
        with gops.temp_branch("ai/req-42-1747200000") as branch:
            (gops.repo_dir / "foo.py").write_text("...")
            gops.commit_all("AI: implement req-42")
            gops.push(branch)
    """
    project_id: int
    git_url: str
    default_branch: str = "main"
    creds: GitCreds = GitCreds(auth_type=None, secret=None)

    @property
    def repo_dir(self) -> Path:
        return WORKSPACES_ROOT / str(self.project_id)

    # ---------------- 远端连通性 ----------------
    def ls_remote(self) -> dict[str, str]:
        """返回 ``{ref: sha}``；用于 PUT /git-config 之后的"测一下"按钮。"""
        with _auth_env(self.creds) as env:
            proc = _run(
                ["git", "ls-remote", "--heads", self.git_url],
                env=env,
                op="ls-remote",
                timeout=30,
            )
        refs: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                refs[parts[1].strip()] = parts[0].strip()
        return refs

    # ---------------- 仓库初始化 / 同步 ----------------
    def ensure_clone(self) -> None:
        """没有 working tree 就 clone；已有就 fetch。

        clone 后 ``.git/config`` 里 remote URL 保持原 git_url（不带 token），后续
        push 仍走 askpass / SSH key 注入。
        """
        WORKSPACES_ROOT.mkdir(parents=True, exist_ok=True)
        if (self.repo_dir / ".git").is_dir():
            self.fetch()
            return

        with _auth_env(self.creds) as env:
            _run(
                ["git", "clone", "--depth", "50", self.git_url, str(self.repo_dir)],
                env=env,
                op="clone",
                timeout=600,
            )

    def fetch(self) -> None:
        with _auth_env(self.creds) as env:
            _run(
                ["git", "fetch", "--prune", "origin"],
                cwd=self.repo_dir,
                env=env,
                op="fetch",
                timeout=300,
            )

    def head_sha(self) -> str:
        proc = _run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            op="rev-parse",
        )
        return proc.stdout.strip()

    def checkout_default(self) -> None:
        """切回默认分支并 reset 到 origin/<default>，丢掉所有本地改动。"""
        _run(
            ["git", "checkout", self.default_branch],
            cwd=self.repo_dir,
            op="checkout-default",
        )
        _run(
            ["git", "reset", "--hard", f"origin/{self.default_branch}"],
            cwd=self.repo_dir,
            op="reset-default",
        )

    # ---------------- 临时分支 + commit ----------------
    @contextmanager
    def temp_branch(self, name: str) -> Iterator[str]:
        """基于 origin/<default_branch> 创建临时分支并 checkout。

        with 块结束**不**自动删分支 —— 让 coding_task 流程决定何时清理
        （reject / 老化扫描）。
        """
        # 先 sync 默认分支，再从最新点拉新分支
        self.fetch()
        _run(
            ["git", "checkout", "-B", name, f"origin/{self.default_branch}"],
            cwd=self.repo_dir,
            op="checkout-temp-branch",
        )
        try:
            yield name
        finally:
            # 不主动删 —— 让上层决定
            pass

    def commit_all(self, message: str, author_name: str = "AI Studio",
                   author_email: str = "ai-studio@local") -> str:
        """git add -A + commit；返回新 commit sha。空提交不抛错（兼容只删除文件场景）。"""
        _run(["git", "add", "-A"], cwd=self.repo_dir, op="add")
        # --allow-empty 兜底纯文件名改动 / 模式改动
        _run(
            [
                "git",
                "-c", f"user.name={author_name}",
                "-c", f"user.email={author_email}",
                "commit", "--allow-empty", "-m", message,
            ],
            cwd=self.repo_dir,
            op="commit",
        )
        return self.head_sha()

    def push(self, branch: str, force: bool = False) -> None:
        args = ["git", "push", "origin", branch]
        if force:
            args.insert(2, "--force-with-lease")  # 比 --force 安全
        with _auth_env(self.creds) as env:
            _run(args, cwd=self.repo_dir, env=env, op="push", timeout=300)

    def delete_branch(self, name: str) -> None:
        """删本地临时分支；远端要单独清。"""
        # 切到 default 再删，避免"can't delete checked-out branch"
        self.checkout_default()
        _run(
            ["git", "branch", "-D", name],
            cwd=self.repo_dir,
            op="branch-delete",
            check=False,  # 分支可能本来就不存在
        )

    # ---------------- 维护 ----------------
    def wipe_workspace(self) -> None:
        """彻底删 working tree —— 凭证轮换 / repo 切换时用。"""
        if self.repo_dir.exists():
            shutil.rmtree(self.repo_dir, ignore_errors=True)
