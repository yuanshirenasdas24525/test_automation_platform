"""为已入库的 AI Web 用例回填账号数据需求。

默认只预览；传 ``--commit`` 才写库。
"""
from __future__ import annotations

import argparse

from database.db import DB
from database.models import Module, TestCase, UiAutomationCaseDraft
from server.services.test_accounts import (
    infer_account_requirement,
    validate_account_requirement,
)


def backfill(project_id: int, *, commit: bool) -> dict[str, int | bool]:
    db = DB()
    session = db.session
    try:
        cases = (
            session.query(TestCase)
            .join(Module, Module.id == TestCase.module_id)
            .filter(
                Module.project_id == project_id,
                TestCase.case_type == "web",
                TestCase.source == "ai_m8_web",
            )
            .order_by(TestCase.id)
            .all()
        )
        blocked = 0
        ready = 0
        for case in cases:
            requirement = infer_account_requirement(
                case.name,
                case.description,
                case.variables or {},
            )
            metadata = dict(case.generation_metadata or {})
            metadata["test_data_requirement"] = requirement
            manual_reasons = list(metadata.get("manual_reasons") or [])
            tags = list(case.tags or [])
            data_errors = validate_account_requirement(session, project_id, requirement)
            if data_errors:
                for reason in data_errors:
                    if reason not in manual_reasons:
                        manual_reasons.append(reason)
                if "test-data-blocked" not in tags:
                    tags.append("test-data-blocked")
                if "需人工调整" not in tags:
                    tags.append("需人工调整")
                case.skip = True
                blocked += 1
            else:
                ready += 1
            metadata["manual_reasons"] = manual_reasons
            if data_errors:
                metadata["needs_manual_adjustment"] = True
                metadata["manual_adjustment_status"] = "pending"
                metadata["manual_adjustment_reasons"] = manual_reasons
            case.generation_metadata = metadata
            case.tags = tags

            draft_id = metadata.get("draft_id")
            if draft_id:
                draft = (
                    session.query(UiAutomationCaseDraft)
                    .filter(UiAutomationCaseDraft.id == int(draft_id))
                    .one_or_none()
                )
                if draft:
                    evidence = dict(draft.evidence or {})
                    evidence["test_data_requirement"] = requirement
                    draft.evidence = evidence
                    if data_errors:
                        draft_reasons = list(draft.manual_reasons or [])
                        for reason in data_errors:
                            if reason not in draft_reasons:
                                draft_reasons.append(reason)
                        draft.manual_reasons = draft_reasons

        if commit:
            session.commit()
        else:
            session.rollback()
        return {
            "total": len(cases),
            "ready": ready,
            "blocked": blocked,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    result = backfill(
        args.project_id,
        commit=args.commit,
    )
    mode = "已写入" if args.commit else "仅预览"
    print(f"{mode}: {result}")


if __name__ == "__main__":
    main()
