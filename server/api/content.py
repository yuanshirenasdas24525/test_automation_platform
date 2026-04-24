"""
/api/content/{project_id} —— 目录树（模块 + 用例混合节点）。

这个接口是前端左侧树的数据源，前端直接遍历。**按老版约定，裸返回数组**（
不裹 `{status, data, message}` 信封）。前端的 lib/api.ts 已经兼容裸数组。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query

from server.api.deps import DBDep
from database.models import Module, TestCase

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/{project_id}")
def get_folder_content(
    project_id: int,
    db: DBDep,
    parent_id: Optional[int] = Query(None),
):
    """
    列出某项目下 `parent_id` 层级的子模块 + 用例。

    - `parent_id` 传 0 或留空 → 视为根节点，只返顶层模块（不返用例，因为用例归属模块）。
    - 其它层级 → 返回该模块下的子模块 + 该模块下的用例，按 sort_order 混合排序。
    """
    # 约定：0 当 None 处理（前端部分历史代码传 0）
    effective_parent = None if parent_id in (None, 0) else parent_id

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
        cases = (
            db.session.query(TestCase)
            .filter(TestCase.module_id == effective_parent)
            .all()
        )

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
