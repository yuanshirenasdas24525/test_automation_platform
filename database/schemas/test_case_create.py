"""创建 / 编辑用例的请求体。

执行定义统一放在 `steps`。method / path / headers 等历史 HTTP 字段只因数据库列
暂时保留而存在，不再作为后端生成 step 的输入。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

import pydantic


class StepPayload(pydantic.BaseModel):
    """用例下发的单步定义。字段名和 test_steps 表一一对应。

    `config` 的具体结构由 step_type 决定，Runner 层自己校验，这里统一用 Dict。
    """
    step_order: int = 0
    step_name: str
    step_type: str
    skip: bool = False
    config: Dict[str, Any] = pydantic.Field(default_factory=dict)
    extract: Optional[List[Dict[str, Any]]] = None
    assertion: Optional[List[Dict[str, Any]]] = None
    wait_before: float = 0
    timeout: int = 30
    retry: int = 0
    on_failure: Literal["stop", "continue", "retry"] = "stop"


class TestCaseCreate(pydantic.BaseModel):
    module_id: int
    name: str

    # ------- 基础描述 / 开关 -------
    description: Optional[str] = None
    skip: bool = False
    sort_order: Optional[int] = None

    # ------- 用例类型 + 步骤 -------
    case_type: Optional[
        Literal["api", "android", "ios", "web", "mixed", "functional"]
    ] = None
    tags: Optional[List[str]] = None
    priority: Optional[int] = None
    steps: Optional[List[StepPayload]] = None

    # ------- 历史 HTTP 字段：暂留到后续删列迁移 -------
    method: Optional[str] = None
    path: Optional[str] = None
    headers: Optional[str] = None
    data_type: Optional[str] = "application/json"
    params: Optional[str] = None
    file_path: Optional[str] = None
    extract_data: Optional[str] = None
    sql_query: Optional[str] = None
    assertion: Optional[str] = None
    wait_time: Optional[int] = 0

    # ------- 单用例重复执行次数（默认 1，不重复）-------
    repeat_count: Optional[int] = 1
