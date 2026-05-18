"""AI Studio M1 —— "对话写需求" 的两个 Celery 任务。

两个入口都接 ``ai_run_id``（与现有 ``dispatch_ai_task`` 风格一致；API 层
先建 AiRun(pending) 再 .delay）：

- ``run_dialogue_turn_task(ai_run_id)``
    跑一轮对话推理。流程：
      1. 把 AiRun 标 running、写 celery_task_id
      2. 从 input_payload 取 session_id + user_message
      3. 把 user_message append 到 session.turns
      4. 调 coding_agent.prompt_templates.run_dialogue_turn —— 出 assistant 文本 + 反问字段 + 覆盖度
      5. assistant turn append 到 session.turns；coverage shallow-merge 到 session.coverage
      6. 写 AiRun.output_payload / tokens / cost / status=success

- ``finalize_dialogue_task(ai_run_id)``
    把整段对话收口为草稿。流程：
      1. 标 running
      2. 从 input_payload 取 session_id
      3. 调 coding_agent.prompt_templates.finalize_dialogue —— 出 markdown + spec
      4. 落 AiRequirementDraft(status=pending_review, ai_run_id=this_run.id)
      5. session.status -> finalized
      6. AiRun.output_payload 存 {draft_id, spec_preview, ...}

为啥不复用通用 ``dispatch_ai_task``？
  - 这两个 task 都要"原子地"改 session + 写 AiRun + 拼 draft，逻辑全在一个事务里更清楚
  - 后续编码任务（M1 Batch 5）也会自建 task，符合既有 ``ai_dialogue / ai_coding`` 分而治之的命名风格

失败兜底：
  - 任意异常 → AiRun.status=failed + error；session 状态不变（保留 turns 给 PM 重试）
  - 返回 dict（不 raise）—— 让 API 层轮询 ai_run 取最终状态
"""
from __future__ import annotations

import logging
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# celery worker fork 后 sys.path 可能不含项目根，保险插入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from celery_app import celery_app
from utils.logger import LOGGER

logger = logging.getLogger(__name__)


# 这两个 feature 名要跟 ai_gateway/prompts/<feature>.md 文件对齐
FEATURE_DIALOGUE_TURN = "ai_studio_dialogue_turn"
FEATURE_FINALIZE = "ai_studio_finalize"


# ---------------------------------------------------------------------------
# 单轮对话
# ---------------------------------------------------------------------------
@celery_app.task(name="tasks.ai_studio.run_dialogue_turn", bind=True)
def run_dialogue_turn_task(self, ai_run_id: int) -> dict:
    """跑一轮对话推理（PM 发一句话 → 模型反问 / 推进）。

    Returns:
        ``{"status": "success", "ai_run_id": int, ...}``
        或 ``{"status": "error", "ai_run_id": int|None, "message": str}``
    """
    from database.db import DB
    from database.models import (
        AiDialogueSession,
        AiRun,
        AI_RUN_STATUS_RUNNING,
        AI_RUN_STATUS_SUCCESS,
        AI_DIALOGUE_STATUS_ACTIVE,
    )

    LOGGER.info(f"[ai_dialogue] turn start ai_run={ai_run_id}")
    db = DB()
    session = db.session

    try:
        run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
        if run is None:
            return {"status": "error", "ai_run_id": ai_run_id, "message": "ai_run 不存在"}

        payload = run.input_payload or {}
        session_id = payload.get("session_id")
        user_message = (payload.get("user_message") or "").strip()
        if not session_id:
            raise ValueError("input_payload.session_id 必填")
        if not user_message:
            raise ValueError("input_payload.user_message 不能为空")

        run.status = AI_RUN_STATUS_RUNNING
        run.celery_task_id = self.request.id
        run.started_at = datetime.now()
        db.commit()

        ds = session.query(AiDialogueSession).filter(
            AiDialogueSession.id == session_id
        ).first()
        if ds is None:
            raise ValueError(f"session {session_id} 不存在")
        if ds.status != AI_DIALOGUE_STATUS_ACTIVE:
            raise ValueError(f"session {session_id} 已 {ds.status}，不能继续对话")

        # 1. append user turn（必须先入 session.turns，模型才能看到本轮）
        turns = list(ds.turns or [])
        turns.append({
            "role": "user",
            "content": user_message,
            "ts": int(time.time()),
        })
        ds.turns = turns

        # 2. 取项目名喂模型
        project_name = _project_name(session, ds.project_id) or "(未命名项目)"

        # 3. 调 prompt_templates
        from coding_agent.prompt_templates import run_dialogue_turn as call_dialogue

        result = call_dialogue(
            project_name=project_name,
            turns=turns,
            project_id=ds.project_id,
        )
        output = result["output"]
        meta = result["meta"]

        # 4. append assistant turn + 合并 coverage
        turns.append({
            "role": "assistant",
            "content": output["assistant"],
            "ts": int(time.time()),
            "asked_fields": output["asked_fields"],
            "done_hint": output["done_hint"],
            "ai_run_id": run.id,
        })
        ds.turns = turns
        ds.coverage = _merge_coverage(ds.coverage or {}, output.get("coverage_delta") or {})

        # 5. 写 AiRun 终态
        run.output_payload = {
            "assistant": output["assistant"],
            "asked_fields": output["asked_fields"],
            "coverage_delta": output["coverage_delta"],
            "done_hint": output["done_hint"],
        }
        _apply_meta(run, meta)
        run.status = AI_RUN_STATUS_SUCCESS
        run.ended_at = datetime.now()
        db.commit()

        LOGGER.info(
            "[ai_dialogue] turn done session=%s run=%s asked=%s done_hint=%s",
            session_id, run.id, output["asked_fields"], output["done_hint"],
        )
        return {
            "status": "success",
            "ai_run_id": run.id,
            "session_id": session_id,
            "assistant": output["assistant"],
            "asked_fields": output["asked_fields"],
            "coverage": ds.coverage,
            "done_hint": output["done_hint"],
        }

    except Exception as exc:
        LOGGER.error(f"[ai_dialogue] turn failed ai_run={ai_run_id}: {exc}")
        traceback.print_exc()
        _mark_run_failed(session, ai_run_id, exc)
        return {"status": "error", "ai_run_id": ai_run_id, "message": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------
@celery_app.task(name="tasks.ai_studio.finalize_dialogue", bind=True)
def finalize_dialogue_task(self, ai_run_id: int) -> dict:
    """整段对话 → markdown + spec_json → 落 AiRequirementDraft。

    Returns:
        ``{"status": "success", "ai_run_id": int, "draft_id": int, ...}``
        或 ``{"status": "error", "ai_run_id": int|None, "message": str}``
    """
    from database.db import DB
    from database.models import (
        AiDialogueSession,
        AiRequirementDraft,
        AiRun,
        AI_RUN_STATUS_RUNNING,
        AI_RUN_STATUS_SUCCESS,
        AI_DIALOGUE_STATUS_ACTIVE,
        AI_DIALOGUE_STATUS_FINALIZED,
        AI_REQ_DRAFT_STATUS_PENDING,
    )

    LOGGER.info(f"[ai_dialogue] finalize start ai_run={ai_run_id}")
    db = DB()
    session = db.session

    try:
        run = session.query(AiRun).filter(AiRun.id == ai_run_id).first()
        if run is None:
            return {"status": "error", "ai_run_id": ai_run_id, "message": "ai_run 不存在"}

        payload = run.input_payload or {}
        session_id = payload.get("session_id")
        if not session_id:
            raise ValueError("input_payload.session_id 必填")

        run.status = AI_RUN_STATUS_RUNNING
        run.celery_task_id = self.request.id
        run.started_at = datetime.now()
        db.commit()

        ds = session.query(AiDialogueSession).filter(
            AiDialogueSession.id == session_id
        ).first()
        if ds is None:
            raise ValueError(f"session {session_id} 不存在")
        if ds.status != AI_DIALOGUE_STATUS_ACTIVE:
            raise ValueError(f"session {session_id} 已 {ds.status}，不能 finalize")

        turns = list(ds.turns or [])
        if not turns:
            raise ValueError("对话尚无内容，无法 finalize")

        project_name = _project_name(session, ds.project_id) or "(未命名项目)"

        # 调 prompt_templates.finalize_dialogue
        from coding_agent.prompt_templates import finalize_dialogue as call_finalize

        result = call_finalize(
            project_name=project_name,
            turns=turns,
            project_id=ds.project_id,
        )
        output = result["output"]
        meta = result["meta"]

        markdown = output["markdown"]
        spec = output["spec"]

        # 落 draft
        draft = AiRequirementDraft(
            session_id=ds.id,
            markdown=markdown,
            spec_json=spec,
            status=AI_REQ_DRAFT_STATUS_PENDING,
            ai_run_id=run.id,
        )
        session.add(draft)
        session.flush()

        # session 收口
        ds.status = AI_DIALOGUE_STATUS_FINALIZED

        # AiRun 终态
        run.output_payload = {
            "draft_id": draft.id,
            "title": spec.get("title"),
            "priority": spec.get("priority"),
            "ac_count": len(spec.get("acceptance_criteria") or []),
            "markdown_preview": markdown[:500],
        }
        _apply_meta(run, meta)
        run.status = AI_RUN_STATUS_SUCCESS
        run.ended_at = datetime.now()
        db.commit()

        LOGGER.info(
            "[ai_dialogue] finalize done session=%s draft=%s run=%s title=%r",
            session_id, draft.id, run.id, spec.get("title"),
        )
        return {
            "status": "success",
            "ai_run_id": run.id,
            "session_id": session_id,
            "draft_id": draft.id,
            "title": spec.get("title"),
            "priority": spec.get("priority"),
        }

    except Exception as exc:
        LOGGER.error(f"[ai_dialogue] finalize failed ai_run={ai_run_id}: {exc}")
        traceback.print_exc()
        _mark_run_failed(session, ai_run_id, exc)
        return {"status": "error", "ai_run_id": ai_run_id, "message": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _project_name(session, project_id: Optional[int]) -> Optional[str]:
    """轻量取项目名 —— 不存在就返回 None，调用方兜底默认值。"""
    if project_id is None:
        return None
    from database.models import Project
    row = session.query(Project.name).filter(Project.id == project_id).first()
    return row[0] if row else None


def _merge_coverage(existing: dict, delta: dict) -> dict:
    """合并 coverage_delta 到现有 coverage。

    规则：
      - boolean 字段：True 优先（一旦覆盖就不再回退到 False）
      - list 字段（acceptance_criteria / module_deps）：取并集，保持顺序去重
      - delta 没出现的 key 不动 existing
    """
    merged = dict(existing or {})
    for k, v in (delta or {}).items():
        if isinstance(v, bool):
            merged[k] = bool(merged.get(k)) or v
        elif isinstance(v, list):
            seen = []
            for item in (merged.get(k) or []) + v:
                if item not in seen:
                    seen.append(item)
            merged[k] = seen
        else:
            # 其它类型（理论上不会出现）直接覆盖
            merged[k] = v
    return merged


def _apply_meta(run, meta: dict) -> None:
    """把 chat_json 返回的 provider / tokens / cost / hash 写到 AiRun。"""
    if not meta:
        return
    run.provider = meta.get("provider")
    run.model = meta.get("model")
    run.tokens_in = meta.get("tokens_in")
    run.tokens_out = meta.get("tokens_out")
    run.cost_usd = meta.get("cost_usd")
    run.prompt_hash = meta.get("prompt_hash")
    run.prompt_version = meta.get("prompt_version")


def _mark_run_failed(session, run_id: Optional[int], exc: Exception) -> None:
    """状态机兜底：把 AiRun 标 failed，独立小事务避免污染外层 rollback。"""
    if run_id is None:
        return
    from database.models import AiRun, AI_RUN_STATUS_FAILED
    try:
        session.rollback()  # 清掉外层未提交脏数据
        run = session.query(AiRun).filter(AiRun.id == run_id).first()
        if run is not None:
            run.status = AI_RUN_STATUS_FAILED
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
            run.ended_at = datetime.now()
            session.commit()
    except Exception as inner:
        LOGGER.error(f"[ai_dialogue] mark_failed 也炸了：{inner}")
