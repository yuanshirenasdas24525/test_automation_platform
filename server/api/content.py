"""
/api/content/{project_id} —— 目录树（模块 + 用例混合节点）。

这个接口是前端左侧树的数据源，前端直接遍历。**按老版约定，裸返回数组**（
不裹 `{status, data, message}` 信封）。前端的 lib/api.ts 已经兼容裸数组。

v2 (2026-04-27)：项目栈无关重构。模块树跨栈共享，用例按 case_type 区分。
  - 新增 `case_type` 查询参数：用于项目详情页的栈 Tab 切换
    （api / app / web / functional / mixed；多值用逗号分隔，
     如 `?case_type=api,mixed` 表示"接口栈视图"含 mixed 用例）。
  - 用例节点新增 `case_type` 字段，前端用它渲染对应栈的图标 / 颜色。
  - 模块节点不做 case_type 过滤（模块本身栈无关），始终全量返回。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query

from server.api.deps import DBDep
from database.models import ALL_CASE_TYPES, Module, TestCase

router = APIRouter(prefix="/content", tags=["content"])


def _parse_case_types(raw: Optional[str]) -> Optional[set[str]]:
    """把 `?case_type=api,mixed` 这种字符串解析成集合；空 / None 返回 None 表示不过滤。

    非法值静默丢弃（不抛 400）—— 前端栈 Tab 切换是高频路径，
    宁可返回空也不希望某次拼错就让整个页面报错。
    """
    if not raw:
        return None
    wanted = {t.strip().lower() for t in raw.split(",") if t.strip()}
    valid = wanted & ALL_CASE_TYPES
    return valid or None


@router.get("/{project_id}")
def get_folder_content(
    project_id: int,
    db: DBDep,
    parent_id: Optional[int] = Query(None),
    case_type: Optional[str] = Query(
        None,
        description="按 case_type 过滤用例（多值逗号分隔）。模块节点始终全量返回。",
    ),
):
    """
    列出某项目下 `parent_id` 层级的子模块 + 用例。

    - `parent_id` 传 0 或留空 → 视为根节点，只返顶层模块（不返用例，因为用例归属模块）。
    - 其它层级 → 返回该模块下的子模块 + 该模块下的用例，按 sort_order 混合排序。
    - `case_type` 非空 → 用例只返 case_type ∈ 集合的；模块树不变。
    """
    # 约定：0 当 None 处理（前端部分历史代码传 0）
    effective_parent = None if parent_id in (None, 0) else parent_id
    type_filter = _parse_case_types(case_type)

    modules = (
        db.session.query(Module)
        .filter(
            Module.project_id == project_id,
            Module.parent_id == effective_parent,
        )
        .all()
    )

    # 根层级不列用例
    cases: list[TestCase] = []
    if effective_parent is not None:
        case_q = db.session.query(TestCase).filter(TestCase.module_id == effective_parent)
        if type_filter is not None:
            case_q = case_q.filter(TestCase.case_type.in_(type_filter))
        cases = case_q.all()

    result: list[dict[str, Any]] = []
    for m in modules:
        result.append(
            {
                "id": m.id,
                "name": m.name,
                "type": "module",
                "sort_order": m.sort_order,
                "parent_id": m.parent_id,
            }
        )
    for c in cases:
        result.append(
            {
                "id": c.id,
                "module_id": c.module_id,
                "type": "case",
                # v2 新增：前端按 case_type 决定渲染哪个栈的图标 / 走哪条编辑路径
                "case_type": c.case_type or "api",
                "name": c.name,
                "description": c.description,
                "skip": c.skip,
                "method": c.method,
                "path": c.path,
                "headers": c.headers,
                "data_type": c.data_type,
                "params": c.params,
                "file_path": c.file_path,
                "extract_data": c.extract_data,
                "sql_query": c.sql_query,
                "assertion": c.assertion,
                "wait_time": c.wait_time,
                "sort_order": c.sort_order,
            }
        )

    return sorted(result, key=lambda x: x.get("sort_order") or 0)
