"""Task 状态级联服务。

  recompute_requirement_status: 读 Req 下所有非 bug Task → 写 Req.system_status

设计：
  - bug 不参与 system_status 聚合（bug 是缺陷，不是开发产出）
  - service 不 commit；只 flush 让调用方在同 session 里看到新值
  - 没有 Task → return None，不写入
  - done 仍属 PM 推进域（由 /requirements/{id}/advance 推动），task 聚合不会回退
  - pm_review 在 M5 改为 task-driven：dev 任务全部 closed/passed 且存在 open pm_review 任务
    → system_status = pm_review；pm_review 任务关掉后再次 recompute 自然走 testing /
    ready_to_release
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from database.models import (
    Task,
    Requirement,
    TASK_TYPE_BUG,
    TASK_TYPE_DEV,
    TASK_TYPE_PM_REVIEW,
    TASK_STATUS_DEV_DOING,
    TASK_STATUS_DEV_DONE,
    TASK_STATUS_TEST_DOING,
    TASK_STATUS_PASSED,
    TASK_STATUS_CLOSED,
    REQUIREMENT_SYSTEM_STATUS_APPROVED,
    REQUIREMENT_SYSTEM_STATUS_DEVELOPING,
    REQUIREMENT_SYSTEM_STATUS_TESTING,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
    REQUIREMENT_SYSTEM_STATUS_PM_REVIEW,
    REQUIREMENT_SYSTEM_STATUS_DONE,
)


# 已进入 PM 推进域的 Requirement，task 聚合不再覆写
_PM_DRIVEN_STATUSES = {
    REQUIREMENT_SYSTEM_STATUS_DONE,
}


def recompute_requirement_status(requirement_id: int, session: Session) -> Optional[str]:
    """读该 Req 下所有非 bug Task，按规则计算并写入 Req.system_status。

    优先级（命中第一条即定）：
      1) 当前 system_status 已是 done → 直接 return（PM 已结案）
      2) 没有任何非 bug Task → return None（不写入）
      3) 全部 closed/passed → ready_to_release
      4) 存在 open pm_review 任务，且所有 dev 任务终态 → pm_review（M5 新增）
      5) 任一 dev_done / test_doing → testing
      6) 任一 dev_doing → developing
      7) 否则（全部 pending / failed） → approved

    返回新 system_status；调用方可在同 session 里读 Req.system_status 看到。
    """
    req = session.query(Requirement).filter(Requirement.id == requirement_id).first()
    if req is not None and req.system_status in _PM_DRIVEN_STATUSES:
        return req.system_status

    rows = session.query(Task.type, Task.status).filter(
        Task.requirement_id == requirement_id,
        Task.type != TASK_TYPE_BUG,
    ).all()
    if not rows:
        return None

    terminal = {TASK_STATUS_CLOSED, TASK_STATUS_PASSED}
    status_set = {s for (_t, s) in rows}
    dev_statuses = [s for (t, s) in rows if t == TASK_TYPE_DEV]
    pm_review_statuses = [s for (t, s) in rows if t == TASK_TYPE_PM_REVIEW]

    if status_set <= terminal:
        new_status = REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE
    elif (
        pm_review_statuses
        and any(s not in terminal for s in pm_review_statuses)
        and (not dev_statuses or all(s in terminal for s in dev_statuses))
    ):
        new_status = REQUIREMENT_SYSTEM_STATUS_PM_REVIEW
    elif status_set & {TASK_STATUS_DEV_DONE, TASK_STATUS_TEST_DOING}:
        new_status = REQUIREMENT_SYSTEM_STATUS_TESTING
    elif TASK_STATUS_DEV_DOING in status_set:
        new_status = REQUIREMENT_SYSTEM_STATUS_DEVELOPING
    else:
        new_status = REQUIREMENT_SYSTEM_STATUS_APPROVED

    if req is not None:
        req.system_status = new_status
        session.flush()
    return new_status
