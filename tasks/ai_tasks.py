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

    cfg = get_ai_model(session, model_name)
    if cfg is None:
        raise ValueError(f"AI 模型 {model_name!r} 未配置（请到配置中心 → AI 添加）")
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

    cfg = get_ai_model(session, model_name)
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

    # ── 6. 落 ai_case_drafts ─────────────────────────────────
    model_label = f"{cfg.provider} / {cfg.model}"
    created_ids: list[int] = []
    for item in parsed:
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
        "prompt_version": "v1",
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


# Feature → handler 注册表。新增 feature 时在这里加一行。
_HANDLERS = {
    "requirement_parse": _handle_requirement_parse,
    "requirement_analyze": _handle_requirement_analyze,    # M6 新流程
    "test_plan": _handle_test_plan,
    "functional_case_gen": _handle_functional_case_gen,    # M7
    "test_result_analysis": _handle_test_result_analysis,
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
    if feature == "test_result_analysis" and payload.get("report_id"):
        return f"/runs?report_id={payload['report_id']}"
    if feature == "functional_case_gen" and project_id:
        return f"/projects/{project_id}/functional"
    if project_id:
        return f"/projects/{project_id}/requirements"
    return "/runs"


_AI_FEATURE_LABELS: dict[str, str] = {
    "requirement_parse": "AI 需求分析（文本→需求点）",
    "requirement_analyze": "AI 生成测试分析文档",
    "test_plan": "AI 生成测试计划",
    "functional_case_gen": "AI 生成测试用例",
    "functional_case_review": "AI 用例质量检查",
    "api_case_gen": "AI 生成 API 用例",
    "test_result_analysis": "AI 执行结果体检",
    "functional_to_auto": "AI 功能转自动化用例",
    "report_summary": "AI 报告摘要",
    "load_plan_gen": "AI 压测脚本生成",
    "bug_fix": "AI 一键修复 Bug",
    "ai_studio_dialogue_turn": "AI 需求工作间对话",
    "ai_studio_finalize": "AI 需求草稿生成",
}

_AI_FEATURE_ICONS: dict[str, str] = {
    "requirement_parse": "Brain",
    "requirement_analyze": "FileText",
    "test_plan": "ClipboardList",
    "functional_case_gen": "Sparkles",
    "functional_case_review": "SearchCheck",
    "api_case_gen": "Globe",
    "test_result_analysis": "SearchCheck",
    "functional_to_auto": "Workflow",
    "report_summary": "FileBarChart",
    "load_plan_gen": "Gauge",
    "bug_fix": "Bug",
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
