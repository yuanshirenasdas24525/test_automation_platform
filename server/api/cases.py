"""
/api/test_cases/* 路由。

历史遗留：
  1. 老版 `@app.post("/api/test_cases")` 注册了两次，第二个盖掉第一个。这里合并成一个
     `create_case`，保留"指定 sort_order 就插入、否则追加到末尾"的语义。
  2. 老版 `@app.put("/api/test_cases/{case_id}")` 过滤用了 `module_id + id` 双约束，
     module_id 一变就 404。改成只按主键 id 定位。

v2 新增（2026-04）：
  - POST / PUT 支持 `steps` 字段：web / app 用例真正的执行步骤下沉到 test_steps 表，
    payload 里带 steps 就按"先清空再重写"的语义写入。
  - POST / PUT 支持 `case_type`：为空时按老字段（method/path）推断 api。
  - 新增 `GET /test_cases/{id}`：返回用例 + 它的所有 steps，给前端编辑态加载用。

注意：`TestCaseCreate` 里没有"硬删除 steps"的信号 —— 判断语义：
  * `steps is None`    → 用户没碰 steps 字段，后端不动；
  * `steps == []`      → 用户显式清空所有步骤；
  * `steps == [..., ]` → 用户给了新列表，整体替换。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from server.api.deps import DBDep
from database.models import TestCase, TestCaseCreate
from database.models.test_step import ALL_STEP_TYPES, TestStep

router = APIRouter(prefix="/test_cases", tags=["test_cases"])


# ---------- 请求体里的 steps → test_steps 行 ----------
def _replace_case_steps(db, case_id: int, steps: list[Any] | None) -> None:
    """把 `steps` 字段完整落进 test_steps 表。

    - None  → 不动（创建时意味着本 case 没有 steps，就真的没有）
    - []    → 清空
    - [...] → 先清空再整体写入

    每条 step 允许是 dict 或 pydantic 实例（StepPayload）。
    """
    if steps is None:
        return

    # 先清空历史
    db.session.query(TestStep).filter(TestStep.case_id == case_id).delete(
        synchronize_session=False,
    )

    for i, raw in enumerate(steps):
        if hasattr(raw, "model_dump"):
            s = raw.model_dump()
        elif isinstance(raw, dict):
            s = dict(raw)
        else:
            raise HTTPException(status_code=422, detail=f"steps[{i}] 不是合法字典")

        step_type = (s.get("step_type") or "").strip()
        if not step_type:
            raise HTTPException(status_code=422, detail=f"steps[{i}].step_type 不能为空")
        if step_type not in ALL_STEP_TYPES:
            # 不是硬失败 —— 允许自定义 step_type 先落库，但记录一下
            # 真跑的时候 dispatcher 找不到 runner 会自己报 ERROR。
            pass

        db.session.add(TestStep(
            case_id=case_id,
            step_order=int(s.get("step_order") if s.get("step_order") is not None else i),
            step_name=s.get("step_name") or f"step-{i + 1}",
            step_type=step_type,
            skip=bool(s.get("skip") or False),
            config=s.get("config") or {},
            extract=s.get("extract"),
            assertion=s.get("assertion"),
            wait_before=float(s.get("wait_before") or 0),
            timeout=int(s.get("timeout") or 30),
            retry=int(s.get("retry") or 0),
            on_failure=(s.get("on_failure") or "stop"),
        ))


def _infer_case_type(payload: dict) -> str:
    """payload 没显式 case_type 时兜个底：
      - 有 steps 且全是 web_* → web
      - 有 steps 且全是 app_* → app
      - 混合类型 → mixed
      - 没 steps 但有 method/path → api
      - 其它 → api（保守）
    """
    explicit = payload.get("case_type")
    if explicit:
        return explicit
    steps = payload.get("steps") or []
    if steps:
        types = {
            (s.get("step_type") if isinstance(s, dict) else getattr(s, "step_type", ""))
            for s in steps
        }
        web_all = types and all(isinstance(t, str) and t.startswith("web_") for t in types)
        app_all = types and all(isinstance(t, str) and t.startswith("app_") for t in types)
        if web_all:
            return "web"
        if app_all:
            # 老 inferer 返回 "app"，现在 app 已废 —— 默认按 android 推断；
            # 真要 iOS 的应该走前端 iOS Tab（case_type 会被 caller 显式指定，
            # 走不到这条 fallback）。
            return "android"
        return "mixed"
    return "api"


@router.post("")
def create_case(case: TestCaseCreate, db: DBDep):
    """
    创建用例。
    - 指定 sort_order：把同模块里 >= 该位置的用例整体后移一位，新用例插进去；
    - 没指定：追加到模块末尾（max+1）。
    - payload 里的 steps 会同步写入 test_steps（见 _replace_case_steps）。
    """
    payload = case.model_dump()
    # steps / case_type 属于新增字段，不在 TestCase model 列里，单独处理
    steps = payload.pop("steps", None)
    if payload.get("case_type") is None:
        payload["case_type"] = _infer_case_type({"steps": steps, **payload})

    explicit_order = payload.get("sort_order")
    if explicit_order is not None:
        db.session.query(TestCase).filter(
            TestCase.module_id == case.module_id,
            TestCase.sort_order >= explicit_order,
        ).update(
            {TestCase.sort_order: TestCase.sort_order + 1},
            synchronize_session=False,
        )
    else:
        max_order = (
            db.session.query(func.max(TestCase.sort_order))
            .filter(TestCase.module_id == case.module_id)
            .scalar()
            or 0
        )
        payload["sort_order"] = max_order + 1

    new_case = TestCase(**payload)
    db.session.add(new_case)
    db.session.flush()
    db.session.refresh(new_case)

    _replace_case_steps(db, new_case.id, steps)
    db.session.flush()

    return {"status": "success", "data": _serialize_case(new_case, include_steps=True, db=db)}


@router.put("/{case_id}")
def update_case(case_id: int, case: TestCaseCreate, db: DBDep):
    """按主键更新用例。允许改 module_id。payload 带 steps 就整体重写。

    历史坑（2026-04 修）：以前 `case.model_dump()` 把所有 Optional 字段都倒
    出来（前端没传的也是 None），然后 setattr 一把梭，导致 DB 里原本的
    sort_order / description / tags 等被清成 NULL。最致命的是 sort_order：
    `walk()` 排序时 `int(None or 0) = 0`，被清空的用例直接跳到执行链路第一位。

    修复：用 `model_dump(exclude_unset=True)`，只更新前端 PUT body 里实际
    传过来的字段，没传的字段保留 DB 原值。前端要显式清空某个字段时，要明确
    传 null（pydantic 会把 None 也算 set）。
    """
    db_case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if not db_case:
        raise HTTPException(status_code=404, detail="用例不存在")

    # 关键：exclude_unset → 没传的字段不进 payload，避免 setattr None 把 DB 清空
    payload = case.model_dump(exclude_unset=True)
    # steps 是子表，单独走 _replace_case_steps；不能 setattr 到 ORM 上
    steps = payload.pop("steps", None)
    # case_type 显式传 None 也别覆盖（前端有时会带 case_type=null）
    if payload.get("case_type") is None and "case_type" in payload:
        payload.pop("case_type")
    # sort_order 不通过这条接口改 —— 想换顺序走 /api/reorder
    payload.pop("sort_order", None)

    for key, value in payload.items():
        setattr(db_case, key, value)

    # steps 只在前端确实传了 steps 字段时才整体替换；没传就别动
    if "steps" in case.model_fields_set:
        _replace_case_steps(db, case_id, steps)
    db.session.flush()
    return {"status": "success", "message": "修改成功"}


@router.delete("/{case_id}")
def delete_case(case_id: int, db: DBDep):
    db_case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if not db_case:
        raise HTTPException(status_code=404, detail="用例不存在")
    db.session.delete(db_case)
    return {"status": "success", "message": "删除成功"}


@router.get("/{case_id}")
def get_case(case_id: int, db: DBDep):
    """返回用例详情 + 它的 steps（按 step_order 升序）。给前端编辑态用。"""
    db_case = (
        db.session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == case_id)
        .first()
    )
    if not db_case:
        raise HTTPException(status_code=404, detail="用例不存在")
    return {"status": "success", "data": _serialize_case(db_case, include_steps=True)}


# ---------- 序列化 ----------
def _serialize_case(c: TestCase, *, include_steps: bool = False, db=None) -> dict:
    data: dict = {
        "id": c.id,
        "module_id": c.module_id,
        "name": c.name,
        "description": c.description,
        "skip": c.skip,
        "case_type": c.case_type,
        "tags": c.tags,
        "priority": c.priority,
        "method": c.method,
        "path": c.path,
        "headers": c.headers,
        "data_type": c.data_type,
        "params": c.params,
        "file_path": c.file_path,
        "extract_data": c.extract_data,
        "sql_query": c.sql_query,
        "assertion": c.assertion,
        "wait_time": c.wait_time,
        "sort_order": c.sort_order,
    }
    if include_steps:
        # 优先用关系上的 steps；如果没 joinload 过就兜底查一次
        steps = getattr(c, "steps", None)
        if steps is None and db is not None:
            steps = (
                db.session.query(TestStep)
                .filter(TestStep.case_id == c.id)
                .order_by(TestStep.step_order)
                .all()
            )
        data["steps"] = [s.to_dict() for s in (steps or [])]
    return data
