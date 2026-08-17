"""修复已入库 AI Web 登录成功用例的跳转后断言。

默认仅预览；传 ``--commit`` 才写库。脚本只处理账号需求为正确凭据的 AI Web
用例，删除点击登录后仍等待登录按钮或虚构“登录成功”页面文案的步骤，改为断言
账号类型对应的真实工作台标题。
"""
from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy.orm import selectinload

from database.db import DB
from database.models import Module, TestCase, TestStep, UiAutomationCaseDraft, UiElement
from server.services.test_accounts import infer_account_requirement


_SUPPORTED_LOCATORS = {"css", "xpath", "id", "name", "class", "text", "link"}
_SUCCESS_PROFILES = {"shared_admin", "dynamic_active", "dynamic_boundary"}


def _preferred_locator(element: UiElement) -> tuple[str, str]:
    candidates = [
        item for item in element.locators
        if str(item.strategy or "").lower() in _SUPPORTED_LOCATORS
        and str(item.locator or "").strip()
    ]
    if not candidates:
        raise ValueError(f"元素 #{element.id} 没有可执行定位器")
    selected = max(
        candidates,
        key=lambda item: (
            int(bool(item.is_primary)),
            int(bool(item.is_unique)),
            int(item.score or 0),
            int(item.id or 0),
        ),
    )
    return str(selected.strategy).lower(), str(selected.locator)


def _step_payload(step: TestStep) -> dict[str, Any]:
    return {
        "step_order": step.step_order,
        "step_name": step.step_name,
        "step_type": step.step_type,
        "skip": bool(step.skip),
        "config": dict(step.config or {}),
        "extract": list(step.extract or []),
        "assertion": list(step.assertion or []),
        "wait_before": float(step.wait_before or 0),
        "timeout": int(step.timeout or 30),
        "retry": int(step.retry or 0),
        "on_failure": str(step.on_failure or "stop"),
    }


def _is_contradictory_step(step: TestStep, submit_element_id: int) -> bool:
    config = step.config or {}
    if int(config.get("element_id") or 0) == submit_element_id:
        return True
    expected = str(config.get("contains") or config.get("equals") or "").strip().lower()
    return step.step_type == "web_assert_text" and expected in {
        "登录成功",
        "login success",
        "login successful",
    }


def repair(project_id: int, *, commit: bool) -> dict[str, int]:
    db = DB()
    session = db.session
    try:
        destinations = (
            session.query(UiElement)
            .options(selectinload(UiElement.locators))
            .filter(
                UiElement.project_id == project_id,
                UiElement.platform == "web",
                UiElement.semantic_name.in_(["测试工作台", "管理员工作台"]),
            )
            .order_by(UiElement.id.desc())
            .all()
        )
        destination_map: dict[str, UiElement] = {}
        for element in destinations:
            destination_map.setdefault(element.semantic_name, element)

        cases = (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .join(Module, Module.id == TestCase.module_id)
            .filter(
                Module.project_id == project_id,
                TestCase.case_type == "web",
                TestCase.source == "ai_m8_web",
            )
            .order_by(TestCase.id)
            .all()
        )
        matched = 0
        repaired = 0
        removed = 0
        for case in cases:
            metadata = dict(case.generation_metadata or {})
            requirement = metadata.get("test_data_requirement")
            if not isinstance(requirement, dict):
                requirement = infer_account_requirement(case.name, case.description, case.variables or {})
            if (
                requirement.get("credential_mode") != "correct"
                or requirement.get("profile") not in _SUCCESS_PROFILES
            ):
                continue
            matched += 1

            ordered_steps = sorted(case.steps, key=lambda item: item.step_order)
            submit_index = next(
                (
                    index for index, step in enumerate(ordered_steps)
                    if step.step_type == "web_click"
                    and "login-submit" in str((step.config or {}).get("locator") or "").lower()
                ),
                -1,
            )
            if submit_index < 0:
                continue
            submit_element_id = int((ordered_steps[submit_index].config or {}).get("element_id") or 0)
            destination_name = (
                "管理员工作台" if requirement.get("profile") == "shared_admin" else "测试工作台"
            )
            destination = destination_map.get(destination_name)
            if destination is None:
                raise ValueError(f"元素库缺少“{destination_name}”，无法修复用例 #{case.id}")
            by, locator = _preferred_locator(destination)

            kept = ordered_steps[:submit_index + 1]
            for step in ordered_steps[submit_index + 1:]:
                if _is_contradictory_step(step, submit_element_id):
                    session.delete(step)
                    removed += 1
                else:
                    kept.append(step)
            destination_step = next(
                (
                    step for step in kept
                    if int((step.config or {}).get("element_id") or 0) == destination.id
                    and step.step_type == "web_wait"
                    and (step.config or {}).get("assertion_kind") == "visible"
                ),
                None,
            )
            if destination_step is None:
                destination_step = TestStep(
                    case_id=case.id,
                    step_order=len(kept) + 1,
                    step_name=f"断言 {destination_name} 可见",
                    step_type="web_wait",
                    skip=False,
                    config={
                        "by": by,
                        "locator": locator,
                        "element_id": destination.id,
                        "state": "visible",
                        "assertion_kind": "visible",
                    },
                    extract=[],
                    assertion=[],
                    wait_before=0,
                    timeout=30,
                    retry=0,
                    on_failure="stop",
                )
                session.add(destination_step)
                kept.append(destination_step)
            for order, step in enumerate(kept, start=1):
                step.step_order = order

            warning = "已将成功登录后的断言修复为目标工作台可见"
            warnings = list(metadata.get("warnings") or [])
            if warning not in warnings:
                warnings.append(warning)
            evidence = dict(metadata.get("evidence") or {})
            evidence_ids = [int(item) for item in evidence.get("element_ids") or []]
            if destination.id not in evidence_ids:
                evidence_ids.append(destination.id)
            evidence["element_ids"] = sorted(set(evidence_ids))
            evidence_pages = [str(item) for item in evidence.get("page_keys") or []]
            if destination.page_key not in evidence_pages:
                evidence_pages.append(destination.page_key)
            evidence["page_keys"] = sorted(set(evidence_pages))
            metadata["warnings"] = warnings
            metadata["evidence"] = evidence
            case.generation_metadata = metadata

            draft_id = metadata.get("draft_id")
            if draft_id:
                draft = session.query(UiAutomationCaseDraft).filter(
                    UiAutomationCaseDraft.id == int(draft_id)
                ).one_or_none()
                if draft:
                    session.flush()
                    draft.steps = [_step_payload(step) for step in kept]
                    draft_evidence = dict(draft.evidence or {})
                    draft_evidence["element_ids"] = evidence["element_ids"]
                    draft_evidence["page_keys"] = evidence["page_keys"]
                    draft.evidence = draft_evidence
                    draft_warnings = list(draft.warnings or [])
                    if warning not in draft_warnings:
                        draft_warnings.append(warning)
                    draft.warnings = draft_warnings
            repaired += 1

        if commit:
            session.commit()
        else:
            session.rollback()
        return {"matched": matched, "repaired": repaired, "removed_steps": removed}
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    result = repair(args.project_id, commit=args.commit)
    print(f"{'已写入' if args.commit else '仅预览'}: {result}")


if __name__ == "__main__":
    main()
