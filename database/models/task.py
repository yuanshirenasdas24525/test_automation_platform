"""Task —— Requirement 下的执行单元（开发 / 测试 / UI 走查 / Bug）。

状态机（Task 线性，bug 解耦）：
  pending → dev_doing → dev_done → test_doing → passed → closed
                                                ↓ failed
                                             （新建 type=bug 的 Task，
                                               指向原 task.parent_task_id）

字段说明：
  - type: dev / test / ui_review / bug
    - bug 是独立 type，不在 dev/test 状态机内回退
  - parent_task_id: bug 指向原 task；非 bug 留空
  - severity: 仅 bug 用（P0/P1/P2/P3），其它 type 留空
  - assignee_dev_id / assignee_test_id: 一个 task 通常只有一方指派
    - dev type → 填 assignee_dev_id
    - test type → 填 assignee_test_id
    - bug type → 填 assignee_dev_id（指派给修复人）
  - related_case_id: bug 来源用例（测试在失败用例处建 bug 时自动带）
  - metadata: 重现步骤、环境快照、截图等附属信息（JSONB）
"""
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, ForeignKey, DateTime, func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# Task 类型
TASK_TYPE_DEV = "dev"
TASK_TYPE_TEST = "test"
TASK_TYPE_UI_REVIEW = "ui_review"
TASK_TYPE_PM_REVIEW = "pm_review"  # 产品体验 / PM 走查
TASK_TYPE_BUG = "bug"
ALL_TASK_TYPES = {
    TASK_TYPE_DEV, TASK_TYPE_TEST, TASK_TYPE_UI_REVIEW,
    TASK_TYPE_PM_REVIEW, TASK_TYPE_BUG,
}

# Task 状态
TASK_STATUS_PENDING = "pending"
TASK_STATUS_DEV_DOING = "dev_doing"
TASK_STATUS_DEV_DONE = "dev_done"
TASK_STATUS_TEST_DOING = "test_doing"
TASK_STATUS_PASSED = "passed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CLOSED = "closed"
ALL_TASK_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_DEV_DOING,
    TASK_STATUS_DEV_DONE,
    TASK_STATUS_TEST_DOING,
    TASK_STATUS_PASSED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CLOSED,
}

# Bug 严重等级
BUG_SEVERITY_P0 = "P0"
BUG_SEVERITY_P1 = "P1"
BUG_SEVERITY_P2 = "P2"
BUG_SEVERITY_P3 = "P3"
ALL_BUG_SEVERITIES = {
    BUG_SEVERITY_P0, BUG_SEVERITY_P1, BUG_SEVERITY_P2, BUG_SEVERITY_P3,
}


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)

    # 归属
    requirement_id = Column(
        Integer,
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # bug 指向原 task；非 bug 留空
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True, index=True)

    # 内容
    title = Column(String(255), nullable=False)
    description = Column(Text)

    # 类型 / 状态 / 严重等级
    type = Column(String(20), nullable=False, index=True)
    status = Column(String(20), nullable=False, default=TASK_STATUS_PENDING, index=True)
    severity = Column(String(4), nullable=True)  # 仅 bug 用

    # 指派
    assignee_dev_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    assignee_test_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # bug 来源用例
    related_case_id = Column(Integer, ForeignKey("test_cases.id"), nullable=True)

    # 重现步骤、环境快照、截图等
    task_metadata = Column("metadata", JSONType, nullable=True)

    # 工时
    estimated_hours = Column(Numeric(5, 2), nullable=True)
    actual_hours = Column(Numeric(5, 2), nullable=True)

    # 时间戳
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at = Column(DateTime, nullable=True)

    # 关系
    requirement = relationship("Requirement")
    parent_task = relationship("Task", remote_side=[id])
    assignee_dev = relationship("User", foreign_keys=[assignee_dev_id])
    assignee_test = relationship("User", foreign_keys=[assignee_test_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    related_case = relationship("TestCase", foreign_keys=[related_case_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "parent_task_id": self.parent_task_id,
            "title": self.title,
            "description": self.description,
            "type": self.type,
            "status": self.status,
            "severity": self.severity,
            "assignee_dev_id": self.assignee_dev_id,
            "assignee_test_id": self.assignee_test_id,
            "created_by_id": self.created_by_id,
            "related_case_id": self.related_case_id,
            "metadata": self.task_metadata or {},
            "estimated_hours": float(self.estimated_hours) if self.estimated_hours is not None else None,
            "actual_hours": float(self.actual_hours) if self.actual_hours is not None else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }
