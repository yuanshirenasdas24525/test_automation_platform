"""把已有的需求分析文档回填进记忆层（project_contexts）。

新生成的分析文档会自动回流（tasks/ai_tasks.py::_handle_requirement_analyze Step 6）；
这个脚本处理存量：遍历 requirement_analysis_documents，逐份提取事实条目入库。

用法（项目根目录）：
    venv/bin/python scripts/backfill_contexts.py                # 列出可用 AI 模型 + 待回填文档
    venv/bin/python scripts/backfill_contexts.py <model_name>   # 用该模型执行回填
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def list_status() -> None:
    from database.db import DB
    from database.models import RequirementAnalysisDocument
    from server.services.ai_model_service import list_ai_models

    db = DB()
    try:
        models = [m for m in list_ai_models(db.session) if m.enabled]
        docs = db.session.query(RequirementAnalysisDocument).count()
        print("== 可用 AI 模型 ==")
        for m in models:
            print(f"  {m.name}  ({m.provider} / {m.model})")
        print(f"\n== 待回填分析文档: {docs} 份 ==")
        if models and docs:
            print(f"\n执行: venv/bin/python scripts/backfill_contexts.py {models[0].name}")
    finally:
        db.close()


def backfill(model_name: str) -> None:
    from database.db import DB
    from database.models import Requirement, RequirementAnalysisDocument
    from server.services.ai_model_service import get_ai_model
    from server.services.context_extraction import extract_and_save_contexts

    db = DB()
    try:
        cfg = get_ai_model(db.session, model_name)
        if cfg is None or not cfg.enabled:
            print(f"❌ 模型 {model_name!r} 未配置或未启用")
            sys.exit(1)

        docs = (
            db.session.query(RequirementAnalysisDocument)
            .order_by(RequirementAnalysisDocument.id)
            .all()
        )
        if not docs:
            print("没有分析文档可回填")
            return

        total_created = 0
        for doc in docs:
            proj_id = (
                db.session.query(Requirement.project_id)
                .filter(Requirement.id == doc.requirement_id)
                .scalar()
            )
            if not proj_id:
                print(f"  跳过 doc={doc.id}（找不到所属项目）")
                continue
            try:
                created = extract_and_save_contexts(
                    db.session,
                    markdown=doc.current_markdown or "",
                    project_id=int(proj_id),
                    cfg=cfg,
                    source_file=doc.title or f"analysis_doc_{doc.id}",
                    ai_run_id=None,
                )
                db.commit()
                total_created += len(created)
                print(f"  doc={doc.id} 《{(doc.title or '')[:40]}》 → 新入库 {len(created)} 条")
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                print(f"  ❌ doc={doc.id} 提取失败: {exc}")

        print(f"\n✅ 完成：共新入库 {total_created} 条上下文")
        print("下一步: venv/bin/python scripts/verify_context_injection.py")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        backfill(sys.argv[1])
    else:
        list_status()
