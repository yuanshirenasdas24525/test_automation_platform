"""AI 接口用例的入库门禁与人工待调整状态。

未通过生成期硬校验的用例默认拒绝入库。唯一例外是用户明确选择保留该用例时，
它必须以“需人工调整 + 跳过执行”状态保存，避免进入执行报告、污染通过率。
"""
from __future__ import annotations

from typing import Any


AI_INTERFACE_SOURCE = "ai_interface"
MANUAL_ADJUSTMENT_TAG = "需人工调整"


def manual_adjustment_reasons(metadata: Any) -> list[str]:
    """返回规范化后的人工调整原因。"""
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("manual_adjustment_reasons") or []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def needs_manual_adjustment(metadata: Any) -> bool:
    """判断生成元数据是否处于“需人工调整”状态。"""
    return isinstance(metadata, dict) and metadata.get("needs_manual_adjustment") is True


def _preflight_passed(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    preflight = metadata.get("preflight")
    return (
        isinstance(preflight, dict)
        and preflight.get("passed") is True
        and not preflight.get("errors")
    )


def _manual_resolution_recorded(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    preflight = metadata.get("preflight")
    return (
        metadata.get("needs_manual_adjustment") is False
        and metadata.get("manual_adjustment_status") == "resolved"
        and isinstance(preflight, dict)
        and preflight.get("manual_override") is True
    )


def validate_ai_interface_admission(
    payload: dict[str, Any],
    *,
    previous_metadata: Any = None,
    previous_source: Any = None,
    steps_provided: bool = False,
) -> None:
    """校验 AI 接口用例是否允许保存。

    规则：
    1. 生成期硬校验通过，可正常保存；
    2. 硬校验未通过，只能带原因、标记为需人工调整并设为 skip；
    3. 已入库的待调整用例，用户在完整编辑器保存了 steps 后可显式解除标记；
       解除动作会留下 manual_override 审计信息，后续普通编辑不再重复阻断。
    """
    previous_pending = needs_manual_adjustment(previous_metadata)
    # 只有"原本就是 ai_interface 的待调整用例"被改走 source 才算绕过本门禁；
    # web 等其它来源的待调整用例(账号数据门禁)不归这个 API 契约门禁管。
    previous_was_ai_interface = str(previous_source or "") == AI_INTERFACE_SOURCE
    if str(payload.get("source") or "manual") != AI_INTERFACE_SOURCE:
        if previous_pending and previous_was_ai_interface:
            raise ValueError("需人工调整的 AI 接口用例不能通过修改 source 绕过入库门禁")
        return

    metadata = payload.get("generation_metadata")
    pending = needs_manual_adjustment(metadata)
    reasons = manual_adjustment_reasons(metadata)
    skipped = payload.get("skip") is True

    if pending:
        if skipped and reasons:
            return
        raise ValueError("需人工调整的 AI 接口用例必须填写原因并设为跳过执行")

    if _preflight_passed(metadata):
        return

    resolved = _manual_resolution_recorded(metadata)
    previous_resolved = _manual_resolution_recorded(previous_metadata)
    if resolved and (previous_resolved or (previous_pending and steps_provided and not skipped)):
        return

    raise ValueError(
        "AI 接口用例未通过契约编译硬校验；如需保留，请标记为需人工调整并跳过执行"
    )
