"""AI 异步任务统一入口。

每个 feature 一个 Celery task：调 ai_gateway.chat_json，落 ai_runs 状态，
按 feature 决定要不要把输出落到对应业务表（比如 requirement_parse → 写
requirements 表）。

设计：
  - 一个通用 task `dispatch_ai_task(ai_run_id)`：从 DB 取 ai_run，按 feature
    分发到具体处理函数；
  - 各 feature 的处理函数职责单一：拿 input → 调 LLM → 写业务表 → 写 output；
  - 失败统一 try/except 兜底，更新 ai_run.status=failed + error。
"""
from __future__ import annotations

import hashlib
import logging
import traceback
from datetime import datetime
from typing import Any, Optional

from celery_app import celery_app
from utils.logger import LOGGER

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.dispatch_ai_task", bind=True)
def dispatch_ai_task(self, ai_run_id: int) -> dict:
    """通用 AI 任务派发入口。

    流程：
        1. 把 ai_run 状态改 running、写 celery_task_id
        2. 按 feature 分发到具体处理函数
        3. 成功 → status=success + output_payload；失败 → status=failed + error
        4. 落 token / cost / model 等审计字段
    """
    from database.db import DB
    from database.models import AiRun, AI_RUN_STATUS_RUNNING, AI_RUN_STATUS_SUCCESS, AI_RUN_STATUS_FAILED

    LOGGER.info(f"[ai_task] start ai_run_id={ai_run_id}")
    db = DB()
    session = db.session

    try:
        run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
        if run is None:
            LOGGER.error(f"[ai_task] ai_run {ai_run_id} 不存在")
            return {"status": "error", "message": "ai_run not found"}

        run.status = AI_RUN_STATUS_RUNNING
        run.celery_task_id = self.request.id
        run.started_at = datetime.now()
        db.commit()

        # 按 feature 分发
        handler = _HANDLERS.get(run.feature)
        if handler is None:
            raise RuntimeError(f"未注册的 feature: {run.feature!r}")

        result = handler(run, session)

        # handler 返回 {"output": ..., "tokens_in": ..., "tokens_out": ...,
        #              "cost_usd": ..., "provider": ..., "model": ...,
        #              "prompt_hash": ..., "prompt_version": ...}
        run.output_payload = result.get("output")
        run.tokens_in = result.get("tokens_in")
        run.tokens_out = result.get("tokens_out")
        run.cost_usd = result.get("cost_usd")
        run.provider = result.get("provider")
        run.model = result.get("model")
        run.prompt_hash = result.get("prompt_hash")
        run.prompt_version = result.get("prompt_version")
        run.status = AI_RUN_STATUS_SUCCESS
        run.ended_at = datetime.now()
        db.commit()

        LOGGER.info(f"[ai_task] ai_run {ai_run_id} success")
        return {"status": "success", "ai_run_id": ai_run_id}

    except Exception as exc:
        LOGGER.error(f"[ai_task] ai_run {ai_run_id} failed: {exc}")
        traceback.print_exc()
        try:
            run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
            if run is not None:
                run.status = AI_RUN_STATUS_FAILED
                run.error = f"{type(exc).__name__}: {exc}"[:2000]
                run.ended_at = datetime.now()
                db.commit()
        except Exception as inner:
            LOGGER.error(f"[ai_task] 兜底状态更新也失败：{inner}")
        return {"status": "error", "message": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Feature 处理函数们
# ---------------------------------------------------------------------------
def _handle_requirement_parse(run: "AiRun", session) -> dict:
    """AI 需求分析（增强版 V2）。

    增强点：
      1. 支持文档上传（文件路径）和文本粘贴两种输入
      2. 检索项目上下文，注入 prompt 增强分析
      3. 提取 context_items（业务规则、数据模型等）写入 project_contexts 表
      4. 支持分析模式（quick / standard / deep / multi_model）
      5. 记录 requirement_analyses（分析过程和匹配的上下文）
    """
    from ai_gateway import chat_json
    from database.models import (
        Requirement,
        REQUIREMENT_STATUS_DRAFT,
        REQUIREMENT_SOURCE_AI,
        ProjectContext,
        RequirementAnalysis,
    )
    from server.services.context_service import (
        retrieve_context,
        build_context_summary,
        save_contexts,
    )
    from server.services.doc_parser import parse_document, parse_text_content
    from sqlalchemy import func as sa_func

    payload = run.input_payload or {}
    analysis_mode = (payload.get("analysis_mode") or "standard").strip()
    if analysis_mode not in ("quick", "standard", "deep", "multi_model"):
        analysis_mode = "standard"

    # ── Step 1: 获取文本内容（文件 or 粘贴文本） ──────────────────────
    file_path = (payload.get("file_path") or "").strip()
    pasted_text = (payload.get("text") or "").strip()

    if file_path:
        # 文档上传模式
        doc = parse_document(file_path)
        text = doc.plain_text
        source_type = "file"
        document_name = doc.filename
        document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    elif pasted_text:
        # 文本粘贴模式
        doc = parse_text_content(pasted_text)
        text = doc.plain_text
        source_type = "text"
        document_name = f"pasted_{run.id}.txt"
        document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:
        raise ValueError("input_payload 中需要提供 text（文本内容）或 file_path（文件路径）")

    if not text.strip():
        raise ValueError("文档内容为空")

    # ── Step 2: 创建分析记录 ──────────────────────────────────────
    analysis = RequirementAnalysis(
        project_id=run.project_id,
        ai_run_id=run.id,
        document_name=document_name,
        document_hash=document_hash,
        source_type=source_type,
        status="running",
        analysis_mode=analysis_mode,
    )
    session.add(analysis)
    session.flush()

    # ── Step 3: 检索项目上下文 ────────────────────────────────────
    from server.services.context_service import DEFAULT_RETRIEVAL_TOP_K, DEFAULT_RETRIEVAL_TOP_K_DEEP, DEFAULT_RETRIEVAL_TOP_K_QUICK

    top_k_map = {
        "quick": DEFAULT_RETRIEVAL_TOP_K_QUICK,
        "standard": DEFAULT_RETRIEVAL_TOP_K,
        "deep": DEFAULT_RETRIEVAL_TOP_K_DEEP,
        "multi_model": DEFAULT_RETRIEVAL_TOP_K_DEEP,
    }
    top_k = top_k_map.get(analysis_mode, DEFAULT_RETRIEVAL_TOP_K)

    matched_contexts = retrieve_context(
        query_text=text[:5000],  # 用文本前 5000 字符做检索
        project_id=run.project_id,
        top_k=top_k,
    )
    context_text = build_context_summary(matched_contexts)
    LOGGER.info(
        "[ai_task] requirement_parse project=%d matched_contexts=%d mode=%s",
        run.project_id, len(matched_contexts), analysis_mode,
    )

    # ── Step 4: 调 LLM ────────────────────────────────────────────
    res = chat_json(
        feature="requirement_parse",
        user_input={"text": text},
        project_id=run.project_id,
        analysis_mode=analysis_mode,
        context_text=context_text if matched_contexts else "",
    )
    output = res["output"]

    # ── Step 5: 校验 + 写入需求表 ──────────────────────────────────
    reqs = output.get("requirements") or []
    if not isinstance(reqs, list):
        raise ValueError(f"LLM 输出 requirements 不是数组：{type(reqs).__name__}")

    if run.project_id is None:
        raise ValueError("project_id 必填")

    max_sort = (
        session.query(sa_func.max(Requirement.sort_order))
        .filter(Requirement.project_id == run.project_id)
        .scalar()
        or 0
    )

    created_req_ids = []
    for i, r in enumerate(reqs):
        if not isinstance(r, dict) or not r.get("title"):
            continue
        new_req = Requirement(
            project_id=run.project_id,
            title=str(r.get("title"))[:200],
            description=r.get("description"),
            acceptance_criteria=r.get("acceptance_criteria") or [],
            priority=int(r.get("priority")) if r.get("priority") is not None else 2,
            tags=r.get("tags") or [],
            depends_on=r.get("depends_on") or [],
            status=REQUIREMENT_STATUS_DRAFT,
            source=REQUIREMENT_SOURCE_AI,
            ai_run_id=run.id,
            sort_order=max_sort + 1 + i,
        )
        session.add(new_req)
        session.flush()
        created_req_ids.append(new_req.id)

    # ── Step 6: 写入项目上下文（context_items）─────────────────────
    context_items = output.get("context_items") or []
    created_ctx_ids = save_contexts(
        contexts=context_items,
        project_id=run.project_id,
        source_type="document",
        source_file=document_name,
        ai_run_id=run.id,
        session=session,  # 共用 Celery 的 session，避免 SQLite 写锁
    ) if context_items else []

    session.flush()

    # ── Step 7: 更新分析记录 ───────────────────────────────────────
    analysis.status = "completed"
    analysis.analysis_result = {
        "summary": output.get("summary") or "",
        "analysis_notes": output.get("analysis_notes") or [],
    }
    analysis.new_requirement_ids = created_req_ids
    analysis.new_context_ids = created_ctx_ids
    analysis.matched_context_ids = [c["id"] for c in matched_contexts]
    analysis.completed_at = datetime.now()

    LOGGER.info(
        "[ai_task] requirement_parse done: reqs=%d contexts=%d matched=%d tokens=%d",
        len(created_req_ids), len(created_ctx_ids), len(matched_contexts),
        (res.get("tokens_in") or 0) + (res.get("tokens_out") or 0),
    )

    return {
        "output": {
            "summary": output.get("summary") or "",
            "requirements": output.get("requirements") or [],
            "created_requirement_ids": created_req_ids,
            "created_count": len(created_req_ids),
            "context_items_count": len(created_ctx_ids),
            "matched_contexts_count": len(matched_contexts),
            "analysis_notes": output.get("analysis_notes") or [],
            "analysis_mode": analysis_mode,
            "analysis_id": analysis.id,
        },
        "tokens_in": res.get("tokens_in"),
        "tokens_out": res.get("tokens_out"),
        "cost_usd": res.get("cost_usd"),
        "provider": res.get("provider"),
        "model": res.get("model"),
        "prompt_hash": res.get("prompt_hash"),
        "prompt_version": res.get("prompt_version"),
    }


def _handle_test_plan(run: "AiRun", session) -> dict:
    """AI 生成测试计划：把 project + 选定的 requirements + module 树喂给 LLM
    → 拿到 markdown content → 写一行进 test_plans 表。"""
    from ai_gateway import chat_json
    from database.models import (
        Project,
        Module,
        Requirement,
        TestPlan,
        TEST_PLAN_STATUS_DRAFT,
        TEST_PLAN_SOURCE_AI,
    )

    payload = run.input_payload or {}
    if run.project_id is None:
        raise ValueError("project_id 必填")

    proj = session.query(Project).filter(Project.id == run.project_id).first()
    if proj is None:
        raise ValueError(f"project {run.project_id} 不存在")

    # 拉选定的需求
    requirement_ids = payload.get("requirement_ids") or []
    reqs = []
    if requirement_ids:
        reqs = (
            session.query(Requirement)
            .filter(
                Requirement.project_id == run.project_id,
                Requirement.id.in_(requirement_ids),
            )
            .order_by(Requirement.sort_order.asc())
            .all()
        )
    # 没选就拉项目下所有 approved 的需求兜底（避免 prompt 太空）
    if not reqs:
        reqs = (
            session.query(Requirement)
            .filter(
                Requirement.project_id == run.project_id,
                Requirement.status.in_(("draft", "approved")),
            )
            .order_by(Requirement.sort_order.asc())
            .limit(50)
            .all()
        )

    # 拉项目模块树（扁平，prompt 里参考用）
    modules = (
        session.query(Module)
        .filter(Module.project_id == run.project_id)
        .order_by(Module.parent_id, Module.sort_order.asc())
        .all()
    )
    modules_text = "\n".join(
        f"- [{m.id}] {m.name}{' (parent=' + str(m.parent_id) + ')' if m.parent_id else ''}"
        for m in modules
    ) or "（暂无模块）"

    # 需求拼成 markdown 列表喂 LLM
    if reqs:
        reqs_text = "\n\n".join(
            f"### {i + 1}. [{r.id}] {r.title} (P{r.priority or 2})\n"
            f"{r.description or ''}\n"
            + ("\n验收标准：\n" + "\n".join(f"- {c}" for c in (r.acceptance_criteria or []))
               if r.acceptance_criteria else "")
            for i, r in enumerate(reqs)
        )
    else:
        reqs_text = "（用户没选需求点，请 AI 基于项目名给一份通用测试计划骨架）"

    res = chat_json(
        feature="test_plan",
        user_input={
            "project_name": proj.name,
            "enabled_stacks": ", ".join(proj.enabled_stacks or []),
            "time_start": payload.get("time_start") or "未指定",
            "time_end": payload.get("time_end") or "未指定",
            "resource_notes": payload.get("resource_notes") or "（用户未提供）",
            "requirements": reqs_text,
            "requirement_count": len(reqs),
            "modules": modules_text,
        },
        project_id=run.project_id,
    )
    output = res["output"] or {}
    title = (output.get("title") or "").strip() or f"{proj.name} 测试计划"
    summary = (output.get("summary") or "").strip()
    content = (output.get("content") or "").strip()
    if not content:
        raise ValueError("LLM 返回的 content 为空")

    # 落库
    plan = TestPlan(
        project_id=run.project_id,
        title=title[:200],
        summary=summary,
        content=content,
        requirement_ids=[r.id for r in reqs],
        module_ids=payload.get("module_ids") or [],
        time_range_start=_parse_dt(payload.get("time_start")),
        time_range_end=_parse_dt(payload.get("time_end")),
        resource_notes=payload.get("resource_notes"),
        status=TEST_PLAN_STATUS_DRAFT,
        source=TEST_PLAN_SOURCE_AI,
        ai_run_id=run.id,
    )
    session.add(plan)
    session.flush()

    return {
        "output": {
            "test_plan_id": plan.id,
            "title": title,
            "summary": summary,
            "content_preview": content[:500],
            "requirement_count": len(reqs),
        },
        "tokens_in": res.get("tokens_in"),
        "tokens_out": res.get("tokens_out"),
        "cost_usd": res.get("cost_usd"),
        "provider": res.get("provider"),
        "model": res.get("model"),
        "prompt_hash": res.get("prompt_hash"),
        "prompt_version": res.get("prompt_version"),
    }


def _parse_dt(s):
    """ISO 字符串 → datetime；解析失败返回 None。前端送 'YYYY-MM-DD' 也兼容。"""
    if not s:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


# Feature → handler 注册表。新增 feature 时在这里加一行。
_HANDLERS = {
    "requirement_parse": _handle_requirement_parse,
    "test_plan": _handle_test_plan,
    # "functional_case_gen": _handle_functional_case_gen,
    # ... 后续 feature 在这里挂
}
