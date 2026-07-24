"""L1 确定性失败分诊的规则单测。

规则优先级和边界都要锁住——尤其是"不该判的别硬判"：
L1 只处理有铁证的情况，判不了就返回 None 交给 L2，宁可待定也不要瞎归因。

跑法：pytest tests/api/test_failure_triage.py -v
"""
from __future__ import annotations

import json

# 用别名导入：模型名以 Test 开头，直接导入会被 pytest 当成测试类去收集
from database.models import TestStepReport as StepReportModel
from server.services.failure_triage import (
    CLS_API, CLS_CASE, CLS_ENV, triage_step,
)


def _step(*, error="", code=None, output=None, sent=None, target="http://h/api/x"):
    return StepReportModel(
        report_id=1, case_id=10, step_id=100, step_name="s", step_type="http_request",
        status="failed", status_code=code, error_message=error,
        target=target,
        input_data=json.dumps(sent, ensure_ascii=False) if sent is not None else None,
        output_data=json.dumps(output, ensure_ascii=False) if output is not None else None,
    )


def _triage(step, producers=None, failed_cases=None):
    return triage_step(step, producers=producers or {}, failed_case_ids=failed_cases or set())


# ---------------------------------------------------------------- 环境类
def test_connection_failure_is_env():
    v = _triage(_step(error="ConnectionError: Max retries exceeded"))
    assert v["classification"] == CLS_ENV and v["subtype"] == "connection"


def test_rate_limit_is_env():
    v = _triage(_step(code=429, error="断言失败: status_code: 429 != 200"))
    assert v["classification"] == CLS_ENV and v["subtype"] == "rate_limit"


def test_runtime_error_without_response_is_env():
    """执行器自身抛异常、请求没发出去（报告 1 那 163 条的真实形态）。"""
    v = _triage(_step(error="ValueError: 平台元数据库只支持 PostgreSQL，不支持: <empty>"))
    assert v["classification"] == CLS_ENV and v["subtype"] == "runtime_error"


# ---------------------------------------------------------------- 接口类
def test_5xx_is_api_problem():
    v = _triage(_step(code=500, error="断言失败: status_code: 500 != 200", output={"detail": "boom"}))
    assert v["classification"] == CLS_API and v["subtype"] == "server_error"


def test_extract_target_absent_from_response_is_api_problem():
    """要提取的字段响应里根本没有 → 接口没返回，不是用例写错。"""
    v = _triage(_step(
        code=200, error="参数提取失败：nickname ($.data.nickname)；HTTP 200",
        output={"data": {"id": 1}},
    ))
    assert v["classification"] == CLS_API and v["subtype"] == "missing_field"


# ---------------------------------------------------------------- 用例类
def test_dangling_variable_literal_is_case_problem():
    v = _triage(_step(code=401, target="http://h/api/users/${user_id}",
                      error="断言失败: status_code: 401 != 200"))
    assert v["classification"] == CLS_CASE and v["subtype"] == "dangling_var"
    assert "user_id" in v["summary"]


def test_upstream_failure_is_attributed_to_producer():
    """变量有产出方、但产出方本轮失败了 → 归因到上游，并带上 case_id。"""
    step = _step(code=401, target="http://h/api/x?t=${token}",
                 error="断言失败: status_code: 401 != 200")
    v = _triage(step, producers={"token": 7}, failed_cases={7})
    assert v["subtype"] == "upstream_failed"
    assert v["related_case_ids"] == [7]


def test_missing_auth_header_is_case_problem():
    """期望 2xx 却 401，且真实请求没带 Authorization（报告 8 里 8 条的形态）。"""
    v = _triage(_step(
        code=401, error="断言失败 1/1 条:\n[equal] status_code: 401 != 201",
        sent={"method": "POST", "headers": {"Content-Type": "application/json"}},
        output={"detail": "未提供认证 token"},
    ))
    assert v["classification"] == CLS_CASE and v["subtype"] == "missing_auth"


def test_negative_auth_case_is_not_flagged_as_missing_auth():
    """【鉴权】类负向用例本就该不带 token，期望 401 —— 绝不能误判成缺鉴权。"""
    v = _triage(_step(
        code=401, error="断言失败 1/1 条:\n[equal] status_code: 401 != 403",
        sent={"method": "GET", "headers": {"Content-Type": "application/json"}},
        output={"detail": "未提供认证 token"},
    ))
    assert v is None or v["subtype"] != "missing_auth"


def test_wrong_jsonpath_is_case_problem_with_fix_hint():
    """值在响应里、只是路径写错 → 直接算出正确路径给 fix_hint。"""
    v = _triage(_step(
        code=200, error="参数提取失败：access_token ($.access_token)；HTTP 200",
        output={"status": "success", "data": {"access_token": "abc123"}},
    ))
    assert v["classification"] == CLS_CASE and v["subtype"] == "wrong_jsonpath"
    assert "access_token" in (v.get("fix_hint") or {}).get("extract", {})


def test_wrong_success_code_assertion_is_case_problem():
    """请求成功了（200），只是断言期望另一个 2xx（201）→ 断言与实现不符，可算出修复。

    真实来源：平台 POST /api/users 返回 200，AI 按 REST 惯例断言 201，
    报告 19 里 58 条同因。
    """
    v = _triage(_step(code=200, error="断言失败 1/1 条:\n[equal] status_code: 200 != 201",
                      output={"data": {"id": 1}}))
    assert v["classification"] == CLS_CASE and v["subtype"] == "wrong_status_assertion"
    assert v["fix_hint"]["assertion"]["status_code"] == 200


def test_cross_class_status_mismatch_is_left_to_l2():
    """2xx vs 4xx 含语义分歧（到底该不该拒绝），L1 不判。"""
    v = _triage(_step(code=200, error="断言失败 1/1 条:\n[equal] status_code: 200 != 401",
                      sent={"headers": {}}, output={"status": "success"}))
    assert v is None


def test_sql_column_error_is_case_problem():
    """SQL 校验里的列名不存在 → 用例写错，不是接口问题。"""
    v = _triage(_step(
        code=200,
        error='ProgrammingError: (psycopg2.errors.UndefinedColumn) column "is_superuser" does not exist',
        output={"data": {}},
    ))
    assert v["classification"] == CLS_CASE and v["subtype"] == "bad_sql"
    assert "is_superuser" in v["summary"]


# ---------------------------------------------------------------- 边界
def test_pure_assertion_mismatch_is_left_to_l2():
    """纯断言语义分歧（登出是否该幂等）L1 判不了 —— 必须返回 None，不硬归因。"""
    v = _triage(_step(
        code=200, error="断言失败 1/1 条:\n[equal] status_code: 200 != 401",
        sent={"method": "POST", "headers": {}}, output={"status": "success"},
    ))
    assert v is None


def test_template_params_not_misread_as_dangling():
    """input_data 里 params 是模板（原样保留 ${var}），不能当成"未解析残留"。

    真实误报：一条登录用例密码写错导致 401，params 模板里有 ${user_admin}
    （body 里其实已解析成 admin），被误判成变量悬空，盖过了真正的失败原因。
    悬空检测只看实际发送的 body / url，不看模板 params。
    """
    sent = {
        "url": "http://h/api/auth/login",
        "body": {"username": "admin", "password": "wrong"},   # 已解析
        "params": {"username": "${user_admin}", "password": "wrong"},  # 模板，含 ${}
    }
    v = _triage(_step(
        code=401, target="http://h/api/auth/login",
        error="断言失败 1/1 条:\n[equal] status_code: 401 != 200",
        sent=sent, output={"detail": "用户名或密码错误"},
    ))
    # 不该判成 dangling_var；登录接口的 401 也不该判成 missing_auth
    assert v is None or v.get("subtype") not in ("dangling_var", "missing_auth"), v


def test_login_401_is_not_missing_auth():
    """登录/注册接口本来就不带 Authorization，其 401 是凭据错，不是缺鉴权头。"""
    for path in ("http://h/api/auth/login", "http://h/api/users/register", "http://h/api/auth/refresh"):
        v = _triage(_step(
            code=401, target=path,
            error="断言失败 1/1 条:\n[equal] status_code: 401 != 200",
            sent={"url": path, "body": {"username": "x"}, "headers": {"Content-Type": "application/json"}},
            output={"detail": "用户名或密码错误"},
        ))
        assert v is None or v.get("subtype") != "missing_auth", f"{path}: {v}"


def test_dangling_var_takes_priority_over_runtime_error():
    """变量悬空的归因优先于"没有响应"的兜底，避免抢走更准确的结论。"""
    v = _triage(_step(target="http://h/api/x/${oid}", error="some failure"))
    assert v["subtype"] == "dangling_var"


# ===================================================================
# L1 → L2 衔接：L1 分掉能算的，LLM 只看剩下的
# ===================================================================
def _fake_triage():
    return {
        "cases": [
            {"case_id": 1, "case_name": "A", "classification": CLS_CASE, "subtype": "dangling_var",
             "summary": "变量无人产出：token", "evidence": "残留 ${token}", "suggestion": "补登录步骤"},
            {"case_id": 2, "case_name": "B", "classification": CLS_CASE, "subtype": "wrong_jsonpath",
             "summary": "路径写错", "evidence": "提取失败", "suggestion": "改路径",
             "fix_hint": {"extract": {"token": "$.data.access_token"}}},
            {"case_id": 3, "case_name": "C", "classification": "待定", "subtype": None,
             "summary": "需 AI 判断", "evidence": "断言不符", "suggestion": "用 AI 分析"},
        ]
    }


def test_undetermined_ids_are_the_ones_sent_to_llm():
    from server.services.failure_triage import undetermined_case_ids
    assert undetermined_case_ids(_fake_triage()) == {3}


def test_l1_items_exclude_undetermined_and_match_diagnosis_shape():
    """L1 项不占位待定用例，且字段与 AI 诊断项对齐（下游无需区分来源）。"""
    from server.services.failure_triage import as_diagnosis_items
    items = as_diagnosis_items(_fake_triage(), {1: 10, 2: 20})
    assert [i["case_id"] for i in items] == [1, 2]        # 待定的不产出
    required = {"case_id", "module_id", "name", "classification", "findings", "fix", "source"}
    assert required <= set(items[0])
    assert items[0]["module_id"] == 10 and items[0]["source"] == "L1"
    # findings 要把归因链条讲清楚：结论 + 依据 + 建议
    assert any("依据" in f for f in items[0]["findings"])
    assert any("建议" in f for f in items[0]["findings"])


def test_only_rule_computable_fixes_are_emitted():
    """规则算得出正确值的才给 fix；缺前置这类给空 fix —— 不猜怎么改。"""
    from server.services.failure_triage import as_diagnosis_items
    items = {i["case_id"]: i for i in as_diagnosis_items(_fake_triage())}
    assert items[1]["fix"]["extract"] == {}                                   # dangling_var 不猜
    assert items[2]["fix"]["extract"] == {"token": "$.data.access_token"}     # jsonpath 可算
