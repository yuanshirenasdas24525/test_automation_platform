"""需求点（Requirement）—— AI 需求分析的产物，也是后续测试计划 / 用例生成的输入。

设计：
  - 一个项目 N 条需求；可手工建，也可由 AI 解析 PRD/user story 批量生成
  - 每条需求带 acceptance_criteria（验收标准列表）—— 后续 AI 生成 functional 用例
    时直接拿这个当 prompt input
  - 状态字段：draft / approved / archived，控制是否进入计划 / 用例生成范围
  - source 字段：标记是 manual 还是 ai_generated（AI 生成的可让用户回头审）
  - ai_run_id：可选，关联到生成它的那次 AI 任务，便于追溯 / 重新生成
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# 状态枚举
REQUIREMENT_STATUS_DRAFT = "draft"        # 草稿（AI 刚生成 / 用户刚建）
REQUIREMENT_STATUS_APPROVED = "approved"  # 评审通过，可纳入计划 / 用例
REQUIREMENT_STATUS_ARCHIVED = "archived"  # 弃用，但保留历史
ALL_REQUIREMENT_STATUSES = {
    REQUIREMENT_STATUS_DRAFT,
    REQUIREMENT_STATUS_APPROVED,
    REQUIREMENT_STATUS_ARCHIVED,
}

# system_status —— approved..ready_to_release + pm_review 由 Task 状态聚合自动算
# （M5：pm_review 改为 task-driven —— 见 TASK_TYPE_PM_REVIEW）；只有 done 属 PM 推进域，
# task_service 不会覆盖 done 态。
REQUIREMENT_SYSTEM_STATUS_APPROVED = "approved"
REQUIREMENT_SYSTEM_STATUS_DEVELOPING = "developing"
REQUIREMENT_SYSTEM_STATUS_TESTING = "testing"
REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE = "ready_to_release"
REQUIREMENT_SYSTEM_STATUS_PM_REVIEW = "pm_review"
REQUIREMENT_SYSTEM_STATUS_DONE = "done"
ALL_REQUIREMENT_SYSTEM_STATUSES = {
    REQUIREMENT_SYSTEM_STATUS_APPROVED,
    REQUIREMENT_SYSTEM_STATUS_DEVELOPING,
    REQUIREMENT_SYSTEM_STATUS_TESTING,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
    REQUIREMENT_SYSTEM_STATUS_PM_REVIEW,
    REQUIREMENT_SYSTEM_STATUS_DONE,
}
# 推进/回退判定用：相邻才能跳，越界 409
REQUIREMENT_SYSTEM_STATUS_ORDER = (
    REQUIREMENT_SYSTEM_STATUS_APPROVED,
    REQUIREMENT_SYSTEM_STATUS_DEVELOPING,
    REQUIREMENT_SYSTEM_STATUS_TESTING,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
    REQUIREMENT_SYSTEM_STATUS_PM_REVIEW,
    REQUIREMENT_SYSTEM_STATUS_DONE,
)
# task_service 自动聚合域（done 之外；PM 一旦把需求推到 done，task 变化不会再回退）
# M5：pm_review 改为 task-driven —— 开发完成 + 存在 open pm_review 任务即派生
REQUIREMENT_SYSTEM_STATUS_TASK_DRIVEN = {
    REQUIREMENT_SYSTEM_STATUS_APPROVED,
    REQUIREMENT_SYSTEM_STATUS_DEVELOPING,
    REQUIREMENT_SYSTEM_STATUS_TESTING,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
    REQUIREMENT_SYSTEM_STATUS_PM_REVIEW,
}

# business_status —— PM 维护，决定是否进入发布
REQUIREMENT_BUSINESS_STATUS_APPROVED = "approved"
REQUIREMENT_BUSINESS_STATUS_ACCEPTED = "accepted"
REQUIREMENT_BUSINESS_STATUS_RELEASED = "released"
ALL_REQUIREMENT_BUSINESS_STATUSES = {
    REQUIREMENT_BUSINESS_STATUS_APPROVED,
    REQUIREMENT_BUSINESS_STATUS_ACCEPTED,
    REQUIREMENT_BUSINESS_STATUS_RELEASED,
}

# 来源
REQUIREMENT_SOURCE_MANUAL = "manual"
REQUIREMENT_SOURCE_AI = "ai_generated"


class Requirement(Base):
    __tablename__ = "requirements"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    # 需求标题（短）
    title = Column(String(200), nullable=False)

    # 详细描述（长，markdown 友好）
    description = Column(Text, nullable=True)

    # 验收标准列表 [str, str, ...]；AI 解析时单独抽出来，方便后续生成用例
    acceptance_criteria = Column(JSONType, nullable=True)

    # 优先级 0/1/2/3（跟 TestCase.priority 对齐）
    priority = Column(Integer, default=2)

    # 标签（可选）
    tags = Column(JSONType, nullable=True)

    # 依赖的其它需求 id 列表（topology 用，比如做计划排期）
    depends_on = Column(JSONType, nullable=True)

    # 状态
    status = Column(
        String(20), nullable=False, default=REQUIREMENT_STATUS_DRAFT, index=True
    )

    # 来源
    source = Column(String(20), nullable=False, default=REQUIREMENT_SOURCE_MANUAL)

    # 如果是 AI 生成，关联到那次 ai_run 方便追溯（同一次 AI 任务可能产 N 个需求点）
    ai_run_id = Column(Integer, ForeignKey("ai_runs.id"), nullable=True, index=True)

    # 排序（同项目内）
    sort_order = Column(Integer, default=0)

    # 版本归属（M1 加）
    version_id = Column(
        Integer, ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # M5：父子需求（自引用），删父级联删子；NULL = 顶层
    parent_requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # M5：关联模块（复用现有 Module 树）；模块删则置空
    module_id = Column(
        Integer,
        ForeignKey("modules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # M5：预计起止时间
    planned_start_at = Column(DateTime, nullable=True)
    planned_end_at = Column(DateTime, nullable=True)

    # 自动算（Task 聚合）
    system_status = Column(String(20), nullable=True, index=True)

    # PM 维护（验收 gate）
    business_status = Column(String(20), nullable=True, index=True)

    # PM 指派人
    assignee_pm_id = Column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )

    # PM 一键 Accept 时间
    accepted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project")
    ai_run = relationship("AiRun")
    assignees = relationship(
        "RequirementAssignee",
        cascade="all, delete-orphan",
        back_populates="requirement",
    )
    # M5：自引用父子需求；单层模型，但允许 N 个 child
    parent = relationship(
        "Requirement",
        remote_side="Requirement.id",
        back_populates="children",
    )
    children = relationship(
        "Requirement",
        back_populates="parent",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    # M5：附件（外链 / 本地文件）
    attachments = relationship(
        "Attachment",
        cascade="all, delete-orphan",
        order_by="Attachment.created_at",
    )
    # M6：编辑历史
    edit_histories = relationship(
        "RequirementEditHistory",
        cascade="all, delete-orphan",
        order_by="RequirementEditHistory.created_at",
        back_populates="requirement",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria or [],
            "priority": self.priority,
            "tags": self.tags or [],
            "depends_on": self.depends_on or [],
            "status": self.status,
            "source": self.source,
            "ai_run_id": self.ai_run_id,
            "sort_order": self.sort_order,
            "version_id": self.version_id,
            "parent_requirement_id": self.parent_requirement_id,
            "module_id": self.module_id,
            "planned_start_at": (
                self.planned_start_at.isoformat() if self.planned_start_at else None
            ),
            "planned_end_at": (
                self.planned_end_at.isoformat() if self.planned_end_at else None
            ),
            "system_status": self.system_status,
            "business_status": self.business_status,
            "assignee_pm_id": self.assignee_pm_id,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
