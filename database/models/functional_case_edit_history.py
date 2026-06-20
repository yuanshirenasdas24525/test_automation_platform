"""FunctionalCaseEditHistory：功能用例的「编辑历史」审计记录。

与 FunctionalCaseRun（测试/勾结果历史）相对：
  - FunctionalCaseRun 记录“这条用例被测成什么样”（passed/failed/...）；
  - FunctionalCaseEditHistory 记录“这条用例被谁、什么时候、改了哪些字段”。

每次对 functional 用例的新建 / 修改 / 删除，写一行本表。
  - action='create'：新建，changes 记录初始字段快照；
  - action='update'：修改，changes 记录每个被改字段的 old→new；
  - action='delete'：删除，changes 为空，case_name 作快照保留。

case_id 用 ON DELETE SET NULL —— 用例删了，编辑历史仍保留（审计用），
所以额外冗余存 module_id / case_name 快照，便于删除后仍能按模块查询、展示。
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from database.base import Base, JSONType


# action 枚举
EDIT_ACTION_CREATE = "create"
EDIT_ACTION_UPDATE = "update"
EDIT_ACTION_DELETE = "delete"
ALL_EDIT_ACTIONS = {EDIT_ACTION_CREATE, EDIT_ACTION_UPDATE, EDIT_ACTION_DELETE}


class FunctionalCaseEditHistory(Base):
    __tablename__ = "functional_case_edit_history"

    id = Column(Integer, primary_key=True, index=True)
    # 用例删了也保留历史 → SET NULL
    case_id = Column(
        Integer,
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 冗余快照：删除后仍能按模块查 / 展示用例名
    module_id = Column(Integer, index=True, nullable=True)
    case_name = Column(String(255), nullable=True)

    action = Column(String(20), nullable=False)  # create | update | delete
    # [{field, old, new}, ...]；create 时记录初始字段，delete 时为空
    changes = Column(JSONType, nullable=True)

    # 快速编辑会话 id：同一次快速编辑里的多条改动共享一个 session_id，
    # 前端按 session_id 聚合成「一条编辑记录」。普通编辑（非会话）为 NULL。
    session_id = Column(String(64), nullable=True, index=True)

    operator = Column(String(64), nullable=True)
    created_at = Column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    case = relationship("TestCase", foreign_keys=[case_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "module_id": self.module_id,
            "case_name": self.case_name,
            "action": self.action,
            "changes": self.changes or [],
            "session_id": self.session_id,
            "operator": self.operator,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<FunctionalCaseEditHistory id={self.id} case={self.case_id} "
            f"action={self.action} at={self.created_at}>"
        )
