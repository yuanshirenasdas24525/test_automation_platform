"""逐条即时自愈 —— 用例一挂就地诊断、修复、重试，再跑下一条。

与"跑完整批再统一修"的本质区别在于**阻断连锁污染**：

    批量修：case1 挂 → case2..N 拿不到 case1 产出的变量 → 连环挂 →
            事后统一修 → 还得再跑一整轮才知道有没有修好
    即时修：case1 挂 → 当场修好 → case2 拿得到变量了 → 顺下去

实测印证：批量模式下修好 58 条断言，通过数只从 63 涨到 72——因为上游没修好，
下游修了也白修（"剥洋葱"效应）。逐条修从根上避免这个问题。

安全边界（与批量修复一致）：
  - 只改用例定义，不碰执行结果；每次改动都落 TestStep 并可回滚
  - 修完**重试一次**，仍失败就如实记为失败，绝不粉饰
  - 自愈本身出任何错都只记日志，用例结论按原样走
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _first_failed(result) -> Optional[Any]:
    """取用例里第一个失败的步骤（用例默认 on_failure=stop，它就是断点）。"""
    from runners.protocol import StepStatus

    for st in result.steps or []:
        if st.status in (StepStatus.FAILED, StepStatus.ERROR):
            return st
    return None


class _StepView:
    """把内存里的 StepResult 适配成 triage 规则认识的形态。

    triage_step 原本吃的是落库后的 TestStepReport；逐条自愈发生在报告落库**之前**，
    手上只有 StepResult。字段基本一一对应，只有 status_code 需要另取——
    http runner 把它写进了 ctx.records 而不是 StepResult。
    """

    def __init__(self, step, ctx, case_id: Optional[int]):
        self.case_id = case_id
        self.step_id = step.step_id
        self.step_name = step.step_name
        self.step_type = step.step_type
        self.status = step.status.value if hasattr(step.status, "value") else str(step.status)
        self.error_message = step.error_message or ""
        self.target = step.target or ""
        self.input_data = step.input_data
        self.output_data = step.output_data
        self.extract_values = step.extracted or {}
        code = (getattr(ctx, "records", {}) or {}).get("status_code")
        try:
            self.status_code = int(code) if code is not None else None
        except (TypeError, ValueError):
            self.status_code = None


def _apply_to_case_dict(case: dict, fix: dict) -> list[str]:
    """把修复应用到**内存里**的 case dict，让本次重试立刻生效。

    只处理第一个 http 步骤，与 ai_fix_service._apply_fix_to_case 的作用范围保持一致
    （调用方已确保失败发生在第一个 http 步骤上）。
    """
    parts: list[str] = []
    steps = [s for s in (case.get("steps") or []) if s.get("step_type") == "http_request"]
    if not steps:
        return parts
    first = steps[0]
    cfg = dict(first.get("config") or {})

    if fix.get("assertion"):
        merged = dict(cfg.get("assertion") or {})
        merged.update(fix["assertion"])
        cfg["assertion"] = merged
        parts.append("assertion")
    if fix.get("extract"):
        merged = dict(cfg.get("extract_data") or {})
        merged.update(fix["extract"])
        cfg["extract_data"] = merged
        parts.append("extract")
    first["config"] = cfg

    # insert_steps：在最前面插入前置步骤，原步骤整体后移
    inserted = fix.get("insert_steps") or []
    if inserted:
        for s in case.get("steps") or []:
            s["step_order"] = int(s.get("step_order") or 0) + len(inserted)
        new_steps = [
            {
                "id": None,
                "step_order": i,
                "step_name": spec["step_name"],
                "step_type": "http_request",
                "config": spec["config"],
                "on_failure": "stop",
            }
            for i, spec in enumerate(inserted)
        ]
        case["steps"] = new_steps + list(case.get("steps") or [])
        parts.append("insert_steps")
    return parts


def _persist(session, case_id: int, fix: dict) -> None:
    """把同一份修复落到数据库，让下次执行也受益（带编辑事件，可回滚）。"""
    from database.models import TestCase
    from server.services.ai_fix_service import _apply_fix_to_case
    from server.services.edit_history_service import create_test_case_batch
    from database.models.functional_case_edit_history import EDIT_ACTION_UPDATE

    case = session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None:
        return
    create_test_case_batch(
        session, action=EDIT_ACTION_UPDATE, operator_id=None,
        summary=f"AI 逐条自愈（用例 #{case_id}）",
    )
    _apply_fix_to_case(case, fix)
    session.commit()


def heal_case_inline(
    case: dict,
    result,
    ctx,
    *,
    session=None,
    model_name: Optional[str] = None,
) -> Optional[dict]:
    """单条用例失败后就地自愈。返回 {parts, summary} 说明改了什么；None = 没修。

    只做**规则能确定算出**的修复（L1）。规则判不了的（断言语义分歧、业务规则问题）
    不在这里猜——它们会照常记为失败，留给报告级的 AI 诊断处理。
    """
    from server.services.failure_triage import triage_step

    step = _first_failed(result)
    if step is None:
        return None

    case_id = case.get("id")
    view = _StepView(step, ctx, case_id)

    # 逐条自愈时没有"整轮的变量产出表"，用本用例已提取到的变量兜底即可：
    # 跨用例的产出关系由 _RUN_SHARED_VARS 在执行层保证，这里只关心本条能不能修。
    producers = {k: case_id for k in (view.extract_values or {})}
    verdict = triage_step(view, producers=producers, failed_case_ids=set())
    if not verdict or not verdict.get("fix_hint"):
        return None

    # 只有失败发生在第一个 http 步骤上才安全 —— 顶层 fix 就是打到那一步的
    http_steps = [s for s in (case.get("steps") or []) if s.get("step_type") == "http_request"]
    if not http_steps or (step.step_name or "") != (http_steps[0].get("step_name") or ""):
        logger.info(
            "[inline_heal] case=%s 问题不在第一个 http 步骤上，跳过就地修复", case_id,
        )
        return None

    fix = {
        "extract": verdict["fix_hint"].get("extract") or {},
        "assertion": verdict["fix_hint"].get("assertion") or {},
        "insert_steps": verdict["fix_hint"].get("insert_steps") or [],
    }
    parts = _apply_to_case_dict(case, fix)
    if not parts:
        return None

    if session is not None and case_id:
        try:
            _persist(session, int(case_id), fix)
        except Exception as exc:  # noqa: BLE001
            # 落库失败不影响本次重试（内存里的改动已生效）
            logger.warning("[inline_heal] case=%s 修复落库失败（已忽略）: %s", case_id, exc)
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass

    return {
        "parts": parts,
        "subtype": verdict.get("subtype"),
        "summary": verdict.get("summary") or "",
    }
