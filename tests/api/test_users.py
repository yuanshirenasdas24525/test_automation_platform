"""用户管理接口的权限与创建参数回归测试。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from server.api.users import UserCreate, _assert_admin, create_user


class _Role:
    def __init__(self, code: str):
        self.code = code


class _User:
    def __init__(self, *role_codes: str):
        self.roles = [_Role(code) for code in role_codes]


class _FailOnDatabaseAccess:
    @property
    def session(self):
        raise AssertionError("非管理员请求不应访问数据库")


def _valid_payload() -> dict:
    return {
        "username": "new_user",
        "password": "secret123",
        "role_codes": ["dev"],
    }


def test_create_user_requires_admin_role():
    """创建接口必须在访问数据库前拒绝非管理员。"""
    with pytest.raises(HTTPException) as exc_info:
        create_user(
            UserCreate.model_validate(_valid_payload()),
            _FailOnDatabaseAccess(),
            _User("dev"),
        )

    assert exc_info.value.status_code == 403


def test_create_user_allows_admin_role():
    """管理员角色可以进入创建流程。"""
    _assert_admin(_User("admin"))


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "new_user", "password": "secret123"},
        {"username": "new_user", "password": "secret123", "role_codes": []},
        {"username": "new_user", "password": "secret123", "role_codes": [""]},
        {"username": "new_user", "password": "secret123", "role_codes": ["  "]},
    ],
)
def test_create_user_requires_at_least_one_role(payload: dict):
    """创建用户必须带至少一个非空职务。"""
    with pytest.raises(ValidationError):
        UserCreate.model_validate(payload)


def test_create_user_normalizes_and_deduplicates_roles():
    """职务代码去除空白并去重，避免重复关联。"""
    payload = _valid_payload()
    payload["role_codes"] = [" dev ", "dev", "test"]

    model = UserCreate.model_validate(payload)

    assert model.role_codes == ["dev", "test"]
