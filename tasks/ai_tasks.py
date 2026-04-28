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
    """AI 需求分析：调 LLM 拆解需求 → 批量写 requirements 表。"""
    from ai_gateway import chat_json
    from database.models import (
        Requirement,
        REQUIREMENT_STATUS_DRAFT,
        REQUIREMENT_SOURCE_AI,
    )

    text = (run.input_payload or {}).get("text") or ""
    if not text.strip():
        raise ValueError("input_payload.text 为空")

    # 调 LLM
    res = chat_json(
        feature="requirement_parse",
        user_input={"text": text},
        project_id=run.project_id,
    )
    output = res["output"]

    # 校验 LLM 输出形状
    reqs = output.get("requirements") or []
    if not isinstance(reqs, list):
        raise ValueError(f"LLM 输出 requirements 不是数组：{type(reqs).__name__}")

    # 批量写 requirements 表，挂在当前 project_id 下
    if run.project_id is None:
        raise ValueError("project_id 必填")

    # 查当前最大 sort_order，新数据接在末尾
    from sqlalchemy import func as sa_func
    max_sort = (
        session.query(sa_func.max(Requirement.sort_order))
        .filter(Requirement.project_id == run.project_id)
        .scalar()
        or 0
    )

    created_ids = []
    for i, r in enumerate(reqs):
        if not isinstance(r, dict) or not r.get("title"):
            continue  # 跳过无 title 的脏数据
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
        created_ids.append(new_req.id)

    session.flush()

    # 把 LLM 原始输出 + 创建的 requirement ids 一起塞进 output_payload，前端能展示
    return {
        "output": {
            "summary": output.get("summary") or "",
            "requirements": output.get("requirements") or [],
            "created_requirement_ids": created_ids,
            "created_count": len(created_ids),
        },
        "tokens_in": res.get("tokens_in"),
        "tokens_out": res.get("tokens_out"),
        "cost_usd": res.get("cost_usd"),
        "provider": res.get("provider"),
        "model": res.get("model"),
        "prompt_hash": res.get("prompt_hash"),
        "prompt_version": res.get("prompt_version"),
    }


# Feature → handler 注册表。新增 feature 时在这里加一行。
_HANDLERS = {
    "requirement_parse": _handle_requirement_parse,
    # "test_plan": _handle_test_plan,
    # "functional_case_gen": _handle_functional_case_gen,
    # ... 后续 feature 在这里挂
}
