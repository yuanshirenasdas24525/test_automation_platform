"""测试计划（TestPlan）—— AI 生成 / 用户编辑的 markdown 文档。

设计：
  - 一个项目 N 份计划（按版本 / 阶段 / 迭代分多份）
  - content 是 markdown 字符串：覆盖范围 / 测试策略 / 用例清单 / 排期 / 风险
  - 关联：选了哪些 requirement / 哪些 module（让 AI 知道要覆盖什么）
  - status：draft / published / archived
  - source：manual / ai_generated（带 ai_run_id 追溯）

为什么不放 server/data/ 下文件？
  - DB 一行，列表 / 搜索 / 跨项目对比都简单
  - 文件太重的场景再说（典型计划文档几十 KB，DB 完全扛得住）
"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


TEST_PLAN_STATUS_DRAFT = "draft"
TEST_PLAN_STATUS_PUBLISHED = "published"
TEST_PLAN_STATUS_ARCHIVED = "archived"
ALL_TEST_PLAN_STATUSES = {
    TEST_PLAN_STATUS_DRAFT,
    TEST_PLAN_STATUS_PUBLISHED,
    TEST_PLAN_STATUS_ARCHIVED,
}

TEST_PLAN_SOURCE_MANUAL = "manual"
TEST_PLAN_SOURCE_AI = "ai_generated"


class TestPlan(Base):
    __tablename__ = "test_plans"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    title = Column(String(200), nullable=False)
    # markdown 内容主体；前端用 textarea / md 预览编辑
    content = Column(Text, nullable=True)
    # 一句话摘要，列表页显示用
    summary = Column(Text, nullable=True)

    # 选定的需求 / 模块 ids（AI 生成时作为 prompt input；后续也方便回看"这份计划覆盖了什么"）
    requirement_ids = Column(JSONType, nullable=True)
    module_ids = Column(JSONType, nullable=True)

    # 时间范围（可选，写到 markdown 里 + 单独存便于未来排期）
    time_range_start = Column(DateTime, nullable=True)
    time_range_end = Column(DateTime, nullable=True)

    # 资源备注（人员 / 设备 / 环境，写到 markdown 里）
    resource_notes = Column(Text, nullable=True)

    status = Column(
        String(20), nullable=False, default=TEST_PLAN_STATUS_DRAFT, index=True
    )
    source = Column(
        String(20), nullable=False, default=TEST_PLAN_SOURCE_MANUAL
    )

    # AI 生成的话回链 ai_run（debug / 重生成 / token 审计）
    ai_run_id = Column(Integer, ForeignKey("ai_runs.id"), nullable=True, index=True)

    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project = relationship("Project")
    ai_run = relationship("AiRun")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "content": self.content,
            "summary": self.summary,
            "requirement_ids": self.requirement_ids or [],
            "module_ids": self.module_ids or [],
            "time_range_start": self.time_range_start.isoformat() if self.time_range_start else None,
            "time_range_end": self.time_range_end.isoformat() if self.time_range_end else None,
            "resource_notes": self.resource_notes,
            "status": self.status,
            "source": self.source,
            "ai_run_id": self.ai_run_id,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
