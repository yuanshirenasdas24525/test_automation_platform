"""Git 配置业务层 —— AI Studio M1。

职责：
1. 读 / 写 ``projects`` 表 git_url / default_branch / auth_type /
   auth_secret_encrypted 四个字段；secret 走 ``utils.crypto`` 加密
2. 给前端"测连接"按钮提供 ``test_git_connectivity()``：构造 ``GitOps`` 跑
   ls-remote，返回 ref 概览 + 是否找到 default_branch
3. 不直接处理 RAG 索引（那一步由 Celery task 触发，见 Batch 3）

设计选择：
- secret 字段在 update 请求里传 None / 空串 ⇒ 不动现有密文（前端方便只改
  URL 或分支）；传非空 ⇒ 先加密、再覆盖
- 切换 auth_type（pat → ssh_key）时**强制要求**重新传 secret，避免老 PAT
  字节被当 SSH 私钥用
- 不在这里清理 ``data/coding_workspaces/<project_id>/``；凭证轮换后旧 workspace
  可以继续用（auth 走环境注入，clone 元信息无差），节省一次 re-clone
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from coding_agent.git_ops import CodingGitError, GitCreds, GitOps
from database.models import Project
from database.models.project import (
    ALL_PROJECT_GIT_AUTH_TYPES,
    PROJECT_GIT_AUTH_PAT,
    PROJECT_GIT_AUTH_SSH_KEY,
)
from utils.crypto import decrypt_secret, encrypt_secret


# ---------------------------------------------------------------------------
# 读 —— 永远不返回密文
# ---------------------------------------------------------------------------
def get_git_config(session: Session, project_id: int) -> dict:
    """返回脱敏 git config dict；project 不存在抛 ``LookupError``。"""
    project = session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise LookupError(f"项目 {project_id} 不存在")

    return {
        "project_id": project.id,
        "git_url": project.git_url,
        "default_branch": project.git_default_branch,
        "auth_type": project.git_auth_type,
        "has_secret": bool(project.git_auth_secret_encrypted),
        "rag_index_status": project.rag_index_status,
        "rag_indexed_at": (
            project.rag_indexed_at.isoformat() if project.rag_indexed_at else None
        ),
    }


# ---------------------------------------------------------------------------
# 写 —— secret 现场加密、传 None 不覆盖
# ---------------------------------------------------------------------------
def set_git_config(
    session: Session,
    project_id: int,
    *,
    git_url: str,
    default_branch: str,
    auth_type: Optional[str],
    auth_secret_plain: Optional[str],
) -> dict:
    """更新 git 配置。返回 ``get_git_config`` 同形态 dict。

    auth_secret_plain 规则：
      - None / "" ：保留现有密文不动（如果 auth_type 切换了 → 抛 ValueError，
        防止把老 PAT 当 SSH 私钥用）
      - 非空：加密落库
      - 显式想清密文 → 前端传 ``auth_type=None`` 即可，本函数会一并清掉密文
    """
    project = session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise LookupError(f"项目 {project_id} 不存在")

    if auth_type is not None and auth_type not in ALL_PROJECT_GIT_AUTH_TYPES:
        raise ValueError(
            f"未知 auth_type: {auth_type!r}"
            f"（合法值：{sorted(ALL_PROJECT_GIT_AUTH_TYPES)}）"
        )

    previous_auth_type = project.git_auth_type

    project.git_url = git_url
    project.git_default_branch = default_branch or "main"
    project.git_auth_type = auth_type

    if auth_type is None:
        # 显式清掉凭证
        project.git_auth_secret_encrypted = None
    elif auth_secret_plain:
        project.git_auth_secret_encrypted = encrypt_secret(auth_secret_plain)
    else:
        # 没传 secret，要求 auth_type 不能换
        if previous_auth_type != auth_type:
            raise ValueError(
                f"切换 auth_type 到 {auth_type!r} 时必须同时提供新的 auth_secret"
            )
        if not project.git_auth_secret_encrypted:
            raise ValueError(
                f"首次设置 auth_type={auth_type!r} 必须提供 auth_secret"
            )

    session.flush()
    return get_git_config(session, project_id)


# ---------------------------------------------------------------------------
# 测连接 —— 给前端的"立刻验一下"按钮
# ---------------------------------------------------------------------------
def _load_creds(project: Project) -> GitCreds:
    """从已加密字段构造 GitCreds；没配凭证返回匿名 creds。"""
    if not project.git_auth_type or not project.git_auth_secret_encrypted:
        return GitCreds(auth_type=None, secret=None)
    plain = decrypt_secret(project.git_auth_secret_encrypted)
    return GitCreds(auth_type=project.git_auth_type, secret=plain)


def _gitops_from_project(project: Project) -> GitOps:
    """构造一次性 GitOps；调用方用完即弃，secret 不长留。"""
    return GitOps(
        project_id=project.id,
        git_url=project.git_url,
        default_branch=project.git_default_branch or "main",
        creds=_load_creds(project),
    )


def test_git_connectivity(session: Session, project_id: int) -> dict:
    """跑一次 ``git ls-remote`` 当连通性测试。

    返回字典对应 ``GitConfigTestResult``，永远不抛 ``CodingGitError`` —— 错误
    经摘要后塞 ``error`` 字段，前端按 ``ok=False`` 渲染失败态即可。
    """
    project = session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise LookupError(f"项目 {project_id} 不存在")
    if not project.git_url:
        return {
            "ok": False,
            "ref_count": 0,
            "default_branch_found": False,
            "sample_refs": [],
            "error": "尚未配置 git_url",
        }

    gops = _gitops_from_project(project)
    try:
        refs = gops.ls_remote()
    except CodingGitError as exc:
        return {
            "ok": False,
            "ref_count": 0,
            "default_branch_found": False,
            "sample_refs": [],
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "ref_count": 0,
            "default_branch_found": False,
            "sample_refs": [],
            "error": f"未预期错误：{exc.__class__.__name__}: {exc}",
        }

    default_ref = f"refs/heads/{project.git_default_branch or 'main'}"
    return {
        "ok": True,
        "ref_count": len(refs),
        "default_branch_found": default_ref in refs,
        "sample_refs": sorted(refs.keys())[:5],
        "error": None,
    }


# ---------------------------------------------------------------------------
# 给 Celery / 编码任务用的便捷构造
# ---------------------------------------------------------------------------
def build_gitops_for_project(session: Session, project_id: int) -> GitOps:
    """编码任务起步时用：拿到一个带凭证的 GitOps 实例。

    secret 只在内存里短暂存在（保存在 GitCreds.secret）；GitOps 跑完一个 task
    后即丢弃。**不要把返回值缓存到模块级变量**。
    """
    project = session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise LookupError(f"项目 {project_id} 不存在")
    if not project.git_url:
        raise ValueError(f"项目 {project_id} 尚未配置 git_url")
    return _gitops_from_project(project)
