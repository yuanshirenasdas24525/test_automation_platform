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
import json
import logging
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Optional

# celery worker fork 子进程时 sys.path 可能不含项目根，导致 handler 里
# `from server.x import y` 失败（ModuleNotFoundError: No module named 'server'）。
# 显式把项目根（这个文件的祖父目录）插到 sys.path 最前面，免依赖 cwd。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from celery_app import celery_app
from utils.logger import LOGGER

logger = logging.getLogger(__name__)

_WEB_UI_TASK_TIME_BUDGET_SECONDS = 12 * 60
_WEB_UI_SELECTED_FUNCTIONAL_BUDGET = 24
_AI_RUN_PROMPT_VERSION_LIMIT = 20


class _AiTaskCancelled(RuntimeError):
    """任务已被用户取消，handler 应立即停止且不能覆盖 cancelled 状态。"""


def _normalize_prompt_version(value: Any) -> str | None:
    """对齐 ai_runs.prompt_version VARCHAR(20)，避免任务在最后收尾时回滚。"""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized[:_AI_RUN_PROMPT_VERSION_LIMIT] or None


def _write_web_ui_progress(
    run: "AiRun",
    session,
    *,
    batch_id: str,
    stage: str,
    message: str,
    selection_completed: int = 0,
    selection_total: int = 0,
    generation_completed: int = 0,
    generation_total: int = 0,
    draft_ids: list[int] | None = None,
    dropped_count: int = 0,
    source_selection: dict[str, Any] | None = None,
) -> None:
    """每个批次提交一次轻量进度；JSON 列必须整体赋值才能触发脏检查。"""
    from database.models import AI_RUN_STATUS_CANCELLED

    session.refresh(run)
    if run.status == AI_RUN_STATUS_CANCELLED:
        raise _AiTaskCancelled("Web UI 用例生成已取消")
    current_output = dict(run.output_payload or {})
    current_output.update({
        "batch_id": batch_id,
        "draft_ids": list(draft_ids or []),
        "draft_count": len(draft_ids or []),
        "dropped_count": dropped_count,
        "progress": {
            "stage": stage,
            "message": message,
            "selection_completed": selection_completed,
            "selection_total": selection_total,
            "generation_completed": generation_completed,
            "generation_total": generation_total,
            "draft_count": len(draft_ids or []),
            "updated_at": datetime.now().isoformat(),
        },
    })
    if source_selection is not None:
        current_output["source_selection"] = source_selection
    run.output_payload = current_output
    session.commit()


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
    from database.models import (
        AI_RUN_STATUS_CANCELLED,
        AI_RUN_STATUS_FAILED,
        AI_RUN_STATUS_RUNNING,
        AI_RUN_STATUS_SUCCESS,
        AiRun,
    )

    LOGGER.info(f"[ai_task] start ai_run_id={ai_run_id}")
    db = DB()
    session = db.session

    try:
        run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
        if run is None:
            LOGGER.error(f"[ai_task] ai_run {ai_run_id} 不存在")
            return {"status": "error", "message": "ai_run not found"}
        if run.status == AI_RUN_STATUS_CANCELLED:
            LOGGER.info(f"[ai_task] ai_run {ai_run_id} 已取消，跳过执行")
            return {"status": "cancelled", "ai_run_id": ai_run_id}

        run.status = AI_RUN_STATUS_RUNNING
        run.celery_task_id = self.request.id
        run.started_at = datetime.now()
        db.commit()

        # 按 feature 分发
        handler = _HANDLERS.get(run.feature)
        if handler is None:
            raise RuntimeError(f"未注册的 feature: {run.feature!r}")

        result = handler(run, session)

        session.refresh(run)
        if run.status == AI_RUN_STATUS_CANCELLED:
            LOGGER.info(f"[ai_task] ai_run {ai_run_id} 在收尾前已取消")
            return {"status": "cancelled", "ai_run_id": ai_run_id}

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
        run.prompt_version = _normalize_prompt_version(result.get("prompt_version"))
        run.status = AI_RUN_STATUS_SUCCESS
        run.ended_at = datetime.now()
        db.commit()

        LOGGER.info(f"[ai_task] ai_run {ai_run_id} success")
        return {"status": "success", "ai_run_id": ai_run_id}

    except _AiTaskCancelled:
        session.rollback()
        LOGGER.info(f"[ai_task] ai_run {ai_run_id} cancelled")
        return {"status": "cancelled", "ai_run_id": ai_run_id}
    except Exception as exc:
        LOGGER.error(f"[ai_task] ai_run {ai_run_id} failed: {exc}")
        traceback.print_exc()
        try:
            # flush/commit 失败后 Session 处于 PendingRollbackError 状态，
            # 必须先回滚才能重新查询并写入失败状态。
            session.rollback()
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


def _handle_requirement_analyze(run: "AiRun", session) -> dict:
    """M6 需求分析（基于已有 requirement_id 拉上下文 → 输出 Markdown 文档）。

    input_payload 形如：
      {
        "requirement_id": 42,
        "model_name": "gpt-4o",          # AiModelConfig.name
        "user_prompt": "重点关注性能",   # 可选
        "document_title": "...",         # 可选；不给就自动拼
      }

    输出：
      - 新增一行 requirement_analysis_documents（current_markdown=md, current_version=1）
      - 同时插一条 requirement_analysis_versions（version_no=1, is_ai_generated=True）
      - ai_run.output_payload = {document_id, version_no, summary, image_strategy}
    """
    # celery prefork 子进程偶发 sys.path 不含项目根（即使父进程已注入），
    # 在 handler 入口处再保险一次。
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

    from datetime import datetime as _dt

    from database.models import (
        RequirementAnalysisDocument,
        RequirementAnalysisVersion,
    )
    from server.services.ai_model_service import get_ai_model
    from server.services.requirement_context_builder import (
        build_requirement_context,
        render_context_as_text,
    )
    from ai_gateway.gateway import (
        ProviderDoesNotSupportVisionError,
        _load_prompt,
        _render_prompt,
        chat_markdown,
        chat_markdown_with_images,
        ocr_extract,
    )

    payload = run.input_payload or {}
    requirement_id = payload.get("requirement_id")
    model_name = (payload.get("model_name") or "").strip()
    user_prompt = (payload.get("user_prompt") or "").strip()
    title_override = (payload.get("document_title") or "").strip()
    analysis_type = (payload.get("analysis_type") or "full").strip()
    prompt_by_type = {
        "clarify": "requirement_analysis_clarify",
        "testability": "requirement_analysis_testability",
        "delivery": "requirement_analysis_delivery",
        "full": "requirement_analysis_v2",
        "market": "requirement_analysis_market",
        "industry": "requirement_analysis_industry",
    }
    if analysis_type not in prompt_by_type:
        analysis_type = "full"

    if not requirement_id:
        raise ValueError("input_payload.requirement_id 必填")
    if not model_name:
        raise ValueError("input_payload.model_name 必填")

    cfg = get_ai_model(session, model_name, project_id=run.project_id)
    if cfg is None:
        raise ValueError(f"AI 模型 {model_name!r} 未配置（请到项目配置 → AI 添加）")
    if not cfg.enabled:
        raise ValueError(f"AI 模型 {model_name!r} 未启用")

    # ── 1. 构建需求上下文 ────────────────────────────────────────────
    ctx = build_requirement_context(session, int(requirement_id))
    placeholders = render_context_as_text(ctx)

    # ── 2. 图片处理：vision 优先，OCR 回退 ──────────────────────────────
    image_paths = [img.abs_path for img in ctx.images]
    use_vision = bool(cfg.supports_vision and image_paths)

    ocr_excerpts_text = "（无）"
    if image_paths and not use_vision:
        chunks: list[str] = []
        for img in ctx.images:
            txt = ocr_extract(img.abs_path)
            if txt:
                chunks.append(f"### {img.name}\n```\n{txt[:2000]}\n```")
            else:
                chunks.append(f"### {img.name}\n> ⚠️ OCR 未提取到文本")
        ocr_excerpts_text = "\n\n".join(chunks) if chunks else "（无）"

    placeholders["OCR_EXCERPTS"] = ocr_excerpts_text
    placeholders["USER_PROMPT"] = user_prompt or "（用户未补充）"

    # ── 3. 渲染 prompt ─────────────────────────────────────────────
    template = _load_prompt(prompt_by_type[analysis_type])
    prompt = _render_prompt(template, placeholders)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    # ── 4. 调用 LLM ────────────────────────────────────────────────
    try:
        if use_vision:
            markdown, tokens_in, tokens_out = chat_markdown_with_images(
                prompt, image_paths, cfg, timeout=180,
            )
            image_strategy = "vision"
        else:
            markdown, tokens_in, tokens_out = chat_markdown(
                prompt, cfg, timeout=120,
            )
            image_strategy = "ocr" if image_paths else "none"
    except ProviderDoesNotSupportVisionError as exc:
        LOGGER.warning("[ai_task] vision 不支持，回退 OCR: %s", exc)
        # 重新走 OCR 分支
        if image_paths:
            chunks = []
            for img in ctx.images:
                txt = ocr_extract(img.abs_path)
                if txt:
                    chunks.append(f"### {img.name}\n```\n{txt[:2000]}\n```")
            placeholders["OCR_EXCERPTS"] = "\n\n".join(chunks) or "（无）"
            prompt = _render_prompt(template, placeholders)
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        markdown, tokens_in, tokens_out = chat_markdown(prompt, cfg, timeout=120)
        image_strategy = "ocr_fallback"

    if not markdown.strip():
        raise ValueError("LLM 返回空 Markdown")

    # ── 5. 写文档 + v1 ─────────────────────────────────────────────
    model_label = f"{cfg.provider} / {cfg.model}"
    title_prefix_by_type = {
        "clarify": "需求澄清",
        "testability": "可测性分析",
        "delivery": "研发落地分析",
        "full": "完整需求分析",
        "market": "市场分析",
        "industry": "行业调研",
    }
    if title_override:
        title = title_override[:200]
    else:
        title_prefix = title_prefix_by_type.get(analysis_type, "AI 分析")
        title = f"{title_prefix} - {model_label} - {_dt.now().strftime('%Y-%m-%d %H:%M')}"[:200]

    doc = RequirementAnalysisDocument(
        requirement_id=int(requirement_id),
        ai_run_id=run.id,
        title=title,
        current_markdown=markdown,
        current_version=1,
        model_label=model_label,
        created_by_id=getattr(run, "created_by_id", None) or payload.get("created_by_id"),
    )
    session.add(doc)
    session.flush()

    version = RequirementAnalysisVersion(
        document_id=doc.id,
        version_no=1,
        markdown=markdown,
        change_summary="AI 初版生成",
        author_id=doc.created_by_id,
        is_ai_generated=True,
    )
    session.add(version)
    session.flush()

    # ── 6. 回流记忆层（增强项：提取文档中的事实条目写 project_contexts；
    #        失败绝不阻断文档产出）────────────────────────────────
    context_ids: list = []
    try:
        from database.models import Requirement as _Req
        from server.services.context_extraction import extract_and_save_contexts

        _proj_id = (
            session.query(_Req.project_id)
            .filter(_Req.id == int(requirement_id))
            .scalar()
        )
        if _proj_id:
            context_ids = extract_and_save_contexts(
                session,
                markdown=markdown,
                project_id=int(_proj_id),
                cfg=cfg,
                source_file=title,
                ai_run_id=run.id,
            )
    except Exception:
        LOGGER.warning(
            "[ai_task] 分析文档回流记忆层失败（不影响文档产出）doc=%s",
            doc.id, exc_info=True,
        )

    summary_excerpt = markdown.strip()[:200]
    return {
        "output": {
            "document_id": doc.id,
            "version_no": 1,
            "summary": summary_excerpt,
            "image_strategy": image_strategy,
            "image_count": len(image_paths),
            "model_label": model_label,
            "analysis_type": analysis_type,
            "context_items_count": len(context_ids),
        },
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": None,
        "provider": cfg.provider,
        "model": cfg.model,
        "prompt_hash": prompt_hash,
        "prompt_version": "v2",
    }


def _handle_functional_case_gen(run: "AiRun", session) -> dict:
    """M7 AI 一键生成 functional 测试用例草稿。

    一次调用 = 一个 (requirement_id, model_name) pair（外层 server 端按 N×M 拆分）。

    input_payload：
      {
        "requirement_id": int,
        "model_name": str,
        "batch_id": str,
        "analysis_document_id": int | None,
        "ui_image_attachment_ids": [int],
        "count": int,                       # 1..30
        "scenario_mix": str,                # positive_only / positive_and_negative / all_scenarios
        "user_prompt": str | None,
        "created_by_id": int | None,
      }

    产物：
      - N 行 ai_case_drafts(status='pending')
      - ai_run.output_payload = {batch_id, draft_count, draft_ids[], image_strategy}
    """
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

    from database.models import (
        AiCaseDraft,
        AI_CASE_DRAFT_STATUS_PENDING,
    )
    from server.services.ai_model_service import get_ai_model
    from server.services.case_generation_context_builder import (
        build_case_generation_context,
        render_case_generation_placeholders,
    )
    from ai_gateway.gateway import (
        ProviderDoesNotSupportVisionError,
        _load_prompt,
        _render_prompt,
        chat_markdown,
        chat_markdown_with_images,
        ocr_extract,
    )

    payload = run.input_payload or {}
    requirement_id = payload.get("requirement_id")
    model_name = (payload.get("model_name") or "").strip()
    batch_id = (payload.get("batch_id") or "").strip()
    analysis_document_id = payload.get("analysis_document_id")
    ui_image_attachment_ids = payload.get("ui_image_attachment_ids") or []
    count = int(payload.get("count") or 5)
    scenario_mix = (payload.get("scenario_mix") or "positive_and_negative").strip()
    user_prompt = (payload.get("user_prompt") or "").strip()

    if not requirement_id:
        raise ValueError("input_payload.requirement_id 必填")
    if not model_name:
        raise ValueError("input_payload.model_name 必填")
    if not batch_id:
        raise ValueError("input_payload.batch_id 必填")

    cfg = get_ai_model(session, model_name, project_id=run.project_id)
    if cfg is None:
        raise ValueError(f"AI 模型 {model_name!r} 未配置")
    if not cfg.enabled:
        raise ValueError(f"AI 模型 {model_name!r} 未启用")

    # ── 1. 构上下文 ───────────────────────────────────────
    ctx = build_case_generation_context(
        session,
        requirement_id=int(requirement_id),
        analysis_document_id=analysis_document_id,
        ui_image_attachment_ids=ui_image_attachment_ids,
    )

    # 渲染占位符（含 OCR_EXCERPTS / IMAGE_COUNT 等 base 字段）
    placeholders = render_case_generation_placeholders(
        ctx, count=count, scenario_mix=scenario_mix, user_prompt=user_prompt,
    )

    # ── 2. UI 图：vision 优先，OCR 回退 ─────────────────────
    image_paths = [img.abs_path for img in ctx.ui_images]
    use_vision = bool(cfg.supports_vision and image_paths)

    # M6 的 OCR 占位 —— 只对 UI 图做（业务附件文档已经在 base placeholder 里处理过了）
    ocr_text = "（无 UI 截图）"
    if image_paths and not use_vision:
        chunks: list[str] = []
        for img in ctx.ui_images:
            txt = ocr_extract(img.abs_path)
            if txt:
                chunks.append(f"### {img.name}\n```\n{txt[:2000]}\n```")
            else:
                chunks.append(f"### {img.name}\n> ⚠️ OCR 未提取到文本")
        ocr_text = "\n\n".join(chunks) if chunks else "（无）"
    placeholders["OCR_EXCERPTS"] = ocr_text

    # ── 3. 渲染 prompt + hash ───────────────────────────────
    template = _load_prompt("case_generation_v1")
    prompt = _render_prompt(template, placeholders)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    # ── 4. 调 LLM ─────────────────────────────────────────
    try:
        if use_vision:
            raw_text, tokens_in, tokens_out = chat_markdown_with_images(
                prompt, image_paths, cfg, timeout=240,
            )
            image_strategy = "vision"
        else:
            raw_text, tokens_in, tokens_out = chat_markdown(
                prompt, cfg, timeout=180,
            )
            image_strategy = "ocr" if image_paths else "none"
    except ProviderDoesNotSupportVisionError as exc:
        LOGGER.warning("[ai_task m7] vision 不支持，回退 OCR：%s", exc)
        if image_paths:
            chunks = []
            for img in ctx.ui_images:
                txt = ocr_extract(img.abs_path)
                if txt:
                    chunks.append(f"### {img.name}\n```\n{txt[:2000]}\n```")
            placeholders["OCR_EXCERPTS"] = "\n\n".join(chunks) or "（无）"
            prompt = _render_prompt(template, placeholders)
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        raw_text, tokens_in, tokens_out = chat_markdown(prompt, cfg, timeout=180)
        image_strategy = "ocr_fallback"

    if not raw_text.strip():
        raise ValueError("LLM 返回空文本")

    # ── 5. 解析 JSON（三道兜底：```json``` 围栏 / 裸 JSON 数组 / 失败兜底） ─
    parsed = _extract_case_json(raw_text)
    if parsed is None:
        raise ValueError(
            "LLM 输出无法解析为 JSON 数组；原文已存 ai_runs.output_payload.raw"
        )

    if not isinstance(parsed, list):
        raise ValueError(f"LLM 输出顶层不是数组：{type(parsed).__name__}")

    # ── 5.5 静态校验 + 一轮自修（P0-2：结构不合格的草稿不进评审页）──
    from server.services.draft_validation import partition_drafts

    valid_items, flawed = partition_drafts(parsed)
    repaired_count = 0
    dropped_count = 0
    if flawed:
        LOGGER.info(
            "[ai_task m7] %d 条草稿未过静态校验，尝试自修：%s",
            len(flawed),
            [(str(i.get("title") or "?")[:30], e) for i, e in flawed],
        )
        try:
            flawed_json = json.dumps(
                [{"case": i, "errors": e} for i, e in flawed],
                ensure_ascii=False, indent=2,
            )
            repair_prompt = _render_prompt(
                _load_prompt("case_repair"),
                {
                    "FLAWED_ITEMS_JSON": flawed_json,
                    "REPAIR_CONTEXT": "（功能用例结构修复，无额外上下文）",
                },
            )
            repair_raw, r_in, r_out = chat_markdown(
                repair_prompt, cfg, timeout=120,
            )
            tokens_in += r_in
            tokens_out += r_out
            repaired = _extract_case_json(repair_raw) or []
            if isinstance(repaired, list):
                re_ok, re_bad = partition_drafts(repaired)
                valid_items.extend(re_ok)
                repaired_count = len(re_ok)
                dropped_count = len(flawed) - len(re_ok)
                if re_bad:
                    LOGGER.warning(
                        "[ai_task m7] 自修后仍不合格,丢弃 %d 条: %s",
                        len(re_bad),
                        [(str(i.get('title') or '?')[:30], e) for i, e in re_bad],
                    )
            else:
                dropped_count = len(flawed)
        except Exception:
            # 自修失败不阻断主流程：合格的照常入库，不合格的丢弃
            dropped_count = len(flawed)
            LOGGER.warning("[ai_task m7] 自修调用失败,丢弃不合格草稿", exc_info=True)

    # ── 5.6 去重：剔除与同模块已有用例近乎重复的草稿（防越攒越冗余）──
    from server.services.draft_validation import dedup_against_existing

    valid_items, dup_items = dedup_against_existing(
        valid_items, ctx.existing_case_titles
    )
    dedup_dropped = len(dup_items)
    if dedup_dropped:
        LOGGER.info(
            "[ai_task m7] 去重剔除 %d 条(与已有用例重复): %s",
            dedup_dropped,
            [str(i.get("title") or "?")[:30] for i in dup_items],
        )

    # ── 6. 落 ai_case_drafts ─────────────────────────────────
    model_label = f"{cfg.provider} / {cfg.model}"
    created_ids: list[int] = []
    for item in valid_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue

        step_template = item.get("step_template") or []
        # needs_ui_detail 整条聚合：任一 step 标记 true 即整条 true
        needs_ui = bool(
            any(
                isinstance(s, dict) and s.get("needs_ui_detail")
                for s in step_template
            )
        )

        # 评审信号埋点：留生成时的原始快照,accept 时比对得 edit_ratio
        original_payload = {
            "title": title[:200],
            "preconditions": str(item.get("preconditions") or "").strip() or None,
            "steps_text": str(item.get("steps_text") or "").strip() or None,
            "expected": str(item.get("expected") or "").strip() or None,
            "priority": _clamp_priority(item.get("priority")),
            "tags": list(item.get("tags") or []),
            "step_template": step_template,
        }

        draft = AiCaseDraft(
            requirement_id=int(requirement_id),
            analysis_document_id=ctx.analysis_document_id,
            ai_run_id=run.id,
            batch_id=batch_id,
            model_label=model_label,
            title=title[:200],
            preconditions=str(item.get("preconditions") or "").strip() or None,
            steps_text=str(item.get("steps_text") or "").strip() or None,
            expected=str(item.get("expected") or "").strip() or None,
            priority=_clamp_priority(item.get("priority")),
            tags=list(item.get("tags") or []),
            step_template=step_template,
            original_payload=original_payload,
            needs_ui_detail=needs_ui,
            ui_image_refs=list(ui_image_attachment_ids),
            status=AI_CASE_DRAFT_STATUS_PENDING,
        )
        session.add(draft)
        session.flush()
        created_ids.append(draft.id)

    LOGGER.info(
        "[ai_task m7] requirement=%s model=%s batch=%s drafts=%d image=%s",
        requirement_id, model_name, batch_id, len(created_ids), image_strategy,
    )

    return {
        "output": {
            "batch_id": batch_id,
            "draft_count": len(created_ids),
            "draft_ids": created_ids,
            "validation": {
                "flawed": len(flawed),
                "repaired": repaired_count,
                "dropped": dropped_count,
                "dedup_dropped": dedup_dropped,
            },
            "image_strategy": image_strategy,
            "ui_image_count": len(image_paths),
            "model_label": model_label,
            "scenario_mix": scenario_mix,
        },
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": None,
        "provider": cfg.provider,
        "model": cfg.model,
        "prompt_hash": prompt_hash,
        # v2: 注入记忆层 PROJECT_CONTEXT + few-shot EXEMPLAR_CASES,
        #     已有用例范围同需求 → 同模块（2026-07,详见 docs/方案-用例生成上下文注入.md）
        "prompt_version": "v2",
    }


_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*([\[\{].*?[\]\}])\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_case_json(raw: str):
    """三道兜底：```json``` 围栏 / 第一个 [...] 块 / 整段直接 json.loads。返回 None 失败。"""
    m = _JSON_FENCE_PATTERN.search(raw)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            pass
    # 第一个完整 [...] 块（贪婪匹配数组）
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:  # noqa: BLE001
            pass
    # 整段直接 parse
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def _clamp_priority(v) -> int:
    try:
        p = int(v)
    except (TypeError, ValueError):
        return 2
    if p < 0:
        return 0
    if p > 3:
        return 3
    return p


def _handle_api_report_fix(run: "AiRun", session) -> dict:
    """API 报告 AI 全面诊断 + 参数修复（异步版）。

    读 report 执行结果 → 分块调 AI → 把每条用例的分类/发现/修复建议落到
    output_payload.items。真正把修复写回用例由前端在轮询到 success 后应用（沿用原交互），
    这里只负责生成诊断结果，因此本任务可在全局看板里查看进度、被终止。
    """
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)

    from server.services.ai_model_service import get_ai_model
    from server.api.functional_cases import diagnose_report_items

    payload = run.input_payload or {}
    report_id = int(payload.get("report_id") or 0)
    model_name = (payload.get("model_name") or "").strip()
    if report_id <= 0:
        raise ValueError("input_payload.report_id 必填")
    if not model_name:
        raise ValueError("input_payload.model_name 必填")

    cfg = get_ai_model(session, model_name, project_id=run.project_id)
    if cfg is None:
        raise ValueError(f"AI 模型 {model_name!r} 未配置")
    if not cfg.enabled:
        raise ValueError(f"AI 模型 {model_name!r} 未启用")

    # L1 确定性分诊先分掉能算的，LLM 只看剩下的（省 token 且更准）。
    # L1 结论与 LLM 结论合并成一份完整结果，下游无需区分来源。
    only_ids: set[int] | None = None
    l1_items: list[dict] = []
    l1_stats: dict = {}
    if payload.get("skip_l1_triaged", True):
        from server.services.failure_triage import (
            as_diagnosis_items, triage_report, undetermined_case_ids,
        )
        from database.models import TestCase as _TestCase
        try:
            triage = triage_report(session, report_id)
            only_ids = undetermined_case_ids(triage)
            done_ids = [
                c["case_id"] for c in triage["cases"]
                if c["case_id"] is not None and c["case_id"] not in only_ids
            ]
            module_ids = {
                c.id: c.module_id
                for c in session.query(_TestCase).filter(_TestCase.id.in_(done_ids or [0])).all()
            }
            l1_items = as_diagnosis_items(triage, module_ids)
            l1_stats = {
                "total_failed": triage["total_failed"],
                "l1_triaged": triage["triaged"],
                "sent_to_llm": len(only_ids),
            }
            LOGGER.info(
                "[ai_task] L1 分诊：失败 %d 条，规则定性 %d 条，送模型 %d 条",
                triage["total_failed"], triage["triaged"], len(only_ids),
            )
            if not only_ids:
                # 全部由规则定性 —— 一次 LLM 都不用调
                return {
                    "output": {"items": l1_items, "total": len(l1_items), "l1": l1_stats},
                    "model": cfg.model, "provider": cfg.provider, "prompt_version": "v1+L1",
                    "tokens_in": 0, "tokens_out": 0,      # 全靠规则定性，一次模型都没调
                }
        except Exception:  # noqa: BLE001
            # 分诊失败不能挡住主流程：退回全量送模型（老行为）
            LOGGER.warning("[ai_task] L1 分诊失败，退回全量诊断", exc_info=True)
            only_ids, l1_items, l1_stats = None, [], {}

    result = diagnose_report_items(session, report_id, cfg, only_case_ids=only_ids)
    merged = l1_items + (result.get("items") or [])
    LOGGER.info(
        "[ai_task] api_report_fix report=%s model=%s items=%d（L1 %d + LLM %d）",
        report_id, model_name, len(merged), len(l1_items), len(result.get("items") or []),
    )
    return {
        "output": {"items": merged, "total": len(merged), **({"l1": l1_stats} if l1_stats else {})},
        "model": cfg.model,
        "provider": cfg.provider,
        "prompt_version": "v1+L1" if l1_stats else "v1",
        "tokens_in": result.get("tokens_in"),
        "tokens_out": result.get("tokens_out"),
    }


def _handle_test_result_analysis(run: "AiRun", session) -> dict:
    """分析测试报告执行结果，输出结构化体检建议。

    这里的 AI 是增强层：规则诊断先给出稳定证据和建议；如果 input_payload.model_name
    有值，再调用模型生成中文总结。模型失败不会让任务失败。
    """
    from server.services.test_result_analysis_service import analyze_report

    payload = run.input_payload or {}
    report_id = int(payload.get("report_id") or 0)
    if report_id <= 0:
        raise ValueError("report_id 不能为空")
    model_name = (payload.get("model_name") or "").strip() or None
    output = analyze_report(session, report_id, model_name=model_name)
    ai_tokens = output.get("ai_tokens") or {}
    return {
        "output": output,
        "tokens_in": ai_tokens.get("tokens_in"),
        "tokens_out": ai_tokens.get("tokens_out"),
        "model": model_name,
        "prompt_version": output.get("rules_version"),
    }


def _handle_web_ui_case_gen(run: "AiRun", session) -> dict:
    """按当前模块自动筛选来源，并分批生成 Web UI 自动化用例草稿。"""
    from ai_gateway.gateway import ProviderError, _load_prompt, _render_prompt, chat_markdown, model_task_options
    from database.models import Module, TestCase, UiAutomationCaseDraft, UI_AUTO_DRAFT_PENDING
    from server.services.ai_model_service import get_ai_model
    from server.services.web_ui_case_generation_service import (
        build_auto_source_catalog,
        build_generation_context,
        compile_ai_case,
        normalize_auto_source_selection,
    )

    payload = run.input_payload or {}
    project_id = int(payload.get("project_id") or run.project_id or 0)
    target_module_id = int(payload.get("target_module_id") or 0)
    model_name = str(payload.get("model_name") or "").strip()
    batch_id = str(payload.get("batch_id") or "").strip()
    platform = str(payload.get("platform") or "web").strip().lower()
    if platform not in ("web", "android", "ios"):
        platform = "web"
    is_mobile = platform in ("android", "ios")
    # 移动端走 app_* 提示词与步骤；无像素视觉基线
    gen_prompt_name = "app_ui_case_gen" if is_mobile else "web_ui_case_gen"
    visual_enabled = (not is_mobile) and bool(payload.get("include_visual_assertions", False))
    if project_id <= 0:
        raise ValueError("project_id 必填")
    if target_module_id <= 0:
        raise ValueError("target_module_id 必填")
    if not model_name:
        raise ValueError("model_name 必填")
    if not batch_id:
        raise ValueError("batch_id 必填")

    cfg = get_ai_model(session, model_name, project_id=project_id)
    if cfg is None or not cfg.enabled:
        raise ValueError(f"AI 模型 {model_name!r} 不存在或未启用")
    target_module = (
        session.query(Module)
        .filter(Module.id == target_module_id, Module.project_id == project_id)
        .one_or_none()
    )
    if target_module is None:
        raise ValueError("当前用例模块不存在或不属于该项目")

    task_started_at = monotonic()
    draft_ids: list[int] = []
    _write_web_ui_progress(
        run,
        session,
        batch_id=batch_id,
        stage="preparing",
        message="正在读取当前模块、功能用例和元素库事实",
        draft_ids=draft_ids,
    )

    source_mode = str(payload.get("source_mode") or "auto")
    functional_case_ids = [int(item) for item in (payload.get("functional_case_ids") or [])]
    page_keys = [str(item) for item in (payload.get("page_keys") or [])]
    selection: dict[str, Any] = {
        "functional_case_ids": functional_case_ids,
        "page_keys": page_keys,
        "rationale": "使用人工指定的生成范围",
        "warnings": [],
        "budget": {},
    }
    selection_tokens_in = 0
    selection_tokens_out = 0
    selection_completed_count = 0
    selection_total_count = 0
    prompt_hashes: list[str] = []
    gap_only = bool(payload.get("gap_only"))
    if source_mode == "auto":
        exclude_ids: set[int] = set()
        if gap_only:
            from server.services.web_ui_case_generation_service import covered_functional_case_ids
            exclude_ids = covered_functional_case_ids(
                session, project_id=project_id, module_id=target_module_id, platform=platform
            )
        catalog = build_auto_source_catalog(
            session,
            project_id=project_id,
            target_module_id=target_module_id,
            user_prompt=str(payload.get("user_prompt") or ""),
            exclude_functional_case_ids=exclude_ids,
            platform=platform,
        )
        if gap_only and not catalog["functional_candidates"]:
            # 查缺补漏：没有未覆盖的功能用例就直接收尾，不退化成元素级冒烟，
            # 以免对已全覆盖的模块重复灌一批 smoke 草稿。
            covered_n = len(exclude_ids)
            msg = (
                f"查缺补漏：本模块 {covered_n} 条功能用例都已生成过 Web 用例，未发现缺口"
                if covered_n
                else "查缺补漏：当前模块没有可自动化的未覆盖功能用例"
            )
            _write_web_ui_progress(
                run, session, batch_id=batch_id, stage="completed",
                message=msg, draft_ids=draft_ids,
            )
            return {
                "output": {
                    "batch_id": batch_id,
                    "draft_ids": [],
                    "draft_count": 0,
                    "dropped_count": 0,
                    "gap_only": True,
                    "no_gap": True,
                    "target_module": {"id": target_module.id, "name": target_module.name},
                    "source_mode": "auto",
                    "budget": catalog["budget"],
                    "progress": {
                        "stage": "completed",
                        "message": msg,
                        "draft_count": 0,
                        "updated_at": datetime.now().isoformat(),
                    },
                },
            }
        selection_options = model_task_options(cfg, "web_ui_source_select")
        candidate_batches: list[list[dict[str, Any]]] = []
        candidate_batch: list[dict[str, Any]] = []
        candidate_batch_chars = 0
        candidate_char_limit = int(catalog["budget"].get("functional_char_budget") or 24_000)
        for candidate in catalog["functional_candidates"]:
            if int(candidate.get("automation_score") or 0) < 2:
                continue
            candidate_chars = len(json.dumps(candidate, ensure_ascii=False, default=str))
            if candidate_batch and candidate_batch_chars + candidate_chars > candidate_char_limit:
                candidate_batches.append(candidate_batch)
                candidate_batch = []
                candidate_batch_chars = 0
            candidate_batch.append(candidate)
            candidate_batch_chars += candidate_chars
        if candidate_batch:
            candidate_batches.append(candidate_batch)
        if not candidate_batches:
            candidate_batches = [[]]
        selection_total_count = len(candidate_batches)

        _write_web_ui_progress(
            run,
            session,
            batch_id=batch_id,
            stage="source_selection",
            message=(
                f"事实门禁已排除 {int(catalog['budget'].get('functional_filtered') or 0)} 条非 UI 用例，"
                f"正在筛选 {len(catalog['functional_candidates'])} 条候选（0/{len(candidate_batches)}）"
            ),
            selection_total=len(candidate_batches),
            draft_ids=draft_ids,
        )

        selected_case_ids: list[int] = []
        selected_page_keys: list[str] = []
        selection_rationales: list[str] = []
        selection_warnings: list[str] = []
        selection_prompt_hashes: list[str] = []
        invalid_selection_batches = 0
        for candidate_index, candidate_items in enumerate(candidate_batches, start=1):
            if candidate_index > 1 and monotonic() - task_started_at >= _WEB_UI_TASK_TIME_BUDGET_SECONDS:
                selection_warnings.append("任务达到内部时间预算，已使用当前已完成筛选结果继续生成")
                break
            selection_placeholders = {
                "TARGET_MODULE": catalog["target_module"],
                "USER_PROMPT": str(payload.get("user_prompt") or "") or "（无）",
                "SOURCE_CATALOG": {
                    "candidate_batch": f"{candidate_index}/{len(candidate_batches)}",
                    "functional_candidates": candidate_items,
                    "page_candidates": catalog["page_candidates"],
                },
            }
            selection_prompt = _render_prompt(
                _load_prompt("web_ui_source_select"),
                selection_placeholders,
            )
            selection_prompt_hash = hashlib.sha256(selection_prompt.encode("utf-8")).hexdigest()
            selection_prompt_hashes.append(selection_prompt_hash)
            prompt_hashes.append(selection_prompt_hash)
            LOGGER.info(
                "[web_ui_case_gen] run=%s source_selection batch=%s/%s candidates=%s",
                run.id,
                candidate_index,
                len(candidate_batches),
                len(candidate_items),
            )
            try:
                selection_text, tokens_in, tokens_out = chat_markdown(
                    selection_prompt,
                    cfg,
                    timeout=selection_options["timeout"],
                    system_prompt="你只负责从给定候选中筛选适合 Web UI 自动化的功能用例和页面，只输出 JSON。",
                    enable_thinking=selection_options["enable_thinking"],
                    json_mode=True,
                    max_tokens=selection_options["max_tokens"],
                    temperature=selection_options["temperature"],
                    reasoning_effort=selection_options["reasoning_effort"],
                )
            except ProviderError as exc:
                selection_text, tokens_in, tokens_out = "", 0, 0
                selection_warnings.append(
                    f"第 {candidate_index} 批 AI 筛选失败，已使用本地事实排序降级：{str(exc)[:120]}"
                )
            selection_tokens_in += tokens_in or 0
            selection_tokens_out += tokens_out or 0
            selection_value = selection_text.strip()
            selection_fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", selection_value, re.IGNORECASE)
            if selection_fenced:
                selection_value = selection_fenced.group(1).strip()
            selection_json_valid = True
            try:
                selection_raw = json.loads(selection_value)
            except json.JSONDecodeError:
                selection_json_valid = False
                invalid_selection_batches += 1
                selection_raw = {"warnings": [f"第 {candidate_index} 批 AI 筛选输出不完整"]}
            batch_catalog = {
                **catalog,
                "functional_candidates": candidate_items,
                "fallback_functional_case_ids": [
                    int(item["functional_case_id"])
                    for item in candidate_items[:40]
                ],
            }
            batch_selection = normalize_auto_source_selection(
                selection_raw,
                batch_catalog,
                fallback_on_empty=not selection_json_valid,
            )
            for case_id in batch_selection["functional_case_ids"]:
                if case_id not in selected_case_ids:
                    selected_case_ids.append(case_id)
            for page_key in batch_selection["page_keys"]:
                if page_key not in selected_page_keys:
                    selected_page_keys.append(page_key)
            if batch_selection.get("rationale"):
                selection_rationales.append(str(batch_selection["rationale"]))
            selection_warnings.extend(batch_selection.get("warnings") or [])
            selection_completed_count = candidate_index
            _write_web_ui_progress(
                run,
                session,
                batch_id=batch_id,
                stage="source_selection",
                message=(
                    f"已完成候选筛选 {candidate_index}/{len(candidate_batches)}，"
                    f"当前选中 {len(selected_case_ids)} 条功能用例"
                ),
                selection_completed=candidate_index,
                selection_total=len(candidate_batches),
                draft_ids=draft_ids,
            )

        if not selected_case_ids and invalid_selection_batches:
            fallback_selection = normalize_auto_source_selection({}, catalog)
            selected_case_ids = fallback_selection["functional_case_ids"]
            selection_warnings.extend(fallback_selection["warnings"])
        elif not selected_case_ids:
            selection_warnings.append(
                "AI 未发现与当前模块录制页面有充分证据关联的功能用例，已降级为仅元素库生成"
            )
        if not selected_page_keys:
            selected_page_keys = list(catalog.get("fallback_page_keys") or [])
        selected_case_ids = selected_case_ids[:_WEB_UI_SELECTED_FUNCTIONAL_BUDGET]
        selection = {
            "target_module": catalog["target_module"],
            "functional_case_ids": selected_case_ids,
            "page_keys": selected_page_keys[:8],
            "rationale": "；".join(selection_rationales)[:1000],
            "warnings": list(dict.fromkeys(selection_warnings)),
            "budget": {
                **catalog["budget"],
                "selection_batch_count": len(candidate_batches),
            },
            "prompt_hashes": selection_prompt_hashes,
        }
        functional_case_ids = selection["functional_case_ids"]
        page_keys = selection["page_keys"]
    elif source_mode == "elements_only":
        functional_case_ids = []
        selection["functional_case_ids"] = []
    if functional_case_ids:
        valid_module_case_ids = {
            int(item[0])
            for item in session.query(TestCase.id)
            .filter(
                TestCase.id.in_(functional_case_ids),
                TestCase.module_id == target_module_id,
                TestCase.case_type == "functional",
            )
            .all()
        }
        out_of_scope_ids = [item for item in functional_case_ids if item not in valid_module_case_ids]
        if out_of_scope_ids:
            raise ValueError(f"功能用例不属于当前模块“{target_module.name}”：{out_of_scope_ids}")
    if not page_keys:
        raise ValueError("没有检索到可生成的 Web 页面；请先补录页面和元素库")
    generation_batch_size = 6
    initial_generation_total = max(1, (len(functional_case_ids) + generation_batch_size - 1) // generation_batch_size)
    _write_web_ui_progress(
        run,
        session,
        batch_id=batch_id,
        stage="generation",
        message=f"筛选完成，准备生成可执行草稿（0/{initial_generation_total}）",
        selection_completed=selection_completed_count,
        selection_total=selection_total_count,
        generation_total=initial_generation_total,
        draft_ids=draft_ids,
        source_selection=selection,
    )
    existing_titles = {
        str(item[0]).strip().lower()
        for item in session.query(TestCase.name)
        .filter(
            TestCase.module_id == target_module_id,
            TestCase.case_type == "web",
        )
        .all()
    }
    compiled_items: list[dict[str, Any]] = []
    dropped = 0
    dropped_reasons: list[str] = []
    generated_functional_ids: set[int] = set()
    generation_tokens_in = 0
    generation_tokens_out = 0
    context_budgets: list[dict[str, Any]] = []
    batch_summaries: list[dict[str, Any]] = []
    evidence_functional_ids: set[int] = set()
    evidence_page_keys: set[str] = set()
    evidence_element_ids: set[int] = set()
    evidence_action_count = 0
    model_label = f"{cfg.provider} / {cfg.model}"
    time_budget_reached = False
    remaining_functional_case_ids: list[int] = []
    queue: list[list[int]] = [
        functional_case_ids[index:index + generation_batch_size]
        for index in range(0, len(functional_case_ids), generation_batch_size)
    ] or [[]]
    options = model_task_options(cfg, "web_ui_case_gen")
    batch_index = 0
    while queue:
        if batch_index > 0 and monotonic() - task_started_at >= _WEB_UI_TASK_TIME_BUDGET_SECONDS:
            time_budget_reached = True
            remaining_functional_case_ids = [case_id for batch in queue for case_id in batch]
            selection["warnings"].append(
                "本轮达到内部时间预算，已保存当前草稿；剩余功能用例可在后续批次继续"
            )
            break
        current_case_ids = queue.pop(0)
        batch_index += 1
        context, element_map, snapshot_map = build_generation_context(
            session,
            project_id=project_id,
            functional_case_ids=current_case_ids,
            page_keys=page_keys,
            platform=platform,
        )
        batch_scope = (
            f"当前模块“{target_module.name}”；功能用例 ID：{current_case_ids}。"
            "每个功能用例最多生成一条直接对应的草稿"
            if current_case_ids
            else f"当前模块“{target_module.name}”；本批仅按匹配页面元素生成基础可执行场景"
        )
        placeholders = {
            "PLATFORM": platform,
            "BATCH_SCOPE": batch_scope,
            "SOURCE_MODE": (
                "auto_functional_and_elements" if source_mode == "auto" and current_case_ids
                else "auto_elements_only" if source_mode == "auto"
                else source_mode
            ),
            "INCLUDE_STRUCTURE_ASSERTIONS": bool(payload.get("include_structure_assertions", True)),
            "INCLUDE_VISUAL_ASSERTIONS": visual_enabled,
            "USER_PROMPT": str(payload.get("user_prompt") or "") or "（无）",
            "EVIDENCE_CONTEXT": context,
        }
        prompt = _render_prompt(_load_prompt(gen_prompt_name), placeholders)
        prompt_hashes.append(hashlib.sha256(prompt.encode("utf-8")).hexdigest())
        LOGGER.info(
            "[web_ui_case_gen] run=%s generation batch=%s cases=%s queued=%s",
            run.id,
            batch_index,
            current_case_ids,
            len(queue),
        )
        try:
            raw_text, tokens_in, tokens_out = chat_markdown(
                prompt,
                cfg,
                timeout=options["timeout"],
                system_prompt=(
                    "你是 Web UI 自动化测试架构师。只输出满足用户指定结构的 JSON，"
                    "不得虚构元素 ID、页面、业务范围或定位器。"
                ),
                enable_thinking=options["enable_thinking"],
                json_mode=True,
                max_tokens=options["max_tokens"],
                temperature=options["temperature"],
                reasoning_effort=options["reasoning_effort"],
            )
            generation_tokens_in += tokens_in or 0
            generation_tokens_out += tokens_out or 0
            text_value = raw_text.strip()
            fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text_value, re.IGNORECASE)
            if fenced:
                text_value = fenced.group(1).strip()
            parsed = json.loads(text_value)
            raw_cases = parsed.get("cases") if isinstance(parsed, dict) else parsed
            if not isinstance(raw_cases, list):
                raise ValueError("AI 输出缺少 cases 数组")
        except (ProviderError, json.JSONDecodeError, ValueError) as exc:
            if len(current_case_ids) > 1:
                midpoint = max(1, len(current_case_ids) // 2)
                queue[0:0] = [current_case_ids[:midpoint], current_case_ids[midpoint:]]
                selection["warnings"].append(
                    f"第 {batch_index} 批输出不完整，系统已自动拆成更小批次继续生成"
                )
            else:
                dropped += max(1, len(current_case_ids))
                dropped_reasons.append(
                    f"功能用例 {current_case_ids or '元素库基础批次'} 生成中断：{str(exc)[:160]}"
                )
            batch_summaries.append({
                "functional_case_ids": current_case_ids,
                "draft_count": 0,
                "status": "split_retry" if len(current_case_ids) > 1 else "failed",
            })
            _write_web_ui_progress(
                run,
                session,
                batch_id=batch_id,
                stage="generation",
                message=(
                    f"第 {batch_index} 批输出不完整，正在缩小批次重试"
                    if len(current_case_ids) > 1
                    else f"第 {batch_index} 批未生成可执行草稿，继续下一批"
                ),
                selection_completed=selection_completed_count,
                selection_total=selection_total_count,
                generation_completed=batch_index,
                generation_total=batch_index + len(queue),
                draft_ids=draft_ids,
                dropped_count=dropped,
                source_selection=selection,
            )
            continue

        selected_in_batch = set(current_case_ids)
        accepted_before = len(compiled_items)
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                dropped += 1
                dropped_reasons.append("AI 返回了非对象用例，已丢弃")
                continue
            compiled = compile_ai_case(
                raw_case,
                element_map=element_map,
                snapshot_map=snapshot_map,
                include_structure_assertions=bool(payload.get("include_structure_assertions", True)),
                include_visual_assertions=visual_enabled,
                visual_threshold=float(payload.get("visual_threshold") or 0.02),
                platform=platform,
            )
            if compiled is None:
                dropped += 1
                dropped_reasons.append("AI 返回了无法编译或没有步骤的用例")
                continue
            functional_case_id = compiled["functional_case_id"]
            if current_case_ids and functional_case_id not in selected_in_batch:
                dropped += 1
                dropped_reasons.append(f"用例“{compiled['title']}”未关联本批功能用例，已阻止跨模块内容入库")
                continue
            if not current_case_ids:
                compiled["functional_case_id"] = None
            elif functional_case_id in generated_functional_ids:
                dropped += 1
                dropped_reasons.append(f"功能用例 #{functional_case_id} 已生成草稿，重复结果已丢弃")
                continue
            normalized_title = compiled["title"].strip().lower()
            if normalized_title in existing_titles:
                dropped += 1
                dropped_reasons.append(f"用例“{compiled['title']}”已存在")
                continue
            if bool(payload.get("executable_only", True)) and compiled["manual_reasons"]:
                dropped += 1
                dropped_reasons.append(f"用例“{compiled['title']}”需要人工处理，未纳入可执行草稿")
                continue
            existing_titles.add(normalized_title)
            if functional_case_id:
                generated_functional_ids.add(functional_case_id)
            compiled_items.append(compiled)

        # 每批通过事实门禁后立即入库并随进度一起提交；关闭页面或后续批次失败都不会丢失。
        for item in compiled_items[accepted_before:]:
            draft = UiAutomationCaseDraft(
                project_id=project_id,
                module_id=target_module_id,
                functional_case_id=item["functional_case_id"],
                ai_run_id=run.id,
                batch_id=batch_id,
                model_label=model_label,
                title=item["title"],
                description=item["description"],
                priority=item["priority"],
                tags=item["tags"],
                variables=item["variables"],
                steps=item["steps"],
                evidence=item["evidence"],
                warnings=item["warnings"],
                manual_reasons=item["manual_reasons"],
                confidence=item["confidence"],
                visual_assertion=item["visual_assertion"],
                status=UI_AUTO_DRAFT_PENDING,
            )
            session.add(draft)
            session.flush()
            draft_ids.append(draft.id)

        context_budget = context.get("context_budget") or {}
        context_budgets.append(context_budget)
        evidence_functional_ids.update(item["id"] for item in context["functional_cases"])
        evidence_page_keys.update(item["page_key"] for item in context["pages"])
        evidence_element_ids.update(
            int(element["element_id"])
            for page in context["pages"]
            for element in page["elements"]
        )
        evidence_action_count += len(context["recorded_actions"])
        batch_summaries.append({
            "functional_case_ids": current_case_ids,
            "draft_count": len(compiled_items) - accepted_before,
            "status": "completed",
        })
        _write_web_ui_progress(
            run,
            session,
            batch_id=batch_id,
            stage="generation",
            message=(
                f"已完成生成批次 {batch_index}/{batch_index + len(queue)}，"
                f"当前得到 {len(draft_ids)} 条可执行草稿"
            ),
            selection_completed=selection_completed_count,
            selection_total=selection_total_count,
            generation_completed=batch_index,
            generation_total=batch_index + len(queue),
            draft_ids=draft_ids,
            dropped_count=dropped,
            source_selection=selection,
        )

    if not compiled_items:
        reason_text = "；".join(list(dict.fromkeys(dropped_reasons))[:3])
        raise ValueError(
            "自动筛选后没有生成通过可执行门禁的草稿。"
            f"{reason_text or '请补录元素、定位器或功能预期后重试'}"
        )

    return {
        "output": {
            "batch_id": batch_id,
            "draft_ids": draft_ids,
            "draft_count": len(draft_ids),
            "dropped_count": dropped,
            "target_module": {"id": target_module.id, "name": target_module.name},
            "source_mode": payload.get("source_mode"),
            "source_selection": selection,
            "context_budget": {
                "batch_count": len(context_budgets),
                "elements_available": sum(int(item.get("elements_available") or 0) for item in context_budgets),
                "elements_included": sum(int(item.get("elements_included") or 0) for item in context_budgets),
                "elements_truncated": any(bool(item.get("elements_truncated")) for item in context_budgets),
            },
            "generation_batches": batch_summaries,
            "remaining_functional_case_ids": remaining_functional_case_ids,
            "time_budget_reached": time_budget_reached,
            "dropped_reasons": list(dict.fromkeys(dropped_reasons))[:20],
            "progress": {
                "stage": "completed",
                "message": (
                    f"本轮已保存 {len(draft_ids)} 条草稿，达到时间预算"
                    if time_budget_reached
                    else f"生成完成，共保存 {len(draft_ids)} 条可执行草稿"
                ),
                "selection_completed": selection_completed_count,
                "selection_total": selection_total_count,
                "generation_completed": batch_index,
                "generation_total": batch_index + len(queue),
                "draft_count": len(draft_ids),
                "updated_at": datetime.now().isoformat(),
            },
            "evidence_summary": {
                "functional_cases": len(evidence_functional_ids),
                "pages": len(evidence_page_keys),
                "elements": len(evidence_element_ids),
                "recorded_actions": evidence_action_count,
            },
        },
        "tokens_in": selection_tokens_in + generation_tokens_in,
        "tokens_out": selection_tokens_out + generation_tokens_out,
        "provider": cfg.provider,
        "model": cfg.model,
        "prompt_hash": hashlib.sha256("".join(prompt_hashes).encode("utf-8")).hexdigest(),
        "prompt_version": "web-ui-v4-batched",
    }


# Feature → handler 注册表。新增 feature 时在这里加一行。
_HANDLERS = {
    "requirement_parse": _handle_requirement_parse,
    "requirement_analyze": _handle_requirement_analyze,    # M6 新流程
    "test_plan": _handle_test_plan,
    "functional_case_gen": _handle_functional_case_gen,    # M7
    "test_result_analysis": _handle_test_result_analysis,
    "api_report_fix": _handle_api_report_fix,              # 报告级 AI 诊断 + 参数修复
    "web_ui_case_gen": _handle_web_ui_case_gen,
    # ... 后续 feature 在这里挂
}


# ---------------------------------------------------------------------------
# 注册到全局任务看板（Task Registry）
# ---------------------------------------------------------------------------
def _query_ai_runs(feature: str):
    """返回查询某类 AI 任务进行中的 query_fn。"""
    def _query(db_session, project_id: int | None, limit: int):
        from datetime import datetime, timedelta
        from sqlalchemy import and_, or_
        from database.models import AiRun, Project, \
            AI_RUN_STATUS_PENDING, AI_RUN_STATUS_RUNNING

        # pending 超过 2 小时视为死任务，不展示
        stale_cutoff = datetime.now() - timedelta(hours=2)

        q = db_session.query(
            AiRun.id,
            AiRun.feature,
            AiRun.status,
            AiRun.project_id,
            AiRun.input_payload,
            AiRun.started_at,
            Project.name.label("project_name"),
        ).outerjoin(Project, Project.id == AiRun.project_id).filter(
            and_(
                AiRun.feature == feature,
                or_(
                    # running：进行中
                    AiRun.status == AI_RUN_STATUS_RUNNING,
                    # pending：且创建时间在 2h 内
                    and_(
                        AiRun.status == AI_RUN_STATUS_PENDING,
                        AiRun.created_at >= stale_cutoff,
                    ),
                ),
            )
        )
        if project_id is not None:
            q = q.filter(AiRun.project_id == project_id)
        rows = q.order_by(AiRun.started_at.desc().nullslast()).limit(limit).all()
        return [
            {
                "id": r.id,
                "name": _AI_FEATURE_LABELS.get(feature, feature),
                "status": r.status,
                "project_id": r.project_id,
                "project_name": r.project_name,
                "started_at": r.started_at,
                "detail_url": _ai_run_detail_url(feature, r.project_id, r.input_payload),
            }
            for r in rows
        ]
    return _query


def _ai_run_detail_url(
    feature: str,
    project_id: int | None,
    input_payload: dict | None,
) -> str:
    """按 AI 任务上下文生成可恢复的前端入口。"""
    payload = input_payload or {}
    if feature == "bug_fix" and payload.get("bug_id"):
        return f"/tasks/{payload['bug_id']}"
    if feature in ("test_result_analysis", "api_report_fix") and payload.get("report_id"):
        return f"/runs?report_id={payload['report_id']}"
    if feature == "functional_case_gen" and project_id:
        return f"/projects/{project_id}/functional"
    if feature == "web_ui_case_gen" and project_id:
        return f"/projects/{project_id}?stack=web&uiElements=web"
    if project_id:
        return f"/projects/{project_id}/requirements"
    return "/runs"


_AI_FEATURE_LABELS: dict[str, str] = {
    "requirement_parse": "AI 需求分析（文本→需求点）",
    "requirement_analyze": "AI 生成测试分析文档",
    "test_plan": "AI 生成测试计划",
    "functional_case_gen": "AI 生成测试用例",
    "functional_case_enhance": "AI 高级补全用例",
    "functional_case_review": "AI 用例质量检查",
    "api_case_gen": "AI 生成 API 用例",
    "test_result_analysis": "AI 执行结果体检",
    "functional_to_auto": "AI 功能转自动化用例",
    "web_ui_case_gen": "AI 生成 Web UI 用例",
    "report_summary": "AI 报告摘要",
    "load_plan_gen": "AI 压测脚本生成",
    "bug_fix": "AI 一键修复 Bug",
    "api_report_fix": "AI 修复参数并应用",
    "ai_studio_dialogue_turn": "AI 需求工作间对话",
    "ai_studio_finalize": "AI 需求草稿生成",
}

_AI_FEATURE_ICONS: dict[str, str] = {
    "requirement_parse": "Brain",
    "requirement_analyze": "FileText",
    "test_plan": "ClipboardList",
    "functional_case_gen": "Sparkles",
    "functional_case_enhance": "Sparkles",
    "functional_case_review": "SearchCheck",
    "api_case_gen": "Globe",
    "test_result_analysis": "SearchCheck",
    "functional_to_auto": "Workflow",
    "web_ui_case_gen": "MousePointerClick",
    "report_summary": "FileBarChart",
    "load_plan_gen": "Gauge",
    "bug_fix": "Bug",
    "api_report_fix": "Wrench",
    "ai_studio_dialogue_turn": "Brain",
    "ai_studio_finalize": "FileText",
}

from server.services.task_registry import task_registry, TaskTypeInfo  # noqa: E402

for _feat in _AI_FEATURE_LABELS:
    task_registry.register(TaskTypeInfo(
        key=f"ai_{_feat}",
        label=_AI_FEATURE_LABELS[_feat],
        category="ai",
        icon=_AI_FEATURE_ICONS.get(_feat, "Brain"),
        query_fn=_query_ai_runs(_feat),
        detail_url_tpl="/projects/{project_id}/requirements",
    ))
