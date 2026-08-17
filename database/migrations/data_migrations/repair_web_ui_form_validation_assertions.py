"""修复已入库 AI Web 表单校验文案断言的目标元素。

默认仅预览；传 ``--commit`` 才写库。动态错误节点在基础录制状态下可能不存在，
旧草稿会把“请输入用户名”断言到静态“用户名”标签。本脚本把这类断言改到同页
已录制的根元素上。
"""
from __future__ import annotations

import argparse

from sqlalchemy.orm import selectinload

from database.db import DB
from database.models import Module, TestCase, UiAutomationCaseDraft, UiElement


def _draft_step_payload(step) -> dict:
    payload = step.to_dict()
    payload.pop("id", None)
    payload.pop("case_id", None)
    return payload


def repair(project_id: int, *, commit: bool) -> dict[str, int]:
    db = DB()
    session = db.session
    try:
        root = (
            session.query(UiElement)
            .options(selectinload(UiElement.locators))
            .filter(
                UiElement.project_id == project_id,
                UiElement.platform == "web",
                UiElement.page_key.ilike("%/login"),
                UiElement.element_type == "html",
            )
            .order_by(UiElement.usage_count.desc(), UiElement.id.desc())
            .first()
        )
        if root is None:
            raise ValueError("元素库缺少登录页根元素")
        locator = next(
            (
                item for item in root.locators
                if item.strategy == "css" and item.locator == "html"
            ),
            None,
        )
        if locator is None:
            raise ValueError("登录页根元素缺少 css=html 定位器")

        cases = (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .join(Module, Module.id == TestCase.module_id)
            .filter(
                Module.project_id == project_id,
                TestCase.case_type == "web",
                TestCase.source == "ai_m8_web",
            )
            .all()
        )
        repaired_cases = 0
        repaired_steps = 0
        normalized_drafts = 0
        warning = "已将动态表单校验文案改为登录页根元素断言"
        for case in cases:
            changed = False
            for step in case.steps:
                if step.step_type != "web_assert_text":
                    continue
                config = dict(step.config or {})
                expected = str(config.get("contains") or config.get("equals") or "").strip()
                if not expected.startswith("请输入") or config.get("locator") == "html":
                    continue
                config.update({"by": "css", "locator": "html", "element_id": root.id})
                step.config = config
                step.step_name = f"断言页面提示 {expected}"
                changed = True
                repaired_steps += 1

            metadata = dict(case.generation_metadata or {})
            warnings = list(metadata.get("warnings") or [])
            if not changed and warning not in warnings:
                continue
            if warning not in warnings:
                warnings.append(warning)
            evidence = dict(metadata.get("evidence") or {})
            evidence_ids = [int(item) for item in evidence.get("element_ids") or []]
            evidence_ids.append(root.id)
            evidence["element_ids"] = sorted(set(evidence_ids))
            metadata["warnings"] = warnings
            metadata["evidence"] = evidence
            case.generation_metadata = metadata

            draft_id = metadata.get("draft_id")
            if draft_id:
                draft = session.query(UiAutomationCaseDraft).filter(
                    UiAutomationCaseDraft.id == int(draft_id)
                ).one_or_none()
                if draft:
                    draft.steps = [
                        _draft_step_payload(step)
                        for step in sorted(case.steps, key=lambda item: item.step_order)
                    ]
                    draft_evidence = dict(draft.evidence or {})
                    draft_ids = [int(item) for item in draft_evidence.get("element_ids") or []]
                    draft_ids.append(root.id)
                    draft_evidence["element_ids"] = sorted(set(draft_ids))
                    draft.evidence = draft_evidence
                    draft_warnings = list(draft.warnings or [])
                    if warning not in draft_warnings:
                        draft_warnings.append(warning)
                    draft.warnings = draft_warnings
                    normalized_drafts += 1
            if changed:
                repaired_cases += 1

        if commit:
            session.commit()
        else:
            session.rollback()
        return {
            "repaired_cases": repaired_cases,
            "repaired_steps": repaired_steps,
            "normalized_drafts": normalized_drafts,
        }
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
