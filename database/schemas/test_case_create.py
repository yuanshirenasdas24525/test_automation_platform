"""
创建 / 编辑用例的请求体（v1 + v2 混合兼容版）。

历史背景：
  - v1 schema 是"一条用例 = 一个 HTTP 请求"，所以字段里有 method/path/headers 这些
    HTTP 专用字段。API 项目到现在还在用这个形状。
  - v2 之后 TestCase 的真正执行步骤下沉到 `test_steps` 表，web/app/mixed 用例必须
    带 `steps` 字段一起创建 / 更新。纯 API 用例要么走老的 HTTP 字段（后端 CaseExecutor
    v1 兼容分支会合成一条 step），要么也走 steps。

因此这里把两种形态都放在同一个 schema 里：
  - 旧字段（method / path / headers / ...）保留，API 用例可以继续用；
  - 新增 `case_type` 明示用例类型（api / app / web / mixed），默认 api；
  - 新增 `steps`：一个 step 字典列表，字段与 TestStep 模型一致。web / app 用例**必传**，
    API 用例可选（不传就走 v1 兼容路径）。

修订点（2026-04）：
  - Pydantic v2：所有 `Optional[X]` 都显式加 `= None`，避免被当成必填。
  - 新增 `sort_order`：让"指定位置插入"的前端功能真正生效。
  - `wait_time: Optional[int] = 0`（旧代码 `int = None` 类型错误）。
  - method / path / assertion / description → Optional，与 TestCase model 对齐。
  - 2026-04 再加：`case_type` + `steps` + `tags` + `priority`，支持 web/app 用例。
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

    # ------- v2 新增：用例类型 + 步骤 -------
    case_type: Optional[
        Literal["api", "android", "ios", "web", "mixed", "functional"]
    ] = None
    tags: Optional[List[str]] = None
    priority: Optional[int] = None
    steps: Optional[List[StepPayload]] = None

    # ------- v1 HTTP 特有字段（App / Web 用例可以不传） -------
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
