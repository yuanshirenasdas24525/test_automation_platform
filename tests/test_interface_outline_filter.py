from __future__ import annotations

from server.api.functional_cases import _filter_interface_outline_points


def _catalog() -> dict:
    return {
        "operations": [
            {"method": "POST", "path": "/api/auth/login"},
            {"method": "POST", "path": "/api/auth/refresh"},
            {"method": "GET", "path": "/api/auth/me"},
        ],
    }


def test_outline_filter_removes_non_executable_points() -> None:
    points = [
        {"title": "POST /api/auth/login 合法凭据登录成功", "category": "正常"},
        {"title": "GET /api/auth/login 错误方法调用", "category": "参数校验"},
        {"title": "使用错误方法访问登录接口返回 405", "category": "参数校验"},
        {"title": "使用 X-API-Key 访问个人信息", "category": "鉴权"},
        {"title": "发送原始畸形 JSON 请求体", "category": "安全"},
        {"title": "并发刷新令牌验证竞态", "category": "场景"},
        {"title": "快速连续调用刷新接口并逐次校验", "category": "场景"},
    ]

    filtered = _filter_interface_outline_points(points, _catalog(), {"token"})

    assert [point["title"] for point in filtered] == [
        "POST /api/auth/login 合法凭据登录成功",
        "快速连续调用刷新接口并逐次校验",
    ]


def test_outline_filter_keeps_api_key_point_only_with_real_variable() -> None:
    point = [{"title": "使用 X-API-Key 访问个人信息", "category": "鉴权"}]

    assert _filter_interface_outline_points(point, _catalog(), {"x_api_key"}) == point
