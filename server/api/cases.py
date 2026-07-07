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
  - POST / PUT 支持 `case_type`：为空时按 steps 推断，自动化用例必须有 steps。
  - 新增 `GET /test_cases/{id}`：返回用例 + 它的所有 steps，给前端编辑态加载用。

注意：`TestCaseCreate` 里没有"硬删除 steps"的信号 —— 判断语义：
  * `steps is None`    → 用户没碰 steps 字段，后端不动；
  * `steps == []`      → 用户显式清空所有步骤；
  * `steps == [..., ]` → 用户给了新列表，整体替换。
"""
from __future__ import annotations

import re
from typing import Annotated, Any

import pydantic
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from server.api.auth import _get_optional_user
from server.api.deps import DBDep
from database.models import ApiCaseEditHistory, EditOperationBatch, Task, TestCase, TestCaseCreate, User
from server.services.edit_history_service import (
    record_test_case_create,
    record_test_case_delete,
    record_test_case_update,
    rollback_test_case_events,
    snapshot_test_case,
)
from database.models.test_step import ALL_STEP_TYPES, TestStep

router = APIRouter(prefix="/test_cases", tags=["test_cases"])
OptionalUserDep = Annotated[User | None, Depends(_get_optional_user)]


def _operator_name(user: User | None) -> str | None:
    if user is None:
        return None
    return getattr(user, "username", None) or getattr(user, "name", None)


def _record_api_edit(
    db,
    case: TestCase,
    action: str,
    operator: str | None,
    *,
    changes: list[dict] | None = None,
    session_id: str | None = None,
) -> None:
    """仅记录 API 用例编辑审计；其它自动化栈暂不混入本页面记录。"""
    if (case.case_type or "api") != "api":
        return
    db.session.add(ApiCaseEditHistory(
        case_id=case.id,
        module_id=case.module_id,
        case_name=case.name,
        action=action,
        changes=changes or [],
        session_id=session_id,
        operator=operator,
    ))


def _load_case_for_history(db, case_id: int) -> TestCase | None:
    """加载用于生成可回滚快照的用例。"""
    return (
        db.session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == case_id)
        .first()
    )


class TestCaseRollbackPayload(pydantic.BaseModel):
    mode: str = "full"
    event_ids: list[int] | None = None
    fields: dict[str, list[str]] | None = None
    operator_id: int | None = None
    reason: str | None = None
    force: bool = False


def _strip_nul_chars(obj: Any) -> Any:
    """递归去掉字符串里的 NUL（U+0000）。

    PostgreSQL 的 text 和 jsonb 都**无法存储 `\\u0000`**，一旦请求参数 / 断言 / step
    config 里出现真实 NUL 字节（常见于安全/控制字符类用例，如 username="test\\u0000user"），
    写库会直接抛错变 500。这里在落库前统一清掉 NUL，避免整条用例存不进去。

    注意：只处理 NUL 这一种 Postgres 真正存不下的字符；其它控制字符（\\t \\n 等）保留。
    为了不把"控制字符安全用例"悄悄变成正常输入，这里把真实 NUL 替换成可见的字面量
    `\\u0000`（6 个字符），既能入库、又保留"该字段含异常字符"的测试意图。
    """
    if isinstance(obj, str):
        return obj.replace("\x00", "\\u0000") if "\x00" in obj else obj
    if isinstance(obj, dict):
        return {_strip_nul_chars(k): _strip_nul_chars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_nul_chars(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_strip_nul_chars(v) for v in obj)
    return obj


# ---------- 请求体里的 steps → test_steps 行 ----------
def _replace_case_steps(db, case_id: int, steps: list[Any] | None) -> None:
    """把 `steps` 字段完整落进 test_steps 表。

    - None  → 不动（更新已有用例时保留原 steps）
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
def create_case(
    case: TestCaseCreate,
    db: DBDep,
    user: OptionalUserDep = None,
    session_id: str | None = Query(None, description="快速编辑会话 id"),
):
    """
    创建用例。
    - 指定 sort_order：把同模块里 >= 该位置的用例整体后移一位，新用例插进去；
    - 没指定：追加到模块末尾（max+1）。
    - payload 里的 steps 会同步写入 test_steps（见 _replace_case_steps）。
    """
    # 落库前清掉 NUL（Postgres text/jsonb 存不下 ，否则整条用例写库 500）
    payload = _strip_nul_chars(case.model_dump())
    # steps / case_type 属于新增字段，不在 TestCase model 列里，单独处理
    steps_field_provided = "steps" in case.model_fields_set
    steps = payload.pop("steps", None)
    if payload.get("case_type") is None:
        payload["case_type"] = _infer_case_type({"steps": steps, **payload})

    if payload.get("case_type") != "functional" and (not steps_field_provided or not steps):
        raise HTTPException(status_code=422, detail="自动化用例必须提交 steps，后端不再兜底生成步骤")

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
    new_case = _load_case_for_history(db, new_case.id)
    if new_case is not None:
        record_test_case_create(
            db.session,
            new_case,
            operator_id=user.id if user else None,
            summary=f"新增 API 用例：{new_case.name}" if (new_case.case_type or "api") == "api" else None,
        )
    _record_api_edit(
        db,
        new_case,
        "create",
        _operator_name(user),
        changes=[
            {"field": key, "old": "", "new": "" if value is None else str(value)}
            for key, value in payload.items()
            if key not in {"module_id", "sort_order"} and value not in (None, "", [], {})
        ],
        session_id=session_id,
    )

    return {"status": "success", "data": _serialize_case(new_case, include_steps=True, db=db)}


@router.put("/{case_id}")
def update_case(
    case_id: int,
    case: TestCaseCreate,
    db: DBDep,
    user: OptionalUserDep = None,
    session_id: str | None = Query(None, description="快速编辑会话 id"),
    history_batch_id: int | None = Query(None, description="复用同一次批量编辑的历史批次 id"),
):
    """按主键更新用例。允许改 module_id。payload 带 steps 就整体重写。

    历史坑（2026-04 修）：以前 `case.model_dump()` 把所有 Optional 字段都倒
    出来（前端没传的也是 None），然后 setattr 一把梭，导致 DB 里原本的
    sort_order / description / tags 等被清成 NULL。最致命的是 sort_order：
    `walk()` 排序时 `int(None or 0) = 0`，被清空的用例直接跳到执行链路第一位。

    修复：用 `model_dump(exclude_unset=True)`，只更新前端 PUT body 里实际
    传过来的字段，没传的字段保留 DB 原值。前端要显式清空某个字段时，要明确
    传 null（pydantic 会把 None 也算 set）。
    """
    db_case = _load_case_for_history(db, case_id)
    if not db_case:
        raise HTTPException(status_code=404, detail="用例不存在")
    before_snapshot = snapshot_test_case(db_case)

    # 关键：exclude_unset → 没传的字段不进 payload，避免 setattr None 把 DB 清空
    # 同时清掉 NUL（Postgres 存不下，否则更新写库 500）
    payload = _strip_nul_chars(case.model_dump(exclude_unset=True))
    # steps 是子表，单独走 _replace_case_steps；不能 setattr 到 ORM 上
    steps = payload.pop("steps", None)
    # case_type 显式传 None 也别覆盖（前端有时会带 case_type=null）
    if payload.get("case_type") is None and "case_type" in payload:
        payload.pop("case_type")
    # sort_order 不通过这条接口改 —— 想换顺序走 /api/reorder
    payload.pop("sort_order", None)

    changes: list[dict] = []
    for key, value in payload.items():
        old_value = getattr(db_case, key)
        if old_value != value:
            changes.append({
                "field": key,
                "old": "" if old_value is None else str(old_value),
                "new": "" if value is None else str(value),
            })
        setattr(db_case, key, value)

    # steps 处理：
    #   1) 前端送了 steps 字段（不管是空还是有值）→ 整体替换
    #   2) 前端没送 steps → 保持 DB 原值，不再兜底生成步骤
    if "steps" in case.model_fields_set:
        next_case_type = (payload.get("case_type") or db_case.case_type or "api").lower()
        if next_case_type != "functional" and not steps:
            raise HTTPException(status_code=422, detail="自动化用例必须提交至少一个 step")
        _replace_case_steps(db, case_id, steps)

    db.session.flush()
    db_case = _load_case_for_history(db, case_id)
    event = None
    if db_case is not None:
        history_batch = None
        if history_batch_id is not None:
            history_batch = db.session.query(EditOperationBatch).filter(EditOperationBatch.id == history_batch_id).first()
            if history_batch is None:
                raise HTTPException(status_code=404, detail="历史批次不存在")
        event = record_test_case_update(
            db.session,
            db_case,
            before_snapshot=before_snapshot,
            field_changes=[
                {"field": ch["field"], "label": ch.get("field", ""), "old": ch["old"], "new": ch["new"]}
                for ch in changes
            ],
            operator_id=user.id if user else None,
            summary=f"修改用例：{db_case.name}",
            batch=history_batch,
        )
    if changes:
        _record_api_edit(
            db,
            db_case,
            "update",
            _operator_name(user),
            changes=changes,
            session_id=session_id,
        )
    return {"status": "success", "message": "修改成功", "data": {"batch_id": event.batch_id if event else None}}


class RenumberRequest(pydantic.BaseModel):
    module_id: int
    case_type: str | None = None          # 默认全部；可传 'api' 只编号接口用例
    width: int = 4                        # 序号位数：4 → 0001
    enable: bool = True                   # False = 去掉已有序号


# 匹配名字开头已有的序号前缀，如 "0001 "、"12. "、"003、"，用于去重/幂等
_NUM_PREFIX_RE = re.compile(r"^\s*\d{2,}[\s.、:：\-_)]+")


@router.post("/renumber")
def renumber_cases(payload: RenumberRequest, db: DBDep):
    """按当前执行顺序（sort_order）给模块下的用例名加序号前缀 0001/0002/...。

    让"执行顺序"在用例名上一眼可见。幂等：会先剥掉已有序号再重新编，重复点不会叠加。
    enable=False 则去掉序号。
    """
    q = db.session.query(TestCase).filter(TestCase.module_id == payload.module_id)
    if payload.case_type:
        q = q.filter(TestCase.case_type == payload.case_type)
    cases = q.order_by(TestCase.sort_order.asc().nullslast(), TestCase.id.asc()).all()
    width = max(1, min(int(payload.width or 4), 8))

    updated = 0
    for i, c in enumerate(cases, start=1):
        base = _NUM_PREFIX_RE.sub("", c.name or "").lstrip()
        new_name = f"{str(i).zfill(width)} {base}" if payload.enable else base
        if new_name != c.name:
            c.name = new_name
            updated += 1
    db.session.flush()
    return {"status": "success", "data": {"total": len(cases), "updated": updated}}


@router.delete("/{case_id}")
def delete_case(
    case_id: int,
    db: DBDep,
    user: OptionalUserDep = None,
    session_id: str | None = Query(None, description="快速编辑会话 id"),
    history_batch_id: int | None = Query(None, description="复用同一次批量编辑的历史批次 id"),
):
    db_case = _load_case_for_history(db, case_id)
    if not db_case:
        raise HTTPException(status_code=404, detail="用例不存在")

    # Bug/任务属于研发过程记录，删用例时不能连带丢失；仅解除来源用例关联。
    # 兼容尚未执行 ON DELETE SET NULL 迁移的数据库，路由层也显式更新一次。
    db.session.query(Task).filter(Task.related_case_id == case_id).update(
        {Task.related_case_id: None},
        synchronize_session=False,
    )
    _record_api_edit(
        db,
        db_case,
        "delete",
        _operator_name(user),
        session_id=session_id,
    )
    history_batch = None
    if history_batch_id is not None:
        history_batch = db.session.query(EditOperationBatch).filter(EditOperationBatch.id == history_batch_id).first()
        if history_batch is None:
            raise HTTPException(status_code=404, detail="历史批次不存在")
    event = record_test_case_delete(
        db.session,
        db_case,
        operator_id=user.id if user else None,
        summary=f"删除用例：{db_case.name}",
        batch=history_batch,
    )
    db.session.flush()
    db.session.delete(db_case)
    # 删除必须在返回成功前真正执行。否则约束错误会发生在 DBDep 的收尾 commit，
    # 客户端却已经收到 200 和“删除成功”。
    db.session.flush()
    return {"status": "success", "message": "删除成功", "data": {"batch_id": event.batch_id}}


@router.post("/edit-history/batches/{batch_id}/rollback")
def rollback_test_case_history(batch_id: int, payload: TestCaseRollbackPayload, db: DBDep):
    """回滚用例编辑历史：支持整次、单条、字段级。"""
    fields_by_event: dict[int, list[str]] = {}
    for key, value in (payload.fields or {}).items():
        fields_by_event[int(key)] = value
    event_ids = payload.event_ids
    if payload.mode == "fields":
        event_ids = [int(k) for k in fields_by_event]
    try:
        result = rollback_test_case_events(
            db.session,
            batch_id=batch_id,
            event_ids=event_ids,
            fields_by_event=fields_by_event,
            operator_id=payload.operator_id,
            reason=payload.reason,
            force=payload.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result.get("conflicts"):
        raise HTTPException(status_code=409, detail=result)
    return {"status": "success", "data": result}


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
        "repeat_count": getattr(c, "repeat_count", 1) or 1,
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
