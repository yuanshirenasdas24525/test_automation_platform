"""变更调整：规划(plan_preview) 与 应用(plan_apply) 编排。

plan_preview：变更文本 + 接口契约/文本 + 现有用例 → AI 产出用例级调整大纲(ops)，
              落一条 AiRun 持久化 ops + 上下文，返回 plan_id + ops。
plan_apply：见 Task 4。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from database.models import CASE_TYPE_API, Module, TestCase
from database.models.ai_run import AiRun, AI_RUN_STATUS_SUCCESS
from server.services.api_case_contract import contract_prompt, contract_hash
from server.services.doc_ingest import IngestResult

AI_FEATURE_CHANGE_PLAN = "change_plan"
_VALID_ACTIONS = {"add", "modify", "delete"}


def _existing_interface_cases(db, module_id: int) -> list[TestCase]:
    return (
        db.session.query(TestCase)
        .filter(TestCase.module_id == module_id, TestCase.case_type == CASE_TYPE_API)
        .order_by(TestCase.sort_order.asc(), TestCase.id.asc())
        .all()
    )


def _normalize_ops(raw: dict[str, Any], existing_ids: set[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (raw or {}).get("ops") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        if action not in _VALID_ACTIONS:
            continue
        title = str(item.get("title") or "").strip()[:200]
        if not title:
            continue
        target = item.get("target_case_id")
        if action in {"modify", "delete"}:
            if not isinstance(target, int) or target not in existing_ids:
                continue
        else:
            target = None
        ep = item.get("endpoint") if isinstance(item.get("endpoint"), dict) else None
        endpoint = None
        if ep:
            endpoint = {
                "method": str(ep.get("method") or "").strip().upper(),
                "path": str(ep.get("path") or "").strip(),
            }
        out.append({
            "id": len(out),
            "action": action,
            "target_case_id": target,
            "title": title,
            "endpoint": endpoint,
            "reason": str(item.get("reason") or "").strip()[:500],
        })
    return out


def _parse_ai_json(raw: str) -> dict[str, Any] | None:
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    s, e = raw.find("{"), raw.rfind("}")
    if 0 <= s < e:
        try:
            obj = json.loads(raw[s:e + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _err_msg(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if detail:
        return str(detail)
    return str(exc) or exc.__class__.__name__


def _load_plan(db, plan_id: int) -> AiRun:
    """按 id 加载调整计划；不存在或不是 change_plan 类型的 AiRun 一律 404。"""
    from fastapi import HTTPException

    run = (
        db.session.query(AiRun)
        .filter(AiRun.id == plan_id, AiRun.feature == AI_FEATURE_CHANGE_PLAN)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="调整计划不存在")
    return run


def _apply_delete(db, op: dict[str, Any], user: Any = None) -> None:
    """删除 op 对应的真实用例。复用 `server.api.cases.delete_case`（任务解绑 + 编辑历史一起处理），
    不在本文件里重新实现一遍删除语义。"""
    from server.api.cases import delete_case

    case_id = op.get("target_case_id")
    if not isinstance(case_id, int):
        raise ValueError("delete op 缺少 target_case_id")
    delete_case(case_id=case_id, db=db, user=user, session_id=None, history_batch_id=None)


def _generate_details(
    db,
    module: Module,
    model_name: str,
    contract: dict[str, Any] | None,
    ops: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """把 add/modify ops 的 title 当测试点，复用接口用例详情生成器(ai_generate_batch)
    产出完整用例（含 steps/request/asserts）。

    `ai_generate_batch` 只返回生成结果，不落库 —— 落库是 plan_apply 自己的事。
    调用本身也包一层 SAVEPOINT：`ai_generate_batch` 内部有大量 `db.session.query/flush`，
    一旦命中真正的 DB 层异常（连接抖动/超时/死锁/完整性错误），Postgres 事务会被置为
    aborted。没有 SAVEPOINT 的话，session 会带着 PendingRollbackError 一路传到外层
    （`server/api/deps.py::get_db` 收尾 commit 时炸），把同一次 plan_apply 里已经成功
    的 delete 也整体拖下水，违反"逐 op 容错、不中断整批"的约定。用 `begin_nested()`
    包住后，异常只回滚到这个 savepoint，session 本身仍可用，后续 op 能正常继续。
    返回 (op_id -> compiled_case 详情字典, errors[{op_id, error}])。
    """
    from server.api.functional_cases import AiBatchRequest, BatchPoint, _norm_name, ai_generate_batch

    payload = AiBatchRequest(
        module_id=module.id,
        model_name=model_name,
        mode="interface",
        points=[BatchPoint(title=op["title"], category="") for op in ops],
        api_contract=contract or {},
    )
    try:
        with db.session.begin_nested():
            resp = ai_generate_batch(payload, db, None)
    except Exception as exc:  # noqa: BLE001 —— 整批生成失败，逐 op 记错，不让其它 op（比如 delete）陪葬
        # begin_nested() 的上下文管理器已经在异常时把事务回滚到这个 savepoint，
        # session 保持可用，不会把 PendingRollbackError 带出这个函数。
        msg = _err_msg(exc)
        return {}, [{"op_id": op["id"], "error": f"生成失败：{msg}"} for op in ops]

    generated = ((resp or {}).get("data") or {}).get("cases") or []
    by_title: dict[str, list[dict[str, Any]]] = {}
    for item in generated:
        key = _norm_name(str(item.get("name") or ""))
        by_title.setdefault(key, []).append(item)

    details: dict[int, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for op in ops:
        candidates = by_title.get(_norm_name(op["title"]))
        if not candidates:
            errors.append({"op_id": op["id"], "error": "生成结果里找不到匹配用例（标题未对上）"})
            continue
        item = candidates.pop(0)
        compiled = item.get("compiled_case")
        if not isinstance(compiled, dict):
            errors.append({"op_id": op["id"], "error": "生成结果缺少 compiled_case，可能命中契约硬校验失败"})
            continue
        details[op["id"]] = compiled
    return details, errors


def _create_case_from_generated(db, module: Module, detail: dict[str, Any], user: Any = None) -> None:
    from database.schemas.test_case_create import TestCaseCreate
    from server.api.cases import create_case

    payload = {**detail, "module_id": module.id}
    create_case(case=TestCaseCreate(**payload), db=db, user=user, session_id=None)


def _overwrite_case_from_generated(
    db, case_id: int, module: Module, detail: dict[str, Any], user: Any = None
) -> None:
    """用生成的详情整体覆盖已有用例，保留其 id（不改 sort_order/所属批次）。"""
    from database.schemas.test_case_create import TestCaseCreate
    from server.api.cases import update_case

    payload = {**detail, "module_id": module.id}
    payload.pop("sort_order", None)
    update_case(case_id=case_id, case=TestCaseCreate(**payload), db=db, user=user, session_id=None, history_batch_id=None)


def plan_apply(
    db,
    plan_id: int,
    model_name: str,
    confirmed_delete_ids: list[int] | set[int] | None = None,
    selected_op_ids: list[int] | set[int] | None = None,
    user: Any = None,
) -> dict[str, Any]:
    """把已持久化的调整计划应用到真实模块用例上。

    - delete：只处理 op.id ∈ confirmed_delete_ids 的 delete op，删真实 TestCase。
    - add/modify：只处理 op.id ∈ selected_op_ids 的 op，复用生成器产出完整用例后写库。
    - 单个 op 失败进 errors，不中断整批；每个写库动作（delete / create / update）都用
      SAVEPOINT 包一层，失败只回滚这一条。批量生成阶段（`_generate_details` 里调
      `ai_generate_batch` 那一步，同一次覆盖这批 add/modify op）也单独用 SAVEPOINT
      包住——它内部会做大量 `db.session` 操作，DB 层异常必须只回滚到那个 savepoint，
      不能把整个 session 拖进 aborted 状态，否则前面已经成功的 delete 会被一起赔进去。
    - `user`：调用方（路由层）传入的当前登录用户，透传给 create_case/update_case/
      delete_case 用于编辑历史（EditOperationEvent）的 operator 归属；可选，缺省 None
      保持旧行为（历史记录 operator 为空）。
    """
    run = _load_plan(db, plan_id)
    ops = (run.output_payload or {}).get("ops") or []
    delete_ids = set(confirmed_delete_ids or [])
    selected_ids = set(selected_op_ids or [])

    result: dict[str, Any] = {"added": 0, "modified": 0, "deleted": 0, "errors": []}

    def _run_in_savepoint(fn, *args) -> str | None:
        """跑一个写库动作，独立 SAVEPOINT；失败只回滚这条，返回错误信息（成功返回 None）。"""
        try:
            with db.session.begin_nested():
                fn(*args)
            return None
        except Exception as exc:  # noqa: BLE001 —— per-op 容错，绝不让一个 op 拖垮整批
            return _err_msg(exc)

    delete_ops = [op for op in ops if op.get("action") == "delete" and op.get("id") in delete_ids]
    for op in delete_ops:
        error = _run_in_savepoint(_apply_delete, db, op, user)
        if error:
            result["errors"].append({"op_id": op["id"], "error": error})
        else:
            result["deleted"] += 1

    gen_ops = [op for op in ops if op.get("action") in {"add", "modify"} and op.get("id") in selected_ids]
    if gen_ops:
        module_id = (run.input_payload or {}).get("module_id")
        module = db.session.query(Module).filter(Module.id == module_id).first() if module_id else None
        if module is None:
            for op in gen_ops:
                result["errors"].append({"op_id": op["id"], "error": "调整计划关联的模块不存在"})
        else:
            contract = (run.output_payload or {}).get("contract")
            details, gen_errors = _generate_details(db, module, model_name, contract, gen_ops)
            result["errors"].extend(gen_errors)
            for op in gen_ops:
                detail = details.get(op["id"])
                if detail is None:
                    continue  # 已经在 gen_errors 里记过错
                if op["action"] == "add":
                    error = _run_in_savepoint(_create_case_from_generated, db, module, detail, user)
                    if error:
                        result["errors"].append({"op_id": op["id"], "error": error})
                    else:
                        result["added"] += 1
                else:
                    target_id = op.get("target_case_id")
                    if not isinstance(target_id, int):
                        result["errors"].append({"op_id": op["id"], "error": "modify op 缺少 target_case_id"})
                        continue
                    error = _run_in_savepoint(_overwrite_case_from_generated, db, target_id, module, detail, user)
                    if error:
                        result["errors"].append({"op_id": op["id"], "error": error})
                    else:
                        result["modified"] += 1

    return result


def plan_preview(
    db,
    module: Module,
    model_name: str,
    change_text: str,
    ingest: IngestResult,
    operator: str | None = None,
) -> dict[str, Any]:
    """跑 AI 产出调整大纲，落 AiRun，返回 {plan_id, ops, warnings}。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options
    from server.api.functional_cases import _resolve_model

    cases = _existing_interface_cases(db, module.id)
    existing_ids = {c.id for c in cases}
    existing_block = "\n".join(f"- #{c.id} {c.name}" for c in cases) or "（本模块暂无接口用例）"
    contract_block = contract_prompt(ingest.contract) if (ingest.contract.get("operations") or []) else "（无结构化接口）"
    doc_text = "\n\n".join(ingest.text_blocks)[:20000] or "（无补充文本）"

    cfg = _resolve_model(db, model_name, module.project_id)
    call_options = model_task_options(cfg, "api_outline")
    template = _load_prompt("change_plan")
    prompt = _render_prompt(template, {
        "MODULE_NAME": module.name,
        "CHANGE_TEXT": change_text.strip(),
        "CONTRACT_BLOCK": contract_block,
        "DOC_TEXT": doc_text,
        "EXISTING_CASES": existing_block,
    })
    raw, tin, tout = chat_markdown(
        prompt, cfg,
        timeout=call_options["timeout"],
        system_prompt="你只输出一个合法 JSON 对象，含 ops 数组。不要输出任何其它文字。",
        enable_thinking=call_options["enable_thinking"],
        json_mode=call_options["json_mode"],
        max_tokens=call_options["max_tokens"],
        temperature=call_options["temperature"],
        reasoning_effort=call_options.get("reasoning_effort"),
    )
    obj = _parse_ai_json(raw or "")
    if obj is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="调整大纲解析失败，请重试或更换模型")
    ops = _normalize_ops(obj, existing_ids)

    run = AiRun(
        feature=AI_FEATURE_CHANGE_PLAN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=module.project_id,
        provider=cfg.provider,
        model=cfg.model,
        tokens_in=tin,
        tokens_out=tout,
        input_payload={
            "module_id": module.id,
            "mode": "interface",
            "model_name": model_name,
            "change_text": change_text,
            "warnings": ingest.warnings,
        },
        output_payload={
            "ops": ops,
            "contract": ingest.contract,
            "contract_hash": contract_hash(ingest.contract),
            "doc_text": doc_text,
        },
        operator=operator,
        started_at=datetime.now(),
        ended_at=datetime.now(),
    )
    db.session.add(run)
    db.session.flush()

    # 接口用例的详情生成需要结构化 OpenAPI 契约（平台防 AI 瞎编接口的硬门禁）。
    # 若有新增/修改但没解析到契约，提前预警——否则「应用」时这些 op 会全部生成失败。
    warnings = list(ingest.warnings)
    needs_contract = any(o["action"] in ("add", "modify") for o in ops)
    if needs_contract and not (ingest.contract.get("operations") or []):
        warnings.append(
            "检测到新增/修改用例，但未解析到结构化 OpenAPI 契约。接口用例的详情生成"
            "必须有 OpenAPI 契约，否则「应用」时这些用例会生成失败。请上传 OpenAPI "
            "JSON/YAML 文件，或在「接口文档链接」填入 Swagger/OpenAPI 地址后重新规划。"
        )
    return {"plan_id": run.id, "ops": ops, "warnings": warnings}
