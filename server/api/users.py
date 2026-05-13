"""/api/users/* —— 平台用户 CRUD + 角色关联。

设计：
  - 软删：is_active=False，不真删（保留历史 FK 引用）
  - 角色多对多：通过 user_roles 关联表（M1 已建）
"""
from __future__ import annotations

from typing import List, Optional
import random
import string

import bcrypt
import pydantic
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_

from server.api.deps import DBDep
from database.models import User, Role, ALL_ROLE_CODES

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(pydantic.BaseModel):
    username: str = pydantic.Field(..., min_length=1, max_length=64)
    full_name: Optional[str] = pydantic.Field(None, max_length=128)
    email: Optional[str] = pydantic.Field(None, max_length=255)
    password: Optional[str] = pydantic.Field(None, min_length=1, max_length=128)
    is_active: bool = True
    role_codes: Optional[List[str]] = None


class UserUpdate(pydantic.BaseModel):
    username: Optional[str] = pydantic.Field(None, max_length=64)
    full_name: Optional[str] = pydantic.Field(None, max_length=128)
    email: Optional[str] = pydantic.Field(None, max_length=255)
    password: Optional[str] = pydantic.Field(None, min_length=1, max_length=128)
    is_active: Optional[bool] = None


class RoleAssign(pydantic.BaseModel):
    role_codes: List[str]


# ============ 用户 CRUD ============

@router.post("")
def create_user(payload: UserCreate, db: DBDep):
    if db.session.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="username 已存在")
    if payload.email and db.session.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="email 已存在")
    if payload.role_codes is not None and not set(payload.role_codes) <= ALL_ROLE_CODES:
        raise HTTPException(status_code=400, detail=f"非法 role_code，可选: {sorted(ALL_ROLE_CODES)}")

    user = User(
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email,
        is_active=payload.is_active,
    )
    if payload.password:
        user.password_hash = bcrypt.hashpw(
            payload.password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
    if payload.role_codes:
        roles = db.session.query(Role).filter(Role.code.in_(payload.role_codes)).all()
        user.roles = list(roles)
    db.session.add(user)
    db.session.flush()
    db.session.refresh(user)
    return {"status": "success", "data": user.to_dict()}


@router.get("")
def list_users(
    db: DBDep,
    is_active: Optional[bool] = Query(None),
    role_code: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    query = db.session.query(User)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    if role_code:
        query = query.join(User.roles).filter(Role.code == role_code)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(User.username.ilike(like), User.full_name.ilike(like))
        )
    rows = query.order_by(User.id.asc()).all()
    return {"status": "success", "data": [u.to_dict() for u in rows]}


@router.get("/{user_id}")
def get_user(user_id: int, db: DBDep):
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "success", "data": user.to_dict()}


@router.put("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, db: DBDep):
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    data = payload.model_dump(exclude_unset=True)

    if data.get("is_active") is False and user.is_active:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        user.username = f"{user.username}_{suffix}"

    if "username" in data and data["username"] is not None:
        user.username = data["username"]

    if "password" in data and data["password"] is not None:
        user.password_hash = bcrypt.hashpw(
            data["password"].encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    for k, v in data.items():
        if k not in ("username", "password") and v is not None:
            setattr(user, k, v)

    db.session.flush()
    return {"status": "success", "data": user.to_dict()}


@router.delete("/{user_id}")
def delete_user(user_id: int, db: DBDep):
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_active = False
    db.session.flush()
    return {"status": "success", "message": "已软删（is_active=False）"}


# ============ 角色管理 ============

@router.post("/{user_id}/roles")
def set_user_roles(user_id: int, payload: RoleAssign, db: DBDep):
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not set(payload.role_codes) <= ALL_ROLE_CODES:
        raise HTTPException(status_code=400, detail=f"非法 role_code，可选: {sorted(ALL_ROLE_CODES)}")

    roles = db.session.query(Role).filter(Role.code.in_(payload.role_codes)).all()
    user.roles.clear()
    user.roles.extend(roles)
    db.session.flush()
    db.session.refresh(user)
    return {"status": "success", "data": user.to_dict()}


@router.delete("/{user_id}/roles/{role_code}")
def remove_user_role(user_id: int, role_code: str, db: DBDep):
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    role = db.session.query(Role).filter(Role.code == role_code).first()
    if role is None or role not in user.roles:
        raise HTTPException(status_code=404, detail="用户没有该角色")

    user.roles.remove(role)
    db.session.flush()
    return {"status": "success", "message": f"已摘除角色 {role_code}"}
