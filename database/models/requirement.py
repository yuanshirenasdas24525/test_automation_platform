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

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project")
    ai_run = relationship("AiRun")

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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
