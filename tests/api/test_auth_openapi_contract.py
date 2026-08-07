"""认证/用户 OpenAPI 必须准确描述真实鉴权方式、状态码和角色枚举。"""
from __future__ import annotations

from server.main import app


def _operation(path: str, method: str) -> dict:
    return app.openapi()["paths"][path][method]


def test_auth_and_user_routes_do_not_advertise_unusable_api_key() -> None:
    for path, method in (
        ("/api/auth/me", "get"),
        ("/api/auth/sessions", "get"),
        ("/api/auth/logout-all", "post"),
        ("/api/auth/password", "put"),
        ("/api/users", "post"),
    ):
        assert _operation(path, method).get("security") == [{"HTTPBearer": []}]


def test_auth_business_error_statuses_are_documented() -> None:
    assert {"200", "401", "422", "429"} <= set(_operation("/api/auth/login", "post")["responses"])
    assert {"200", "400", "401", "403", "422"} <= set(
        _operation("/api/auth/password", "put")["responses"]
    )
    assert {"200", "401", "403", "409", "422"} <= set(
        _operation("/api/users", "post")["responses"]
    )


def test_user_role_codes_are_exposed_as_enum() -> None:
    schema = app.openapi()["components"]["schemas"]["UserCreate"]
    role_items = schema["properties"]["role_codes"]["items"]
    assert set(role_items["enum"]) == {"admin", "dev", "ops", "pm", "test", "ui"}
