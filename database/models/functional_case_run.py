"""FunctionalCaseRun：功能用例的人工执行结果记录。

一条 functional TestCase（case_type='functional'）可以被多次人工执行，
每次"勾结果"产生一行 FunctionalCaseRun。最近一次结果给前端列表展示用，
历史记录给追溯用。

字段：
  - case_id    : 关联的 TestCase（case_type 必须是 'functional'）
  - status     : passed | failed | blocked | na
                 （pending 是"还没勾"的占位状态，一般不入库；前端列表里
                  没有 FunctionalCaseRun 的 case 直接显示为"待执行"。）
  - actual_result : 实际表现描述（自由文本）
  - note       : 备注 / Bug 链接 / 任何附加信息
  - operator   : 谁勾的（暂存字符串，后续接用户系统再转 FK）
  - executed_at: 执行时间，DB 侧默认 now()
  - batch_id   : 批次 ID（同一轮回归测试里勾的若干结果归为一批）。
                 不强 FK，前端生成 UUID 透传，便于按批次聚合通过率。
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from database.base import Base


# 状态枚举常量（业务代码用，避免到处写魔法字符串）
RUN_STATUS_PASSED = "passed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_BLOCKED = "blocked"   # 被其它问题挡住，没法判断真实结果
RUN_STATUS_NA = "na"             # 不适用（环境/前置不满足，跳过）
RUN_STATUS_PENDING = "pending"   # 占位用，正常不入库
ALL_RUN_STATUSES = {
    RUN_STATUS_PASSED,
    RUN_STATUS_FAILED,
    RUN_STATUS_BLOCKED,
    RUN_STATUS_NA,
}


class FunctionalCaseRun(Base):
    __tablename__ = "functional_case_runs"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(20), nullable=False)
    actual_result = Column(Text)
    note = Column(Text)
    operator = Column(String(64))
    executed_at = Column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    batch_id = Column(String(64), index=True, nullable=True)

    # 反向关系
    case = relationship("TestCase", back_populates="functional_runs")

    def to_dict(self) -> dict:
        """给 API 序列化用的纯字典形式。"""
        return {
            "id": self.id,
            "case_id": self.case_id,
            "status": self.status,
            "actual_result": self.actual_result,
            "note": self.note,
            "operator": self.operator,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "batch_id": self.batch_id,
        }

    def __repr__(self) -> str:
        return (
            f"<FunctionalCaseRun id={self.id} case={self.case_id} "
            f"status={self.status} at={self.executed_at}>"
        )
