"""AI 接口生成链路的黄金契约回归。

这组测试不访问数据库和被测服务，固定验证最容易造成批量红灯的规则：枚举、认证、
FastAPI 422、可选 null、响应 JSONPath 和统一编译产物。
"""
from __future__ import annotations

from server.services.api_case_contract import (
    build_contract_catalog,
    compile_generated_case,
    empty_contract_catalog,
    validate_compiled_case,
)


def _auth_openapi() -> dict:
    return {
        "openapi": "3.1.0",
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            },
            "schemas": {
                "CreateUser": {
                    "type": "object",
                    "required": ["username", "password", "role_codes"],
                    "properties": {
                        "username": {"type": "string", "minLength": 1},
                        "password": {"type": "string", "minLength": 1},
                        "role_codes": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["test", "dev", "admin"]},
                        },
                    },
                },
                "Logout": {
                    "type": "object",
                    "properties": {
                        "refresh_token": {"type": "string", "nullable": True},
                    },
                },
                "Refresh": {
                    "type": "object",
                    "required": ["refresh_token"],
                    "properties": {
                        "refresh_token": {"type": "string", "minLength": 1},
                    },
                },
                "Login": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {"type": "string", "minLength": 1},
                        "password": {"type": "string", "minLength": 1},
                    },
                },
                "LoginResponse": {
                    "type": "object",
                    "required": ["status", "data"],
                    "properties": {
                        "status": {"type": "string"},
                        "data": {
                            "type": "object",
                            "required": ["access_token", "refresh_token"],
                            "properties": {
                                "access_token": {"type": "string"},
                                "refresh_token": {"type": "string"},
                            },
                        },
                    },
                },
                "ChangePassword": {
                    "type": "object",
                    "required": ["old_password", "new_password"],
                    "properties": {
                        "old_password": {"type": "string"},
                        "new_password": {"type": "string", "minLength": 1, "maxLength": 128},
                    },
                },
                "UserResponse": {
                    "type": "object",
                    "required": ["status", "data"],
                    "properties": {
                        "status": {"type": "string"},
                        "data": {
                            "type": "object",
                            "required": ["id", "username"],
                            "properties": {
                                "id": {"type": "integer"},
                                "username": {"type": "string"},
                            },
                        },
                    },
                },
                "MessageResponse": {
                    "type": "object",
                    "required": ["status", "message"],
                    "properties": {
                        "status": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
                "ValidationError": {
                    "type": "object",
                    "required": ["detail"],
                    "properties": {"detail": {"type": "array", "items": {"type": "object"}}},
                },
            },
        },
        "paths": {
            "/api/auth/login": {
                "post": {
                    "operationId": "login_api_auth_login_post",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Login"},
                            }
                        },
                    },
                    # 故意模拟 FastAPI 未显式声明业务 401 的常见 OpenAPI。
                    "responses": {
                        "200": {
                            "description": "登录成功",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/LoginResponse"},
                                }
                            },
                        },
                        "422": {"description": "参数校验失败"},
                    },
                }
            },
            "/api/users": {
                "post": {
                    "operationId": "create_user_api_users_post",
                    "security": [{"BearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateUser"},
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/UserResponse"},
                                }
                            }
                        },
                        "401": {"description": "未认证"},
                        "422": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ValidationError"},
                                }
                            }
                        },
                    },
                }
            },
            "/api/auth/logout": {
                "post": {
                    "operationId": "logout_api_auth_logout_post",
                    "security": [{"BearerAuth": []}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Logout"},
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/MessageResponse"},
                                }
                            }
                        },
                        "401": {"description": "未认证"},
                        "422": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ValidationError"},
                                }
                            }
                        },
                    },
                }
            },
            "/api/auth/refresh": {
                "post": {
                    "operationId": "refresh_token_api_auth_refresh_post",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Refresh"},
                            }
                        },
                    },
                    # 业务 401 没写进 OpenAPI，验证编译器仍能按鉴权语义保留。
                    "responses": {
                        "200": {"description": "刷新成功"},
                        "422": {"description": "参数校验失败"},
                    },
                }
            },
            "/api/auth/password": {
                "put": {
                    "operationId": "change_password_api_auth_password_put",
                    "security": [{"BearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ChangePassword"},
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "修改成功"},
                        "422": {"description": "参数校验失败"},
                    },
                }
            },
        },
    }


def _compile(case: dict) -> tuple[dict, list[str]]:
    catalog = build_contract_catalog(_auth_openapi())
    return compile_generated_case(case, module_id=1, catalog=catalog)


def _status(compiled: dict) -> int:
    return next(
        rule["expected"]
        for rule in compiled["steps"][0]["assertion"]
        if rule.get("target") == "status_code"
    )


def _statuses(compiled: dict) -> list[int]:
    return [
        next(
            rule["expected"]
            for rule in step["assertion"]
            if rule.get("target") == "status_code"
        )
        for step in compiled["steps"]
    ]


def test_positive_enum_is_compiled_to_documented_value() -> None:
    compiled, issues = _compile({
        "name": "创建用户成功",
        "method": "POST",
        "path": "/api/users",
        "headers": {"Authorization": "Bearer ${admin_token}"},
        "body": {"username": "AUTO_TEST_user", "password": "Test@123", "role_codes": ["user"]},
        "assertion": {"status_code": 201, "$.data.username": "not_empty"},
    })

    assert compiled["steps"][0]["config"]["json"]["role_codes"] == ["test"]
    assert _status(compiled) == 200
    assert compiled["generation_metadata"]["preflight"]["passed"] is True
    assert not compiled["generation_metadata"]["preflight"]["errors"]
    assert any("非法枚举" in issue for issue in issues)


def test_secured_operation_without_auth_is_blocked() -> None:
    compiled, _issues = _compile({
        "name": "创建用户成功",
        "method": "POST",
        "path": "/api/users",
        "body": {"username": "AUTO_TEST_user", "password": "Test@123", "role_codes": ["test"]},
    })

    errors = compiled["generation_metadata"]["preflight"]["errors"]
    assert any("需要认证" in error for error in errors)
    assert compiled["generation_metadata"]["preflight"]["passed"] is False


def test_missing_required_field_keeps_invalid_payload_and_expects_422() -> None:
    compiled, _issues = _compile({
        "name": "缺失 username 返回校验错误",
        "category": "参数校验",
        "method": "POST",
        "path": "/api/users",
        "headers": {"Authorization": "Bearer ${admin_token}"},
        "body": {"password": "Test@123", "role_codes": ["test"]},
        "assertion": {"status_code": 400, "$.detail": None},
    })

    assert "username" not in compiled["steps"][0]["config"]["json"]
    assert _status(compiled) == 422
    detail_rule = next(rule for rule in compiled["steps"][0]["assertion"] if rule.get("target") == "$.detail")
    assert detail_rule["type"] == "is_not_null"


def test_optional_null_logout_uses_success_contract() -> None:
    compiled, _issues = _compile({
        "name": "refresh_token 为 null 时退出成功",
        "method": "POST",
        "path": "/api/auth/logout",
        "headers": {"Authorization": "Bearer ${token}"},
        "body": {"refresh_token": None},
        "assertion": {"status_code": 422, "$.message": "not_empty"},
    })

    assert _status(compiled) == 200
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_nonexistent_jsonpath_is_removed_before_execution() -> None:
    compiled, issues = _compile({
        "name": "创建用户成功",
        "method": "POST",
        "path": "/api/users",
        "headers": {"Authorization": "Bearer ${admin_token}"},
        "body": {"username": "AUTO_TEST_user", "password": "Test@123", "role_codes": ["test"]},
        "extract": {"username": "$.data.user.username"},
        "assertion": {"$.data.user.username": "not_empty"},
    })

    assert compiled["steps"][0]["extract"] is None
    assert all(rule.get("target") != "$.data.user.username" for rule in compiled["steps"][0]["assertion"])
    assert any("不存在" in issue for issue in issues)


def test_missing_structured_contract_is_never_auto_admitted() -> None:
    compiled, _issues = compile_generated_case(
        {
            "name": "没有契约时模型猜出的登录用例",
            "method": "POST",
            "path": "/api/auth/login",
            "body": {"username": "tester", "password": "secret"},
        },
        module_id=1,
        catalog=empty_contract_catalog(),
    )

    assert compiled["generation_metadata"]["preflight"]["passed"] is False
    assert any("OpenAPI 契约" in error for error in compiled["generation_metadata"]["preflight"]["errors"])


def test_undocumented_login_business_failures_keep_semantic_401() -> None:
    """OpenAPI 只有 422 时，错误凭据/安全载荷也不能被替换成无关的 422 或 200。"""
    for case in (
        {
            "name": "【鉴权】密码错误登录返回 401",
            "method": "POST",
            "path": "/api/auth/login",
            "body": {"username": "admin", "password": "wrong"},
        },
        {
            "name": "【鉴权】不存在的用户名登录返回 401",
            "method": "POST",
            "path": "/api/auth/login",
            "body": {"username": "missing-user", "password": "x"},
        },
        {
            "name": "【安全】登录接口 SQL 注入测试",
            "method": "POST",
            "path": "/api/auth/login",
            "body": {"username": "' OR 1=1 --", "password": "x"},
        },
    ):
        compiled, _issues = _compile(case)
        assert _status(compiled) == 401
        assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_missing_auth_is_valid_for_explicit_unauthenticated_case() -> None:
    compiled, _issues = _compile({
        "name": "【鉴权】未带 token 修改密码返回 401",
        "method": "PUT",
        "path": "/api/auth/password",
        "body": {"old_password": "old", "new_password": "new"},
    })

    assert _status(compiled) == 401
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_declared_422_boundary_payload_is_not_rejected_by_preflight() -> None:
    compiled, _issues = _compile({
        "name": "【边界】新密码长度 129 修改返回 422",
        "method": "PUT",
        "path": "/api/auth/password",
        "headers": {"Authorization": "Bearer ${token}"},
        "body": {"old_password": "old", "new_password": "x" * 129},
    })

    assert _status(compiled) == 422
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_invalid_refresh_token_keeps_semantic_401() -> None:
    """报告 74：无效 refresh_token 是业务鉴权失败，不能按成功响应编译成 200。"""
    compiled, _issues = _compile({
        "name": "【鉴权】使用无效refresh_token刷新返回401",
        "expected": ["返回 401，detail 为错误信息字符串"],
        "method": "POST",
        "path": "/api/auth/refresh",
        "body": {"refresh_token": "invalid_refresh_token_xyz"},
        "assertion": {"status_code": 401, "$.detail": "not_empty"},
    })

    assert _status(compiled) == 401
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_logged_out_refresh_token_only_marks_last_step_as_401() -> None:
    """报告 74：多步骤场景必须逐步识别，不能把前置成功步骤一起标成负向。"""
    compiled, _issues = _compile({
        "name": "【场景】登出后使用原 refresh_token 刷新失败",
        "expected": ["前两步均返回 200", "最后一步刷新返回 401，提示无效刷新令牌"],
        "requests": [
            {
                "name": "新账号登录获取 own_refresh_token",
                "method": "POST",
                "path": "/api/auth/login",
                "body": {"username": "tester", "password": "secret"},
                "extract": {"own_refresh_token": "$.data.refresh_token"},
            },
            {
                "name": "登出当前会话",
                "method": "POST",
                "path": "/api/auth/logout",
                "headers": {"Authorization": "Bearer ${token}"},
                "body": {"refresh_token": "${own_refresh_token}"},
            },
            {
                "name": "使用已登出的 refresh_token 刷新令牌",
                "method": "POST",
                "path": "/api/auth/refresh",
                "body": {"refresh_token": "${own_refresh_token}"},
                "assertion": {"status_code": 401},
            },
        ],
    })

    assert _statuses(compiled) == [200, 200, 401]
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_old_password_after_change_only_marks_final_login_as_401() -> None:
    """报告 74：改密后的旧密码登录是最后一步的凭据失败。"""
    compiled, _issues = _compile({
        "name": "【场景】使用旧密码登录失败（验证密码已改）",
        "expected": ["前两步返回 200", "最后使用旧密码登录返回 401"],
        "requests": [
            {
                "name": "登录新账号获取 token",
                "method": "POST",
                "path": "/api/auth/login",
                "body": {"username": "tester", "password": "OldPass123"},
                "extract": {"own_token": "$.data.access_token"},
            },
            {
                "name": "修改密码",
                "method": "PUT",
                "path": "/api/auth/password",
                "headers": {"Authorization": "Bearer ${own_token}"},
                "body": {"old_password": "OldPass123", "new_password": "NewPass456"},
            },
            {
                "name": "使用旧密码登录，验证失败",
                "method": "POST",
                "path": "/api/auth/login",
                "body": {"username": "tester", "password": "OldPass123"},
                "assertion": {"status_code": 401},
            },
        ],
    })

    assert _statuses(compiled) == [200, 200, 401]
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_auth_failure_intent_mismatch_is_blocked_by_preflight() -> None:
    """即使后续回归把编译结果误改成 200，语义硬校验也必须阻止入库。"""
    case = {
        "name": "【鉴权】使用无效refresh_token刷新返回401",
        "method": "POST",
        "path": "/api/auth/refresh",
        "body": {"refresh_token": "invalid_refresh_token_xyz"},
    }
    catalog = build_contract_catalog(_auth_openapi())
    compiled, _issues = compile_generated_case(case, module_id=1, catalog=catalog)
    compiled["steps"][0]["assertion"] = [
        {"type": "equal", "target": "status_code", "expected": 200}
    ]

    errors = validate_compiled_case(compiled, catalog, case)

    assert any("鉴权失败意图" in error and "HTTP 200" in error for error in errors)


def _anyof_catalog(error_status: int = 422) -> dict:
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/api/items": {
                "get": {
                    "operationId": "list_items",
                    "parameters": [{
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {
                            "anyOf": [
                                {"type": "integer", "minimum": 1},
                                {"type": "null"},
                            ],
                        },
                    }],
                    "responses": {
                        "200": {"description": "成功"},
                        str(error_status): {"description": "参数错误"},
                    },
                },
            },
        },
    }
    return build_contract_catalog(document)


def test_anyof_accepts_each_valid_branch_and_rejects_other_types() -> None:
    for value in (1, None):
        compiled, _issues = compile_generated_case(
            {
                "name": "【正常】按可选 limit 查询成功",
                "method": "GET",
                "path": "/api/items",
                "query_params": {"limit": value},
            },
            module_id=1,
            catalog=_anyof_catalog(),
        )
        assert _status(compiled) == 200
        assert compiled["generation_metadata"]["preflight"]["passed"] is True

    compiled, _issues = compile_generated_case(
        {
            "name": "【参数校验】limit 类型错误返回校验失败",
            "method": "GET",
            "path": "/api/items",
            "query_params": {"limit": "wrong"},
        },
        module_id=1,
        catalog=_anyof_catalog(),
    )
    assert _status(compiled) == 422


def test_contract_violation_uses_400_only_when_422_is_not_documented() -> None:
    compiled, _issues = compile_generated_case(
        {
            "name": "【参数校验】limit 类型错误",
            "method": "GET",
            "path": "/api/items",
            "query_params": {"limit": "wrong"},
        },
        module_id=1,
        catalog=_anyof_catalog(error_status=400),
    )

    assert _status(compiled) == 400


def _api_key_catalog() -> dict:
    return build_contract_catalog({
        "openapi": "3.1.0",
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            },
        },
        "paths": {
            "/api/internal": {
                "get": {
                    "operationId": "read_internal",
                    "security": [{"ApiKeyAuth": []}],
                    "responses": {"200": {"description": "成功"}, "401": {"description": "未认证"}},
                },
            },
        },
    })


def test_literal_api_key_cannot_impersonate_an_available_credential() -> None:
    compiled, _issues = compile_generated_case(
        {
            "name": "【正常】使用 API Key 访问成功",
            "method": "GET",
            "path": "/api/internal",
            "headers": {"X-API-Key": "test-key"},
        },
        module_id=1,
        catalog=_api_key_catalog(),
        generation_metadata={"available_variables": []},
    )

    assert compiled["generation_metadata"]["preflight"]["passed"] is False
    assert any("需要认证" in error for error in compiled["generation_metadata"]["preflight"]["errors"])


def test_api_key_variable_is_accepted_only_when_variable_pool_provides_it() -> None:
    compiled, _issues = compile_generated_case(
        {
            "name": "【正常】使用 API Key 访问成功",
            "method": "GET",
            "path": "/api/internal",
            "headers": {"X-API-Key": "${x_api_key}"},
        },
        module_id=1,
        catalog=_api_key_catalog(),
        generation_metadata={"available_variables": ["x_api_key"]},
    )

    assert _status(compiled) == 200
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_positive_auth_wording_does_not_turn_success_into_401() -> None:
    compiled, _issues = _compile({
        "name": "【鉴权】使用有效 token 鉴权成功",
        "method": "POST",
        "path": "/api/users",
        "headers": {"Authorization": "Bearer ${admin_token}"},
        "body": {"username": "AUTO_TEST_user", "password": "Test@123", "role_codes": ["test"]},
    })

    assert _status(compiled) == 200


def test_refresh_route_openapi_exposes_trusted_access_token_path() -> None:
    from fastapi import FastAPI

    from server.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    catalog = build_contract_catalog(
        app.openapi(),
        operation_ids={"refresh_token_api_auth_refresh_post"},
    )
    compiled, issues = compile_generated_case(
        {
            "name": "【正常】刷新 access token 成功",
            "operation_id": "refresh_token_api_auth_refresh_post",
            "method": "POST",
            "path": "/api/auth/refresh",
            "body": {"refresh_token": "${refresh_token}"},
            "extract": {"fresh_access_token": "$.data.access_token"},
            "assertion": {"status_code": 200, "$.data.expires_in": "not_empty"},
        },
        module_id=1,
        catalog=catalog,
        generation_metadata={"available_variables": ["refresh_token"]},
    )

    assert compiled["steps"][0]["extract"] == [{
        "name": "fresh_access_token",
        "from": "response.body",
        "jsonpath": "$.data.access_token",
        "required": True,
    }]
    assert not any("JSONPath" in issue for issue in issues)
    assert compiled["generation_metadata"]["preflight"]["passed"] is True


def test_wrong_old_password_uses_documented_400() -> None:
    document = _auth_openapi()
    document["paths"]["/api/auth/password"]["put"]["responses"]["400"] = {
        "description": "旧密码错误",
    }
    catalog = build_contract_catalog(document)
    compiled, _issues = compile_generated_case(
        {
            "name": "【鉴权】旧密码错误修改密码返回400",
            "method": "PUT",
            "path": "/api/auth/password",
            "headers": {"Authorization": "Bearer ${token}"},
            "body": {"old_password": "wrong", "new_password": "NewPass123"},
        },
        module_id=1,
        catalog=catalog,
    )
    assert _status(compiled) == 400


def test_repeated_logout_idempotency_stays_success_instead_of_409() -> None:
    compiled, _issues = _compile({
        "name": "【幂等】重复调用退出接口仍成功",
        "method": "POST",
        "path": "/api/auth/logout",
        "body": {"refresh_token": None},
    })
    assert _status(compiled) == 200


def test_content_type_mismatch_is_preserved_and_compiled_as_validation_failure() -> None:
    compiled, _issues = _compile({
        "name": "【参数校验】错误 Content-Type 创建用户返回422",
        "method": "POST",
        "path": "/api/users",
        "headers": {
            "Authorization": "Bearer ${admin_token}",
            "Content-Type": "text/plain",
        },
        "body": {"username": "u1", "password": "Test@123", "role_codes": ["test"]},
    })
    assert compiled["steps"][0]["config"]["data_type"] == "text/plain"
    assert _status(compiled) == 422


def test_type_sentinel_is_compiled_to_type_assertion() -> None:
    compiled, _issues = _compile({
        "name": "创建用户成功并校验 id 类型",
        "method": "POST",
        "path": "/api/users",
        "headers": {"Authorization": "Bearer ${admin_token}"},
        "body": {"username": "u2", "password": "Test@123", "role_codes": ["test"]},
        "assertion": {"$.data.id": "type:number"},
    })
    rule = next(item for item in compiled["steps"][0]["assertion"] if item["target"] == "$.data.id")
    assert rule == {"type": "type", "target": "$.data.id", "expected": "number"}
