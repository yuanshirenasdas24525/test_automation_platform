"""VersionTestSummary —— 版本测试汇总（一对一挂 ProjectVersion）。

发版时由 version_summary_service.generate(version_id) 生成 / 更新。
之后版本归档页直接读这张表，避免每次重算。

字段语义：
  - first_pass_rate: 1 - bug_count / dev_task_count（无 bug 的开发任务比例）
  - test_coverage: 关联了用例的需求数 / 全部需求数
  - payload: 完整 JSONB 快照，便于发布后回溯（含每个 Req / Task / Bug 的明细 ID）
"""
from sqlalchemy import (
    Column, Integer, Numeric, ForeignKey, DateTime, func,
)
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


class VersionTestSummary(Base):
    __tablename__ = "version_test_summaries"

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(
        Integer,
        ForeignKey("project_versions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # 计数
    total_requirements = Column(Integer, default=0, nullable=False)
    total_tasks = Column(Integer, default=0, nullable=False)
    total_test_cases = Column(Integer, default=0, nullable=False)

    # 用例执行结果
    passed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    blocked = Column(Integer, default=0, nullable=False)

    # Bug 统计
    total_bugs = Column(Integer, default=0, nullable=False)
    p0_bugs = Column(Integer, default=0, nullable=False)
    p1_bugs = Column(Integer, default=0, nullable=False)
    p2_bugs = Column(Integer, default=0, nullable=False)
    p3_bugs = Column(Integer, default=0, nullable=False)

    # 计算指标
    first_pass_rate = Column(Numeric(5, 4), nullable=True)
    avg_fix_time_hours = Column(Numeric(8, 2), nullable=True)
    test_coverage = Column(Numeric(5, 4), nullable=True)

    # 完整快照
    payload = Column(JSONType, nullable=True)

    generated_at = Column(DateTime, server_default=func.now(), nullable=False)

    version = relationship("ProjectVersion")

    def to_dict(self) -> dict:
        def _f(v):
            return float(v) if v is not None else None

        return {
            "id": self.id,
            "version_id": self.version_id,
            "total_requirements": self.total_requirements,
            "total_tasks": self.total_tasks,
            "total_test_cases": self.total_test_cases,
            "passed": self.passed,
            "failed": self.failed,
            "blocked": self.blocked,
            "total_bugs": self.total_bugs,
            "p0_bugs": self.p0_bugs,
            "p1_bugs": self.p1_bugs,
            "p2_bugs": self.p2_bugs,
            "p3_bugs": self.p3_bugs,
            "first_pass_rate": _f(self.first_pass_rate),
            "avg_fix_time_hours": _f(self.avg_fix_time_hours),
            "test_coverage": _f(self.test_coverage),
            "payload": self.payload or {},
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
        }
