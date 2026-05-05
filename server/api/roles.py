"""/api/roles —— 平台固定 6 个角色（只读）。

Role 在 migration 里 seed，运行期不开放增删改。本端点仅供前端拉下拉项。
"""
from __future__ import annotations

from fastapi import APIRouter

from server.api.deps import DBDep
from database.models import Role

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("")
def list_roles(db: DBDep):
    rows = db.session.query(Role).order_by(Role.id.asc()).all()
    return {"status": "success", "data": [r.to_dict() for r in rows]}
