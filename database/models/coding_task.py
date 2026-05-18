"""CodingTask —— "AI 下发给其他 AI 写代码" 的执行任务。

一个 requirement → N 个 coding_task（第 1 期始终 N=1，预留 parent_requirement_id +
sequence 给后期 Planner Agent 拆分子任务用）。

完整流程：
    queued → indexing → generating → applied
                 │             │
                 │             └─ failed（diff 解析失败 / apply 冲突 / 超 token）
                 │
    applied → committed（用户在 UI 上 accept 选中 hunks 后 commit 到临时分支）
    committed → pushed（用户点 push）
    任意 applied 之后用户也可走 rejected，临时分支被删

字段语义见 database/migrations/versions/20260514_0001_ai_studio_tables.py。
"""
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# 状态枚举
CODING_TASK_STATUS_QUEUED = "queued"             # 刚创建，等 worker
CODING_TASK_STATUS_INDEXING = "indexing"         # 跑 RAG 索引
CODING_TASK_STATUS_GENERATING = "generating"     # 调 LLM 拿 diff
CODING_TASK_STATUS_APPLIED = "applied"           # diff apply 到临时分支，等用户 review
CODING_TASK_STATUS_FAILED = "failed"             # 任意阶段失败
CODING_TASK_STATUS_COMMITTED = "committed"       # 用户 accept hunks → 临时分支 commit
CODING_TASK_STATUS_PUSHED = "pushed"             # 已 push 到远端
CODING_TASK_STATUS_REJECTED = "rejected"         # 用户主动放弃，临时分支被清理
ALL_CODING_TASK_STATUSES = {
    CODING_TASK_STATUS_QUEUED,
    CODING_TASK_STATUS_INDEXING,
    CODING_TASK_STATUS_GENERATING,
    CODING_TASK_STATUS_APPLIED,
    CODING_TASK_STATUS_FAILED,
    CODING_TASK_STATUS_COMMITTED,
    CODING_TASK_STATUS_PUSHED,
    CODING_TASK_STATUS_REJECTED,
}


class CodingTask(Base):
    __tablename__ = "coding_tasks"

    id = Column(Integer, primary_key=True, index=True)

    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 第 1 期始终 = requirement_id；后期 Planner 拆子任务时填父需求 id
    parent_requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 同 parent_requirement_id 下的顺序号，第 1 期恒 = 0
    sequence = Column(Integer, nullable=False, default=0, server_default="0")

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 这次编码用的 LLM
    model_name = Column(String(80), nullable=True)

    status = Column(
        String(20),
        nullable=False,
        default=CODING_TASK_STATUS_QUEUED,
        server_default=CODING_TASK_STATUS_QUEUED,
        index=True,
    )

    # apply 后的临时分支名（ai/req-{requirement_id}-{ts}）
    temp_branch = Column(String(200), nullable=True)

    # 完整 unified diff（用于 UI 渲染 + accept/reject 持久化）
    diff_blob = Column(Text, nullable=True)

    # 用户勾选的 hunks：[{file: "x.py", hunk_indices: [0, 2]}]
    accepted_hunks = Column(JSONType, nullable=True)

    error_message = Column(Text, nullable=True)

    # 关联 ai_runs（一次任务可能多次 LLM 调用 —— 第 1 期只记最后一次）
    ai_run_id = Column(Integer, ForeignKey("ai_runs.id"), nullable=True)
    # Celery task_id：取消 / 状态反查
    celery_task_id = Column(String(64), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    requirement = relationship(
        "Requirement",
        foreign_keys=[requirement_id],
    )
    parent_requirement = relationship(
        "Requirement",
        foreign_keys=[parent_requirement_id],
    )
    project = relationship("Project")
    ai_run = relationship("AiRun")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "parent_requirement_id": self.parent_requirement_id,
            "sequence": self.sequence,
            "project_id": self.project_id,
            "model_name": self.model_name,
            "status": self.status,
            "temp_branch": self.temp_branch,
            "diff_blob": self.diff_blob,
            "accepted_hunks": self.accepted_hunks or [],
            "error_message": self.error_message,
            "ai_run_id": self.ai_run_id,
            "celery_task_id": self.celery_task_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
