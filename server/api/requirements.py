"""/api/requirements/* —— 项目下的需求点 CRUD。

数据来源：
  - 用户手工新建（source=manual）
  - AI 解析 PRD 批量生成（source=ai_generated，由 tasks/ai_tasks.py 写）

M5：TAPD 化扩展
  - 新字段：parent_requirement_id / module_id / planned_start_at / planned_end_at
  - 多角色 assignees（dev/test/pm/ui）走 RequirementAssignee 表，全量替换语义
  - GET ?tree=true → 顶层需求 + children
  - POST /{id}/split → 批量拆子需求
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import pydantic
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from server.api.deps import DBDep
from database.models import (
    EditOperationEvent,
    Module,
    Project,
    ProjectVersion,
    Requirement,
    RequirementAssignee,
    RequirementEditHistory,
    User,
    REQUIREMENT_STATUS_DRAFT,
    REQUIREMENT_STATUS_APPROVED,
    REQUIREMENT_STATUS_ARCHIVED,
    ALL_REQUIREMENT_STATUSES,
    ALL_REQUIREMENT_SYSTEM_STATUSES,
    ALL_REQUIREMENT_BUSINESS_STATUSES,
    ALL_REQUIREMENT_ASSIGNEE_ROLES,
    REQUIREMENT_ASSIGNEE_ROLE_PM,
    REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE,
    REQUIREMENT_BUSINESS_STATUS_ACCEPTED,
    REQUIREMENT_SOURCE_MANUAL,
    ROLE_PM,
)
from database.models.edit_operation import (
    EDIT_ACTION_DELETE,
    EDIT_ACTION_MIXED,
    ENTITY_TYPE_REQUIREMENT,
)
from server.services.edit_history_service import (
    create_requirement_batch,
    record_requirement_create,
    record_requirement_delete,
    record_requirement_update,
    rollback_requirement_events,
    serialize_requirement_event,
    snapshot_requirement,
)

router = APIRouter(prefix="/requirements", tags=["requirements"])


# ---------- pydantic 模型 ----------

class AssigneesByRole(pydantic.BaseModel):
    dev: Optional[List[int]] = None
    test: Optional[List[int]] = None
    pm: Optional[List[int]] = None
    ui: Optional[List[int]] = None


class RequirementCreate(pydantic.BaseModel):
    project_id: int
    title: str = pydantic.Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    priority: int = 2
    tags: Optional[List[str]] = None
    depends_on: Optional[List[int]] = None
    status: Optional[str] = REQUIREMENT_STATUS_DRAFT
    version_id: Optional[int] = None
    business_status: Optional[str] = None
    assignee_pm_id: Optional[int] = None
    # M5
    parent_requirement_id: Optional[int] = None
    module_id: Optional[int] = None
    planned_start_at: Optional[datetime] = None
    planned_end_at: Optional[datetime] = None
    assignees: Optional[AssigneesByRole] = None


class RequirementUpdate(pydantic.BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    priority: Optional[int] = None
    tags: Optional[List[str]] = None
    depends_on: Optional[List[int]] = None
    status: Optional[str] = None
    version_id: Optional[int] = None
    business_status: Optional[str] = None
    assignee_pm_id: Optional[int] = None
    # M5
    parent_requirement_id: Optional[int] = None
    module_id: Optional[int] = None
    planned_start_at: Optional[datetime] = None
    planned_end_at: Optional[datetime] = None
    assignees: Optional[AssigneesByRole] = None
    # M6：编辑历史可选摘要
    change_summary: Optional[str] = None
    # 允许 PM 手动推进到 done；其余状态由 task_service 自动派生
    system_status: Optional[str] = None
    # 编辑人
    edited_by_id: Optional[int] = None


class RequirementAccept(pydantic.BaseModel):
    pm_id: Optional[int] = None


class RequirementSplitItem(pydantic.BaseModel):
    title: str = pydantic.Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    priority: Optional[int] = None
    module_id: Optional[int] = None
    version_id: Optional[int] = None
    planned_start_at: Optional[datetime] = None
    planned_end_at: Optional[datetime] = None


class RequirementRollbackPayload(pydantic.BaseModel):
    mode: str = "full"
    event_ids: Optional[List[int]] = None
    fields: Optional[Dict[str, List[str]]] = None
    operator_id: Optional[int] = None
    reason: Optional[str] = None
    force: bool = False


# ---------- 辅助 ----------

def _serialize(req: Requirement, include_children: bool = False) -> dict:
    """to_dict() + assignees（按 role 分桶）+ 可选 children 递归。"""
    out = req.to_dict()
    by_role: Dict[str, List[int]] = {role: [] for role in ALL_REQUIREMENT_ASSIGNEE_ROLES}
    for a in req.assignees:
        if a.role in by_role:
            by_role[a.role].append(a.user_id)
    out["assignees"] = by_role
    if include_children:
        out["children"] = [_serialize(c, include_children=False) for c in req.children]
    return out


def _validate_module(session, module_id: int, project_id: int) -> None:
    m = session.query(Module).filter(Module.id == module_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    if m.project_id != project_id:
        raise HTTPException(status_code=400, detail="模块不属于该项目")


def _validate_parent(session, parent_id: int, project_id: int, self_id: Optional[int]) -> None:
    """父需求必须存在、同项目、且不能是自己 / 自己的后代（单层嵌套也禁自指）。"""
    if self_id is not None and parent_id == self_id:
        raise HTTPException(status_code=400, detail="不能把自己设为父需求")
    p = session.query(Requirement).filter(Requirement.id == parent_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="父需求不存在")
    if p.project_id != project_id:
        raise HTTPException(status_code=400, detail="父需求不属于该项目")
    # 单层嵌套：父不能再有父
    if p.parent_requirement_id is not None:
        raise HTTPException(status_code=400, detail="父需求自身已是子需求，M5 不支持多级嵌套")


def _validate_version(session, version_id: int, project_id: int) -> None:
    v = session.query(ProjectVersion).filter(ProjectVersion.id == version_id).first()
    if v is None:
        raise HTTPException(status_code=404, detail="迭代不存在")
    if v.project_id != project_id:
        raise HTTPException(status_code=400, detail="迭代不属于该项目")


def _replace_assignees(
    session, req_id: int, assignees: AssigneesByRole
) -> None:
    """全量替换：把指定 role 的旧 assignee 行删掉再批量插。

    传入的 dict 中 None 表示"不变"，[] 表示"清空该 role"。
    """
    payload = assignees.model_dump(exclude_unset=True)
    if not payload:
        return

    # 校验 user 存在
    all_uids = {uid for ids in payload.values() if ids for uid in ids}
    if all_uids:
        found = {
            uid for (uid,) in session.query(User.id).filter(User.id.in_(all_uids)).all()
        }
        missing = all_uids - found
        if missing:
            raise HTTPException(status_code=404, detail=f"用户不存在：{sorted(missing)}")

    for role, uids in payload.items():
        if role not in ALL_REQUIREMENT_ASSIGNEE_ROLES:
            raise HTTPException(status_code=400, detail=f"非法 assignee role：{role}")
        # 全量删该 role
        session.query(RequirementAssignee).filter(
            RequirementAssignee.requirement_id == req_id,
            RequirementAssignee.role == role,
        ).delete(synchronize_session=False)
        if uids:
            # 去重保留顺序
            seen = set()
            for uid in uids:
                if uid in seen:
                    continue
                seen.add(uid)
                session.add(RequirementAssignee(
                    requirement_id=req_id, user_id=uid, role=role,
                ))

    # 兼容：assignees.pm 第一个写回 Requirement.assignee_pm_id（旧字段保留）
    if "pm" in payload:
        pm_ids = payload["pm"] or []
        new_pm = pm_ids[0] if pm_ids else None
        req = session.query(Requirement).filter(Requirement.id == req_id).first()
        if req is not None:
            req.assignee_pm_id = new_pm


def _collect_subtree_ids(session, root_id: int) -> List[int]:
    """单层父子模型：root + 直接 children 的 id 列表。"""
    ids = [root_id]
    child_ids = [
        (cid,) for (cid,) in session.query(Requirement.id).filter(
            Requirement.parent_requirement_id == root_id,
        ).all()
    ]
    ids.extend(cid for (cid,) in child_ids)
    return ids


def _load_requirement_for_history(session, req_id: int) -> Requirement | None:
    """加载用于生成编辑快照的需求。"""
    return (
        session.query(Requirement)
        .options(selectinload(Requirement.assignees))
        .filter(Requirement.id == req_id)
        .first()
    )


# ---------- 路由 ----------

@router.post("")
def create_requirement(payload: RequirementCreate, db: DBDep):
    proj = db.session.query(Project).filter(Project.id == payload.project_id).first()
    if proj is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    if payload.status and payload.status not in ALL_REQUIREMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status：{payload.status}")
    if payload.business_status and payload.business_status not in ALL_REQUIREMENT_BUSINESS_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 business_status，可选: {sorted(ALL_REQUIREMENT_BUSINESS_STATUSES)}",
        )
    if payload.module_id is not None:
        _validate_module(db.session, payload.module_id, payload.project_id)
    if payload.parent_requirement_id is not None:
        _validate_parent(
            db.session, payload.parent_requirement_id, payload.project_id, self_id=None,
        )
    if payload.version_id is not None:
        _validate_version(db.session, payload.version_id, payload.project_id)

    max_sort = (
        db.session.query(func.max(Requirement.sort_order))
        .filter(Requirement.project_id == payload.project_id)
        .scalar()
        or 0
    )
    req = Requirement(
        project_id=payload.project_id,
        title=payload.title.strip(),
        description=payload.description,
        acceptance_criteria=payload.acceptance_criteria or [],
        priority=payload.priority,
        tags=payload.tags or [],
        depends_on=payload.depends_on or [],
        status=payload.status or REQUIREMENT_STATUS_DRAFT,
        source=REQUIREMENT_SOURCE_MANUAL,
        sort_order=max_sort + 1,
        version_id=payload.version_id,
        business_status=payload.business_status,
        assignee_pm_id=payload.assignee_pm_id,
        parent_requirement_id=payload.parent_requirement_id,
        module_id=payload.module_id,
        planned_start_at=payload.planned_start_at,
        planned_end_at=payload.planned_end_at,
    )
    db.session.add(req)
    db.session.flush()

    if payload.assignees is not None:
        _replace_assignees(db.session, req.id, payload.assignees)
        db.session.flush()

    db.session.refresh(req)
    req = _load_requirement_for_history(db.session, req.id)
    record_requirement_create(db.session, req, summary="新增需求")
    return {"status": "success", "data": _serialize(req)}


@router.get("")
def list_requirements(
    db: DBDep,
    project_id: int = Query(..., description="项目 id 必填"),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    version_id: Optional[int] = Query(None),
    system_status: Optional[str] = Query(None),
    business_status: Optional[str] = Query(None),
    assignee_pm_id: Optional[int] = Query(None),
    module_id: Optional[int] = Query(None),
    tree: bool = Query(False, description="true=只返回顶层，并嵌入 children"),
):
    q = (
        db.session.query(Requirement)
        .filter(Requirement.project_id == project_id)
        .options(selectinload(Requirement.assignees))
    )
    if status:
        if status not in ALL_REQUIREMENT_STATUSES:
            raise HTTPException(status_code=400, detail=f"非法 status：{status}")
        q = q.filter(Requirement.status == status)
    if source:
        q = q.filter(Requirement.source == source)
    if version_id is not None:
        q = q.filter(Requirement.version_id == version_id)
    if system_status:
        if system_status not in ALL_REQUIREMENT_SYSTEM_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"非法 system_status，可选: {sorted(ALL_REQUIREMENT_SYSTEM_STATUSES)}",
            )
        q = q.filter(Requirement.system_status == system_status)
    if business_status:
        if business_status not in ALL_REQUIREMENT_BUSINESS_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"非法 business_status，可选: {sorted(ALL_REQUIREMENT_BUSINESS_STATUSES)}",
            )
        q = q.filter(Requirement.business_status == business_status)
    if assignee_pm_id is not None:
        q = q.filter(Requirement.assignee_pm_id == assignee_pm_id)
    if module_id is not None:
        q = q.filter(Requirement.module_id == module_id)

    if tree:
        q = q.filter(Requirement.parent_requirement_id.is_(None)).options(
            selectinload(Requirement.children).selectinload(Requirement.assignees),
        )

    rows = q.order_by(Requirement.sort_order.asc(), Requirement.id.asc()).all()
    return {
        "status": "success",
        "data": [_serialize(r, include_children=tree) for r in rows],
    }


@router.get("/{req_id}")
def get_requirement(req_id: int, db: DBDep):
    req = (
        db.session.query(Requirement)
        .options(
            selectinload(Requirement.assignees),
            selectinload(Requirement.children).selectinload(Requirement.assignees),
        )
        .filter(Requirement.id == req_id)
        .first()
    )
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    return {"status": "success", "data": _serialize(req, include_children=True)}


@router.put("/{req_id}")
def update_requirement(req_id: int, payload: RequirementUpdate, db: DBDep):
    req = (
        db.session.query(Requirement)
        .options(selectinload(Requirement.assignees))
        .filter(Requirement.id == req_id)
        .first()
    )
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    before_snapshot = snapshot_requirement(req)

    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in ALL_REQUIREMENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 status：{data['status']}")
    if "business_status" in data and data["business_status"] is not None \
            and data["business_status"] not in ALL_REQUIREMENT_BUSINESS_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 business_status，可选: {sorted(ALL_REQUIREMENT_BUSINESS_STATUSES)}",
        )
    if "system_status" in data and data["system_status"] not in ALL_REQUIREMENT_SYSTEM_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法 system_status：{data['system_status']}")
    if "module_id" in data and data["module_id"] is not None:
        _validate_module(db.session, data["module_id"], req.project_id)
    if "parent_requirement_id" in data and data["parent_requirement_id"] is not None:
        _validate_parent(
            db.session, data["parent_requirement_id"], req.project_id, self_id=req.id,
        )
    if "version_id" in data and data["version_id"] is not None:
        _validate_version(db.session, data["version_id"], req.project_id)

    change_summary = data.pop("change_summary", None)
    edited_by_id = data.pop("edited_by_id", None)

    # ---- 计算 diff（在变更前） ----
    tracked_fields = [
        ("title", "标题"),
        ("description", "描述"),
        ("acceptance_criteria", "验收标准"),
        ("priority", "优先级"),
        ("tags", "标签"),
        ("depends_on", "依赖需求"),
        ("version_id", "关联迭代"),
        ("module_id", "模块"),
        ("planned_start_at", "预计开始"),
        ("planned_end_at", "预计完成"),
        ("system_status", "状态"),
    ]
    changes = []
    for field_name, field_label in tracked_fields:
        if field_name not in data:
            continue
        old_val = getattr(req, field_name)
        new_val = data[field_name]
        if _values_equal(old_val, new_val):
            continue
        changes.append({
            "field": field_name,
            "label": field_label,
            "old": _serialize_val(old_val),
            "new": _serialize_val(new_val),
        })

    # 协作人员 diff
    assignees_payload = data.pop("assignees", None)
    if assignees_payload is not None:
        old_by_role = _get_assignees_by_role(db.session, req.id)
        new_by_role = {k: v for k, v in assignees_payload.items() if v is not None}
        if not _assignees_equal(old_by_role, new_by_role):
            changes.append({
                "field": "assignees",
                "label": "协作人员",
                "old": _serialize_assignees(old_by_role),
                "new": _serialize_assignees(new_by_role),
            })

    for k, v in data.items():
        setattr(req, k, v)
    db.session.flush()

    if assignees_payload is not None:
        _replace_assignees(db.session, req.id, AssigneesByRole(**assignees_payload))
        db.session.flush()

    req = _load_requirement_for_history(db.session, req.id)
    record_requirement_update(
        db.session,
        req,
        before_snapshot=before_snapshot,
        field_changes=changes,
        operator_id=edited_by_id,
        summary=change_summary[:512] if change_summary else None,
    )

    db.session.refresh(req)
    return {"status": "success", "data": _serialize(req)}


def _get_assignees_by_role(session, req_id: int) -> dict:
    rows = session.query(RequirementAssignee).filter(
        RequirementAssignee.requirement_id == req_id,
    ).all()
    by_role: dict = {}
    for r in rows:
        by_role.setdefault(r.role, []).append(r.user_id)
    return by_role


def _assignees_equal(a: dict, b: dict) -> bool:
    roles = set(a.keys()) | set(b.keys())
    for role in roles:
        av = sorted(a.get(role) or [])
        bv = sorted(b.get(role) or [])
        if av != bv:
            return False
    return True


def _serialize_assignees(by_role: dict) -> str:
    parts = []
    for role, ids in sorted(by_role.items()):
        if ids:
            parts.append(f"{role}: {', '.join(f'#{uid}' for uid in sorted(ids))}")
    return "; ".join(parts) if parts else "（无）"


def _values_equal(a, b) -> bool:
    """比较两个值是否相等（处理 list / datetime / None 场景）。"""
    sa = _serialize_val(a)
    sb = _serialize_val(b)
    return sa == sb


def _serialize_val(v) -> str | list | None:
    """将字段值序列化为可 JSON 存储的形态。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


@router.delete("/{req_id}")
def delete_requirement(req_id: int, db: DBDep):
    req = (
        db.session.query(Requirement)
        .options(selectinload(Requirement.assignees))
        .filter(Requirement.id == req_id)
        .first()
    )
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    deleted_ids = _collect_subtree_ids(db.session, req_id)
    batch = create_requirement_batch(
        db.session,
        action=EDIT_ACTION_DELETE,
        summary=f"删除需求 REQ-{req_id}",
    )
    for rid in reversed(deleted_ids):
        item = _load_requirement_for_history(db.session, rid)
        if item is not None:
            record_requirement_delete(db.session, item, batch=batch)
    db.session.delete(req)
    db.session.flush()
    return {
        "status": "success",
        "message": "已删除",
        "data": {"deleted_ids": deleted_ids, "batch_id": batch.id},
    }


@router.get("/{req_id}/history")
def get_requirement_history(req_id: int, db: DBDep):
    """查询需求的编辑历史（倒序）。"""
    req = db.session.query(Requirement).filter(Requirement.id == req_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")
    event_rows = (
        db.session.query(EditOperationEvent)
        .filter(
            EditOperationEvent.entity_type == ENTITY_TYPE_REQUIREMENT,
            EditOperationEvent.entity_id == req_id,
        )
        .order_by(EditOperationEvent.created_at.desc(), EditOperationEvent.id.desc())
        .all()
    )
    rows = (
        db.session.query(RequirementEditHistory)
        .filter(RequirementEditHistory.requirement_id == req_id)
        .order_by(RequirementEditHistory.created_at.desc())
        .all()
    )
    data = [serialize_requirement_event(r) for r in event_rows]
    data.extend(r.to_dict() for r in rows)
    data.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"status": "success", "data": data}


@router.post("/history/batches/{batch_id}/rollback")
def rollback_requirement_history(batch_id: int, payload: RequirementRollbackPayload, db: DBDep):
    """回滚需求编辑历史：支持全量、按事件、按字段。"""
    fields_by_event: dict[int, list[str]] = {}
    for key, value in (payload.fields or {}).items():
        fields_by_event[int(key)] = value
    event_ids = payload.event_ids
    if payload.mode == "fields":
        event_ids = [int(k) for k in fields_by_event]
    try:
        result = rollback_requirement_events(
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


@router.post("/{req_id}/split")
def split_requirement(req_id: int, items: List[RequirementSplitItem], db: DBDep):
    """把当前需求拆 N 个子需求；缺省继承父需求的 module / version / priority。"""
    parent = db.session.query(Requirement).filter(Requirement.id == req_id).first()
    if parent is None:
        raise HTTPException(status_code=404, detail="父需求不存在")
    if parent.parent_requirement_id is not None:
        raise HTTPException(
            status_code=400, detail="父需求自身已是子需求，M5 不支持多级嵌套",
        )
    if not items:
        raise HTTPException(status_code=400, detail="子需求列表不能为空")

    # 校验：所有子项里指定的 module / version 都在同项目
    for it in items:
        if it.module_id is not None:
            _validate_module(db.session, it.module_id, parent.project_id)
        if it.version_id is not None:
            _validate_version(db.session, it.version_id, parent.project_id)

    max_sort = (
        db.session.query(func.max(Requirement.sort_order))
        .filter(Requirement.project_id == parent.project_id)
        .scalar()
        or 0
    )

    created: List[Requirement] = []
    for idx, it in enumerate(items, start=1):
        child = Requirement(
            project_id=parent.project_id,
            title=it.title.strip(),
            description=it.description,
            priority=it.priority if it.priority is not None else parent.priority,
            status=REQUIREMENT_STATUS_DRAFT,
            source=REQUIREMENT_SOURCE_MANUAL,
            sort_order=max_sort + idx,
            version_id=it.version_id if it.version_id is not None else parent.version_id,
            module_id=it.module_id if it.module_id is not None else parent.module_id,
            parent_requirement_id=parent.id,
            planned_start_at=it.planned_start_at,
            planned_end_at=it.planned_end_at,
        )
        db.session.add(child)
        created.append(child)

    db.session.flush()
    for c in created:
        db.session.refresh(c)
    batch = create_requirement_batch(
        db.session,
        action=EDIT_ACTION_MIXED,
        summary=f"拆分需求 REQ-{parent.id}，新增 {len(created)} 条子需求",
    )
    for c in created:
        item = _load_requirement_for_history(db.session, c.id)
        record_requirement_create(db.session, item, summary=batch.summary, batch=batch)
    return {
        "status": "success",
        "data": [_serialize(c) for c in created],
    }


@router.post("/{req_id}/accept")
def accept_requirement(req_id: int, payload: RequirementAccept, db: DBDep):
    """PM 一键验收：要求 system_status==ready_to_release，
    设 business_status=accepted + accepted_at=now()。"""
    req = db.session.query(Requirement).filter(Requirement.id == req_id).first()
    if req is None:
        raise HTTPException(status_code=404, detail="需求不存在")

    if req.system_status != REQUIREMENT_SYSTEM_STATUS_READY_TO_RELEASE:
        raise HTTPException(
            status_code=409,
            detail="需求未完成开发测试，不能验收",
        )

    if payload.pm_id is not None:
        user = db.session.query(User).filter(User.id == payload.pm_id).first()
        if user is None:
            raise HTTPException(status_code=404, detail="pm 用户不存在")
        if not any(r.code == ROLE_PM for r in user.roles):
            raise HTTPException(status_code=400, detail="该用户没有 pm 角色")
        req.assignee_pm_id = payload.pm_id

    req.business_status = REQUIREMENT_BUSINESS_STATUS_ACCEPTED
    req.accepted_at = func.now()
    db.session.flush()
    db.session.refresh(req)
    return {"status": "success", "data": _serialize(req)}
