"""集中式项目访问授权 —— 对象级授权 / 防水平越权（IDOR）的**唯一入口**。

背景
====
盲点扫描 #5 指出：全局只挂了 ``get_current_user``（校验"登录了"），但绝大多数路由
不校验"这个资源是不是你能访问的"，任何最低权限账号可以遍历 id 读/改/删全平台数据。

现状（本次落地时）
==================
平台**尚未**建立"项目-成员"关系，产品上所有登录成员都能访问所有项目。因此
:func:`_member_can_access_project` 目前对任意 active 用户放行——接入本层**不会改变**
当前可见性，是零行为变更的安全脚手架。

未来（管理员指定成员-项目关系后）
================================
只需在 :func:`_member_can_access_project` 里接上 ``ProjectMember`` 查询，整个平台的
对象级授权立刻全局生效，**无需改动任何调用方**。这正是"把校验收敛到一处"的价值：
避免每个路由各写一遍、写漏一个就是一个越权口子。

用法
====
路由拿到 ``project_id`` 后调用一次即可：

    from server.api.authz import assert_project_access

    @router.get("/foo")
    def foo(project_id: int, db: DBDep, current_user: CurrentUserDep):
        assert_project_access(db, current_user, project_id)
        ...

嵌套资源（附件→需求→项目）用便捷函数：

    assert_requirement_access(db, current_user, requirement)
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from database.models import User
from server.api.deps import DBDep

ADMIN_ROLE = "admin"


def _user_role_codes(user: User) -> set[str]:
    return {role.code for role in (getattr(user, "roles", None) or [])}


def _member_can_access_project(db: DBDep, user: User, project_id: int) -> bool:
    """成员 → 项目 的访问判定（**未来在此接入项目成员表**）。

    当前无"项目-成员"数据模型，产品语义为"所有成员访问所有项目"，故一律放行。

    待管理员指定成员-项目关系后，把下面这行替换为类似：

        from database.models import ProjectMember
        return (
            db.session.query(ProjectMember)
            .filter(
                ProjectMember.user_id == user.id,
                ProjectMember.project_id == project_id,
            )
            .first()
            is not None
        )

    仅此一处改动即可让全平台对象级授权生效。
    """
    return True


def user_can_access_project(db: DBDep, user: User | None, project_id: int | None) -> bool:
    """当前用户是否可访问指定项目。

    - 未认证 / 已停用：拒绝
    - admin：始终放行
    - 其他：走 :func:`_member_can_access_project`（当前全放行，未来按成员表）

    ``project_id`` 为 None 视为无法定位归属，保守拒绝（调用方应先解析出 project_id）。
    """
    if user is None or not getattr(user, "is_active", False):
        return False
    # admin 始终放行（包括资源已成孤儿、project_id 解析不出来的清理场景）
    if ADMIN_ROLE in _user_role_codes(user):
        return True
    if project_id is None:
        return False
    return _member_can_access_project(db, user, project_id)


def assert_project_access(db: DBDep, user: User | None, project_id: int | None) -> None:
    """无权访问则抛 403。这是路由里最常用的一行式守卫。"""
    if not user_can_access_project(db, user, project_id):
        raise HTTPException(status_code=403, detail="无权访问该项目资源")


def assert_requirement_access(db: DBDep, user: User | None, requirement: Any) -> None:
    """嵌套资源便捷守卫：需求 → 其所属项目 → 访问判定。"""
    project_id = getattr(requirement, "project_id", None)
    assert_project_access(db, user, project_id)
