"""
TestStep 的 Pydantic v2 schemas。

提取规则 (ExtractRule) 和断言 (Assertion) 是强类型的；
config 本身是 Dict[str, Any]，因为不同 step_type 的字段差异太大，
由各 Runner 根据 step_type 自己做 schema 校验（建议未来用 JSON Schema）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


# ============ 子结构 ============

class ExtractRule(BaseModel):
    """变量提取规则，用于把某一步的输出存成变量供后续步骤使用"""
    name: str = Field(..., description="变量名")
    from_: str = Field(..., alias="from",
                       description="提取来源：response.body | response.header | text | attr")
    jsonpath: Optional[str] = Field(None, description="JSONPath 或 XPath 或属性名")
    regex: Optional[str] = Field(None, description="可选正则提取")
    default: Optional[Any] = Field(None, description="提取失败时的默认值")

    model_config = ConfigDict(populate_by_name=True)


class Assertion(BaseModel):
    """断言规则"""
    type: Literal[
        "equal", "not_equal", "contains", "not_contains",
        "jsonpath", "text_equal", "text_contains",
        "regex", "gt", "lt", "in", "not_null", "is_not_null", "is_null",
    ] = Field(..., description="断言类型")
    target: Optional[str] = Field(None, description="断言目标（JSONPath / 元素 locator / 字段路径）")
    expected: Any = Field(None, description="期望值")
    description: Optional[str] = None


# ============ Step 主体 ============

class _TestStepBase(BaseModel):
    step_order: int = 0
    step_name: str
    step_type: str
    skip: bool = False
    config: Dict[str, Any] = Field(default_factory=dict)
    extract: Optional[List[ExtractRule]] = None
    assertion: Optional[List[Assertion]] = None
    wait_before: float = 0
    timeout: int = 30
    retry: int = 0
    on_failure: Literal["stop", "continue", "retry"] = "stop"


class TestStepCreate(_TestStepBase):
    case_id: int


class TestStepUpdate(BaseModel):
    """所有字段都可选，用于 PATCH"""
    step_order: Optional[int] = None
    step_name: Optional[str] = None
    step_type: Optional[str] = None
    skip: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None
    extract: Optional[List[ExtractRule]] = None
    assertion: Optional[List[Assertion]] = None
    wait_before: Optional[float] = None
    timeout: Optional[int] = None
    retry: Optional[int] = None
    on_failure: Optional[Literal["stop", "continue", "retry"]] = None


class TestStepRead(_TestStepBase):
    id: int
    case_id: int

    model_config = ConfigDict(from_attributes=True)
