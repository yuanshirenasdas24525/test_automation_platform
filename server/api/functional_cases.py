"""/api/functional_cases/* —— 功能（人工）用例 + 执行记录路由。

设计动机
========
功能用例 (`TestCase.case_type == 'functional'`) 不走 dispatcher / pytest，
执行结果是测试人员"勾"出来的：通过 / 失败 / 阻塞 / 不适用。一条用例可以
被多次勾选，每次产出一行 `FunctionalCaseRun`。

为什么要单独开一组路由（不复用 /api/test_cases）？
  - schema 完全不同：functional 用例的"步骤 / 期望"是自由文本结构（preconditions /
    steps / expected），落在 `TestCase.functional_spec` JSON 列里，没有 step_type / config 这套；
  - 执行模型完全不同：自动化用例 = 一次 dispatcher → TestReport，功能用例 = 多次手动勾 →
    多行 FunctionalCaseRun，根本不重叠；
  - 入口分流后，前端编辑器（FunctionalCaseEditor）和"测试模式"（批量勾）页面
    可以围绕这一组路由直接建 React Query 键，不污染 /api/test_cases 的缓存。

本模块覆盖：
  - CRUD     : POST / PUT / GET / DELETE /api/functional_cases[/{id}]
  - 列表     : GET /api/functional_cases (按 module_id 过滤，附最近一次 run 状态)
  - 勾结果   : POST /api/functional_cases/{id}/mark
  - 批量勾   : POST /api/functional_cases/batch_mark
  - 历史     : GET /api/functional_cases/{id}/runs
  - 批次概览 : GET /api/functional_cases/batches
  - 导入导出 : POST /api/functional_cases/import & GET /api/functional_cases/export

字段冷清：
  TestCase 上跟功能用例真正相关的只有 name / description / module_id / sort_order /
  case_type / tags / priority / skip / functional_spec。其它兼容字段（method/path/...）
  对功能用例无意义，全部留空。
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime
from typing import Any, Optional

import pydantic
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func

from server.api.deps import DBDep
from database.models import (
    ALL_RUN_STATUSES,
    CASE_TYPE_FUNCTIONAL,
    FunctionalCaseRun,
    Module,
    Project,
    TestCase,
)


router = APIRouter(prefix="/functional_cases", tags=["functional_cases"])


# ---------------------------------------------------------------------------
# 请求 / 响应 schema
# ---------------------------------------------------------------------------
class FunctionalSpec(pydantic.BaseModel):
    """`TestCase.functional_spec` JSON 列里存的形状。

    - preconditions / steps 都是字符串列表，方便前端按行渲染（每条可勾"做完了"）；
    - expected 是单条字符串，足够覆盖 95% 场景；要分多条期望就换行写。

    这里不强制非空 —— 用例刚被创建时常常是"占位"，先有标题后补内容。
    """
    preconditions: list[str] = pydantic.Field(default_factory=list)
    steps: list[str] = pydantic.Field(default_factory=list)
    expected: Optional[str] = None


class FunctionalCaseCreate(pydantic.BaseModel):
    module_id: int
    name: str
    description: Optional[str] = None
    skip: bool = False
    priority: Optional[int] = None
    tags: Optional[list[str]] = None
    functional_spec: Optional[FunctionalSpec] = None
    sort_order: Optional[int] = None


class FunctionalCaseUpdate(pydantic.BaseModel):
    """PUT 用：所有字段都可选，None = 不动；
    `module_id` 允许改，让用户可以把功能用例搬到别的模块。"""
    module_id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    skip: Optional[bool] = None
    priority: Optional[int] = None
    tags: Optional[list[str]] = None
    functional_spec: Optional[FunctionalSpec] = None


class FunctionalMarkPayload(pydantic.BaseModel):
    """单条勾结果。`batch_id` 由前端在"测试模式"开始前生成 UUID；
    单点勾不传 batch_id 也行。"""
    status: str  # passed | failed | blocked | na
    actual_result: Optional[str] = None
    note: Optional[str] = None
    operator: Optional[str] = None
    batch_id: Optional[str] = None


class FunctionalBatchItem(pydantic.BaseModel):
    case_id: int
    status: str
    actual_result: Optional[str] = None
    note: Optional[str] = None


class FunctionalBatchMark(pydantic.BaseModel):
    """`batch_id` 必填 —— 批量勾的全部点意义就是同一批的聚合查询。
    没有 batch_id 就退化成 N 次单点 mark，应该走 /mark 接口。"""
    batch_id: str
    operator: Optional[str] = None
    items: list[FunctionalBatchItem]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _validate_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s not in ALL_RUN_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法状态 {status!r}，合法值：{sorted(ALL_RUN_STATUSES)}",
        )
    return s


def _get_functional_case_or_404(db, case_id: int) -> TestCase:
    case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="用例不存在")
    if case.case_type != CASE_TYPE_FUNCTIONAL:
        raise HTTPException(
            status_code=400,
            detail=f"用例 {case_id} 不是功能用例（case_type={case.case_type}），不能用本接口操作",
        )
    return case


def _serialize_case(c: TestCase, *, latest_run: Optional[FunctionalCaseRun] = None) -> dict:
    """统一序列化形状。`latest_run` 由调用方决定要不要传，避免重复查。"""
    spec = c.functional_spec or {}
    return {
        "id": c.id,
        "module_id": c.module_id,
        "name": c.name,
        "description": c.description,
        "skip": c.skip,
        "priority": c.priority,
        "tags": c.tags or [],
        "case_type": c.case_type,
        "sort_order": c.sort_order,
        "functional_spec": {
            "preconditions": spec.get("preconditions") or [],
            "steps": spec.get("steps") or [],
            "expected": spec.get("expected"),
        },
        "latest_run": latest_run.to_dict() if latest_run else None,
    }


def _latest_runs_map(db, case_ids: list[int]) -> dict[int, FunctionalCaseRun]:
    """一次拿一组 case 的"最近一次 run"，避免 N+1。

    实现：先 GROUP BY case_id 拿 max(executed_at)，再 join 回 FunctionalCaseRun 取整行。
    """
    if not case_ids:
        return {}
    latest_sq = (
        db.session.query(
            FunctionalCaseRun.case_id.label("cid"),
            func.max(FunctionalCaseRun.executed_at).label("ts"),
        )
        .filter(FunctionalCaseRun.case_id.in_(case_ids))
        .group_by(FunctionalCaseRun.case_id)
        .subquery()
    )
    rows = (
        db.session.query(FunctionalCaseRun)
        .join(
            latest_sq,
            (FunctionalCaseRun.case_id == latest_sq.c.cid)
            & (FunctionalCaseRun.executed_at == latest_sq.c.ts),
        )
        .all()
    )
    # 同 (case_id, executed_at) 极小概率撞上多行（同一秒勾两次）—— 取最大 id 兜底
    out: dict[int, FunctionalCaseRun] = {}
    for r in rows:
        prev = out.get(r.case_id)
        if prev is None or r.id > prev.id:
            out[r.case_id] = r
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.post("")
def create_functional_case(payload: FunctionalCaseCreate, db: DBDep):
    """创建功能用例：往 test_cases 写一行 case_type='functional'。"""
    if not payload.name:
        raise HTTPException(status_code=400, detail="名称不能为空")

    explicit_order = payload.sort_order
    if explicit_order is not None:
        # 指定位置插入：把同模块里 >= 该位置的所有用例（含其它栈）整体后移
        db.session.query(TestCase).filter(
            TestCase.module_id == payload.module_id,
            TestCase.sort_order >= explicit_order,
        ).update(
            {TestCase.sort_order: TestCase.sort_order + 1},
            synchronize_session=False,
        )
        sort_order = explicit_order
    else:
        max_order = (
            db.session.query(func.max(TestCase.sort_order))
            .filter(TestCase.module_id == payload.module_id)
            .scalar()
            or 0
        )
        sort_order = max_order + 1

    spec_dict = payload.functional_spec.model_dump() if payload.functional_spec else None
    new_case = TestCase(
        module_id=payload.module_id,
        name=payload.name,
        description=payload.description,
        skip=payload.skip,
        priority=payload.priority,
        tags=payload.tags,
        case_type=CASE_TYPE_FUNCTIONAL,
        sort_order=sort_order,
        functional_spec=spec_dict,
    )
    db.session.add(new_case)
    db.session.flush()
    db.session.refresh(new_case)
    return {"status": "success", "data": _serialize_case(new_case)}


@router.put("/{case_id}")
def update_functional_case(case_id: int, payload: FunctionalCaseUpdate, db: DBDep):
    """部分更新。Pydantic 字段为 None 视为"用户没碰它"，保留旧值。"""
    case = _get_functional_case_or_404(db, case_id)

    data = payload.model_dump(exclude_unset=True)
    if "functional_spec" in data and data["functional_spec"] is not None:
        # FunctionalSpec 对象会被 Pydantic 自动转 dict（exclude_unset 导致它来时已是 dict）
        data["functional_spec"] = data["functional_spec"]

    for key, value in data.items():
        setattr(case, key, value)

    db.session.flush()
    db.session.refresh(case)
    latest_map = _latest_runs_map(db, [case.id])
    return {
        "status": "success",
        "data": _serialize_case(case, latest_run=latest_map.get(case.id)),
    }


@router.delete("/{case_id}")
def delete_functional_case(case_id: int, db: DBDep):
    """删除功能用例。`functional_runs` 关系上挂了 cascade='all, delete-orphan'，
    历史勾结果会一起被删掉（如果要保留审计就改 cascade 策略）。"""
    case = _get_functional_case_or_404(db, case_id)
    db.session.delete(case)
    return {"status": "success", "message": "用例已删除"}


@router.get("/batches")
def list_recent_batches(
    db: DBDep,
    project_id: int = Query(..., description="哪个项目下的批次"),
    limit: int = Query(20, ge=1, le=200),
):
    """按批次（batch_id）聚合的执行汇总。

    用途：给"回归测试"看板用 —— 一次回归把全部功能用例跑完后，能在卡片上
    显示"x/y 通过 / z 失败 / w 阻塞"。

    项目维度过滤：通过 case 的 module → project 反查（不强 FK，挂在 batch_id 上）。
    """
    project = db.session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 拿到该项目所有 functional case 的 id
    case_ids_q = (
        db.session.query(TestCase.id)
        .join(Module, TestCase.module_id == Module.id)
        .filter(
            Module.project_id == project_id,
            TestCase.case_type == CASE_TYPE_FUNCTIONAL,
        )
    )
    case_ids = [r[0] for r in case_ids_q.all()]
    if not case_ids:
        return {"status": "success", "data": []}

    # 按 batch_id 聚合：起止时间 + 各状态 count
    rows = (
        db.session.query(
            FunctionalCaseRun.batch_id,
            func.min(FunctionalCaseRun.executed_at).label("started_at"),
            func.max(FunctionalCaseRun.executed_at).label("finished_at"),
            func.count(FunctionalCaseRun.id).label("total"),
        )
        .filter(
            FunctionalCaseRun.case_id.in_(case_ids),
            FunctionalCaseRun.batch_id.isnot(None),
        )
        .group_by(FunctionalCaseRun.batch_id)
        .order_by(func.max(FunctionalCaseRun.executed_at).desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return {"status": "success", "data": []}

    batch_ids = [r[0] for r in rows]

    # 每个 batch 的状态分布：再来一次 group by
    status_rows = (
        db.session.query(
            FunctionalCaseRun.batch_id,
            FunctionalCaseRun.status,
            func.count(FunctionalCaseRun.id),
        )
        .filter(
            FunctionalCaseRun.case_id.in_(case_ids),
            FunctionalCaseRun.batch_id.in_(batch_ids),
        )
        .group_by(FunctionalCaseRun.batch_id, FunctionalCaseRun.status)
        .all()
    )
    status_map: dict[str, dict[str, int]] = {}
    for bid, st, n in status_rows:
        status_map.setdefault(bid, {s: 0 for s in ALL_RUN_STATUSES})
        status_map[bid][st] = int(n or 0)

    data = []
    for bid, started_at, finished_at, total in rows:
        sm = status_map.get(bid, {s: 0 for s in ALL_RUN_STATUSES})
        passed = sm.get("passed", 0)
        pass_rate = round((passed / total) * 100, 1) if total else 0.0
        data.append({
            "batch_id": bid,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
            "total": int(total or 0),
            "passed": passed,
            "failed": sm.get("failed", 0),
            "blocked": sm.get("blocked", 0),
            "na": sm.get("na", 0),
            "pass_rate": pass_rate,
        })
    return {"status": "success", "data": data}


# /export 必须在 /{case_id} 之前注册：
#   `case_id: int` 类型校验失败时 FastAPI 返 422，不会回退到下一条路由 ——
#   如果先注册了 /{case_id}，对 GET /functional_cases/export 的请求会被它拦截，
#   把 "export" 拿去转 int 失败 → 422。所以静态路径必须排在动态路径前面。
#   函数体放在文件下面"Excel 导入 / 导出"那块（代码就近），这里只是路由顺序锚点。
@router.get("/export")
def export_functional_cases(  # noqa: F811  真正实现在下方
    db: DBDep,
    project_id: int = Query(..., description="导出该项目下的功能用例"),
    module_id: Optional[int] = Query(
        None, description="可选：只导这个模块及其子模块；不传 = 整个项目",
    ),
):
    return _export_functional_cases_impl(db, project_id, module_id)


@router.get("/{case_id}")
def get_functional_case(case_id: int, db: DBDep):
    """单个用例详情 + 最近一次执行结果。"""
    case = _get_functional_case_or_404(db, case_id)
    latest_map = _latest_runs_map(db, [case.id])
    return {
        "status": "success",
        "data": _serialize_case(case, latest_run=latest_map.get(case.id)),
    }


# ---------------------------------------------------------------------------
# 列表（分页 + 状态过滤）
# ---------------------------------------------------------------------------
@router.get("")
def list_functional_cases(
    db: DBDep,
    module_id: Optional[int] = Query(None, description="只列该模块下的用例"),
    project_id: Optional[int] = Query(None, description="跨模块按项目列；与 module_id 互斥"),
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description=(
            "按最近一次执行状态过滤（多值逗号分隔，如 failed,blocked）；"
            "包含 'pending' 表示还没执行过的用例"
        ),
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """列功能用例，附带最近一次执行结果。

    `status_filter` 在 Python 侧过滤（因为状态来自 latest_run，原始查询拿不到）。
    分页针对"过滤后"做，不是"过滤前" —— 否则带 status 过滤时分页计数会失真。
    """
    if module_id is None and project_id is None:
        raise HTTPException(status_code=400, detail="必须指定 module_id 或 project_id")
    if module_id is not None and project_id is not None:
        raise HTTPException(status_code=400, detail="module_id 与 project_id 互斥")

    q = db.session.query(TestCase).filter(TestCase.case_type == CASE_TYPE_FUNCTIONAL)
    if module_id is not None:
        q = q.filter(TestCase.module_id == module_id)
    else:
        # project 维度：跨模块，用 join Module 反查
        q = q.join(Module, TestCase.module_id == Module.id).filter(Module.project_id == project_id)

    cases = q.order_by(TestCase.sort_order, TestCase.id).all()
    if not cases:
        return {"status": "success", "data": {"items": [], "total": 0, "page": page, "page_size": page_size}}

    latest_map = _latest_runs_map(db, [c.id for c in cases])

    # 状态过滤
    wanted: Optional[set[str]] = None
    if status_filter:
        wanted = {s.strip().lower() for s in status_filter.split(",") if s.strip()}

    items: list[dict] = []
    for c in cases:
        latest = latest_map.get(c.id)
        if wanted is not None:
            current_status = latest.status if latest else "pending"
            if current_status not in wanted:
                continue
        items.append(_serialize_case(c, latest_run=latest))

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "status": "success",
        "data": {
            "items": items[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


# ---------------------------------------------------------------------------
# 勾结果（单条 / 批量）
# ---------------------------------------------------------------------------
@router.post("/{case_id}/mark")
def mark_functional_case(case_id: int, payload: FunctionalMarkPayload, db: DBDep):
    """单条勾结果，写一行 FunctionalCaseRun。"""
    case = _get_functional_case_or_404(db, case_id)
    status = _validate_status(payload.status)
    run = FunctionalCaseRun(
        case_id=case.id,
        status=status,
        actual_result=payload.actual_result,
        note=payload.note,
        operator=payload.operator,
        batch_id=payload.batch_id,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    return {"status": "success", "data": run.to_dict()}


@router.post("/batch_mark")
def batch_mark(payload: FunctionalBatchMark, db: DBDep):
    """批量勾结果。

    实现策略：
      - 单事务里循环写入；某一条失败不回滚整批，转为 errors 列表返回，
        让前端能给"哪几条没成功"提示，符合"测试模式"边勾边走的体感；
      - case_id 不存在 / 不是 functional → 单条 error，跳过；
      - 状态非法 → 单条 error，跳过。
    """
    if not payload.batch_id:
        raise HTTPException(status_code=400, detail="batch_id 不能为空（批量勾必须有批次标识）")
    if not payload.items:
        raise HTTPException(status_code=400, detail="items 为空")

    # 先一次性把这批 case 拉出来，验证类型
    case_ids = [it.case_id for it in payload.items]
    cases = (
        db.session.query(TestCase)
        .filter(TestCase.id.in_(case_ids))
        .all()
    )
    case_map = {c.id: c for c in cases}

    created: list[dict] = []
    errors: list[dict] = []
    for it in payload.items:
        c = case_map.get(it.case_id)
        if c is None:
            errors.append({"case_id": it.case_id, "error": "case_not_found"})
            continue
        if c.case_type != CASE_TYPE_FUNCTIONAL:
            errors.append({
                "case_id": it.case_id,
                "error": f"not_functional (case_type={c.case_type})",
            })
            continue
        s = (it.status or "").strip().lower()
        if s not in ALL_RUN_STATUSES:
            errors.append({"case_id": it.case_id, "error": f"invalid_status:{it.status}"})
            continue

        run = FunctionalCaseRun(
            case_id=it.case_id,
            status=s,
            actual_result=it.actual_result,
            note=it.note,
            operator=payload.operator,
            batch_id=payload.batch_id,
        )
        db.session.add(run)
        # 不在循环里 flush 每一条 —— 一次 flush 在循环外更省 IO；refresh 也可省（用 dict 而非 ORM 回写）
        created.append({
            "case_id": it.case_id,
            "status": s,
            "batch_id": payload.batch_id,
        })

    db.session.flush()
    return {
        "status": "success",
        "data": {
            "batch_id": payload.batch_id,
            "created": len(created),
            "errors": errors,
            "items": created,
        },
    }


# ---------------------------------------------------------------------------
# 历史 / 批次
# ---------------------------------------------------------------------------
@router.get("/{case_id}/runs")
def list_runs(case_id: int, db: DBDep, limit: int = Query(20, ge=1, le=200)):
    """某条用例的最近 N 次勾结果（按时间倒序）。"""
    case = _get_functional_case_or_404(db, case_id)
    rows = (
        db.session.query(FunctionalCaseRun)
        .filter(FunctionalCaseRun.case_id == case.id)
        .order_by(FunctionalCaseRun.executed_at.desc(), FunctionalCaseRun.id.desc())
        .limit(limit)
        .all()
    )
    return {"status": "success", "data": [r.to_dict() for r in rows]}


# ---------------------------------------------------------------------------
# Excel 导入 / 导出
# ---------------------------------------------------------------------------
# 列定义（与导出严格对齐，导出 → 编辑 → 导入是个闭环）
# 这里故意保持"简单的 7 列"，user 决策："功能用例导入 Excel 的列简单一点，后期再扩展"。
_IMPORT_COLUMNS = [
    "name",            # 必填
    "description",
    "preconditions",   # 多行用 "\n" 分隔
    "steps",           # 多行用 "\n" 分隔
    "expected",
    "priority",        # int 0/1/2/3，留空 = 默认
    "tags",            # 逗号分隔
]


def _split_lines(value: Any) -> list[str]:
    """Excel 单元格里的多行文本（"\n" 分隔）→ 列表，去空行。"""
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [t.strip() for t in text.split(",") if t.strip()]


@router.post("/import")
async def import_functional_cases(
    db: DBDep,
    module_id: int = Query(..., description="导入到哪个模块下"),
    file: UploadFile = File(...),
):
    """从 Excel 批量导入功能用例。模板列：name / description / preconditions / steps /
    expected / priority / tags。

    解析失败按 400 返回原因。每行成功就 +1，整体一次性 flush；中途某行
    失败不会回滚之前已经入库的（前端会显示成功 N 条 + 失败列表）。
    """
    import pandas as pd

    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{exc}") from exc

    missing = [c for c in ("name", "steps") if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Excel 缺少必需列：{missing}（最少要 name + steps）",
        )

    # 当前模块下最大 sort_order，新导入的追加到末尾
    base_order = (
        db.session.query(func.max(TestCase.sort_order))
        .filter(TestCase.module_id == module_id)
        .scalar()
        or 0
    )

    imported = 0
    errors: list[dict] = []
    for idx, row in df.iterrows():
        try:
            name = str(row.get("name") or "").strip()
            if not name:
                errors.append({"row": int(idx) + 2, "error": "name 为空"})
                continue
            spec = {
                "preconditions": _split_lines(row.get("preconditions")),
                "steps": _split_lines(row.get("steps")),
                "expected": (str(row.get("expected")).strip()
                             if pd.notna(row.get("expected")) and str(row.get("expected")).strip()
                             else None),
            }
            priority_raw = row.get("priority")
            try:
                priority = int(priority_raw) if pd.notna(priority_raw) and str(priority_raw).strip() else None
            except (TypeError, ValueError):
                priority = None
            tags = _split_csv(row.get("tags"))

            db.session.add(TestCase(
                module_id=module_id,
                name=name,
                description=str(row.get("description") or "").strip() or None,
                case_type=CASE_TYPE_FUNCTIONAL,
                priority=priority,
                tags=tags or None,
                functional_spec=spec,
                sort_order=base_order + imported + 1,
            ))
            imported += 1
        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    db.session.flush()
    return {
        "status": "success",
        "data": {"imported": imported, "errors": errors},
    }


def _export_functional_cases_impl(
    db,
    project_id: int,
    module_id: Optional[int],
):
    """导出功能用例为 xlsx。列与 import 对齐 + 多带几列只读信息（最近状态 / 时间）。

    实现独立成函数是因为 `@router.get("/export")` 装饰器需要在 /{case_id} 之前
    注册（参见上方注释），而具体实现就近放在 Excel 导入下方更易读。
    """
    import pandas as pd

    project = db.session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 模块范围：传了 module_id 就 BFS 收子树
    module_ids: Optional[list[int]] = None
    if module_id is not None:
        root = (
            db.session.query(Module)
            .filter(Module.id == module_id, Module.project_id == project_id)
            .first()
        )
        if root is None:
            raise HTTPException(status_code=404, detail=f"模块 {module_id} 不属于项目 {project_id}")
        module_ids = [root.id]
        frontier = [root.id]
        while frontier:
            children = (
                db.session.query(Module.id)
                .filter(Module.parent_id.in_(frontier))
                .all()
            )
            next_ids = [r[0] for r in children]
            if not next_ids:
                break
            module_ids.extend(next_ids)
            frontier = next_ids

    q = (
        db.session.query(TestCase, Module.name.label("module_name"))
        .join(Module, TestCase.module_id == Module.id)
        .filter(
            Module.project_id == project_id,
            TestCase.case_type == CASE_TYPE_FUNCTIONAL,
        )
    )
    if module_ids is not None:
        q = q.filter(TestCase.module_id.in_(module_ids))
    rows = q.order_by(TestCase.module_id, TestCase.sort_order, TestCase.id).all()

    # 一次拿所有最近 run，避免 N+1
    latest_map = _latest_runs_map(db, [c.id for c, _ in rows])

    records = []
    for case, module_name in rows:
        spec = case.functional_spec or {}
        latest = latest_map.get(case.id)
        records.append({
            "module_name": module_name or "",       # 只读
            "name": case.name or "",
            "description": case.description or "",
            "preconditions": "\n".join(spec.get("preconditions") or []),
            "steps": "\n".join(spec.get("steps") or []),
            "expected": spec.get("expected") or "",
            "priority": case.priority if case.priority is not None else "",
            "tags": ", ".join(case.tags or []),
            # 只读的执行情况列：导入侧不识别，导出仅供查看
            "latest_status": latest.status if latest else "pending",
            "latest_executed_at": latest.executed_at.isoformat() if latest and latest.executed_at else "",
            "latest_operator": latest.operator if latest else "",
        })
    df = pd.DataFrame(records)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = (project.name or f"project_{project_id}").replace("/", "_").replace("\\", "_")
    filename = f"{safe_name}_functional_cases_{ts}.xlsx"

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="functional_cases")
    buf.seek(0)

    from urllib.parse import quote
    disposition = (
        f'attachment; filename="{quote(filename)}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


# ---------------------------------------------------------------------------
# 工具：生成一个新 batch_id（前端"测试模式"开始时调用，避免前后端各生成一份）
# ---------------------------------------------------------------------------
@router.post("/new_batch_id")
def new_batch_id():
    """返回一个供"测试模式"使用的新 batch_id。

    放在后端是因为：
      - 前端生成 UUID 也行，但放后端可以未来加上"项目+用户+时间"前缀做易读化；
      - 单点接口好测，前端直接 POST 就能拿。
    """
    return {"status": "success", "data": {"batch_id": uuid.uuid4().hex}}
