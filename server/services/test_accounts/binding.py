from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from database.models.script_store import SCRIPT_KIND_WORKFLOW
from server.services.test_accounts.errors import WebTestDataError
from server.services.test_accounts.requirements import infer_account_requirement
from server.services.test_accounts.resolver import (
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
        errors = validate_account_requirement(session, project_id, requirement)
        if errors:
            raise WebTestDataError(
                f"用例“{case.get('name')}”测试数据未就绪：{'；'.join(errors)}"
            )
        resolved = resolve_account(
            requirement, sources, session=session, project_id=project_id
        )
        if resolved.bindings:
            variables = dict(case.get("variables") or {})
            variables.update(resolved.bindings)
            case["variables"] = variables
        if resolved.cleanup_token is not None:
            cleanup_tokens.append(resolved.cleanup_token)
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
