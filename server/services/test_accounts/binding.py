from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models.script_store import SCRIPT_KIND_WORKFLOW
from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.requirements import infer_account_requirement
from server.services.test_accounts.resolver import (
    ResolvedAccount,
    resolve_account,
    validate_account_requirement,
)
from server.services.test_accounts.sources import load_account_sources
from utils.script_runtime import run_named_script


def prepare_web_test_data(
    session: Session, cases: list[dict[str, Any]], *, project_id: int
) -> list[dict[str, Any]]:
    """运行前为每条 web 用例绑定账号变量，返回需清理的脚本令牌。"""
    sources = load_account_sources(session, project_id)
    cleanup_tokens: list[dict[str, Any]] = []
    for case in cases:
        metadata = dict(case.get("generation_metadata") or {})
        requirement = metadata.get("test_data_requirement")
        if not isinstance(requirement, dict):
            if str(case.get("source") or "") != "ai_m8_web":
                continue
            requirement = infer_account_requirement(
                case.get("name"), case.get("description"), case.get("variables") or {}
            )
        # 用户已"完成调整"(needs_manual_adjustment=False 且 manual_adjustment_status
        # =resolved)的用例:尊重其判断,跳过运行前数据门禁,尽力绑账号、绑不上也放行执行
        # (让断言自然失败),而不是继续以"测试数据未就绪"阻断。默认(未解除)仍照常拦。
        manually_resolved = (
            metadata.get("needs_manual_adjustment") is False
            and metadata.get("manual_adjustment_status") == "resolved"
        )
        if not manually_resolved:
            errors = validate_account_requirement(
                session, project_id, requirement, sources=sources
            )
            if errors:
                raise WebTestDataError(
                    f"用例“{case.get('name')}”测试数据未就绪：{'；'.join(errors)}"
                )
        try:
            resolved = resolve_account(
                requirement, sources, session=session, project_id=project_id
            )
        except WebTestDataError:
            if not manually_resolved:
                raise
            resolved = ResolvedAccount()  # 已解除:绑不上也放行,按步骤自带值执行
        if resolved.bindings:
            variables = dict(case.get("variables") or {})
            variables.update(resolved.bindings)
            case["variables"] = variables
        if resolved.cleanup_token is not None:
            cleanup_tokens.append(resolved.cleanup_token)
            # 令牌必须挂回 case：cases 会被序列化进 Celery 载荷，run_test_task 收尾时
            # 从 case["_test_data_cleanup_tokens"] 重建令牌来清理脚本造的号。只返回列表
            # 传不过进程边界（runs.py 不转发返回值），会导致动态账号泄漏。
            case_tokens = list(case.get("_test_data_cleanup_tokens") or [])
            case_tokens.append(resolved.cleanup_token)
            case["_test_data_cleanup_tokens"] = case_tokens
    return cleanup_tokens


def cleanup_web_test_accounts(
    tokens: list[dict[str, Any]], *, project_id: int | None = None
) -> None:
    """任务收尾：对脚本造的号调 workflow 清理。静态池账号无需清理。"""
    for token in tokens or []:
        script_name = str(token.get("script_name") or "").strip()
        if not script_name:
            continue
        pid = token.get("project_id") if project_id is None else project_id
        try:
            run_named_script(
                script_name,
                kind=SCRIPT_KIND_WORKFLOW,
                project_id=pid,
                body={"action": "cleanup", "cleanup": token.get("payload")},
                config={"project_id": pid},
                vars={},
                timeout=30,
            )
        except Exception:  # noqa: BLE001 —— 清理失败不阻塞收尾
            pass
