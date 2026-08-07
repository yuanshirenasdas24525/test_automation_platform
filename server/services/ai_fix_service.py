"""AI 报告参数修复：预检（sanitize）→ 应用（快照）→ 闭环验证（重跑对比 + 自动回滚）。

背景：模型产出的 fix 直接整体写进用例，会把"本来通过的用例改坏"（修复率为负）。
这里把模型输出当**候选**，分三道防线：

  1. preflight_report_fixes —— 应用前用报告里存的真实响应做程序化预检：
       - classification != 用例问题 → 整条丢弃；
       - 本次已通过的用例 → 只允许纯增量的 extract/assertion（治"假通过"），
         不允许动 params/headers/steps；
       - fix.extract 的 JSONPath 直接对真实响应跑一遍，取不到值就拦；
       - fix.assertion 对真实响应验证；expected 像动态值（token/时间戳）强制转 not_empty；
       - fix 里新引用的 ${var} 必须能在报告的变量产出表里找到、且产出方排在本用例之前；
       - params 与原值**合并**而不是整体替换（fix 里显式 null 表示删除该键），
         防止模型给残缺对象清掉正确字段。
     注意：fix 同时改了请求（params/headers/steps）时，响应会变，此时 extract/assertion
     不做响应预检（deferred），交给第 3 道防线裁决。

  2. apply_report_fixes —— 服务端应用；每条用例一个 EditOperationEvent，全部
     挂同一个 EditOperationBatch，天然支持按用例精准回滚。

  3. compare_and_rollback —— 重跑后按用例对比新旧报告：
       green→red 自动按事件回滚（快照恢复），red→green 记为修复成功，
       red→red 也视为未验证修复并自动回滚。只有重跑转绿的修改才会永久保留。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlalchemy.orm import Session, selectinload

from database.models import TestCase, TestStepReport
from database.models.edit_operation import EDIT_ACTION_UPDATE
from server.services.edit_history_service import (
    create_test_case_batch,
    record_test_case_update,
    rollback_test_case_events,
    snapshot_test_case,
)
from utils.logger import LOGGER
from utils.parameter_flow import (
    extract_rule,
    infer_rebound_extracts,
    is_response_jsonpath,
    merge_rebound_extracts,
)
from utils.platform_utils import extractor

_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][\w.-]*)\}")
_STATUS_INTENT_RE = re.compile(
    r"(?:返回|http(?:\s*状态码)?)[：:\s_-]*(\d{3})",
    re.IGNORECASE,
)

# 断言 expected 像"每次执行都会变的动态值"→ 等值断言下次必挂，强制转 not_empty
_DYNAMIC_VALUE_RE = re.compile(
    r"^(?:"
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
    r"|[0-9a-fA-F]{16,}"                                        # 长 hex（token/签名）
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
    r"|\d{10,}"                                                 # 时间戳/长数字
    r")$"
)
_DYNAMIC_TARGET_HINTS = ("token", "sign", "session", "ticket", "nonce", "timestamp")

_NOT_EMPTY_SENTINELS = {"not_empty", "notempty", "not_null", "notnull", "非空"}

_RED_STATUSES = {"failed", "broken", "error"}


def _status_family(value: Any) -> int | None:
    try:
        return int(value) // 100
    except (TypeError, ValueError):
        return None


def _explicit_case_statuses(case: TestCase, first_http: Any) -> set[int]:
    """只从用户可见的用例/步骤说明读取明确状态码，作为跨状态族修复依据。"""
    text = " ".join((
        str(getattr(case, "name", "") or ""),
        str(getattr(case, "description", "") or ""),
        str(getattr(first_http, "step_name", "") or ""),
    ))
    return {int(value) for value in _STATUS_INTENT_RE.findall(text)}


# ---------------------------------------------------------------------------
# 报告数据装载
# ---------------------------------------------------------------------------
def _load_report_rows(session: Session, report_id: int) -> dict[int, list[TestStepReport]]:
    """report → {case_id: [按 id 升序的步骤行]}。"""
    rows = (
        session.query(TestStepReport)
        .filter(TestStepReport.report_id == report_id)
        .order_by(TestStepReport.case_id, TestStepReport.id)
        .all()
    )
    by_case: dict[int, list[TestStepReport]] = {}
    for r in rows:
        if r.case_id is None:
            continue
        by_case.setdefault(r.case_id, []).append(r)
    return by_case


def case_status_of(rows: list[TestStepReport]) -> str:
    """聚合一条用例的执行状态：any red → failed；全 passed/skipped 且有 passed → passed。"""
    if not rows:
        return "unknown"
    statuses = [(r.status or "").lower() for r in rows]
    if any(s in _RED_STATUSES for s in statuses):
        return "failed"
    if any(s == "passed" for s in statuses):
        return "passed"
    return "unknown"  # 全 skipped / 无状态


def _first_http_response(rows: list[TestStepReport]) -> tuple[Optional[Any], Optional[int]]:
    """第一条 http 步骤的 (响应 JSON, status_code)；响应非 JSON 返回 (None, code)。

    与前端应用语义对齐：顶层 fix.extract / fix.assertion 落到第一条 http_request 步骤。
    """
    target = None
    for r in rows:
        if (r.step_type or "") == "http_request" or r.status_code is not None:
            target = r
            break
    if target is None and rows:
        target = rows[0]
    if target is None:
        return None, None
    body = None
    raw = target.output_data or ""
    if raw.strip():
        try:
            body = json.loads(raw)
        except Exception:  # noqa: BLE001
            body = None
    return body, target.status_code


# ---------------------------------------------------------------------------
# 变量产出表（var → 最早产出它的用例在执行顺序里的位置）
# ---------------------------------------------------------------------------
def _step_extract_vars(step_extract: Any) -> list[str]:
    out: list[str] = []
    if isinstance(step_extract, dict):
        out.extend(str(k) for k in step_extract.keys())
    elif isinstance(step_extract, list):
        for rule in step_extract:
            if isinstance(rule, dict) and rule.get("name"):
                out.append(str(rule.get("name")))
    return out


def _effective_extract(step: Any) -> Any:
    """返回 Runner 真正会使用的提取配置。

    快速编辑器把规则保存在 ``config.extract_data``，而结构化步骤保存在
    ``step.extract``。HTTP Runner 明确以前者优先，诊断与修复也必须使用同一优先级。
    """
    config = step.config if isinstance(step.config, dict) else {}
    raw = config.get("extract_data")
    if raw not in (None, "", {}, []):
        parsed = _parse_json_loose(raw)
        return parsed if parsed else raw
    return step.extract or []


def _effective_assertion(step: Any) -> Any:
    """返回 Runner 真正会使用的断言配置。"""
    config = step.config if isinstance(step.config, dict) else {}
    raw = config.get("assertion")
    if raw not in (None, "", {}, []):
        parsed = _parse_json_loose(raw)
        return parsed if parsed else raw
    return step.assertion or []


def _build_producer_positions(
    ordered_cases: list[TestCase],
    rows_by_case: dict[int, list[TestStepReport]],
) -> tuple[dict[str, int], dict[int, int]]:
    """返回 (var → 最早产出位置, case_id → 位置)。位置 = 执行顺序下标。"""
    producers: dict[str, int] = {}
    case_pos: dict[int, int] = {}
    for pos, case in enumerate(ordered_cases):
        case_pos[case.id] = pos
        for step in case.steps or []:
            for var in _step_extract_vars(_effective_extract(step)):
                producers.setdefault(var, pos)
        # 实际执行提取到的变量也算（比定义更真实）
        for r in rows_by_case.get(case.id, []):
            for var in _parse_json_loose(r.extract_values).keys():
                producers.setdefault(str(var), pos)
    return producers, case_pos


def _parse_json_loose(text: Any) -> dict:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _referenced_vars(obj: Any) -> set[str]:
    try:
        blob = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        blob = str(obj)
    return set(_VAR_REF_RE.findall(blob))


# ---------------------------------------------------------------------------
# 程序化候选线索（喂给诊断 prompt，把"猜"变成"选"）
# ---------------------------------------------------------------------------
def find_jsonpath_candidates(body: Any, var_name: str, max_results: int = 3) -> list[tuple[str, Any]]:
    """在响应 JSON 树里按 key 名搜索与 var_name 精确/近似匹配的路径。

    extract 取不到值多半是路径写错而值其实在响应里——正确路径可以直接算出来，
    不需要模型猜。返回 [(jsonpath, 示例值)]，按匹配度/深度排序。
    """
    vn = str(var_name or "").strip().strip("_").lower()
    if not vn or body is None:
        return []
    hits: list[tuple[int, int, str, Any]] = []   # (score, depth, path, value)
    queue: list[tuple[str, Any, int]] = [("$", body, 0)]
    while queue:
        path, node, depth = queue.pop(0)
        if depth > 8 or len(hits) > 40:
            break
        if isinstance(node, dict):
            for k, v in node.items():
                kp = f"{path}.{k}"
                kl = str(k).lower()
                score = None
                if kl == vn:
                    score = 0
                elif kl.endswith(f"_{vn}") or kl.startswith(f"{vn}_"):
                    score = 1
                elif len(vn) >= 3 and (vn in kl or kl in vn):
                    score = 2
                if score is not None and not isinstance(v, (dict, list)):
                    hits.append((score, depth, kp, v))
                if isinstance(v, (dict, list)):
                    queue.append((kp, v, depth + 1))
        elif isinstance(node, list):
            for i, v in enumerate(node[:3]):
                if isinstance(v, (dict, list)):
                    queue.append((f"{path}[{i}]", v, depth + 1))
    hits.sort(key=lambda h: (h[0], h[1], len(h[2])))
    out: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for _s, _d, p, v in hits:
        if p in seen:
            continue
        seen.add(p)
        out.append((p, v))
        if len(out) >= max_results:
            break
    return out


def _sample(v: Any, limit: int = 60) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
    return s[:limit] + ("…" if len(s) > limit else "")


def _iter_pydantic_errors(body: Any):
    """FastAPI/pydantic 风格错误：{"detail":[{"loc":[...,"field"],"msg":...,"type":...}]}。"""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        for e in detail:
            if isinstance(e, dict) and e.get("loc"):
                loc = e["loc"]
                field = str(loc[-1]) if isinstance(loc, list) and loc else ""
                yield field, str(e.get("msg") or ""), str(e.get("type") or "")


def build_case_hints(case_def: dict, rows: list, producers: dict[str, str]) -> list[str]:
    """对一条用例做确定性预分析，产出高置信线索（注入诊断 prompt 的 hints 字段）。

    case_def: _serialize_api_case_definition 的产物；rows: 该用例的 TestStepReport 行。
    producers: 变量 → 最早产出方描述（"登录成功 (extract $.data.token)"）。
    """
    hints: list[str] = []

    # 逐行解析响应 JSON（用未截断的原始 output_data）
    bodies: list[tuple[Any, Any]] = []   # (body, status_code)
    for r in rows:
        raw = getattr(r, "output_data", None) or ""
        body = None
        if isinstance(raw, str) and raw.strip():
            try:
                body = json.loads(raw)
            except Exception:  # noqa: BLE001
                body = None
        bodies.append((body, getattr(r, "status_code", None)))

    # 1) extract 规则失效 → 在真实响应里算出候选路径
    extract_rules: dict[str, str] = {}
    ed = case_def.get("extract_data")
    if isinstance(ed, dict):
        extract_rules.update({str(k): str(v) for k, v in ed.items()})
    for st in case_def.get("steps") or []:
        ex = st.get("extract") if isinstance(st, dict) else None
        if isinstance(ex, dict):
            for k, v in ex.items():
                extract_rules.setdefault(str(k), str(v))
        elif isinstance(ex, list):
            for rule in ex:
                if isinstance(rule, dict) and rule.get("name"):
                    extract_rules.setdefault(
                        str(rule["name"]), str(rule.get("jsonpath") or rule.get("path") or ""),
                    )
    for var, jp in list(extract_rules.items())[:8]:
        if not jp.startswith("$"):
            continue
        resolved = any(extractor(b, jp) is not None for b, _ in bodies if b is not None)
        if resolved:
            continue
        for b, _ in bodies:
            if b is None:
                continue
            cands = find_jsonpath_candidates(b, var)
            if cands:
                cand_txt = "；".join(f"{p}（示例 {_sample(v)}）" for p, v in cands[:2])
                hints.append(
                    f"extract「{var}」路径 {jp} 在真实响应取不到，但响应里存在近似键：{cand_txt}。"
                    f"fix.extract 应改用其中正确的路径"
                )
                break

    # 2) 已发送请求里出现字面 ${var} = 变量未解析
    unresolved: set[str] = set()
    for r in rows:
        req_raw = getattr(r, "input_data", None) or ""
        if isinstance(req_raw, str):
            unresolved |= set(_VAR_REF_RE.findall(req_raw))
    for var in sorted(unresolved)[:4]:
        root = var.split(".")[0]
        prod = producers.get(root)
        if prod:
            hints.append(
                f"请求发送时变量 ${{{var}}} 未被解析（原样发出）。产出方是「{prod}」——"
                f"检查其是否排在本用例之前且执行成功；若是顺序问题给 fix.reorder"
            )
        else:
            hints.append(
                f"请求发送时变量 ${{{var}}} 未被解析，且报告内没有任何用例产出它——"
                f"改用变量产出表里已有的变量，或先给上游用例补 extract"
            )

    # 3) 401 且缺 Authorization → 直接给出可用 token 变量
    has_401 = any(sc == 401 for _, sc in bodies)
    if has_401:
        headers = {}
        for st in case_def.get("steps") or []:
            if isinstance(st, dict) and st.get("headers"):
                headers = st["headers"] if isinstance(st["headers"], dict) else {}
                break
        auth = str(headers.get("Authorization") or headers.get("authorization") or "")
        if (not auth) or ("TODO" in auth.upper()) or ("<" in auth):
            token_vars = [v for v in producers if "token" in v.lower()]
            if token_vars:
                tv = sorted(token_vars)[0]
                hints.append(
                    f"响应 401 且 Authorization 头缺失/含占位（当前值 {auth!r}）。"
                    f"变量产出表已有 ${{{tv}}}（产出方：{producers[tv]}）→ "
                    f'fix.headers 建议 {{"Authorization": "Bearer ${{{tv}}}"}}'
                )

    # 4) 断言失败的 actual 值就在结果里
    shown = 0
    for r in rows:
        ar_raw = getattr(r, "assertion_results", None) or ""
        try:
            ar = json.loads(ar_raw) if isinstance(ar_raw, str) and ar_raw.strip() else []
        except Exception:  # noqa: BLE001
            ar = []
        for item in ar if isinstance(ar, list) else []:
            if not isinstance(item, dict) or item.get("status") != "failed" or shown >= 3:
                continue
            hints.append(
                f"断言失败 {item.get('target')}: expected={_sample(item.get('expected'))} "
                f"actual={_sample(item.get('actual'))}。若 actual 才是正确业务结果，"
                f"fix.assertion 把 expected 改成 actual（动态值用 not_empty）"
            )
            shown += 1

    # 5) 4xx 错误响应里明确指出的字段问题（FastAPI/pydantic detail）
    params_keys: set[str] = set()
    for st in case_def.get("steps") or []:
        if isinstance(st, dict) and isinstance(st.get("params"), dict):
            params_keys |= {str(k) for k in st["params"].keys()}
    for b, sc in bodies:
        if sc not in (400, 422) or b is None:
            continue
        for field, msg, typ in list(_iter_pydantic_errors(b))[:3]:
            if field and field not in params_keys:
                hints.append(
                    f"错误响应指出字段「{field}」有问题（{typ or msg}），但当前 params 键为 "
                    f"{sorted(params_keys) or '（空）'}——疑似字段名写错/缺失，fix.params 按响应要求修正"
                )
        break

    return hints[:6]


# ---------------------------------------------------------------------------
# 预检
# ---------------------------------------------------------------------------
def _looks_dynamic(target: str, expected: Any) -> bool:
    if isinstance(expected, str) and _DYNAMIC_VALUE_RE.match(expected.strip()):
        return True
    tail = (target or "").rsplit(".", 1)[-1].lower()
    return any(h in tail for h in _DYNAMIC_TARGET_HINTS)


def _merge_params(orig: dict, fix: dict) -> dict:
    """浅合并：fix 覆盖 orig；fix 里显式 null 的键删除（字段改名后清旧键用）。"""
    merged = {**(orig or {}), **(fix or {})}
    return {k: v for k, v in merged.items() if not (k in fix and fix[k] is None)}


def preflight_report_fixes(session: Session, report_id: int, items: list[dict]) -> list[dict]:
    """对诊断结果做应用前预检。

    返回与 items 等长的列表，每项：
      {case_id, name, eligible, fix: {sanitized...}, request_changed: bool,
       dropped: [{part, reason}], deferred: [part...]}
    """
    rows_by_case = _load_report_rows(session, report_id)
    case_ids = list(rows_by_case.keys())
    cases = (
        session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id.in_(case_ids))
        .all()
    ) if case_ids else []
    case_map = {c.id: c for c in cases}
    ordered = sorted(cases, key=lambda c: (c.sort_order if c.sort_order is not None else 1 << 30, c.id))
    producers, case_pos = _build_producer_positions(ordered, rows_by_case)

    # 用户反馈硬约束：标过「无需处理」或更正为「正常」的用例，无论模型这次怎么判，
    # 自动修复一律跳过（提示模型是软约束，这里是程序保险）。
    try:
        from server.services.ai_flag_service import get_no_touch_case_ids
        no_touch = get_no_touch_case_ids(session, case_ids)
    except Exception:  # noqa: BLE001
        no_touch = set()

    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result = _preflight_one(item, case_map, rows_by_case, producers, case_pos)
        if result.get("eligible") and result.get("case_id") in no_touch:
            result["eligible"] = False
            result["request_changed"] = False
            result["fix"] = {"extract": {}, "assertion": {}, "params": {}, "headers": {}, "steps": [], "pre_hook": []}
            result["dropped"].append({
                "part": "all",
                "reason": "用户曾将该用例标记为『无需处理/正常』，跳过自动修复",
            })
        out.append(result)
    return out


def _sanitize_pre_hook(raw: Any) -> tuple[list, list[str]]:
    """规整 fix.pre_hook（会话隔离用的登录前置）。

    期望形态：[{"type":"http_request","config":{"method":"POST","path":"/api/.../login",
              "params":{...}, "extract_data":{"token":"$.data.access_token"}}}]
    返回 (规整后的 hook 列表, 提取出的变量名列表)。非法 → ([], [])。

    只做结构校验：必须是 http 登录 hook 且 extract 出至少一个变量;能否真登录成功
    由应用后的自动重跑验证兜底（绿变红自动回滚）。
    """
    if not isinstance(raw, list) or not raw:
        return [], []
    out: list = []
    produced: list[str] = []
    for hk in raw:
        if not isinstance(hk, dict):
            continue
        cfg = hk.get("config") if isinstance(hk.get("config"), dict) else hk
        method = str(cfg.get("method") or "").upper()
        path = str(cfg.get("path") or cfg.get("url") or "")
        if not path or method not in ("POST", "GET", "PUT"):
            continue
        # 提取规则：兼容 extract_data({var:jsonpath}) 与 extract(规则列表)
        ext = cfg.get("extract_data") or cfg.get("extract") or {}
        if isinstance(ext, dict):
            produced.extend(str(k) for k in ext if str(k).strip())
        elif isinstance(ext, list):
            produced.extend(
                str(r.get("name")) for r in ext
                if isinstance(r, dict) and r.get("name")
            )
        out.append({
            "type": hk.get("type") or "http_request",
            "config": {
                "method": method or "POST",
                "path": path,
                "data_type": cfg.get("data_type") or "application/json",
                "params": cfg.get("params") or cfg.get("body") or {},
                "headers": cfg.get("headers") or {},
                "extract_data": ext if isinstance(ext, dict) else {},
            },
        })
    if not produced:
        # 登录 hook 不提取任何变量 = 没意义
        return [], []
    return out, produced


def _sanitize_insert_steps(raw: Any) -> tuple[list, list[str]]:
    """规整 fix.insert_steps（在用例开头插入**可见**的前置步骤）。

    与被禁用的 pre_hook 的本质区别：insert_steps 落成真正的 TestStep 行，
    用户在步骤编辑器里看得见、能改、能删；pre_hook 是藏在用例配置里的隐藏逻辑。
    「不做隐藏魔法」的原则保留，但"给用例补一个登录步骤"这件事本身应当允许。

    白名单（宁可拒绝也不冒险，任何一条不满足就整体丢弃）：
      - 只允许 GET / POST（不许 PUT/PATCH/DELETE —— 补前置不该改动或删除数据）
      - 必须 extract 出至少一个变量（不产出变量的前置步骤没有意义）
      - 凭据类字段（password/secret/token 等）只能引用 ${变量}，不许明文字面量
        —— 防止模型把猜的密码硬编码进用例库

    返回 (规整后的步骤列表, 产出的变量名列表)。
    """
    if not isinstance(raw, list) or not raw:
        return [], []

    cred_keys = ("password", "passwd", "pwd", "secret", "token", "credential", "api_key")
    out: list = []
    produced: list[str] = []
    for st in raw[:3]:                      # 最多补 3 步，防止模型塞一整条用例进来
        if not isinstance(st, dict):
            continue
        cfg = st.get("config") if isinstance(st.get("config"), dict) else st
        method = str(cfg.get("method") or "").upper()
        path = str(cfg.get("path") or cfg.get("url") or "")
        if not path or method not in ("GET", "POST"):
            return [], []

        ext = cfg.get("extract_data") or cfg.get("extract") or {}
        names = (
            [str(k) for k in ext if str(k).strip()] if isinstance(ext, dict)
            else [str(r.get("name")) for r in ext
                  if isinstance(r, dict) and r.get("name")] if isinstance(ext, list)
            else []
        )
        if not names:
            return [], []

        params = cfg.get("params") or cfg.get("body") or {}
        if isinstance(params, dict):
            for k, v in params.items():
                if any(c in str(k).lower() for c in cred_keys):
                    if not (isinstance(v, str) and "${" in v):
                        # 明文凭据 —— 整体拒绝
                        return [], []

        out.append({
            "step_name": str(st.get("step_name") or "AI 补充的前置步骤")[:200],
            "step_type": "http_request",
            "config": {
                "method": method,
                "path": path,
                "data_type": cfg.get("data_type") or "application/json",
                "params": params if isinstance(params, dict) else {},
                "headers": cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {},
                "extract_data": ext if isinstance(ext, dict) else {},
            },
        })
        produced.extend(names)
    return (out, produced) if out else ([], [])


def _preflight_one(
    item: dict,
    case_map: dict[int, TestCase],
    rows_by_case: dict[int, list[TestStepReport]],
    producers: dict[str, int],
    case_pos: dict[int, int],
) -> dict:
    dropped: list[dict] = []
    deferred: list[str] = []

    def _drop(part: str, reason: str) -> None:
        dropped.append({"part": part, "reason": reason})

    cid = item.get("case_id")
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        cid = None
    name = str(item.get("name") or "")
    base = {
        "case_id": cid, "name": name, "eligible": False, "request_changed": False,
        "fix": {"extract": {}, "assertion": {}, "params": {}, "headers": {}, "steps": [],
                "pre_hook": [], "insert_steps": []},
        "dropped": dropped, "deferred": deferred,
    }

    fix = item.get("fix") if isinstance(item.get("fix"), dict) else {}
    has_any_fix = any(
        fix.get(k)
        for k in ("extract", "assertion", "params", "headers", "steps", "pre_hook", "insert_steps")
    )
    if cid is None or cid not in case_map:
        if has_any_fix:
            _drop("all", "case_id 无效或用例已不存在")
        return base
    if not has_any_fix:
        return base

    classification = str(item.get("classification") or "").strip()
    if classification != "用例问题":
        _drop("all", f"classification={classification or '空'}，仅「用例问题」允许自动修复")
        return base

    case = case_map[cid]
    rows = rows_by_case.get(cid) or []
    status = case_status_of(rows)
    response_body, status_code = _first_http_response(rows)

    fe = fix.get("extract") if isinstance(fix.get("extract"), dict) else {}
    fa = fix.get("assertion") if isinstance(fix.get("assertion"), dict) else {}
    fp = fix.get("params") if isinstance(fix.get("params"), dict) else {}
    fh = fix.get("headers") if isinstance(fix.get("headers"), dict) else {}
    step_fixes_in = [sf for sf in (fix.get("steps") or []) if isinstance(sf, dict)]

    # AI 不得再向用户不可见的执行区写入前置步骤。现有 pre_hook 已通过用例编辑器
    # 开放给用户维护；模型若建议新增登录准备，只保留为诊断建议，不自动落库。
    sanitized_pre_hook: list[dict] = []
    pre_hook_vars: list[str] = []
    if fix.get("pre_hook"):
        _drop(
            "pre_hook",
            "已禁止 AI 自动写入前置步骤；请改用 insert_steps 插入用户可见的步骤",
        )

    # insert_steps：补一个**可见**的前置步骤（典型是登录拿 token）。
    # 与 pre_hook 的区别是它落成真正的 TestStep，用户在编辑器里看得见、可改可删。
    sanitized_inserts: list[dict] = []
    insert_vars: list[str] = []
    if fix.get("insert_steps"):
        sanitized_inserts, insert_vars = _sanitize_insert_steps(fix.get("insert_steps"))
        if not sanitized_inserts:
            _drop(
                "insert_steps",
                "前置步骤未通过白名单：只允许 GET/POST、必须 extract 出变量、"
                "且凭据字段只能引用 ${变量} 不能写明文",
            )
        elif status == "passed":
            sanitized_inserts, insert_vars = [], []
            _drop("insert_steps", "用例本次已通过，不插入前置步骤（防绿变红）")

    # ── 已通过的用例：只允许纯增量 extract/assertion（治假通过），不许动请求 ──
    if status == "passed" and (fp or fh or step_fixes_in):
        _drop("params/headers", "用例本次已通过，不自动改请求参数/请求头（防绿变红）")
        fp, fh = {}, {}
        step_fixes_in = [
            {
                "step_id": sf.get("step_id"),
                "extract": sf.get("extract"),
            }
            for sf in step_fixes_in
            if isinstance(sf.get("extract"), dict) and sf.get("extract")
        ]

    # ── 请求侧 fix：变量校验 + 合并 ────────────────────────────────
    my_pos = case_pos.get(cid, 1 << 30)
    original_refs = _referenced_vars(
        [{"config": s.config, "extract": s.extract, "assertion": s.assertion} for s in (case.steps or [])]
    )
    # 新插入的前置步骤产出的变量，视作本用例内可用（它们排在所有原步骤之前）
    pre_hook_var_names = set(pre_hook_vars) | set(insert_vars)

    def _vars_ok(obj: Any, part: str) -> bool:
        new_vars = _referenced_vars(obj) - original_refs
        bad = [
            v for v in new_vars
            if v.split(".")[0] not in pre_hook_var_names
            and producers.get(v.split(".")[0], 1 << 31) > my_pos
        ]
        if bad:
            _drop(part, f"引用的变量 {sorted(bad)} 没有排在本用例之前的产出方，运行时无法解析")
            return False
        return True

    http_steps = [s for s in sorted(case.steps or [], key=lambda s: (int(s.step_order or 0), s.id or 0))
                  if s.step_type == "http_request"]
    first_http = http_steps[0] if http_steps else None
    step_by_id = {s.id: s for s in http_steps}

    sanitized_params: dict = {}
    sanitized_headers: dict = {}
    if fp:
        if first_http is None:
            _drop("params", "用例没有 http_request 步骤")
        elif _vars_ok(fp, "params"):
            cfg = first_http.config if isinstance(first_http.config, dict) else {}
            merged = _merge_params(cfg.get("params") or {}, fp)
            if merged == (cfg.get("params") or {}):
                _drop("params", "与原参数一致（no-op）")
            else:
                sanitized_params = merged
    if fh:
        if first_http is None:
            _drop("headers", "用例没有 http_request 步骤")
        elif _vars_ok(fh, "headers"):
            cfg = first_http.config if isinstance(first_http.config, dict) else {}
            merged = _merge_params(cfg.get("headers") or {}, fh)
            if merged == (cfg.get("headers") or {}):
                _drop("headers", "与原请求头一致（no-op）")
            else:
                sanitized_headers = merged

    # AI 一旦把请求中的 ${原变量} 改成新值，自动给当前步骤补同名提取赋值。
    # 这是确定性推导，不依赖模型再次正确抄写变量名或秘密值。
    inferred_first_parts: list[dict[str, Any]] = []
    if first_http is not None:
        first_cfg = first_http.config if isinstance(first_http.config, dict) else {}
        if sanitized_params:
            inferred_first_parts.append(
                infer_rebound_extracts(
                    first_cfg.get("params") or {},
                    sanitized_params,
                )
            )
        if sanitized_headers:
            inferred_first_parts.append(
                infer_rebound_extracts(
                    first_cfg.get("headers") or {},
                    sanitized_headers,
                )
            )
    inferred_first_extract = merge_rebound_extracts(*inferred_first_parts)
    fe = {**fe, **inferred_first_extract}

    sanitized_steps: list[dict] = []
    for sf in step_fixes_in:
        try:
            sid = int(sf.get("step_id"))
        except (TypeError, ValueError):
            _drop("steps", f"step_id={sf.get('step_id')!r} 无效")
            continue
        step = step_by_id.get(sid)
        if step is None:
            _drop("steps", f"step_id={sid} 不是本用例的 http 步骤")
            continue
        cfg = step.config if isinstance(step.config, dict) else {}
        entry: dict = {"step_id": sid}
        sp = sf.get("params") if isinstance(sf.get("params"), dict) else {}
        sh = sf.get("headers") if isinstance(sf.get("headers"), dict) else {}
        se = sf.get("extract") if isinstance(sf.get("extract"), dict) else {}
        merged_params: dict = {}
        merged_headers: dict = {}
        if sp and _vars_ok(sp, f"steps[{sid}].params"):
            merged = _merge_params(cfg.get("params") or {}, sp)
            if merged != (cfg.get("params") or {}):
                merged_params = merged
                entry["params"] = merged_params
        if sh and _vars_ok(sh, f"steps[{sid}].headers"):
            merged = _merge_params(cfg.get("headers") or {}, sh)
            if merged != (cfg.get("headers") or {}):
                merged_headers = merged
                entry["headers"] = merged_headers

        inferred_parts = []
        if merged_params:
            inferred_parts.append(
                infer_rebound_extracts(cfg.get("params") or {}, merged_params)
            )
        if merged_headers:
            inferred_parts.append(
                infer_rebound_extracts(cfg.get("headers") or {}, merged_headers)
            )
        inferred_extract = merge_rebound_extracts(*inferred_parts)
        combined_extract = {**se, **inferred_extract}
        if combined_extract:
            existing = _effective_extract(step)
            existing_map = (
                {str(k): v for k, v in existing.items()}
                if isinstance(existing, dict)
                else {
                    str(rule.get("name")): (
                        rule.get("jsonpath")
                        if str(rule.get("from") or "response.body").lower() == "response.body"
                        else rule.get("value")
                    )
                    for rule in (existing or [])
                    if isinstance(rule, dict) and rule.get("name")
                }
            )
            row = next((item for item in rows if item.step_id == sid), None)
            row_body = None
            if row is not None:
                parsed_body = _parse_json_loose(row.output_data)
                row_body = parsed_body if parsed_body else None
            sanitized_step_extract = {}
            for var, expression in combined_extract.items():
                name = str(var)
                if existing_map.get(name) == expression:
                    continue
                if is_response_jsonpath(expression):
                    if merged_params or merged_headers:
                        deferred.append(f"steps[{sid}].extract.{name}")
                        sanitized_step_extract[name] = expression
                    elif row_body is not None and extractor(row_body, str(expression)) is not None:
                        sanitized_step_extract[name] = expression
                    else:
                        _drop(
                            f"steps[{sid}].extract",
                            f"{name}: JSONPath 在该步骤真实响应中取不到值",
                        )
                elif inferred_extract.get(name) == expression:
                    sanitized_step_extract[name] = expression
                else:
                    _drop(
                        f"steps[{sid}].extract",
                        f"{name}: 固定值赋值不是由本步骤参数修改确定性推导，已拒绝",
                    )
            if sanitized_step_extract:
                entry["extract"] = sanitized_step_extract
        if len(entry) > 1:
            sanitized_steps.append(entry)

    request_changed = bool(
        sanitized_params
        or sanitized_headers
        or any(entry.get("params") or entry.get("headers") for entry in sanitized_steps)
    )

    # ── extract / assertion：请求没变才能对真实响应预检，否则 deferred ──
    sanitized_extract: dict = {}
    existing_extract = {}
    if first_http is not None:
        effective_extract = _effective_extract(first_http)
        if isinstance(effective_extract, dict):
            existing_extract = {str(k): v for k, v in effective_extract.items()}
        else:
            for rule in effective_extract or []:
                if isinstance(rule, dict) and rule.get("name"):
                    source = str(rule.get("from") or "response.body").lower()
                    existing_extract[str(rule["name"])] = (
                        rule.get("value")
                        if source == "value"
                        else rule.get("jsonpath") or rule.get("path") or ""
                    )
    for var, expression in (fe or {}).items():
        var = str(var)
        if first_http is None:
            _drop("extract", "用例没有 http_request 步骤")
            break
        if existing_extract.get(var) == expression:
            _drop("extract", f"{var} 已有相同提取规则（no-op）")
            continue
        if not is_response_jsonpath(expression):
            if inferred_first_extract.get(var) == expression:
                sanitized_extract[var] = expression
            else:
                _drop(
                    "extract",
                    f"{var}: 固定值赋值不是由第一步参数修改确定性推导，已拒绝",
                )
            continue
        if request_changed:
            deferred.append(f"extract.{var}")
            sanitized_extract[var] = expression
            continue
        if response_body is None:
            _drop("extract", f"{var}: 响应不是 JSON，无法预检 JSONPath，跳过")
            continue
        if extractor(response_body, str(expression)) is None:
            _drop("extract", f"{var}: JSONPath {expression} 在真实响应里取不到值")
            continue
        sanitized_extract[var] = expression

    sanitized_assertion: dict = {}
    existing_assertion: dict[str, Any] = {}
    if first_http is not None:
        effective_assertion = _effective_assertion(first_http)
        if isinstance(effective_assertion, dict):
            existing_assertion = {str(k): v for k, v in effective_assertion.items()}
        else:
            for rule in effective_assertion or []:
                if isinstance(rule, dict) and rule.get("target"):
                    existing_assertion[str(rule["target"])] = rule.get("expected")
    for target, expected in (fa or {}).items():
        target = str(target)
        if first_http is None:
            _drop("assertion", "用例没有 http_request 步骤")
            break
        if target in existing_assertion and existing_assertion[target] == expected:
            _drop("assertion", f"{target} 已是 {expected!r}（no-op）")
            continue
        if target.startswith("sql:") or str(expected).startswith("sql:"):
            _drop("assertion", f"{target}: SQL 断言无法预检，请人工确认后手动添加")
            continue
        if target == "status_code" and target in existing_assertion:
            previous = existing_assertion[target]
            if (
                _status_family(previous) is not None
                and _status_family(expected) is not None
                and _status_family(previous) != _status_family(expected)
                and int(expected) not in _explicit_case_statuses(case, first_http)
            ):
                _drop(
                    "assertion",
                    f"禁止仅凭一次真实响应把状态码从 {previous} 跨状态族改为 {expected}；"
                    f"用例名称/描述/步骤名需明确写出“返回{expected}”",
                )
                continue
        # 动态值 → 强制 not_empty
        if not (isinstance(expected, str) and expected.strip().lower() in _NOT_EMPTY_SENTINELS) \
                and _looks_dynamic(target, expected):
            expected = "not_empty"
        if request_changed:
            deferred.append(f"assertion.{target}")
            sanitized_assertion[target] = expected
            continue
        if target == "status_code":
            if status_code is None or str(expected) != str(status_code):
                _drop("assertion", f"status_code 期望 {expected!r}，真实响应是 {status_code!r}")
                continue
        elif target.startswith("$"):
            if response_body is None:
                _drop("assertion", f"{target}: 响应不是 JSON，无法预检，跳过")
                continue
            actual = extractor(response_body, target)
            if isinstance(expected, str) and expected.strip().lower() in _NOT_EMPTY_SENTINELS:
                if actual in (None, "", [], {}):
                    _drop("assertion", f"{target}: 真实响应里该值为空，not_empty 断言会失败")
                    continue
            elif actual != expected and str(actual) != str(expected):
                _drop("assertion", f"{target}: 期望 {expected!r}，真实响应是 {actual!r}")
                continue
        # 其它 target（header 之类）无法预检 → 保守丢弃
        elif not request_changed:
            _drop("assertion", f"{target}: 无法对真实响应预检的断言目标，跳过")
            continue
        sanitized_assertion[target] = expected

    base["fix"] = {
        "extract": sanitized_extract,
        "assertion": sanitized_assertion,
        "params": sanitized_params,
        "headers": sanitized_headers,
        "steps": sanitized_steps,
        "pre_hook": sanitized_pre_hook,
        "insert_steps": sanitized_inserts,
    }
    base["request_changed"] = request_changed or bool(sanitized_inserts)
    base["eligible"] = bool(
        sanitized_extract or sanitized_assertion or request_changed or sanitized_steps
        or sanitized_pre_hook or sanitized_inserts
    )
    return base


# ---------------------------------------------------------------------------
# 应用（每用例一个事件，同一个 batch）
# ---------------------------------------------------------------------------
def _upsert_extract_rule(rules: Any, name: str, expression: Any) -> list:
    new = [dict(r) for r in (rules or []) if isinstance(r, dict)]
    for r in new:
        if str(r.get("name") or "") == name:
            r.clear()
            r.update(extract_rule(name, expression))
            return new
    new.append(extract_rule(name, expression))
    return new


def _apply_extract_patch_to_step(step: Any, patch: dict[str, Any]) -> None:
    """把提取补丁落到步骤，并在固定值赋值出现时切换为无歧义的结构化规则。"""
    cfg = dict(step.config or {})
    config_extract = _parse_json_loose(cfg.get("extract_data"))
    has_assignment = any(
        not is_response_jsonpath(expression)
        for expression in patch.values()
    )

    if has_assignment:
        rules = [dict(rule) for rule in (step.extract or []) if isinstance(rule, dict)]
        # config.extract_data 是历史简写，所有值都保持“响应路径”语义；迁移到结构化
        # 规则后才能与 from=value 的固定值赋值安全共存。
        for name, jsonpath in config_extract.items():
            rules = [
                rule for rule in rules
                if str(rule.get("name") or "") != str(name)
            ]
            rules.append({
                "name": str(name),
                "from": "response.body",
                "jsonpath": str(jsonpath),
            })
        for name, expression in patch.items():
            rules = _upsert_extract_rule(rules, str(name), expression)
        step.extract = rules
        cfg.pop("extract_data", None)
        step.config = cfg
        return

    for name, expression in patch.items():
        step.extract = _upsert_extract_rule(
            step.extract,
            str(name),
            expression,
        )
    if cfg.get("extract_data") not in (None, "", {}, []) or config_extract:
        cfg["extract_data"] = {**config_extract, **patch}
        step.config = cfg


def _upsert_assertion_rule(rules: Any, target: str, expected: Any) -> list:
    if isinstance(expected, str) and expected.strip().lower() in _NOT_EMPTY_SENTINELS:
        rule = {"type": "is_not_null", "target": target, "expected": None,
                "description": f"AI 修复补充：{target}"}
    else:
        rule = {"type": "jsonpath" if target.startswith("$") else "equal",
                "target": target, "expected": expected,
                "description": f"AI 修复补充：{target}"}
    new = [dict(r) for r in (rules or []) if isinstance(r, dict)]
    for i, r in enumerate(new):
        if str(r.get("target") or "") == target:
            new[i] = rule
            return new
    new.append(rule)
    return new


def apply_report_fixes(
    session: Session,
    report_id: int,
    items: list[dict],
    *,
    operator_id: int | None = None,
) -> dict:
    """预检 + 应用。返回 {batch_id, applied:[{case_id,name,event_id,parts}], skipped:[...]}."""
    checked = preflight_report_fixes(session, report_id, items)
    eligible = [c for c in checked if c["eligible"]]
    skipped = [
        {"case_id": c["case_id"], "name": c["name"], "reasons": [d["reason"] for d in c["dropped"]]}
        for c in checked if not c["eligible"] and (c["dropped"] or c["case_id"] is None)
    ]
    if not eligible:
        return {"batch_id": None, "applied": [], "skipped": skipped}

    batch = create_test_case_batch(
        session,
        action=EDIT_ACTION_UPDATE,
        operator_id=operator_id,
        summary=f"AI 参数修复（报告 #{report_id}，预检通过 {len(eligible)} 条）",
    )

    applied: list[dict] = []
    for c in eligible:
        case = (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id == c["case_id"])
            .first()
        )
        if case is None:
            skipped.append({"case_id": c["case_id"], "name": c["name"], "reasons": ["用例已被删除"]})
            continue
        before = snapshot_test_case(case)
        parts = _apply_fix_to_case(case, c["fix"])
        if not parts:
            skipped.append({"case_id": c["case_id"], "name": c["name"], "reasons": ["无实际改动"]})
            continue
        session.flush()
        event = record_test_case_update(
            session,
            case,
            before_snapshot=before,
            field_changes=[],
            operator_id=operator_id,
            summary=f"AI 参数修复：{case.name}",
            batch=batch,
        )
        applied.append({
            "case_id": case.id,
            "name": case.name,
            "event_id": event.id if event else None,
            "parts": parts,
            "dropped": c["dropped"],
            "deferred": c["deferred"],
        })
    session.flush()
    return {"batch_id": batch.id if applied else None, "applied": applied, "skipped": skipped}


def _apply_fix_to_case(case: TestCase, fix: dict) -> list[str]:
    """把 sanitize 过的 fix 落到 TestStep / TestCase。返回实际改动的部分。"""
    parts: list[str] = []

    steps_sorted = sorted(case.steps or [], key=lambda s: (int(s.step_order or 0), s.id or 0))
    http_steps = [s for s in steps_sorted if s.step_type == "http_request"]
    if not http_steps:
        return parts
    first_http = http_steps[0]
    step_by_id = {s.id: s for s in http_steps}

    # insert_steps：在用例最前面插入可见的前置步骤（如登录拿 token）。
    # 落成真正的 TestStep 行，用户在编辑器里看得见、可改可删 —— 与被禁的 pre_hook
    # （隐藏逻辑）的本质区别就在这里。原有步骤整体后移。
    inserted = fix.get("insert_steps") or []
    if inserted:
        from database.models import TestStep

        shift = len(inserted)
        for s in steps_sorted:
            s.step_order = int(s.step_order or 0) + shift
        for idx, spec in enumerate(inserted):
            case.steps.append(TestStep(
                case_id=case.id,
                step_order=idx,
                step_name=spec["step_name"],
                step_type="http_request",
                config=spec["config"],
                on_failure="stop",          # 前置失败就没必要继续跑主步骤
            ))
        parts.append("insert_steps")

    for var, expression in (fix.get("extract") or {}).items():
        _apply_extract_patch_to_step(
            first_http,
            {str(var): expression},
        )
        if "extract" not in parts:
            parts.append("extract")
    for target, expected in (fix.get("assertion") or {}).items():
        target_name = str(target)
        first_http.assertion = _upsert_assertion_rule(first_http.assertion, target_name, expected)
        cfg = dict(first_http.config or {})
        config_assertion = _parse_json_loose(cfg.get("assertion"))
        if cfg.get("assertion") not in (None, "", {}, []) or config_assertion:
            cfg["assertion"] = {**config_assertion, target_name: expected}
            first_http.config = cfg
        if "assertion" not in parts:
            parts.append("assertion")

    if fix.get("params"):
        cfg = dict(first_http.config or {})
        cfg["params"] = fix["params"]          # 预检阶段已合并完成
        first_http.config = cfg
        parts.append("params")
    if fix.get("headers"):
        cfg = dict(first_http.config or {})
        cfg["headers"] = fix["headers"]        # 预检阶段已合并完成
        first_http.config = cfg
        parts.append("headers")

    for sf in fix.get("steps") or []:
        step = step_by_id.get(sf.get("step_id"))
        if step is None:
            continue
        cfg = dict(step.config or {})
        if sf.get("params"):
            cfg["params"] = sf["params"]
        if sf.get("headers"):
            cfg["headers"] = sf["headers"]
        step_extract = sf.get("extract") if isinstance(sf.get("extract"), dict) else {}
        step.config = cfg
        if step_extract:
            _apply_extract_patch_to_step(step, step_extract)
        if "steps" not in parts:
            parts.append("steps")

    return parts


# ---------------------------------------------------------------------------
# 验证执行装配（端点首轮 + 循环后续轮共用）
# ---------------------------------------------------------------------------
def prepare_verification_run(session: Session, *, project_id: int, category: str,
                             base_report_id: int) -> Optional[dict]:
    """装配一次验证执行：重跑 base_report 涉及的全部用例（按执行顺序）。

    重跑全量而不是只跑被修用例：保证 ${var} 依赖链完整，且能发现跨用例连带回归。
    返回 {"report_id", "task_id", "cases_to_run"}；调用方 commit 后自行 dispatch
    run_test_task（保证 worker 能看到 report 行）。无可跑用例返回 None。
    """
    import uuid
    from datetime import datetime

    from database.models import AUTOMATED_CASE_TYPES, TestReport
    from utils.read_test_cases import get_cases_v2_from_db

    rows_by_case = _load_report_rows(session, base_report_id)
    if not rows_by_case:
        return None
    case_rows = (
        session.query(TestCase.id, TestCase.sort_order)
        .filter(TestCase.id.in_(list(rows_by_case.keys())))
        .all()
    )
    ordered_ids = [r.id for r in sorted(
        case_rows, key=lambda r: (r.sort_order if r.sort_order is not None else 1 << 30, r.id),
    )]
    cases_to_run: list = []
    for case_id in ordered_ids:
        try:
            cases_to_run.extend(get_cases_v2_from_db(
                {"project": project_id, "module": None, "category": category, "case": case_id},
                session,
            ))
        except Exception as exc:  # noqa: BLE001 —— 单条用例被删/损坏不阻塞整体验证
            LOGGER.warning("[ai_fix] 验证装载用例 %s 失败：%s", case_id, exc)
    cases_to_run = [
        c for c in cases_to_run
        if (c.get("case_type") if isinstance(c, dict) else None) in AUTOMATED_CASE_TYPES
    ]
    if not cases_to_run:
        return None

    now = datetime.now()
    report = TestReport(
        project_id=project_id,
        category=category,
        status="running",
        start_time=now,
        executor="AI修复验证",
        total_count=len(cases_to_run),
    )
    session.add(report)
    session.flush()
    session.refresh(report)
    return {
        "report_id": report.id,
        "task_id": f"{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
        "cases_to_run": cases_to_run,
    }


# ---------------------------------------------------------------------------
# 闭环：对比 + 回滚
# ---------------------------------------------------------------------------
def compare_and_rollback(
    session: Session,
    *,
    orig_report_id: int,
    verify_report_id: int,
    batch_id: int | None,
    applied: list[dict],
) -> dict:
    """按用例对比新旧报告；绿变红和红仍红都回滚，只有验证转绿才保留。"""
    old_rows = _load_report_rows(session, orig_report_id)
    new_rows = _load_report_rows(session, verify_report_id)

    fixed: list[dict] = []          # red → green
    regressed: list[dict] = []      # green → red（已回滚）
    still_red: list[dict] = []      # red → red
    kept_green: list[dict] = []     # green → green
    unknown: list[dict] = []
    collateral: list[dict] = []     # 未被修改的用例绿变红（被别的修复连带打挂，无法自动回滚）

    applied_ids = {a.get("case_id") for a in applied}
    rollback_event_ids: list[int] = []
    for a in applied:
        cid = a.get("case_id")
        old_s = case_status_of(old_rows.get(cid) or [])
        new_s = case_status_of(new_rows.get(cid) or [])
        entry = {"case_id": cid, "name": a.get("name"), "before": old_s, "after": new_s}
        if old_s == "failed" and new_s == "passed":
            fixed.append(entry)
        elif old_s == "passed" and new_s == "failed":
            regressed.append(entry)
            if a.get("event_id"):
                rollback_event_ids.append(int(a["event_id"]))
        elif old_s == "failed" and new_s == "failed":
            still_red.append(entry)
            if a.get("event_id"):
                rollback_event_ids.append(int(a["event_id"]))
        elif old_s == "passed" and new_s == "passed":
            kept_green.append(entry)
        else:
            unknown.append(entry)

    # 跨用例连带回归：本轮没改过它，但它从绿变红（典型：某修复改了共享状态）。
    # 没有对应编辑事件可回滚，单独列出来给人工/下一轮判断。
    for cid in old_rows.keys() & new_rows.keys():
        if cid in applied_ids:
            continue
        if case_status_of(old_rows[cid]) == "passed" and case_status_of(new_rows[cid]) == "failed":
            collateral.append({"case_id": cid, "before": "passed", "after": "failed"})

    rollback_result: dict = {"rolled_back": 0, "conflicts": []}
    if rollback_event_ids and batch_id:
        try:
            rollback_result = rollback_test_case_events(
                session,
                batch_id=batch_id,
                event_ids=rollback_event_ids,
                reason=f"AI 修复验证：{len(rollback_event_ids)} 条修改未通过转绿验证，自动回滚",
                force=False,
            )
            if rollback_result.get("conflicts"):
                # 用户在验证期间改过这些用例 → 不强行覆盖，保留冲突信息给人工
                LOGGER.warning(
                    "[ai_fix] 回滚存在冲突（验证期间用例被修改），放弃自动回滚: %s",
                    rollback_result["conflicts"],
                )
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("[ai_fix] 自动回滚失败：%s", exc)
            rollback_result = {"rolled_back": 0, "conflicts": [], "error": str(exc)}

    return {
        "fixed": fixed,
        "regressed": regressed,
        "still_red": still_red,
        "kept_green": kept_green,
        "unknown": unknown,
        "collateral_regressed": collateral,
        "rolled_back_count": rollback_result.get("rolled_back", 0),
        "rollback_conflicts": rollback_result.get("conflicts") or [],
        "rollback_error": rollback_result.get("error"),
    }


def rollback_applied_fixes(
    session: Session,
    *,
    batch_id: int | None,
    applied: list[dict],
    reason: str,
) -> dict:
    """验证任务无法得出结论时，回滚本轮所有候选修改，避免未验证内容留库。"""
    event_ids = [
        int(item["event_id"])
        for item in applied
        if item.get("event_id") is not None
    ]
    if not batch_id or not event_ids:
        return {"rolled_back": 0, "conflicts": []}
    try:
        return rollback_test_case_events(
            session,
            batch_id=batch_id,
            event_ids=event_ids,
            reason=reason,
            force=False,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("[ai_fix] 未验证修改回滚失败：%s", exc)
        return {"rolled_back": 0, "conflicts": [], "error": str(exc)}


def compute_final_summary(
    session: Session,
    *,
    orig_report_id: int,
    final_report_id: int,
    rounds: list[dict],
) -> dict:
    """多轮结束后按**最初报告**为基线做总账。

    fixed = 最初红、最终绿、且任一轮改过它的用例；
    still_red = 最初红、最终仍红、且改过它的用例；
    regressed/rolled_back/collateral 按轮累计。
    """
    orig_rows = _load_report_rows(session, orig_report_id)
    final_rows = _load_report_rows(session, final_report_id)

    ever_applied: dict[int, str] = {}
    regressed_total = 0
    rolled_back_total = 0
    collateral_ids: set[int] = set()
    for rd in rounds:
        for a in rd.get("applied") or []:
            if a.get("case_id") is not None:
                ever_applied[int(a["case_id"])] = str(a.get("name") or "")
        v = rd.get("verify") or {}
        regressed_total += int(v.get("regressed_count") or 0)
        rolled_back_total += int(v.get("rolled_back_count") or 0)
        for c in v.get("collateral_regressed") or []:
            if c.get("case_id") is not None:
                collateral_ids.add(int(c["case_id"]))

    fixed: list[dict] = []
    still_red: list[dict] = []
    for cid, cname in ever_applied.items():
        old_s = case_status_of(orig_rows.get(cid) or [])
        new_s = case_status_of(final_rows.get(cid) or [])
        if old_s != "failed":
            continue
        entry = {"case_id": cid, "name": cname, "before": old_s, "after": new_s}
        if new_s == "passed":
            fixed.append(entry)
        elif new_s == "failed":
            still_red.append(entry)

    # 最初就红、但模型从头到尾没给出可应用修复的
    untouched_red = [
        {"case_id": cid}
        for cid, rows in orig_rows.items()
        if cid not in ever_applied and case_status_of(rows) == "failed"
    ]

    return {
        "fixed_count": len(fixed),
        "still_red_count": len(still_red),
        "regressed_count": regressed_total,
        "rolled_back_count": rolled_back_total,
        "collateral_regressed_count": len(collateral_ids),
        "untouched_red_count": len(untouched_red),
        "rounds_used": len(rounds),
        "details": {"fixed": fixed, "still_red": still_red, "untouched_red": untouched_red},
    }
