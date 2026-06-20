from database.base import Base, JSONType
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship


# 项目支持的栈枚举。functional 是"人工功能用例"，没有自动化执行步骤。
# app 已废，跑 app_to_android 迁移脚本后剩下：
#   api / web / android / ios / functional
PROJECT_STACK_API = "api"
PROJECT_STACK_WEB = "web"
PROJECT_STACK_ANDROID = "android"
PROJECT_STACK_IOS = "ios"
PROJECT_STACK_FUNCTIONAL = "functional"
ALL_PROJECT_STACKS = {
    PROJECT_STACK_API,
    PROJECT_STACK_WEB,
    PROJECT_STACK_ANDROID,
    PROJECT_STACK_IOS,
    PROJECT_STACK_FUNCTIONAL,
}

# Git 凭证类型（AI Studio M1）
PROJECT_GIT_AUTH_PAT = "pat"          # GitHub Personal Access Token / GitLab token
PROJECT_GIT_AUTH_SSH_KEY = "ssh_key"  # base64(SSH private key)
ALL_PROJECT_GIT_AUTH_TYPES = {
    PROJECT_GIT_AUTH_PAT,
    PROJECT_GIT_AUTH_SSH_KEY,
}

# RAG 索引状态（AI Studio M1）
PROJECT_RAG_STATUS_PENDING = "pending"   # 还没建过
PROJECT_RAG_STATUS_RUNNING = "running"   # 索引中
PROJECT_RAG_STATUS_READY = "ready"       # 可用
PROJECT_RAG_STATUS_FAILED = "failed"     # 上次失败
ALL_PROJECT_RAG_STATUSES = {
    PROJECT_RAG_STATUS_PENDING,
    PROJECT_RAG_STATUS_RUNNING,
    PROJECT_RAG_STATUS_READY,
    PROJECT_RAG_STATUS_FAILED,
}


class Project(Base):
    """业务项目。

    重构后项目不再绑定单一栈 —— 一个"电商"项目可以同时启用 API / Web /
    App / 功能用例四个执行视角，模块树跨栈共享。`enabled_stacks` 决定
    项目详情页里出现哪几个 Tab。

    迁移历史：
      v1: type 列单字符串（"API"/"Web"/"Mobile"），一个项目=一个栈
      v2 (2026-04-27): 改为 enabled_stacks JSON 列；type 列已通过
                       proj_stk_000001 迁移 drop 掉。
    """
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    icon = Column(String)
    # JSON 数组，元素 ⊆ ALL_PROJECT_STACKS。校验在 API 层做（pydantic）。
    # server_default 给 '["api"]' 是为了让历史脚本/外部工具直接 INSERT
    # projects 不传 enabled_stacks 时不会炸；正常业务路径都会显式传值。
    enabled_stacks = Column(
        JSONType,
        nullable=False,
        default=lambda: [PROJECT_STACK_API],
        server_default='["api"]',
    )
    sort_order = Column(Integer, default=0)

    # -------------------- AI Studio M1：Git & RAG 配置 --------------------
    # 项目绑定的 Git 仓库 URL（编码任务会 clone / push 到这里）
    git_url = Column(String(500), nullable=True)
    git_default_branch = Column(
        String(80), nullable=True, server_default="main"
    )
    # 凭证类型，见 PROJECT_GIT_AUTH_* 常量
    git_auth_type = Column(String(20), nullable=True)
    # AES-256-GCM 加密的凭证密文（utils.crypto.encrypt_secret 产物）
    git_auth_secret_encrypted = Column(Text, nullable=True)

    # RAG 索引最后一次成功完成的时间
    rag_indexed_at = Column(DateTime, nullable=True)
    # RAG 索引当前状态，见 PROJECT_RAG_STATUS_* 常量
    rag_index_status = Column(
        String(20),
        nullable=True,
        server_default=PROJECT_RAG_STATUS_PENDING,
    )
    # ---------------------------------------------------------------------

    # -------------------- AI 项目概览（模块关联图谱） --------------------
    # AI 生成的项目概览，结构：
    #   {"summary": str,
    #    "modules": [{"name": str, "purpose": str}],
    #    "relations": [{"from": str, "to": str, "relation": str}]}
    # 给「按模块生成用例」时提供跨模块关联依据；前端项目页/AI 弹窗可预览。
    ai_overview = Column(JSONType, nullable=True)
    ai_overview_updated_at = Column(DateTime, nullable=True)
    # ---------------------------------------------------------------------

    modules = relationship("Module", back_populates="project", cascade="all, delete-orphan")
