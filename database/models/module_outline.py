"""ModuleOutline / ModuleOutlinePoint —— 模块级"测试点大纲"长期保存。

替代原来存在浏览器 localStorage 的临时草稿。一个模块的每种用例类型各有一份大纲（digest + 测试点），
测试点可关联到具体用例，据此区分"已覆盖 / 缺口"。

- 初次：AI 规划产出 → 落库（source=ai，status=gap）。
- 刷新对齐：大纲 ↔ 当前用例，diff 预览后应用（不调 AI）。
- AI 增量重规划：大纲 ↔ AI 基于变更产出的新点，diff 预览后应用。

设计文档见 docs/module_outline_design.md。
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from database.base import Base


# 覆盖状态
OUTLINE_POINT_COVERED = "covered"    # 已关联到存在的用例
OUTLINE_POINT_GAP = "gap"            # 缺口：还没有对应用例
OUTLINE_POINT_OBSOLETE = "obsolete"  # 需求变更后不再适用（人工确认后删）
ALL_OUTLINE_POINT_STATUSES = {
    OUTLINE_POINT_COVERED,
    OUTLINE_POINT_GAP,
    OUTLINE_POINT_OBSOLETE,
}

# 来源
OUTLINE_POINT_SOURCE_AI = "ai"          # AI 规划产出
OUTLINE_POINT_SOURCE_MANUAL = "manual"  # 对齐时按已有用例补 / 人工新增
ALL_OUTLINE_POINT_SOURCES = {
    OUTLINE_POINT_SOURCE_AI,
    OUTLINE_POINT_SOURCE_MANUAL,
}


class ModuleOutline(Base):
    """一个模块按用例类型各保存一份大纲。"""
    __tablename__ = "module_outlines"
    __table_args__ = (
        UniqueConstraint("module_id", "mode", name="uq_module_outlines_module_mode"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # 功能/API/Web/Android/iOS 等用例域相互独立，唯一性由 (module_id, mode) 保证。
    module_id = Column(
        Integer,
        ForeignKey("modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # functional | interface(API) | web | android | ios | mixed
    mode = Column(String(20), nullable=False, default="functional")
    # AI 产出的需求摘要，供分批生成 / 增量规划复用
    digest = Column(Text, nullable=True)
    model_name = Column(String(100), nullable=True)
    last_aligned_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    points = relationship(
        "ModuleOutlinePoint",
        back_populates="outline",
        cascade="all, delete-orphan",
        order_by="ModuleOutlinePoint.sort_order",
    )

    def to_dict(self, include_points: bool = True) -> dict:
        data = {
            "id": self.id,
            "module_id": self.module_id,
            "mode": self.mode,
            "digest": self.digest,
            "model_name": self.model_name,
            "last_aligned_at": self.last_aligned_at.isoformat() if self.last_aligned_at else None,
        }
        if include_points:
            pts = sorted(self.points or [], key=lambda p: p.sort_order or 0)
            data["points"] = [p.to_dict() for p in pts]
            data["covered_count"] = sum(1 for p in pts if p.status == OUTLINE_POINT_COVERED)
            data["gap_count"] = sum(1 for p in pts if p.status == OUTLINE_POINT_GAP)
        return data


class ModuleOutlinePoint(Base):
    """大纲里的一条测试点。"""
    __tablename__ = "module_outline_points"

    id = Column(Integer, primary_key=True, index=True)
    outline_id = Column(
        Integer,
        ForeignKey("module_outlines.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = Column(String(200), nullable=False)
    category = Column(String(50), nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    # 关联到具体用例；用例被物理删时 DB 层自动置 NULL，业务对齐时据此标 gap
    linked_case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(String(20), nullable=False, default=OUTLINE_POINT_GAP)   # covered | gap | obsolete
    source = Column(String(20), nullable=False, default=OUTLINE_POINT_SOURCE_AI)  # ai | manual

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    outline = relationship("ModuleOutline", back_populates="points")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "outline_id": self.outline_id,
            "title": self.title,
            "category": self.category,
            "sort_order": self.sort_order,
            "linked_case_id": self.linked_case_id,
            "status": self.status,
            "source": self.source,
        }
