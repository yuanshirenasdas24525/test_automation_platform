"""生成执行接地精修闭环：草稿真跑 → 按真实响应精修 extract/assertion。

把现成但从未接上的 ai_gateway/prompts/api_probe_refine.md 接进接口用例生成：
生成草稿后真跑一遍（不落库）→ 收集「请求 + 真实响应」样本 → 让模型按真实响应
重写 extract/assertion → 合并回草稿。纯逻辑（本文件的 format_sample /
apply_refinements）单测覆盖；真跑与 LLM 调用（probe_drafts / refine_from_samples /
probe_and_refine）走集成验证。
"""
from __future__ import annotations

import json
import re
from typing import Any


def _jsonpath_exists(value: Any, path: str) -> bool:
    """验证精修器给出的简单 JSONPath 是否真的存在于本次响应。"""
    if not isinstance(path, str) or not path.startswith("$"):
        return False
    current = value
    for segment in [part for part in path[1:].lstrip(".").split(".") if part]:
        key = segment.split("[", 1)[0]
        if key:
            if not isinstance(current, dict) or key not in current:
                return False
            current = current[key]
        if "[" in segment:
            if not isinstance(current, list) or not current:
                return False
            current = current[0]
    return True


def format_sample(draft: dict, step_record: dict) -> dict:
    """把一条草稿 + 它真跑第一个 http step 的记录，压成 api_probe_refine 的 SAMPLES 元素。"""
    inp = step_record.get("input_data") or {}
    return {
        "name": draft.get("name") or "",
        "request": {
            "method": inp.get("method"),
            "url": inp.get("url"),
            "body": inp.get("body"),
        },
        "response": step_record.get("output_data"),
        "status": step_record.get("status_code"),
        "expected_status": step_record.get("expected_status"),
        "eligible": bool(step_record.get("eligible")),
    }


def apply_refinements(drafts: list[dict], refinements: list[dict]) -> list[dict]:
    """按名称把精修结果合并回 AI 场景层，而不是已编译步骤。

    编译步骤属于派生产物，随后会由契约编译器重新生成；直接修改 compiled_case 会导致
    评审页、探测和最终入库再次分叉。
    """
    by_name = {r.get("name"): r for r in (refinements or []) if r.get("name")}
    for draft in drafts:
        r = by_name.get(draft.get("name"))
        if not r:
            continue
        target = None
        legacy_config = False
        requests = draft.get("requests")
        if isinstance(requests, list) and len(requests) == 1 and isinstance(requests[0], dict):
            target = requests[0]
        elif draft.get("path"):
            target = draft
        elif isinstance(draft.get("steps"), list) and draft["steps"]:
            first = draft["steps"][0]
            if isinstance(first, dict) and isinstance(first.get("config"), dict):
                target = first["config"]
                legacy_config = True
        if target is None:
            continue
        response = draft.pop("_probe_response", None)
        verified_paths: set[str] = set()
        if "extract" in r:
            extract = r["extract"] if isinstance(r.get("extract"), dict) else {}
            target["extract"] = {
                name: path
                for name, path in extract.items()
                if response is None or _jsonpath_exists(response, str(path))
            }
            if legacy_config:
                target["extract_data"] = target.pop("extract")
                extract_values = target["extract_data"]
            else:
                extract_values = target["extract"]
            verified_paths.update(str(path) for path in extract_values.values())
        if "assertion" in r:
            assertions = r["assertion"] if isinstance(r.get("assertion"), dict) else {}
            target["assertion"] = {
                path: expected
                for path, expected in assertions.items()
                if response is None or path == "status_code" or _jsonpath_exists(response, str(path))
            }
            verified_paths.update(
                str(path) for path in target["assertion"] if str(path).startswith("$")
            )
        if verified_paths:
            draft["_probe_verified_paths"] = sorted(verified_paths)
        draft["probe_refined"] = True
    return drafts


_DESTRUCTIVE_HINTS = ("change-password", "reset-password", "/password", "logout", "signout")


def _step_is_destructive(cfg: dict) -> bool:
    """步骤是否破坏性操作（改密码/登出/删除账号）。"""
    path = str(cfg.get("path") or "").lower()
    method = str(cfg.get("method") or "").upper()
    if any(h in path for h in _DESTRUCTIVE_HINTS):
        return True
    return method == "DELETE" and "user" in path


def validate_isolation(draft: dict) -> list[str]:
    """草稿对共享账号做破坏性操作却没先建一次性账号 → 返回违规说明；否则空列表。"""
    compiled = draft.get("compiled_case") if isinstance(draft.get("compiled_case"), dict) else draft
    # 功能/接口草稿的 ``steps`` 可能只是供评审展示的中文字符串。隔离规则只接受
    # 编译后的结构化步骤，遇到其它历史形态要忽略而不是让整批重新校验 500。
    steps = [step for step in (compiled.get("steps") or []) if isinstance(step, dict)]
    def mutates_shared_state(step: dict) -> bool:
        config = step.get("config")
        if not isinstance(config, dict) or not _step_is_destructive(config):
            return False
        expected_status = next(
            (
                rule.get("expected")
                for rule in (step.get("assertion") or [])
                if isinstance(rule, dict) and rule.get("target") == "status_code"
            ),
            None,
        )
        # 明确断言 4xx 的负向请求不会进入成功变更分支：包括未认证 401/403，
        # 也包括缺字段/类型/边界校验触发的 400/409/422。它们不应被误判为
        # “必须先建一次性账号”的正向破坏性场景。
        return not isinstance(expected_status, int) or expected_status < 400

    if not any(mutates_shared_state(s) for s in steps):
        return []
    blob = json.dumps([s.get("config") or {} for s in steps], ensure_ascii=False)
    if "function:unique" in blob:
        destructive_steps = [s for s in steps if mutates_shared_state(s)]
        destructive_blob = json.dumps(destructive_steps, ensure_ascii=False).lower()
        if "${token}" not in destructive_blob and "${admin_token}" not in destructive_blob:
            return []
    return [
        "破坏性操作（改密码/删除/登出）未用一次性账号："
        "应先 function:unique 建号→登录提取 own_token→只对 own_token 操作，不能直接用共享 token/admin"
    ]


def _cross_case_probe_refs(compiled: dict) -> set[str]:
    """返回只能由前序用例产出的变量；单条在线探测无法为它们提供真实值。"""
    metadata = compiled.get("generation_metadata") or {}
    carried = {str(name) for name in (metadata.get("carried_variables") or [])}
    if not carried:
        return set()
    blob = json.dumps(
        {
            "pre_hook": compiled.get("pre_hook") or [],
            "steps": compiled.get("steps") or [],
            "post_hook": compiled.get("post_hook") or [],
        },
        ensure_ascii=False,
        default=str,
    )
    references = {match.split(".")[0] for match in re.findall(r"\$\{([A-Za-z_][\w.-]*)\}", blob)}
    produced: set[str] = set()
    for hook in compiled.get("pre_hook") or []:
        if not isinstance(hook, dict):
            continue
        config = hook.get("config") if isinstance(hook.get("config"), dict) else hook
        extract = config.get("extract_data") or config.get("extract") or {}
        if isinstance(extract, dict):
            produced.update(str(name) for name in extract)
    for step in compiled.get("steps") or []:
        for rule in step.get("extract") or []:
            if isinstance(rule, dict) and rule.get("name"):
                produced.add(str(rule["name"]))
    return (references & carried) - produced


def probe_drafts(drafts: list[dict], project_id: int) -> list[dict]:
    """真跑每条草稿（不落库），返回 SAMPLES 列表。

    隔离保护：对"破坏性操作打共享账号"的违规草稿**跳过真跑**（给空样本），
    避免 probe 真跑污染共享账号；执行异常的草稿也给空样本，不中断。
    """
    from runners.case_executor import CaseExecutor
    from runners.context.execution_context import ExecutionContext

    samples: list[dict] = []
    for draft in drafts:
        compiled = draft.get("compiled_case") if isinstance(draft.get("compiled_case"), dict) else draft
        preflight = compiled.get("generation_metadata", {}).get("preflight", {})
        steps = compiled.get("steps") or []
        skip_reason = None
        if draft.get("needs_fix") or not preflight.get("passed", False):
            skip_reason = "静态硬校验未通过"
        elif external_refs := _cross_case_probe_refs(compiled):
            skip_reason = "依赖前序用例变量，单条在线探测不具备真实值：" + "、".join(sorted(external_refs))
        elif validate_isolation(draft):
            skip_reason = "破坏性场景未完成数据隔离"
        elif len(steps) != 1:
            # 多步场景可能包含建号/改密/登出，逐条在线探测会制造数据和会话副作用。
            # 它仍经过完整契约编译与静态门禁，真实执行交给用户显式试跑。
            skip_reason = "多步场景不做自动在线探测"
        elif (
            str((steps[0].get("config") or {}).get("method") or "GET").upper()
            in {"POST", "PUT", "PATCH", "DELETE"}
            and not any(
                hint in str((steps[0].get("config") or {}).get("path") or "").lower()
                for hint in ("login", "signin", "sign_in", "/auth/token")
            )
            and next(
                (
                    rule.get("expected")
                    for rule in (steps[0].get("assertion") or [])
                    if isinstance(rule, dict) and rule.get("target") == "status_code"
                ),
                200,
            ) < 400
            and not compiled.get("post_hook")
        ):
            skip_reason = "成功写操作没有可验证的清理步骤，不做自动在线探测"
        if skip_reason:
            draft["probe"] = {"status": "skipped", "reason": skip_reason}
            continue
        try:
            ctx = ExecutionContext()
            ctx.set_var("_project_id", project_id)
            result = CaseExecutor().run(dict(compiled), ctx)
            first = next(
                (s for s in (result.steps or []) if getattr(s, "step_type", "") == "http_request"),
                None,
            )
            expected_status = next(
                (
                    rule.get("expected")
                    for rule in (steps[0].get("assertion") or [])
                    if isinstance(rule, dict) and rule.get("target") == "status_code"
                ),
                None,
            )
            actual_status = (getattr(ctx, "records", {}) or {}).get("status_code")
            eligible = actual_status is not None and actual_status == expected_status
            rec = {
                "input_data": getattr(first, "input_data", None) if first else None,
                "output_data": getattr(first, "output_data", None) if first else None,
                "status_code": actual_status,
                "expected_status": expected_status,
                "eligible": eligible,
            }
            draft["probe"] = {
                "status": "passed" if eligible else "failed",
                "actual_status": actual_status,
                "expected_status": expected_status,
            }
            if not eligible:
                warning = f"在线探测实际 HTTP {actual_status}，契约期望 HTTP {expected_status}；禁止把错误响应学习成正确断言"
                draft.setdefault("warnings", []).append(warning)
                draft.setdefault("blocking_warnings", []).append(warning)
                draft["needs_fix"] = True
            else:
                draft["_probe_response"] = rec["output_data"]
        except Exception:  # noqa: BLE001
            draft["probe"] = {"status": "failed", "reason": "在线探测执行异常"}
            continue
        if rec.get("eligible"):
            samples.append(format_sample(draft, rec))
    return samples


def refine_from_samples(samples: list[dict], cfg: Any) -> list[dict]:
    """把样本喂给 api_probe_refine.md，返回精修后的 [{name, extract, assertion, note}]。"""
    import json

    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown

    samples = [sample for sample in samples if sample.get("eligible")]
    if not samples:
        return []
    template = _load_prompt("api_probe_refine")
    prompt = _render_prompt(template, {"SAMPLES": json.dumps(samples, ensure_ascii=False)})
    raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=180)
    from server.api.functional_cases import _extract_json_list

    return _extract_json_list(raw, allow_salvage=False) or []


def probe_and_refine(drafts: list[dict], project_id: int, cfg: Any) -> list[dict]:
    """闭环编排：真跑收集样本 → 精修 → 合并回草稿。任何一步失败都回退原草稿（不阻断生成）。"""
    try:
        samples = probe_drafts(drafts, project_id)
        refinements = refine_from_samples(samples, cfg)
        return apply_refinements(drafts, refinements)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning("[gen-probe] 精修闭环失败，返回原草稿", exc_info=True)
        return drafts
