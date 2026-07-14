"""验证 Phase A 上下文注入是否生效（不调 LLM，只读库 + 渲染 prompt）。

用法（项目根目录）：
    python scripts/verify_context_injection.py                 # 列出各项目记忆层条数 + 可用需求
    python scripts/verify_context_injection.py <requirement_id> # 对指定需求真跑一遍上下文构建
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def list_overview() -> None:
    from database.db import DB
    from database.models import ProjectContext, Requirement
    from sqlalchemy import func

    db = DB()
    try:
        rows = (
            db.session.query(
                ProjectContext.project_id,
                ProjectContext.context_type,
                func.count(ProjectContext.id),
            )
            .group_by(ProjectContext.project_id, ProjectContext.context_type)
            .all()
        )
        if not rows:
            print("⚠️  project_contexts 表是空的 —— 先对某个需求跑一次 M6 需求解析,记忆层才有数据")
            return
        print("== 记忆层数据分布 ==")
        for pid, ct, n in rows:
            print(f"  project={pid}  {ct:<18} {n} 条")

        pids = {r[0] for r in rows}
        print("\n== 这些项目下可用来验证的需求（最近 5 条/项目）==")
        for pid in sorted(pids):
            reqs = (
                db.session.query(Requirement.id, Requirement.title)
                .filter(Requirement.project_id == pid)
                .order_by(Requirement.id.desc())
                .limit(5)
                .all()
            )
            for rid, title in reqs:
                print(f"  requirement={rid}  [{pid}] {title[:50]}")
        print("\n下一步: python scripts/verify_context_injection.py <requirement_id>")
    finally:
        db.close()


def verify(requirement_id: int) -> None:
    from database.db import DB
    from server.services.case_generation_context_builder import (
        build_case_generation_context,
        render_case_generation_placeholders,
    )
    from ai_gateway.gateway import _load_prompt, _render_prompt

    db = DB()
    try:
        ctx = build_case_generation_context(db.session, requirement_id=requirement_id)
    finally:
        db.close()

    print(f"== requirement={requirement_id} ==")
    print(f"记忆层命中: {len(ctx.matched_context_ids)} 条  ids={ctx.matched_context_ids}")
    print("\n== PROJECT_CONTEXT 渲染结果 ==")
    print(ctx.project_context_text[:2000] or "（空）")

    placeholders = render_case_generation_placeholders(
        ctx, count=5, scenario_mix="positive_and_negative"
    )
    # OCR_EXCERPTS 在真实链路里由 task handler 填充（vision/OCR 分支），这里模拟之
    placeholders["OCR_EXCERPTS"] = "（无 UI 截图）"
    prompt = _render_prompt(_load_prompt("case_generation_v1"), placeholders)

    # 只匹配全大写下划线形态的占位符，避免把附件文档里的 {{prompt}} 等代码片段误报
    import re

    leftovers = sorted(set(re.findall(r"\{\{[A-Z][A-Z0-9_]*\}\}", prompt)))
    if leftovers:
        print(f"\n❌ prompt 有未渲染占位符: {leftovers}")
        sys.exit(1)
    print(f"\n✅ prompt 渲染完整（共 {len(prompt)} 字符,无残留占位符）")
    if ctx.matched_context_ids:
        print("✅ Phase A 注入生效")
    else:
        print("⚠️  该需求没检索到记忆层内容——换一个跑过 M6 解析的项目下的需求再试")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        verify(int(sys.argv[1]))
    else:
        list_overview()
