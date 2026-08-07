"""接口文档 URL 识别与平台自身 Swagger 契约恢复回归。"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from server.api.functional_cases import (
    _extract_doc_urls,
    _fetch_openapi_catalog_url,
    _local_platform_openapi,
    _normalize_pre_hook,
)


def test_extract_doc_urls_from_free_text_and_fix_missing_h() -> None:
    urls = _extract_doc_urls(
        "登录 http://127.0.0.1:54351/docs#/auth/login_api_auth_login_post\n"
        "注册 ttp://127.0.0.1:54351/docs#/users/create_user_api_users_post。"
    )

    assert urls == [
        "http://127.0.0.1:54351/docs#/auth/login_api_auth_login_post",
        "http://127.0.0.1:54351/docs#/users/create_user_api_users_post",
    ]


def test_self_swagger_uses_local_app_schema_without_ssrf_request(monkeypatch) -> None:
    fake_main = ModuleType("server.main")
    fake_main.app = SimpleNamespace(
        openapi=lambda: {
            "openapi": "3.1.0",
            "paths": {
                "/api/auth/login": {
                    "post": {
                        "operationId": "login_api_auth_login_post",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/api/auth/me": {
                    "get": {
                        "operationId": "me_api_auth_me_get",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
            },
        }
    )
    monkeypatch.setitem(sys.modules, "server.main", fake_main)

    url = "http://127.0.0.1:54351/docs#/auth/login_api_auth_login_post"
    assert _local_platform_openapi(url) is not None
    catalog = _fetch_openapi_catalog_url(url)

    assert catalog is not None
    assert [(op["method"], op["path"]) for op in catalog["operations"]] == [
        ("POST", "/api/auth/login")
    ]


def test_other_loopback_port_is_not_treated_as_platform_self() -> None:
    assert _local_platform_openapi("http://127.0.0.1:9999/docs") is None


def test_pre_hook_keeps_partitioned_json_body() -> None:
    hooks = _normalize_pre_hook([
        {
            "type": "http_request",
            "config": {
                "method": "POST",
                "path": "/api/auth/login",
                "json": {"username": "${user_admin}", "password": "${password_admin}"},
                "extract_data": {"token": "$.data.access_token"},
            },
        }
    ])

    config = hooks[0]["config"]
    assert config["json"] == {"username": "${user_admin}", "password": "${password_admin}"}
    assert "params" not in config
