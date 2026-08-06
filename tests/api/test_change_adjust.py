"""server/api/change_adjust.py 的授权 + 404 行为。

本仓库没有 FastAPI TestClient / HTTP 层测试基础设施（tests/api 下现有用例都是直接
调用路由函数或纯单元测试），这里延续同样的写法：直接调用
`change_plan_apply` / `_module_or_404` 这些路由函数，配合 tests/services 里
已验证过的真实 Postgres `db_session` fixture。

关于 403 的真实触发条件（读过 server/api/authz.py 后确认）：
- `assert_project_access` 只有在 `user is None` 或 `user.is_active` 为假时才会拒绝
  （`user_can_access_project`）；只要 user 是激活状态，`_member_can_access_project`
  当前恒为 True（平台还没有项目成员表，产品语义是"所有登录成员可访问所有项目"）。
- 但通过真实 HTTP 链路根本走不到"激活用户被拒绝"这条路径：`/change_plan/*` 在
  `server/main.py` 里跟 cases/functional_cases 一样，被注册进
  `dependencies=[Depends(get_current_user)]` 那一组——`get_current_user` 本身就会对
  停用/不存在用户直接抛 401（在到达路由体之前），所以路由体里再解出的
  `OptionalUserDep` 不可能是一个"已通过外层认证但 is_active=False"的用户。
- 因此这里按任务说明里给的第二条路径来测：
  1. 直接对 `assert_project_access` 做真·拒绝断言（is_active=False 的用户 → 403），
     证明 change_adjust.py 接入的这层校验本身是有效的；
  2. 对 `/change_plan/apply` 端点函数本身，测两条能在当前逻辑下真实复现的行为：
     - 不存在的 plan_id → 404；
     - 一个真实存在的调整计划 + 激活用户 → 能通过 `_module_or_404` +
       `assert_project_access` 走到 `plan_apply`，返回 success 信封。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from server.api import authz


@pytest.fixture
def db_session():
    """真实 Postgres session；数据库没配置时跳过（同 tests/services/test_change_plan_service.py）。"""
    from database.db import DB

    try:
        db = DB()
    except RuntimeError as exc:
        pytest.skip(f"数据库未配置，跳过 change_adjust 的真实 DB 测试：{exc}")
    try:
        yield db
    finally:
        db.session.rollback()
        db.close()


def _make_user(*, is_active: bool = True, username: str = "tester"):
    from database.models import User

    # 不落库、不 add 进 session：assert_project_access 只读 is_active / roles，
    # 一个未持久化的 ORM 实例就够用，roles 关系默认是空列表。
    return User(username=username, is_active=is_active)


# ---------------------------------------------------------------------------
# 1. authz 层：真实触发 403 的条件
# ---------------------------------------------------------------------------
def test_assert_project_access_denies_inactive_user(db_session) -> None:
    inactive_user = _make_user(is_active=False)
    with pytest.raises(HTTPException) as exc_info:
        authz.assert_project_access(db_session, inactive_user, project_id=1)
    assert exc_info.value.status_code == 403


def test_assert_project_access_denies_none_user(db_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        authz.assert_project_access(db_session, None, project_id=1)
    assert exc_info.value.status_code == 403


def test_assert_project_access_allows_any_active_user_for_any_project(db_session) -> None:
    """当前产品语义：没有项目成员表，任意激活用户都能访问任意项目——不是漏测，是真实行为。"""
    active_user = _make_user(is_active=True)
    authz.assert_project_access(db_session, active_user, project_id=999999)  # 不抛就是通过


# ---------------------------------------------------------------------------
# 2. /change_plan/apply 端点函数：404 + 授权后能走到 plan_apply
# ---------------------------------------------------------------------------
def test_change_plan_apply_missing_plan_returns_404(db_session) -> None:
    from server.api.change_adjust import ApplyRequest, change_plan_apply

    active_user = _make_user()
    with pytest.raises(HTTPException) as exc_info:
        change_plan_apply(ApplyRequest(plan_id=-1), db_session, user=active_user)
    assert exc_info.value.status_code == 404


def test_change_plan_apply_succeeds_for_active_user_end_to_end(db_session) -> None:
    """构造一个真实的 change_plan AiRun（delete 一条已有用例），确认端点函数能拿到
    module → 通过 assert_project_access → 调用 plan_apply → 返回 success 信封，
    且 user 被透传下去（用于 delete_case 的编辑历史 operator 归属）。"""
    from database.models import CASE_TYPE_API, Module, Project, TestCase
    from database.models.ai_run import AI_RUN_STATUS_SUCCESS, AiRun
    from server.api.change_adjust import AI_FEATURE_CHANGE_PLAN, ApplyRequest, change_plan_apply
    from database.models.edit_operation import EditOperationEvent

    db = db_session

    project = Project(name="[test] change_adjust 路由", enabled_stacks=["api"])
    db.session.add(project)
    db.session.flush()

    module = Module(project_id=project.id, name="[test] change_adjust 模块")
    db.session.add(module)
    db.session.flush()

    case = TestCase(module_id=module.id, name="待删用例", case_type=CASE_TYPE_API)
    db.session.add(case)
    db.session.flush()

    ops = [{
        "id": 0,
        "action": "delete",
        "target_case_id": case.id,
        "title": "删除用例",
        "endpoint": None,
        "reason": "",
    }]
    run = AiRun(
        feature=AI_FEATURE_CHANGE_PLAN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=project.id,
        input_payload={"module_id": module.id, "model_name": "unused-model"},
        output_payload={"ops": ops},
    )
    db.session.add(run)
    db.session.flush()

    active_user = _make_user(username="operator-1")
    db.session.add(active_user)
    db.session.flush()

    payload = ApplyRequest(plan_id=run.id, selected_op_ids=[], confirmed_delete_ids=[0])
    resp = change_plan_apply(payload, db, user=active_user)

    assert resp["status"] == "success"
    assert resp["data"] == {"added": 0, "modified": 0, "deleted": 1, "errors": []}

    remaining = db.session.query(TestCase).filter(TestCase.module_id == module.id).all()
    assert remaining == []

    # user 被透传进 delete_case，编辑历史批次应该记到这个 operator 身上
    # （operator_id 落在 EditOperationBatch 上，EditOperationEvent 本身没有这一列）。
    event = (
        db.session.query(EditOperationEvent)
        .filter(EditOperationEvent.entity_id == case.id)
        .order_by(EditOperationEvent.id.desc())
        .first()
    )
    assert event is not None
    assert event.batch.operator_id == active_user.id


def test_change_plan_apply_module_missing_returns_404(db_session) -> None:
    """调整计划关联的 module_id 在库里已经不存在（比如模块被删了）——也应该 404，
    而不是让 assert_project_access 之前的 `_module_or_404` 意外通过 None。"""
    from database.models import Project
    from database.models.ai_run import AI_RUN_STATUS_SUCCESS, AiRun
    from server.api.change_adjust import AI_FEATURE_CHANGE_PLAN, ApplyRequest, change_plan_apply

    db = db_session
    project = Project(name="[test] change_adjust 孤儿计划", enabled_stacks=["api"])
    db.session.add(project)
    db.session.flush()

    run = AiRun(
        feature=AI_FEATURE_CHANGE_PLAN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=project.id,
        input_payload={"module_id": 999999999, "model_name": "unused-model"},
        output_payload={"ops": []},
    )
    db.session.add(run)
    db.session.flush()

    active_user = _make_user()
    with pytest.raises(HTTPException) as exc_info:
        change_plan_apply(ApplyRequest(plan_id=run.id), db, user=active_user)
    assert exc_info.value.status_code == 404
