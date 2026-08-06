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


def _fake_compiled_case(module_id: int, name: str) -> dict:
    """打桩 ai_generate_batch 时用的最小合法 compiled_case——source 用 manual，
    绕开 AI 接口用例的入库门禁（validate_ai_interface_admission），聚焦测本文件要测的东西。"""
    return {
        "module_id": module_id,
        "name": name,
        "description": "",
        "case_type": "api",
        "priority": 3,
        "source": "manual",
        "generation_metadata": {},
        "pre_hook": [],
        "post_hook": [],
        "steps": [
            {
                "step_order": 0,
                "step_name": "请求",
                "step_type": "http_request",
                "skip": False,
                "config": {"method": "GET", "path": "/health"},
                "extract": None,
                "assertion": None,
                "wait_before": 0,
                "timeout": 30,
                "retry": 0,
                "on_failure": "stop",
            }
        ],
    }


def _seed_add_and_delete_plan(db):
    """给下面两条 savepoint 测试公用的建数据逻辑：一个模块 + 一条待删用例 +
    一个 change_plan AiRun（ops = [add, delete]）。返回 (module, case_to_delete, run)。"""
    from database.models import CASE_TYPE_API, Module, Project, TestCase
    from database.models.ai_run import AI_RUN_STATUS_SUCCESS, AiRun

    project = Project(name="[test] plan_apply savepoint", enabled_stacks=["api"])
    db.session.add(project)
    db.session.flush()

    module = Module(project_id=project.id, name="[test] plan_apply savepoint 模块")
    db.session.add(module)
    db.session.flush()

    case_to_delete = TestCase(module_id=module.id, name="待删用例", case_type=CASE_TYPE_API)
    db.session.add(case_to_delete)
    db.session.flush()

    ops = [
        {"id": 0, "action": "add", "target_case_id": None, "title": "新增用例", "endpoint": None, "reason": ""},
        {
            "id": 1,
            "action": "delete",
            "target_case_id": case_to_delete.id,
            "title": "删除用例",
            "endpoint": None,
            "reason": "",
        },
    ]
    run = AiRun(
        feature=svc.AI_FEATURE_CHANGE_PLAN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=project.id,
        input_payload={"module_id": module.id},
        output_payload={"ops": ops, "contract": {}},
    )
    db.session.add(run)
    db.session.flush()
    return module, case_to_delete, run


def test_plan_apply_add_and_delete_succeed_in_same_batch(db_session, monkeypatch):
    """打桩 ai_generate_batch 让它正常返回一条用例：add 建出新用例，同批的 confirmed
    delete 也照常成功——两者互不干扰。"""
    from database.models import TestCase

    db = db_session
    module, case_to_delete, run = _seed_add_and_delete_plan(db)

    def _fake_ai_generate_batch(payload, db_arg, user):
        title = payload.points[0].title
        compiled = _fake_compiled_case(payload.module_id, title)
        return {"status": "success", "data": {"cases": [{"name": title, "compiled_case": compiled}]}}

    monkeypatch.setattr(
        "server.api.functional_cases.ai_generate_batch", _fake_ai_generate_batch
    )

    result = svc.plan_apply(
        db,
        plan_id=run.id,
        model_name="unused-model",
        confirmed_delete_ids=[1],
        selected_op_ids=[0],
    )

    assert result == {"added": 1, "modified": 0, "deleted": 1, "errors": []}

    remaining = db.session.query(TestCase).filter(TestCase.module_id == module.id).all()
    assert case_to_delete.id not in {c.id for c in remaining}
    assert any(c.name == "新增用例" for c in remaining)


def test_plan_apply_generation_db_error_is_isolated_by_savepoint(db_session, monkeypatch):
    """核心回归用例：生成阶段命中 DB 层异常（模拟连接抖动/死锁等）时，
    - plan_apply 本身不能往外抛异常（否则外层路由收尾 commit 会撞见坏掉的 session）；
    - 该批 add op 的失败要单独记进 errors；
    - 同一次调用里已经跑过的 confirmed delete 不能被这次生成异常连带回滚掉。
    """
    from sqlalchemy.exc import OperationalError

    from database.models import TestCase

    db = db_session
    module, case_to_delete, run = _seed_add_and_delete_plan(db)

    def _boom(payload, db_arg, user):
        # 模拟 ai_generate_batch 内部踩到真正的 DB 层异常，把当前事务打成 aborted。
        raise OperationalError("SELECT 1", {}, Exception("simulated connection reset"))

    monkeypatch.setattr("server.api.functional_cases.ai_generate_batch", _boom)

    result = svc.plan_apply(
        db,
        plan_id=run.id,
        model_name="unused-model",
        confirmed_delete_ids=[1],
        selected_op_ids=[0],
    )

    assert result["added"] == 0
    assert result["modified"] == 0
    assert result["deleted"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["op_id"] == 0
    assert "生成失败" in result["errors"][0]["error"]

    # session 必须仍然可用（没有被 PendingRollbackError 毒化），且 delete 的效果真的落地了。
    remaining_ids = {
        c.id for c in db.session.query(TestCase).filter(TestCase.module_id == module.id).all()
    }
    assert case_to_delete.id not in remaining_ids


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
