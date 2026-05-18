"""Git 配置请求 / 响应 schema —— AI Studio M1。

绑定到 ``projects`` 表的 git_url / git_auth_type / git_auth_secret_encrypted /
git_default_branch 四个新列。secret 走单向：写入时明文 → 后端 AES-256 加密
落库；读出时**永远不**返回明文（前端只看到 ``has_secret`` 布尔位）。
"""
from __future__ import annotations

from typing import Optional

import pydantic

from database.models.project import ALL_PROJECT_GIT_AUTH_TYPES, ALL_PROJECT_RAG_STATUSES


class GitConfigUpdate(pydantic.BaseModel):
    """PUT /api/projects/{id}/git-config 请求体。

    - ``git_url``：完整 HTTPS / SSH URL（``https://github.com/x/y.git`` 或
      ``git@github.com:x/y.git``）
    - ``auth_type``：``pat`` 或 ``ssh_key``；公开 repo 可传 None / 空串 → 走匿名
    - ``auth_secret``：凭证明文（PAT token / SSH 私钥 PEM）。**首次必填，
      后续可传 None 表示"不动现有密文"**（前端典型用法：只想改 default_branch）
    - ``default_branch``：默认分支，缺省 main
    """
    git_url: str
    auth_type: Optional[str] = None
    auth_secret: Optional[str] = None
    default_branch: Optional[str] = "main"

    @pydantic.field_validator("git_url")
    @classmethod
    def _validate_git_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("git_url 不能为空")
        # 简单 sanity check：要么 https 要么 ssh-like
        if not (v.startswith(("http://", "https://", "git@", "ssh://"))):
            raise ValueError(
                "git_url 必须以 http(s):// 或 git@host: 或 ssh:// 开头"
            )
        return v

    @pydantic.field_validator("auth_type")
    @classmethod
    def _validate_auth_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.strip().lower()
        if v not in ALL_PROJECT_GIT_AUTH_TYPES:
            raise ValueError(
                f"未知 auth_type: {v!r}（合法值：{sorted(ALL_PROJECT_GIT_AUTH_TYPES)}）"
            )
        return v

    @pydantic.field_validator("default_branch")
    @classmethod
    def _normalize_default_branch(cls, v: Optional[str]) -> str:
        v = (v or "").strip() or "main"
        return v


class GitConfigRead(pydantic.BaseModel):
    """GET /api/projects/{id}/git-config 响应。明文凭证永不出去。"""
    project_id: int
    git_url: Optional[str] = None
    default_branch: Optional[str] = None
    auth_type: Optional[str] = None
    has_secret: bool = False
    rag_index_status: Optional[str] = None
    rag_indexed_at: Optional[str] = None  # ISO8601 字符串

    @pydantic.field_validator("rag_index_status")
    @classmethod
    def _validate_rag_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in ALL_PROJECT_RAG_STATUSES:
            # 历史脏数据兜底：原样返出，不抛
            return v
        return v


class GitConfigTestResult(pydantic.BaseModel):
    """POST /api/projects/{id}/git-config/test 响应体。"""
    ok: bool
    ref_count: int = 0
    default_branch_found: bool = False
    sample_refs: list[str] = []   # 最多 5 条，给前端展示"看，我连上了"
    error: Optional[str] = None
