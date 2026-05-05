"""Task 状态级联服务。

  recompute_requirement_status: 读 Req 下所有非 bug Task → 写 Req.system_status

设计：
  - bug 不参与 system_status 聚合（bug 是缺陷，不是开发产出）
  - service 不 commit；只 flush 让调用方在同 session 里看到新值
  - 没有 Task → return None，不写入
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from database.models import (
    Task,
    Requirement,
    TASK_TYPE_BUG,
    TASK_STATUS_DEV_DOING,
    TASK_STATUS_DEV_DONE,
    TASK_STATUS_TEST_DOING,
    TASK_STATUS_PASSED,
    TASK_STATUS_CLOSED,
    REQUIREMENT_SYSTEM_STATUS_APPROVED,
    REQUIREMENT_SYSTEM_STATUS_DEVELOPING,
    REQUIREMENT_SYSTEM_STATUS_TESTING,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
)


def recompute_requirement_status(requirement_id: int, session: Session) -> Optional[str]:
    """读该 Req 下所有非 bug Task，按规则计算并写入 Req.system_status。

    优先级（命中第一条即定）：
      1) 没有任何非 bug Task → return None（不写入）
      2) 全部 closed 或 passed → ready_to_release
      3) 任一 dev_done / test_doing → testing
      4) 任一 dev_doing → developing
      5) 否则（全部 pending / failed） → approved

    返回新 system_status；调用方可在同 session 里读 Req.system_status 看到。
    """
    statuses = [
        s for (s,) in session.query(Task.status).filter(
            Task.requirement_id == requirement_id,
            Task.type != TASK_TYPE_BUG,
        ).all()
    ]
    if not statuses:
        return None

    status_set = set(statuses)
    terminal = {TASK_STATUS_CLOSED, TASK_STATUS_PASSED}
    if status_set <= terminal:
        new_status = REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE
    elif status_set & {TASK_STATUS_DEV_DONE, TASK_STATUS_TEST_DOING}:
        new_status = REQUIREMENT_SYSTEM_STATUS_TESTING
    elif TASK_STATUS_DEV_DOING in status_set:
        new_status = REQUIREMENT_SYSTEM_STATUS_DEVELOPING
    else:
        new_status = REQUIREMENT_SYSTEM_STATUS_APPROVED

    req = session.query(Requirement).filter(Requirement.id == requirement_id).first()
    if req is not None:
        req.system_status = new_status
        session.flush()
    return new_status
