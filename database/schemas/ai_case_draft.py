"""AI 用例草稿 / 一键生成相关的 Pydantic schema（M7）。

字段语义与 ai_case_drafts 表 1:1，并加上几条触发 / commit 用的辅助请求体。
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


SCENARIO_MIX_POSITIVE_ONLY = "positive_only"
SCENARIO_MIX_POSITIVE_AND_NEGATIVE = "positive_and_negative"
SCENARIO_MIX_ALL = "all_scenarios"
ALL_SCENARIO_MIXES = {
    SCENARIO_MIX_POSITIVE_ONLY,
    SCENARIO_MIX_POSITIVE_AND_NEGATIVE,
    SCENARIO_MIX_ALL,
}


class CaseGenerationTriggerRequest(BaseModel):
    """触发 AI 一键生成测试用例。

    多需求 × 多模型 → 每对 (requirement, model) 起一个 batch（独立 ai_run）。
    UI 截图通过 ui_image_attachment_ids 把已上传的 Attachment.id 传进来。
    """

    # 我们的字段名以 model_ 开头不是 Pydantic 内部命名空间冲突，关闭保护
    model_config = {"protected_namespaces": ()}

    requirement_ids: List[int] = Field(..., min_length=1)
    analysis_document_id: Optional[int] = None
    model_names: List[str] = Field(..., min_length=1)
    count_per_requirement: int = Field(default=5, ge=1, le=30)
    scenario_mix: Literal[
        "positive_only", "positive_and_negative", "all_scenarios"
    ] = "positive_and_negative"
    user_prompt: Optional[str] = None
    # 已上传到 attachments 表的 UI 截图 id（kind=file）
    ui_image_attachment_ids: List[int] = Field(default_factory=list)


class CaseGenerationBatch(BaseModel):
    """触发后返回的单个 batch 信息。"""

    model_config = {"protected_namespaces": ()}

    batch_id: str
    requirement_id: int
    run_id: int
    model_name: str


class CaseGenerationTriggerResponse(BaseModel):
    batches: List[CaseGenerationBatch]


class AiCaseDraftRead(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: int
    requirement_id: int
    analysis_document_id: Optional[int] = None
    ai_run_id: Optional[int] = None
    batch_id: str
    model_label: Optional[str] = None

    title: str
    preconditions: Optional[str] = None
    steps_text: Optional[str] = None
    expected: Optional[str] = None
    priority: int = 2
    tags: List[Any] = Field(default_factory=list)
    step_template: List[Any] = Field(default_factory=list)
    needs_ui_detail: bool = False
    ui_image_refs: List[int] = Field(default_factory=list)
    status: Literal["pending", "accepted", "rejected"] = "pending"
    committed_case_id: Optional[int] = None

    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AiCaseDraftUpdate(BaseModel):
    """PM 在 Review Dialog 内联编辑草稿。所有字段都可选，None = 不改。"""

    title: Optional[str] = None
    preconditions: Optional[str] = None
    steps_text: Optional[str] = None
    expected: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=0, le=3)
    tags: Optional[List[Any]] = None
    step_template: Optional[List[Any]] = None
    needs_ui_detail: Optional[bool] = None


class CommitDraftsRequest(BaseModel):
    """批量入库：勾选若干草稿 → 写 test_cases。"""

    draft_ids: List[int] = Field(..., min_length=1)
    # 不给走每条草稿 requirement.module_id；给了就强制覆盖
    target_module_id: Optional[int] = None


class CommitDraftsResponse(BaseModel):
    created_case_ids: List[int]
    skipped: List[Dict[str, Any]] = Field(default_factory=list)
