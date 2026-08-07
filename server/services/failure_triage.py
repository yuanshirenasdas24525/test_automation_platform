"""L1 确定性失败分诊 —— 不调 LLM，按规则给失败用例定性。

出处：docs/AI用例质量-实施方案.md 第 2.3 节。

为什么先做确定性层：一份报告里大部分失败的归因是**可以算出来的**，不需要模型判断——
比如"请求里带着没解析的 ${admin_token} 字面量"就是铁证，"响应树里明明有 access_token
只是 extract 路径写错了"也能直接搜出来。把这些先分掉，LLM 只处理真正需要语义判断的
（业务规则是否符合预期、断言是否合理），既省 token 又更准。

分类与 ai_gateway/prompts/api_report_diagnose.md 的四分类保持一致：
  用例问题 / 接口问题 / 环境/其他 / 待定（L1 判不了，交给 L2）

每条结论都带 evidence（为什么这么判），不做无依据的猜测。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import TestCase, TestReport, TestStepReport

# 分类
CLS_CASE = "用例问题"
CLS_API = "接口问题"
CLS_ENV = "环境/其他"
CLS_UNKNOWN = "待定"

# 未解析变量字面量：请求里出现 ${xxx} 说明变量池里没有它
_VAR_LITERAL_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")
# "参数提取失败：token ($.data.token), uid ($.data.id)；HTTP 200"
_EXTRACT_FAIL_RE = re.compile(r"参数提取失败：(.+?)(?:；|$)")
_EXTRACT_ITEM_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
# "[equal] status_code: 401 != 201" —— 左实际、右期望
_STATUS_ASSERT_RE = re.compile(r"status_code:\s*(\d{3})\s*!=\s*(\d{3})")
_EXPLICIT_STATUS_INTENT_RE = re.compile(
    r"(?:返回|http(?:\s*状态码)?)[：:\s_-]*(\d{3})",
    re.IGNORECASE,
)
# 连接层失败（不是被测服务返回的，是根本没连上）
_CONN_HINTS = (
    "ConnectionError", "Max retries", "Connection refused", "Timeout", "timed out",
    "NewConnectionError", "ReadTimeout", "ConnectTimeout", "Name or service not known",
)


def _loads(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not raw or not str(raw).strip():
        return None
    try:
        return json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return None


def _failed(step: TestStepReport) -> bool:
    return (step.status or "").lower() in ("failed", "error", "broken")


def _collect_producers(session: Session, report_id: int) -> dict[str, int]:
    """本轮报告里 var → 产出它的 case_id（取最早一次成功提取）。"""
    producers: dict[str, int] = {}
    rows = (
        session.query(TestStepReport)
        .filter(TestStepReport.report_id == report_id)
        .order_by(TestStepReport.create_time.asc().nullslast())
        .all()
    )
    for r in rows:
        got = _loads(r.extract_values)
        if isinstance(got, dict):
            for name in got:
                producers.setdefault(str(name), r.case_id)
    return producers


def _status_pair(error_message: str) -> tuple[Optional[int], Optional[int]]:
    """从断言失败信息里取 (实际状态码, 期望状态码)。

    平台的断言错误格式是 `[equal] status_code: 401 != 201`，左实际右期望。
    """
    m = _STATUS_ASSERT_RE.search(error_message or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _extract_failed_vars(error_message: str) -> list[tuple[str, str]]:
    """从错误信息里解析出 [(变量名, 表达式)]。"""
    m = _EXTRACT_FAIL_RE.search(error_message or "")
    if not m:
        return []
    return [(name, expr.strip()) for name, expr in _EXTRACT_ITEM_RE.findall(m.group(1))]


def _auth_failure_assertion_intent(
    *,
    case_name: str,
    step_name: str,
    target: str,
) -> bool:
    """判断 401/403 是否正是用例标题明确表达的鉴权失败结果。"""
    text = f"{case_name} {step_name}".lower().replace("_", " ")
    path = (target or "").lower()
    if any(hint in text for hint in ("返回401", "返回 401", "返回403", "返回 403")):
        return True
    if "refresh" in path and any(
        hint in text
        for hint in (
            "无效refresh token", "无效 refresh token", "无效刷新令牌",
            "已登出", "登出后", "退出后", "已退出", "会话已失效",
            "refresh token失效", "refresh token 失效", "刷新令牌失效",
            "撤销", "注销后",
        )
    ):
        return True
    if any(hint in path for hint in ("/login", "/signin", "/sign-in", "/auth/token")):
        return "旧密码" in text and any(
            hint in text
            for hint in ("失败", "拒绝", "密码已改", "修改密码后", "改密后")
        )
    return False


def _explicit_status_intent(*, case_name: str, step_name: str) -> set[int]:
    """从标题/步骤名读取明确写出的 HTTP 状态码，只把这种铁证用于跨状态族修复。"""
    text = f"{case_name} {step_name}"
    return {int(value) for value in _EXPLICIT_STATUS_INTENT_RE.findall(text)}


def triage_step(
    step: TestStepReport,
    *,
    producers: dict[str, int],
    failed_case_ids: set[int],
    case_name: str = "",
) -> Optional[dict]:
    """给单个失败步骤定性。返回 None 表示 L1 判不了（留给 L2）。

    规则按优先级排列——先排除"根本没跑通"的外部原因，再判用例自身问题。
    """
    err = step.error_message or ""
    code = step.status_code
    body = _loads(step.output_data)

    # 检测"未解析的 ${var}"只能看**实际发送的内容**：URL + 已解析的请求体（body）。
    # 不能看整个 input_data —— 它同时存了模板 params（原样保留 ${var}，正常现象）。
    # 曾误报：一条密码写错导致 401 的登录用例，因 params 模板里有 ${user_admin}，
    # 被判成"变量悬空"，盖过了真正的失败原因（密码不对）。
    sent = _loads(step.input_data) or {}
    if isinstance(sent, dict):
        sent_payload = {
            "url": step.target or "",
            "body": sent.get("body"),          # 已解析的真实请求体
            "headers": sent.get("headers"),    # 头里可能残留未解析的 Authorization
        }
        request_blob = json.dumps(sent_payload, ensure_ascii=False, default=str)
    else:
        request_blob = f"{step.target or ''} {step.input_data or ''}"

    # 1) 连接层失败：压根没拿到响应 —— 环境问题，跟用例写得对不对无关
    if code is None and any(h.lower() in err.lower() for h in _CONN_HINTS):
        return {
            "classification": CLS_ENV, "subtype": "connection",
            "summary": "连不上被测服务（无 HTTP 响应）",
            "evidence": err[:200],
            "suggestion": "确认被测服务已启动、host 配置与网络可达后重跑",
        }

    # 2) 限流：多为本轮执行自身造成的串扰（前面大量失败登录触发），不是这条用例的错
    if code == 429:
        return {
            "classification": CLS_ENV, "subtype": "rate_limit",
            "summary": "被限流（429），通常是本轮前序用例反复失败登录触发",
            "evidence": f"HTTP 429；{err[:160]}",
            "suggestion": "先修好触发限流的上游用例，等限流窗口过去再重跑本条",
        }

    # 3) 5xx：服务端自己炸了 —— 接口问题
    if isinstance(code, int) and code >= 500:
        return {
            "classification": CLS_API, "subtype": "server_error",
            "summary": f"服务端返回 {code}",
            "evidence": err[:200] or f"HTTP {code}",
            "suggestion": "查被测服务日志定位异常；这条不是用例写错",
        }

    # 4) 变量悬空：请求里带着没解析的 ${var} 字面量 —— 铁证
    dangling = sorted(set(_VAR_LITERAL_RE.findall(request_blob)))
    if dangling:
        upstream_failed = [
            v for v in dangling
            if v.split(".")[0] in producers and producers[v.split(".")[0]] in failed_case_ids
        ]
        no_producer = [v for v in dangling if v.split(".")[0] not in producers]
        if upstream_failed:
            return {
                "classification": CLS_CASE, "subtype": "upstream_failed",
                "summary": f"依赖的变量没拿到：{', '.join(upstream_failed)}（产出它的上游用例本轮失败了）",
                "evidence": f"请求里残留未解析字面量：{', '.join(dangling)}",
                "suggestion": "先修上游用例；本条大概率会跟着恢复",
                "related_case_ids": sorted({producers[v.split('.')[0]] for v in upstream_failed}),
            }
        return {
            "classification": CLS_CASE, "subtype": "dangling_var",
            "summary": f"变量无人产出：{', '.join(no_producer or dangling)}",
            "evidence": f"请求里残留未解析字面量：{', '.join(dangling)}",
            "suggestion": (
                "在本用例前面补一个能 extract 出它的步骤（如登录），"
                "或把该变量配进配置中心的 default_parameters / auth_provider"
            ),
        }

    # 4b) 期望成功却 401/403，且真实发出的请求没带 Authorization —— 缺鉴权，铁证。
    #     证据取自 input_data 里记录的**真实请求**，不看步骤定义：定义里写了
    #     `Authorization: Bearer ${admin_token}` 但变量解析不出来时，平台会把这个头丢掉，
    #     所以"定义里有、实际没发"正是悬空的表现形态。
    #     只在"期望 2xx"时判定——否则会把【鉴权】类负向用例（本就该不带 token）误伤。
    # 登录/注册/刷新这类"用凭据换 token"的接口本来就不带 Authorization，
    # 它们的 401 是凭据不对（账号/密码错），不是缺鉴权头 —— 不能套 missing_auth。
    _target = (step.target or "").lower()
    _is_credential_endpoint = any(
        h in _target for h in ("login", "signin", "sign_in", "register", "/auth/token", "refresh")
    )
    if code in (401, 403) and not _is_credential_endpoint:
        actual, expected = _status_pair(err)
        if expected is not None and 200 <= expected < 300:
            sent = _loads(step.input_data) or {}
            headers = {
                str(k).lower(): str(v)
                for k, v in (sent.get("headers") or {}).items()
            }
            auth = headers.get("authorization", "").strip()
            if not auth:
                return {
                    "classification": CLS_CASE, "subtype": "missing_auth",
                    "summary": f"期望 {expected} 却 {actual or code}：请求没带 Authorization 头",
                    "evidence": f"真实发出的请求头只有 {sorted(headers) or '（空）'}",
                    "suggestion": (
                        "给这一步补 Authorization 头（值引用能产出 token 的前置步骤变量），"
                        "或在配置中心配 auth_provider 让平台自动补齐"
                    ),
                }

    # 4.5) 执行期异常：既没状态码也没响应体，说明请求根本没发出去（或执行器自己就炸了）。
    #      典型是配置缺失/依赖不可用，如"平台元数据库只支持 PostgreSQL，不支持: <empty>"
    #      （worker 没带 DB 环境变量启动）。放在变量悬空之后，避免抢走那类的归因。
    if code is None and not body:
        return {
            "classification": CLS_ENV, "subtype": "runtime_error",
            "summary": "请求未发出，执行期异常（多为配置缺失或依赖不可用）",
            "evidence": err[:200],
            "suggestion": "看错误信息定位缺失的配置/依赖；这类与用例写法无关，整批同因",
        }

    # 4.6) SQL 校验本身写错：列名/表名不存在等。AI 写 sql 断言时按语义猜列名，
    #      猜错了就是这个形态。归用例问题——接口没毛病，是校验语句写错了。
    if any(h in err for h in ("UndefinedColumn", "UndefinedTable", "ProgrammingError", "psycopg2.errors")):
        m = re.search(r'column "([^"]+)" does not exist|relation "([^"]+)" does not exist', err)
        missing = next((g for g in (m.groups() if m else ()) if g), None)
        return {
            "classification": CLS_CASE, "subtype": "bad_sql",
            "summary": (
                f"SQL 校验里的 {missing!r} 在库里不存在" if missing else "SQL 校验语句执行报错"
            ),
            "evidence": err[:200],
            "suggestion": "对照真实表结构修正 SQL 断言的表名/列名；接口本身没问题",
        }

    # 4.7) 断言的成功码写错：请求其实成功了（2xx），只是期望的是另一个 2xx。
    #      典型是 AI 按 REST 惯例写 201，而接口实际返回 200。接口没毛病，是断言不符实现。
    #      严格限定"两边都是 2xx"——跨类比较（如 200 vs 401）含语义分歧，留给 L2。
    actual, expected = _status_pair(err)
    if (
        actual is not None and expected is not None
        and 200 <= actual < 300 and 200 <= expected < 300 and actual != expected
    ):
        return {
            "classification": CLS_CASE, "subtype": "wrong_status_assertion",
            "summary": f"请求已成功（{actual}），但断言期望 {expected} —— 断言与接口实现不符",
            "evidence": err[:200],
            "suggestion": f"确认接口约定后把断言改成 {actual}（或推动接口按 {expected} 返回）",
            "fix_hint": {"assertion": {"status_code": actual}},
        }

    # 4.8) 用例标题已经明确写的是鉴权失败，但断言却被编译成成功码。
    #      这类跨 2xx/4xx 比较通常有业务歧义，只有标题/步骤存在铁证时才自动定性。
    if (
        actual in (401, 403)
        and expected is not None
        and 200 <= expected < 300
        and _auth_failure_assertion_intent(
            case_name=case_name,
            step_name=step.step_name or "",
            target=step.target or "",
        )
    ):
        return {
            "classification": CLS_CASE,
            "subtype": "wrong_auth_status_assertion",
            "summary": f"鉴权失败符合用例意图（{actual}），但断言被错误编译为 {expected}",
            "evidence": err[:200],
            "suggestion": f"把当前失败步骤的状态码断言改成 {actual}",
            "fix_hint": {"assertion": {"status_code": actual}},
        }

    # 4.9) 标题/步骤名明确写了实际返回码，断言却被编译成另一个状态码。
    #      允许跨 2xx/4xx 自动修，但必须有明确文字证据，不能把偶发实际响应当契约。
    if (
        actual is not None and expected is not None and actual != expected
        and actual in _explicit_status_intent(
            case_name=case_name,
            step_name=step.step_name or "",
        )
    ):
        return {
            "classification": CLS_CASE,
            "subtype": "wrong_explicit_status_assertion",
            "summary": f"用例意图明确要求返回 {actual}，但断言被错误编译为 {expected}",
            "evidence": f"标题/步骤名明确包含返回码 {actual}；{err[:160]}",
            "suggestion": f"把当前失败步骤的状态码断言改成 {actual}",
            "fix_hint": {"assertion": {"status_code": actual}},
        }

    # 5) extract 路径写错：值其实在响应里，只是 JSONPath 指错了 —— 能直接算出正确路径
    failed_vars = _extract_failed_vars(err)
    if failed_vars and body is not None:
        # 请求先被 4xx 拒绝时，响应自然不会有成功响应字段。这里不能再归为“接口少字段”，
        # 也不能机械删除 extract；应把真实拒绝原因连同完整响应交给 L2 做语义修复。
        if isinstance(code, int) and 400 <= code < 500:
            detail = body.get("detail") if isinstance(body, dict) else None
            detail_text = json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else ""
            combined = f"{err} {detail_text}".lower()
            if code in (401, 403):
                subtype = "auth_lifecycle"
                summary = f"请求先被鉴权拒绝（{code}），提取失败只是后果"
            elif code == 422 and any(h in combined for h in ("role_code", "enum", "枚举", "可选")):
                subtype = "invalid_enum"
                summary = "请求参数不符合枚举/字段契约（422），提取失败只是后果"
            else:
                subtype = "request_rejected_before_extract"
                summary = f"请求先被接口拒绝（{code}），提取失败只是后果"
            return {
                "classification": CLS_CASE,
                "subtype": subtype,
                "summary": summary,
                "evidence": f"HTTP {code}；响应 detail={detail_text[:160] or '（无）'}",
                "suggestion": "先依据请求契约和业务场景修正请求/状态码断言，再校验成功响应的提取路径",
                "needs_semantic_fix": True,
            }

        from server.services.ai_fix_service import find_jsonpath_candidates
        fixes: dict[str, str] = {}
        for name, _expr in failed_vars:
            cands = find_jsonpath_candidates(body, name, max_results=1)
            if cands:
                fixes[name] = cands[0][0]
        if fixes:
            pairs = "，".join(f"{k} → {v}" for k, v in fixes.items())
            return {
                "classification": CLS_CASE, "subtype": "wrong_jsonpath",
                "summary": f"extract 路径写错，值其实在响应里：{pairs}",
                "evidence": err[:200],
                "suggestion": "把 extract 的 JSONPath 改成上面算出的路径",
                "fix_hint": {"extract": fixes},
            }
        return {
            "classification": CLS_API, "subtype": "missing_field",
            "summary": f"响应里找不到要提取的字段：{', '.join(n for n, _ in failed_vars)}",
            "evidence": err[:200],
            "suggestion": "确认接口是否真的该返回这些字段；是则为接口问题，否则删掉多余的 extract",
        }

    # 6) L1 判不了：断言语义、业务规则是否符合预期等 —— 交给 L2（LLM）
    return None


def triage_report(session: Session, report_id: int) -> dict:
    """对整份报告做 L1 分诊。返回 {report_id, total_failed, triaged, undetermined, cases:[…]}。"""
    report = session.query(TestReport).filter(TestReport.id == report_id).first()
    if report is None:
        raise ValueError(f"报告 {report_id} 不存在")

    rows = (
        session.query(TestStepReport)
        .filter(TestStepReport.report_id == report_id)
        .order_by(TestStepReport.create_time.asc().nullslast())
        .all()
    )
    producers = _collect_producers(session, report_id)
    failed_case_ids = {r.case_id for r in rows if _failed(r)}

    names = {
        c.id: c.name
        for c in session.query(TestCase).filter(TestCase.id.in_(failed_case_ids or {0})).all()
    }

    # fix_hint 能否安全应用：apply 层的顶层 fix 一律打到用例的**第一个 http 步骤**上
    # （见 ai_fix_service._apply_fix_to_case）。若失败发生在后面的步骤，把修复打到第一步
    # 会改坏一个本来正确的断言 —— 这种情况下宁可不给 fix，只报结论。
    from database.models import TestStep
    first_http: dict[int, tuple] = {}
    for st in (
        session.query(TestStep)
        .filter(TestStep.case_id.in_(failed_case_ids or {0}))
        .order_by(TestStep.case_id, TestStep.step_order)
        .all()
    ):
        if st.step_type == "http_request":
            first_http.setdefault(st.case_id, (st.id, st.step_name))

    seen: set[int] = set()
    cases: list[dict] = []
    for step in rows:
        if not _failed(step) or step.case_id in seen:
            continue
        seen.add(step.case_id)          # 每条用例只按它第一个失败步骤定性
        verdict = triage_step(
            step,
            producers=producers,
            failed_case_ids=failed_case_ids,
            case_name=names.get(step.case_id) or "",
        )
        if verdict and verdict.get("fix_hint"):
            target = first_http.get(step.case_id)
            # step_id 可能因为步骤被重建而对不上，退回按步骤名比对
            same = bool(target) and (
                step.step_id == target[0] or (step.step_name or "") == (target[1] or "")
            )
            if not same:
                verdict.pop("fix_hint", None)
                verdict["suggestion"] = (
                    f"{verdict.get('suggestion', '')}"
                    "（注意：问题不在本用例第一个请求上，需手工改对应步骤，平台的一键修复只作用于第一步）"
                ).strip()
        cases.append({
            "case_id": step.case_id,
            "case_name": names.get(step.case_id),
            "step_name": step.step_name,
            "status_code": step.status_code,
            **(verdict or {
                "classification": CLS_UNKNOWN,
                "subtype": None,
                "summary": "L1 规则无法定性，需 AI 语义判断",
                "evidence": (step.error_message or "")[:200],
                "suggestion": "用 AI 辅助分诊进一步分析",
            }),
        })

    by_class: dict[str, int] = {}
    for c in cases:
        by_class[c["classification"]] = by_class.get(c["classification"], 0) + 1

    return {
        "report_id": report_id,
        "report_status": report.status,
        "total_failed": len(cases),
        "triaged": sum(
            1 for c in cases
            if c["classification"] != CLS_UNKNOWN and not c.get("needs_semantic_fix")
        ),
        "undetermined": sum(
            1 for c in cases
            if c["classification"] == CLS_UNKNOWN or c.get("needs_semantic_fix")
        ),
        "by_classification": by_class,
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# 供 AI 诊断链路（L2）使用：L1 先分掉能算的，LLM 只看剩下的
# ---------------------------------------------------------------------------
def undetermined_case_ids(triage: dict) -> set[int]:
    """L1 判不了、需要 LLM 语义判断的用例 id。"""
    return {
        int(c["case_id"])
        for c in triage.get("cases") or []
        if (
            c.get("classification") == CLS_UNKNOWN or c.get("needs_semantic_fix")
        ) and c.get("case_id") is not None
    }


def as_diagnosis_items(triage: dict, module_ids: dict[int, Optional[int]] | None = None) -> list[dict]:
    """把 L1 结论转成 AI 诊断 item 的形态，好和 LLM 结果合并成一份完整结果。

    字段与 functional_cases._normalize_report_diagnosis_item 的输出对齐，
    这样下游（前端展示、ai_flag_service 打标、preflight 应用）不用区分来源。
    额外带一个 `source` 字段标明是规则判的还是 AI 判的。

    只有 wrong_jsonpath 这类"规则能直接算出正确值"的才给 fix；
    其余（缺前置步骤、环境问题）给空 fix —— 规则不该猜怎么改。
    """
    module_ids = module_ids or {}
    items: list[dict] = []
    for c in triage.get("cases") or []:
        if c.get("classification") == CLS_UNKNOWN or c.get("needs_semantic_fix"):
            continue          # 留给 LLM，别在这里占位或重复生成结论
        cid = c.get("case_id")
        hint = c.get("fix_hint") or {}
        findings = [c.get("summary") or "", f"依据：{c.get('evidence') or ''}"]
        if c.get("suggestion"):
            findings.append(f"建议：{c['suggestion']}")
        items.append({
            "case_id": cid,
            "module_id": module_ids.get(cid),
            "name": c.get("case_name") or "",
            "classification": c.get("classification"),
            "findings": [f for f in findings if f.strip()],
            "fix": {
                "extract": hint.get("extract") or {},
                "assertion": hint.get("assertion") or {},
                "params": {},
                "headers": {},
                "steps": [],
                "pre_hook": [],
                "reorder": {},
            },
            "source": "L1",
            "subtype": c.get("subtype"),
        })
    return items
