"""一次性数据迁移：知识库文档 project_contexts → knowledge_documents。

把现有 ``project_contexts WHERE source_type='knowledge'`` 的每一行，建一条对应的
``knowledge_documents``（doc_type='rich_text'，搬 title/content/content_html/
context_type/module_id，include_in_rag = importance>0），并把**原 project_contexts
行复用为该文档的 RAG 投影**——回填其 knowledge_document_id 指向新文档，不新增投影行。

幂等：已回填过（knowledge_document_id 非空）的行跳过。

须先执行 ``alembic upgrade head`` 建好表和列，再跑本脚本。

跑法（先 dry-run 看数量）：
    ./venv/bin/python -m database.migrations.data_migrations.migrate_knowledge_to_dedicated_tables

确认无误后真跑：
    ./venv/bin/python -m database.migrations.data_migrations.migrate_knowledge_to_dedicated_tables --commit
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="真的写库；默认 dry-run")
    args = parser.parse_args()

    from database.db import DB
    from database.models import (
        ProjectContext,
        KnowledgeDocument,
        CONTEXT_SOURCE_KNOWLEDGE,
    )

    db = DB()
    session = db.session

    rows = (
        session.query(ProjectContext)
        .filter(ProjectContext.source_type == CONTEXT_SOURCE_KNOWLEDGE)
        .filter(ProjectContext.knowledge_document_id.is_(None))
        .order_by(ProjectContext.id.asc())
        .all()
    )
    print(f"待迁移知识行：{len(rows)}")

    migrated = 0
    for ctx in rows:
        doc = KnowledgeDocument(
            project_id=ctx.project_id,
            module_id=ctx.module_id,
            folder_id=None,                      # 阶段 0 落根级；目录树阶段 1 再分
            doc_type="rich_text",
            title=(ctx.title or "")[:255] or "未命名文档",
            content=ctx.content or "",
            content_html=ctx.content_html or "",
            context_type=ctx.context_type or "term_definition",
            include_in_rag=(ctx.importance or 0) > 0,
        )
        session.add(doc)
        session.flush()                          # 拿 doc.id
        if doc.include_in_rag:
            ctx.knowledge_document_id = doc.id   # 复用旧行做投影
        else:
            # 不纳入检索的文档不应有投影行（与 sync_rag_projection 的不变量一致）
            session.delete(ctx)
        migrated += 1
        print(f"  ✓ ctx#{ctx.id} → doc#{doc.id}  {doc.title[:30]}")

    if args.commit:
        session.commit()
        print(f"已提交：迁移 {migrated} 篇。")
    else:
        session.rollback()
        print(f"[dry-run] 将迁移 {migrated} 篇；加 --commit 真写。")

    db.close()


if __name__ == "__main__":
    main()
