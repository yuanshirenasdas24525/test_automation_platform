"""AI 生成 Web UI 自动化用例请求结构。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class WebUiCaseGenerationRequest(BaseModel):
    """触发功能用例/元素库到 Web 自动化草稿的生成任务。"""

    model_config = {"protected_namespaces": ()}

    project_id: int
    model_name: str = Field(..., min_length=1, max_length=120)
    source_mode: Literal["functional_and_elements", "elements_only"] = "functional_and_elements"
    functional_case_ids: list[int] = Field(default_factory=list, max_length=50)
    page_keys: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    count: int = Field(default=8, ge=1, le=20)
    include_structure_assertions: bool = True
    include_visual_assertions: bool = False
    visual_threshold: float = Field(default=0.02, ge=0, le=1)
    user_prompt: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_mode == "functional_and_elements" and not self.functional_case_ids:
            raise ValueError("联合生成模式至少选择一条功能用例")
        return self


class WebUiCaseDraftUpdate(BaseModel):
    """评审时允许修改的草稿字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    priority: int | None = Field(default=None, ge=0, le=3)
    tags: list[str] | None = Field(default=None, max_length=20)
    variables: dict[str, Any] | None = Field(default=None, max_length=100)
    steps: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_explicit_null(self):
        """字段可以缺省，但非空数据库字段不能显式更新为 null。"""
        non_nullable = {"title", "priority", "tags", "variables", "steps"}
        invalid = [name for name in self.model_fields_set if name in non_nullable and getattr(self, name) is None]
        if invalid:
            raise ValueError(f"字段不能为 null：{', '.join(sorted(invalid))}")
        return self


class WebUiCaseDraftCommitRequest(BaseModel):
    """把评审通过的草稿批量写入正式 Web 用例库。"""

    draft_ids: list[int] = Field(..., min_length=1, max_length=100)
    module_id: int


class WebUiCaseDraftRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
