"""/api/users/* —— 平台用户 CRUD + 角色关联。

设计：
  - 软删：is_active=False，不真删（保留历史 FK 引用）
  - 角色多对多：通过 user_roles 关联表（M1 已建）
"""
from __future__ import annotations

import random
import re
import string
from typing import Annotated, Any, Literal

import bcrypt
import pydantic
from fastapi import APIRouter, HTTPException, Path, Query
from sqlalchemy import or_

from server.api.deps import BearerUserDep, DBDep
from server.api.auth import HTTPErrorResponse, revoke_user_sessions
from database.models import User, Role, ALL_ROLE_CODES

router = APIRouter(prefix="/users", tags=["users"])

PROTECTED_ADMIN_USERNAME = "admin"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
# 枚举顺序也会成为契约编译器的安全默认值；最低权限 test 必须放首位，
# 避免 AI 把未知角色修正成 admin。
RoleCode = Literal["test", "dev", "ops", "pm", "ui", "admin"]

AUTH_ADMIN_RESPONSES = {
    401: {"model": HTTPErrorResponse, "description": "未认证或会话失效"},
    403: {"model": HTTPErrorResponse, "description": "仅管理员可访问"},
}


def _is_protected_admin(user: User) -> bool:
    """内置 admin 账号是平台兜底入口，不允许通过用户管理改动。"""
    return user.username == PROTECTED_ADMIN_USERNAME


def _assert_not_protected_admin(user: User) -> None:
    if _is_protected_admin(user):
        raise HTTPException(status_code=403, detail="admin 账号为系统内置账号，禁止编辑、停用或修改密码")


def _assert_admin(user: User) -> None:
    """用户管理写操作只允许管理员执行。"""
    if not any(role.code == "admin" for role in (user.roles or [])):
        raise HTTPException(status_code=403, detail="仅管理员可管理用户")


def _validate_username(value: str) -> str:
    """账号名只能作为登录标识，禁止 HTML/脚本片段混入。"""
    username = value.strip()
    if not username:
        raise ValueError("username 不能为空")
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("username 只能包含字母、数字、下划线、点和短横线")
    return username


class UserCreate(pydantic.BaseModel):
    username: str = pydantic.Field(..., min_length=1, max_length=64)
    full_name: str | None = pydantic.Field(None, max_length=128)
    email: str | None = pydantic.Field(None, max_length=255)
    password: str = pydantic.Field(..., min_length=6, max_length=128)
    is_active: bool = True
    role_codes: list[RoleCode] = pydantic.Field(..., min_length=1)

    @pydantic.field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _validate_username(value)

    @pydantic.field_validator("role_codes", mode="before")
    @classmethod
    def validate_role_codes(cls, value: Any) -> list[str]:
        """创建账号时至少指定一个有效职务（角色）。"""
        if not isinstance(value, list):
            raise ValueError("role_codes 必须是数组")
        cleaned = [str(item).strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("role_code 不能为空")
        return list(dict.fromkeys(cleaned))


class UserUpdate(pydantic.BaseModel):
    username: str | None = pydantic.Field(None, min_length=1, max_length=64)
    full_name: str | None = pydantic.Field(None, max_length=128)
    email: str | None = pydantic.Field(None, max_length=255)
    password: str | None = pydantic.Field(None, min_length=6, max_length=128)
    is_active: bool | None = None

    @pydantic.model_validator(mode="before")
    @classmethod
    def validate_payload(cls, data):
        """更新用户必须至少改一个字段，且不接受显式 null。"""
        if not isinstance(data, dict):
            return data
        if not data:
            raise ValueError("至少提供一个要更新的字段")
        null_fields = [key for key, value in data.items() if value is None]
        if null_fields:
            raise ValueError(f"字段不能为 null: {', '.join(null_fields)}")
        return data

    @pydantic.field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        return _validate_username(value) if value is not None else None


class RoleAssign(pydantic.BaseModel):
    role_codes: list[RoleCode]

    @pydantic.field_validator("role_codes", mode="before")
    @classmethod
    def validate_role_codes(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("role_codes 必须是数组")
        cleaned = [str(item).strip() for item in value]
        if not cleaned:
            raise ValueError("role_codes 不能为空")
        if any(not item for item in cleaned):
            raise ValueError("role_code 不能为空")
        return cleaned


def _parse_bool_query(value: str | None) -> bool | None:
    """用户列表筛选只接受 true/false，避免 yes/on 这类宽松布尔绕过参数校验。"""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise HTTPException(status_code=422, detail="is_active 只能是 true 或 false")


# ============ 用户 CRUD ============

@router.post(
    "",
    responses={
        **AUTH_ADMIN_RESPONSES,
        409: {"model": HTTPErrorResponse, "description": "username 或 email 已存在"},
    },
)
def create_user(payload: UserCreate, db: DBDep, current_user: BearerUserDep):
    _assert_admin(current_user)
    if db.session.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="username 已存在")
    if payload.email and db.session.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="email 已存在")
    if not set(payload.role_codes) <= ALL_ROLE_CODES:
        raise HTTPException(status_code=422, detail=f"非法 role_code，可选: {sorted(ALL_ROLE_CODES)}")

    user = User(
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email,
        is_active=payload.is_active,
    )
    user.password_hash = bcrypt.hashpw(
        payload.password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    roles = db.session.query(Role).filter(Role.code.in_(payload.role_codes)).all()
    user.roles = list(roles)
    db.session.add(user)
    db.session.flush()
    db.session.refresh(user)
    data = user.to_dict()
    # 兼容历史/AI 生成用例里的 $.data.user.id / $.data.user.username 提取路径；
    # 同时保留原来的 $.data.id / $.data.username，避免影响前端现有调用。
    return {"status": "success", "data": {**data, "user": data}}


@router.get("", responses=AUTH_ADMIN_RESPONSES)
def list_users(
    db: DBDep,
    current_user: BearerUserDep,
    is_active: str | None = Query(None),
    role_code: str | None = Query(None),
    q: str | None = Query(None),
):
    _assert_admin(current_user)
    active_filter = _parse_bool_query(is_active)
    query = db.session.query(User)
    if active_filter is not None:
        query = query.filter(User.is_active == active_filter)
    if role_code:
        query = query.join(User.roles).filter(Role.code == role_code)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(User.username.ilike(like), User.full_name.ilike(like))
        )
    rows = query.order_by(User.id.asc()).all()
    return {"status": "success", "data": [u.to_dict() for u in rows]}


@router.get(
    "/{user_id}",
    responses={**AUTH_ADMIN_RESPONSES, 404: {"model": HTTPErrorResponse, "description": "用户不存在"}},
)
def get_user(user_id: Annotated[int, Path(gt=0)], db: DBDep, current_user: BearerUserDep):
    _assert_admin(current_user)
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"status": "success", "data": user.to_dict()}


@router.put(
    "/{user_id}",
    responses={
        **AUTH_ADMIN_RESPONSES,
        404: {"model": HTTPErrorResponse, "description": "用户不存在"},
        409: {"model": HTTPErrorResponse, "description": "username 已存在"},
    },
)
def update_user(user_id: Annotated[int, Path(gt=0)], payload: UserUpdate, db: DBDep, current_user: BearerUserDep):
    _assert_admin(current_user)
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _assert_not_protected_admin(user)

    data = payload.model_dump(exclude_unset=True)

    if data.get("is_active") is False and user.is_active:
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        user.username = f"{user.username}_{suffix}"

    if "username" in data and data["username"] is not None:
        exists = (
            db.session.query(User)
            .filter(User.username == data["username"], User.id != user.id)
            .first()
        )
        if exists:
            raise HTTPException(status_code=409, detail="username 已存在")
        user.username = data["username"]

    if "password" in data and data["password"] is not None:
        user.password_hash = bcrypt.hashpw(
            data["password"].encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        revoke_user_sessions(db, user.id, "password_reset_by_admin")

    for k, v in data.items():
        if k not in ("username", "password") and v is not None:
            setattr(user, k, v)

    if data.get("is_active") is False:
        revoke_user_sessions(db, user.id, "user_disabled")

    db.session.flush()
    return {"status": "success", "data": user.to_dict()}


@router.delete(
    "/{user_id}",
    responses={**AUTH_ADMIN_RESPONSES, 404: {"model": HTTPErrorResponse, "description": "用户不存在"}},
)
def delete_user(user_id: Annotated[int, Path(gt=0)], db: DBDep, current_user: BearerUserDep):
    _assert_admin(current_user)
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _assert_not_protected_admin(user)
    user.is_active = False
    revoke_user_sessions(db, user.id, "user_deleted")
    db.session.flush()
    return {"status": "success", "message": "已软删（is_active=False）"}


# ============ 角色管理 ============

@router.post(
    "/{user_id}/roles",
    responses={**AUTH_ADMIN_RESPONSES, 404: {"model": HTTPErrorResponse, "description": "用户不存在"}},
)
def set_user_roles(user_id: Annotated[int, Path(gt=0)], payload: RoleAssign, db: DBDep, current_user: BearerUserDep):
    _assert_admin(current_user)
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _assert_not_protected_admin(user)
    if not set(payload.role_codes) <= ALL_ROLE_CODES:
        raise HTTPException(status_code=422, detail=f"非法 role_code，可选: {sorted(ALL_ROLE_CODES)}")

    roles = db.session.query(Role).filter(Role.code.in_(payload.role_codes)).all()
    user.roles.clear()
    user.roles.extend(roles)
    db.session.flush()
    db.session.refresh(user)
    return {"status": "success", "data": user.to_dict()}


@router.delete(
    "/{user_id}/roles/{role_code}",
    responses={**AUTH_ADMIN_RESPONSES, 404: {"model": HTTPErrorResponse, "description": "用户或角色不存在"}},
)
def remove_user_role(
    user_id: Annotated[int, Path(gt=0)],
    role_code: Annotated[str, Path(min_length=1)],
    db: DBDep,
    current_user: BearerUserDep,
):
    _assert_admin(current_user)
    user = db.session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    _assert_not_protected_admin(user)

    role_code = role_code.strip()
    if not role_code:
        raise HTTPException(status_code=422, detail="role_code 不能为空")

    role = db.session.query(Role).filter(Role.code == role_code).first()
    if role is None or role not in user.roles:
        raise HTTPException(status_code=404, detail="用户没有该角色")

    user.roles.remove(role)
    db.session.flush()
    return {"status": "success", "message": f"已摘除角色 {role_code}"}
