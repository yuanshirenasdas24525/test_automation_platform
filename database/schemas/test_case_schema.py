"""
TestCase v2 Pydantic schemas。
与 models/test_case_create.py（v1 API 形状）并存，逐步替换。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from .test_step_schema import TestStepCreate, TestStepRead


CaseType = Literal["api", "android", "ios", "web", "mixed", "functional"]


class _TestCaseBase(BaseModel):
    name: str
    description: Optional[str] = None
    case_type: CaseType = "api"
    tags: Optional[List[str]] = None
    skip: bool = False
    priority: int = 2
    sort_order: int = 0

    env_id: Optional[int] = None
    pre_hook: Optional[List[Dict[str, Any]]] = None
    post_hook: Optional[List[Dict[str, Any]]] = None
    variables: Optional[Dict[str, Any]] = None
    timeout: int = 60
    retry: int = 0


class TestCaseCreateV2(_TestCaseBase):
    module_id: int
    # 创建用例时可以同时带上步骤（推荐）
    steps: Optional[List[TestStepCreate]] = None


class TestCaseUpdateV2(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    case_type: Optional[CaseType] = None
    tags: Optional[List[str]] = None
    skip: Optional[bool] = None
    priority: Optional[int] = None
    sort_order: Optional[int] = None
    env_id: Optional[int] = None
    pre_hook: Optional[List[Dict[str, Any]]] = None
    post_hook: Optional[List[Dict[str, Any]]] = None
    variables: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = None
    retry: Optional[int] = None


class TestCaseReadV2(_TestCaseBase):
    id: int
    module_id: int
    steps: List[TestStepRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
