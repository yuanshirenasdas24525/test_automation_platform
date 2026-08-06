import pytest

from server.services import change_plan_service as svc


@pytest.fixture
def db_session():
    """真实 Postgres session（本仓库没有 sqlite 兼容，也没有现成的 DB 测试 fixture）。

    整个用例期间不 commit，最后统一 rollback，不污染本地开发库。
    数据库没配置（DB_HOST 等环境变量缺失）时直接跳过，不让这条测试拖垮没配 DB 的环境。
    """
    from database.db import DB

    try:
        db = DB()
    except RuntimeError as exc:
        pytest.skip(f"数据库未配置，跳过 plan_apply 的真实 DB 测试：{exc}")
    try:
        yield db
    finally:
        db.session.rollback()
        db.close()


def test_plan_apply_delete_only_removes_confirmed_case(db_session):
    """delete op 只处理 confirmed_delete_ids 命中的那条；其余 delete op 保持不动。"""
    from database.models import CASE_TYPE_API, Module, Project, TestCase
    from database.models.ai_run import AI_RUN_STATUS_SUCCESS, AiRun

    db = db_session

    project = Project(name="[test] plan_apply 删除用例", enabled_stacks=["api"])
    db.session.add(project)
    db.session.flush()

    module = Module(project_id=project.id, name="[test] plan_apply 模块")
    db.session.add(module)
    db.session.flush()

    case_a = TestCase(module_id=module.id, name="用例A", case_type=CASE_TYPE_API)
    case_b = TestCase(module_id=module.id, name="用例B", case_type=CASE_TYPE_API)
    db.session.add_all([case_a, case_b])
    db.session.flush()

    ops = [
        {
            "id": 0,
            "action": "delete",
            "target_case_id": case_a.id,
            "title": "删除用例A",
            "endpoint": None,
            "reason": "",
        },
        {
            "id": 1,
            "action": "delete",
            "target_case_id": case_b.id,
            "title": "删除用例B",
            "endpoint": None,
            "reason": "",
        },
    ]
    run = AiRun(
        feature=svc.AI_FEATURE_CHANGE_PLAN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=project.id,
        input_payload={"module_id": module.id},
        output_payload={"ops": ops},
    )
    db.session.add(run)
    db.session.flush()

    result = svc.plan_apply(
        db,
        plan_id=run.id,
        model_name="unused-model",
        confirmed_delete_ids=[0],
        selected_op_ids=[],
    )

    assert result == {"added": 0, "modified": 0, "deleted": 1, "errors": []}

    remaining_ids = {
        c.id
        for c in db.session.query(TestCase).filter(TestCase.module_id == module.id).all()
    }
    assert remaining_ids == {case_b.id}


def test_plan_apply_missing_plan_raises_404(db_session):
    from fastapi import HTTPException

    db = db_session
    with pytest.raises(HTTPException) as exc_info:
        svc.plan_apply(db, plan_id=-1, model_name="unused-model")
    assert exc_info.value.status_code == 404


def test_normalize_ops_filters_bad_target():
    existing_ids = {12, 34}
    raw = {"ops": [
        {"action": "add", "title": "新增登录成功"},
        {"action": "modify", "target_case_id": 12, "title": "改登录"},
        {"action": "delete", "target_case_id": 999, "title": "删不存在"},
        {"action": "delete", "title": "缺 id"},
        {"action": "weird", "title": "非法动作"},
    ]}
    ops = svc._normalize_ops(raw, existing_ids)
    actions = [(o["action"], o.get("target_case_id")) for o in ops]
    assert ("add", None) in actions
    assert ("modify", 12) in actions
    assert all(not (o["action"] == "delete" and o["target_case_id"] == 999) for o in ops)
    assert all(o["action"] != "weird" for o in ops)
    assert [o["id"] for o in ops] == list(range(len(ops)))
