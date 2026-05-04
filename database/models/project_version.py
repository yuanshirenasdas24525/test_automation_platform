"""版本迭代（ProjectVersion）—— 按版本组织测试工作。

每个项目有多个迭代版本，每个版本：
  - 关联一组模块（多对多）
  - 记录前/后端提测版本号（支持多条）
  - 统一 Markdown 版本说明（SQL / 配置 / 常用命令 / 注意事项）
  - 关联文档链接（测试计划 / 需求文档 / 设计稿 / UI原型）
"""
from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey, DateTime, Table, func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType

# 多对多关联表
project_version_modules = Table(
    "project_version_modules",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "version_id", Integer, ForeignKey("project_versions.id"), nullable=False
    ),
    Column(
        "module_id", Integer, ForeignKey("modules.id"), nullable=False
    ),
)


# 状态枚举
VERSION_STATUS_PLANNING = "planning"      # 规划中
VERSION_STATUS_DEVELOPING = "developing"  # 开发中
VERSION_STATUS_TESTING = "testing"        # 测试中
VERSION_STATUS_RELEASED = "released"      # 已发布
VERSION_STATUS_READY_TO_RELEASE = "ready_to_release"  # 全部 Req business_status=accepted，待发布
VERSION_STATUS_ARCHIVED = "archived"      # 已归档

ALL_VERSION_STATUSES = {
    VERSION_STATUS_PLANNING,
    VERSION_STATUS_DEVELOPING,
    VERSION_STATUS_TESTING,
    VERSION_STATUS_READY_TO_RELEASE,
    VERSION_STATUS_RELEASED,
    VERSION_STATUS_ARCHIVED,
}


class ProjectVersion(Base):
    """版本迭代。"""
    __tablename__ = "project_versions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id"), nullable=False, index=True
    )

    # 版本标识
    version_name = Column(String(100), nullable=False)       # "v2.3.0"、"Sprint 12"
    display_name = Column(String(200))                       # "2026年Q2 春季大促"
    status = Column(String(20), nullable=False, default=VERSION_STATUS_PLANNING, index=True)
    sort_order = Column(Integer, default=0)

    # 提测版本记录（多条）
    # [{"version": "feat/v2.3.0-beta.1", "date": "2026-04-28", "notes": "首次提测"}]
    frontend_versions = Column(JSONType, default=list)
    backend_versions = Column(JSONType, default=list)

    # 统一 Markdown 文档
    release_notes = Column(Text, default="")

    # 文档条目（JSON 数组，每项格式 {id,name,type,url,content}）
    test_plan_items = Column(JSONType, default=list)
    requirement_doc_items = Column(JSONType, default=list)
    design_doc_items = Column(JSONType, default=list)
    ui_prototype_items = Column(JSONType, default=list)

    # 时间
    planned_start_at = Column(DateTime)
    planned_end_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project = relationship("Project")
    associated_modules = relationship(
        "Module",
        secondary=project_version_modules,
        backref="versions",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "version_name": self.version_name,
            "display_name": self.display_name,
            "status": self.status,
            "sort_order": self.sort_order,
            "frontend_versions": self.frontend_versions or [],
            "backend_versions": self.backend_versions or [],
            "release_notes": self.release_notes or "",
            "test_plan_items": self.test_plan_items or [],
            "requirement_doc_items": self.requirement_doc_items or [],
            "design_doc_items": self.design_doc_items or [],
            "ui_prototype_items": self.ui_prototype_items or [],
            "planned_start_at": self.planned_start_at.isoformat() if self.planned_start_at else None,
            "planned_end_at": self.planned_end_at.isoformat() if self.planned_end_at else None,
            "associated_module_ids": [
                m.id for m in (self.associated_modules or [])
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
