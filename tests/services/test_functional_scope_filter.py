"""functional_scope_filter 纯函数单测。

跑：./venv/bin/python -m pytest tests/services/test_functional_scope_filter.py -q
"""
from server.services.functional_scope_filter import (
    build_context_compact,
    classify_point_scope,
    dedup_points,
    filter_out_of_scope_points,
)


def _titles(points):
    return [p["title"] for p in points]


def test_drops_infra_and_compliance_and_process_points():
    points = [
        {"title": "管理员用合法账号登录成功并跳转工作台", "category": "正向"},
        {"title": "高并发登录场景下自动伸缩增加实例并正常处理请求", "category": "安全"},
        {"title": "登录接口GDPR合规（用户数据删除）", "category": "安全"},
        {"title": "登录接口技术债务评估", "category": "其它"},
        {"title": "登录接口OAuth2.0集成探索测试", "category": "安全"},
        {"title": "登录时用户名输入超长字符串导致缓冲区溢出", "category": "边界"},
    ]
    kept, dropped = filter_out_of_scope_points(points, "")
    assert _titles(kept) == ["管理员用合法账号登录成功并跳转工作台"]
    assert len(dropped) == 5
    # 每条剔除都带可追溯的分类原因
    assert all(d["_scope_reason"].startswith("out_of_scope:") for d in dropped)


def test_keeps_grounded_negative_and_boundary_points():
    points = [
        {"title": "密码错误时统一提示用户名或密码错误", "category": "异常"},
        {"title": "连续5次密码错误后账号锁定5分钟", "category": "安全"},
        {"title": "用户名输入SQL注入特殊字符被安全处理", "category": "安全"},
        {"title": "越权访问其他用户资源被拒绝", "category": "权限"},
    ]
    kept, dropped = filter_out_of_scope_points(points, "")
    assert dropped == []
    assert len(kept) == 4


def test_project_whitelist_exempts_capability_that_really_exists():
    points = [{"title": "使用微信登录第三方账号成功", "category": "正向"}]
    # 项目上下文里没提 → 判越界
    kept, dropped = filter_out_of_scope_points(points, "本模块为用户名密码登录")
    assert kept == [] and len(dropped) == 1
    # 项目上下文里确有"微信登录" → 白名单豁免放行
    kept2, dropped2 = filter_out_of_scope_points(
        points, "本项目支持微信登录与账号密码登录两种方式"
    )
    assert len(kept2) == 1 and dropped2 == []


def test_matches_across_interspersed_whitespace():
    is_out, reason = classify_point_scope("负 载 均 衡 分发登录请求", build_context_compact(""))
    assert is_out and "infra" in reason


def test_ignores_non_dict_and_blank_titles():
    points = [{"title": "", "category": "正向"}, "not-a-dict", {"category": "正向"}]
    kept, dropped = filter_out_of_scope_points(points, "")
    # 空标题保留（结构校验另有其人），非 dict 丢弃，不抛异常
    assert dropped == []
    assert len(kept) == 2


def test_dedup_points_removes_detailed_and_generic_near_duplicates():
    points = [
        {"title": "登录接口请求体格式错误（非JSON）返回422", "category": "异常"},
        {"title": "登录接口请求体格式错误（非JSON）", "category": "异常"},
        {"title": "连续5次密码错误后账号锁定", "category": "安全"},
        {"title": "【异常】连续5次密码错误后账号锁定", "category": "异常"},
        {"title": "管理员登录成功跳转工作台", "category": "正向"},
    ]
    kept, dropped = dedup_points(points)
    kept_titles = [p["title"] for p in kept]
    # 两组近重复各自只留一条 + 唯一的正向点 = 3 条
    assert len(kept) == 3
    assert len(dropped) == 2
    assert "管理员登录成功跳转工作台" in kept_titles


def test_dedup_points_keeps_distinct_points():
    points = [
        {"title": "用户名为空时提示请输入用户名", "category": "异常"},
        {"title": "密码为空时提示请输入密码", "category": "异常"},
        {"title": "用户名密码均为空时两个输入框都提示", "category": "异常"},
    ]
    kept, dropped = dedup_points(points)
    assert dropped == []
    assert len(kept) == 3
