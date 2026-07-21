"""HttpRequestStepRunner：执行 step_type='http_request' 这一步。

新版 step 的 JSON 结构约定（v2）：

    step.config = {
        "method": "POST",
        "path": "/api/login",                   # 支持完整 URL 或 相对路径（拼 base_url）
        "headers": {"X-Trace": "abc"},          # dict，不是 JSON 字符串
        "data_type": "application/json",        # json / form / multipart / x-www-form-urlencoded
        "params": {"username": "${USERNAME}"},  # dict / list，支持 ${var} 占位符
        "file_path": null,                      # multipart 用
        "sql_query": null,                      # 可选：默认请求后跑 SQL 校验；sql_query_phase='before' 可改为请求前
    }

    step.extract = [
        {"name": "token", "from": "response.body", "jsonpath": "$.data.token"},
        {"name": "uid",   "from": "response.body", "jsonpath": "$.data.user.id"},
    ]

    step.assertion = [
        {"type": "equal",    "target": "status_code", "expected": 200},
        {"type": "jsonpath", "target": "$.code",       "expected": 0},
        {"type": "contains", "target": "body_text",    "expected": "ok"},
    ]

Runner 职责：
  1. 解析变量（${var}）
  2. 发请求
  3. 执行 extract，把有效新变量塞进 ctx.vars 和 processor.extra_pool（便于后续 step 引用）
  4. 执行 assertion，失败 raise AssertionError 让 BaseStepRunner 走 FAILED 分支

实现上尽量复用 v1 的 `ApiClient._send_api` 内的 requests 细节，但走新的 step 字典，
避免与老字段（method/path/headers as 字符串）混淆。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests
from requests.exceptions import JSONDecodeError

from runners.context.auth_cache import RunAuthCache, build_auth_request_signature, is_login_path
from runners.context.execution_context import ExecutionContext
from runners.protocol import BaseStepRunner, StepResult
from utils.allure_utils import add_allure_failed_step, add_allure_step, set_allure_link
from utils.encrypt import RequestCryptoProcessor
from utils.logger import LOGGER
from utils.platform_utils import extractor, rep_expr
from utils.value_resolver import resolve_value


# 允许的 data_type（与 v1 一致 + 增加两个别名）
_DATA_TYPE_ALIASES = {
    "json": "application/json",
    "form": "application/x-www-form-urlencoded",
    "multipart": "multipart/form-data",
    "application/json": "application/json",
    "application/x-www-form-urlencoded": "application/x-www-form-urlencoded",
    "multipart/form-data": "multipart/form-data",
}

# "非空 / 为空"断言哨兵：expected 写成这些词时按操作符语义判断，而不是字面量相等。
_NOT_EMPTY_SENTINELS = {"not_empty", "not_null", "notnull", "notempty", "非空", "@notnull", "@notempty"}
_IS_EMPTY_SENTINELS = {"is_null", "为空", "空", "@null"}


def _response_shape(value: Any, depth: int = 0) -> Any:
    """生成不包含业务值的响应结构，供参数提取错误报告安全展示。"""
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        items = list(value.items())
        shaped = {
            str(key): _response_shape(item, depth + 1)
            for key, item in items[:30]
        }
        if len(items) > 30:
            shaped["..."] = f"另有 {len(items) - 30} 个字段"
        return shaped
    if isinstance(value, list):
        return [_response_shape(value[0], depth + 1)] if value else []
    if value is None:
        return "null"
    return type(value).__name__


def _response_error_message(value: Any) -> str | None:
    """从常见错误响应字段中提取简短原因，不把完整响应塞进异常标题。"""
    if not isinstance(value, dict):
        return None
    for key in ("detail", "message", "error", "msg"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int, float, bool)):
            return str(candidate)
    data = value.get("data")
    if isinstance(data, dict):
        return _response_error_message(data)
    return None


def _format_extract_error(
    failures: list[dict[str, Any]],
    status_code: int,
    response_body: Any,
) -> str:
    """把提取失败项压成适合 Allure 标题和用例错误摘要的一行。"""
    items = []
    for failure in failures:
        name = failure.get("变量名") or "未命名变量"
        expression = failure.get("表达式") or failure.get("来源") or "response.body"
        items.append(f"{name} ({expression})")
    message = f"参数提取失败：{', '.join(items)}；HTTP {status_code}"
    response_error = _response_error_message(response_body)
    if response_error:
        message += f"；接口返回：{response_error}"
    return message


def _v1_json_extract_to_rules(raw: Any) -> list[dict]:
    """把 v1 风格的提取 JSON（{"token1": "$.data.token"}）转成 extract 规则列表。

    多步骤 API 用例在 StepEditor 里把"提取"以这种 JSON 存进 config.extract_data，
    runner 在顶层 step.extract 为空时用它兜底。与 server/api/cases.py 的
    _v1_extract_to_step 行为保持一致（同步维护）。
    """
    if not raw:
        return []
    obj = raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except Exception:
            return []
    out: list[dict] = []
    if isinstance(obj, dict):
        for name, jp in obj.items():
            if str(name).strip() and str(jp).strip():
                out.append({"name": str(name), "from": "response.body", "jsonpath": str(jp)})
    return out


def _v1_json_assertion_to_rules(raw: Any) -> list[dict]:
    """把 v1 风格的断言 JSON（{"status_code": 200, "$.code": 0}）转成断言规则列表。

    与 server/api/cases.py 的 _v1_assertion_to_step 行为保持一致（同步维护）。
    """
    if not raw:
        return []
    obj = raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except Exception:
            return []
    _NOT_NULL = {"not_empty", "not_null", "非空", "@notnull", "@notempty", "notnull", "notempty"}
    _IS_NULL = {"is_null", "null", "为空", "空", "@null"}
    out: list[dict] = []
    if isinstance(obj, dict):
        for target, expected in obj.items():
            t = str(target).strip()
            if not t:
                continue
            ev = expected.strip().lower() if isinstance(expected, str) else expected
            if isinstance(expected, str) and ev in _NOT_NULL:
                out.append({"type": "is_not_null", "target": t, "expected": None})
            elif isinstance(expected, str) and ev in _IS_NULL:
                out.append({"type": "is_null", "target": t, "expected": None})
            else:
                atype = "jsonpath" if t.startswith("$") else "equal"
                out.append({"type": atype, "target": t, "expected": expected})
    return out


class HttpRequestStepRunner(BaseStepRunner):
    step_types = ("http_request",)

    def __init__(self, processor=None, session: requests.Session | None = None):
        """
        :param processor: 复用 v1 的 RequestDataProcessor，拿到 base_url / base_header / 加密 等配置。
                          为 None 时首次 execute 才惰性构造，避免 import 期连数据库。
        :param session: 可以从外面传一个已经塞好 Cookie 的 session（用例链场景）。
        """
        self._processor = processor
        self._processor_project_id = None
        self._session = session or requests.Session()

    # ---------------------- 惰性构造 processor ----------------------
    @property
    def processor(self):
        if self._processor is None:
            from runners.api.factory import create_request_data_processor
            self._processor = create_request_data_processor()
        return self._processor

    def _ensure_processor(self, ctx: ExecutionContext) -> None:
        """按当前用例项目构造配置处理器。"""
        project_id = ctx.get_var("_project_id")
        if self._processor is not None and self._processor_project_id == project_id:
            return
        from runners.api.factory import create_request_data_processor
        self._processor = create_request_data_processor(project_id=project_id)
        self._processor_project_id = project_id

    # ---------------------- 主逻辑 ----------------------
    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        self._ensure_processor(ctx)
        config = step.get("config") or {}
        self._select_target_db(config.get("target_db_group"), ctx)

        method = str(config.get("method") or "GET").upper().strip()
        path = str(config.get("path") or "")
        if not path:
            raise ValueError("http_request step 缺少 config.path")

        data_type_raw = str(config.get("data_type") or "application/json").strip()
        data_type = _DATA_TYPE_ALIASES.get(data_type_raw.lower(), data_type_raw)

        headers_in = config.get("headers") or {}
        params_in = config.get("params") or {}
        file_path = config.get("file_path")

        # 1) 变量替换（${var} → ctx/pool 里的值）
        url = self._resolve_url(path, ctx)
        headers = self._resolve_dict(headers_in, ctx)
        # base_header 合并（保持跟 v1 行为一致）
        headers = {**self._base_headers(), **headers}
        # 头值安全编码：requests 用 latin-1 编码请求头，含中文/非 latin-1（如"过期token"
        # 占位、未解析变量）会直接抛 UnicodeEncodeError 把整步搞崩。这里转成可发送的形式，
        # 服务端会当成无效 token 返回 401，正好符合"过期/无效 token"类负向用例预期。
        headers = self._latin1_safe_headers(headers)
        params_template = self._decode_jsonish_value(params_in)
        body = self._resolve_value(params_template, ctx)
        if str(config.get("sql_query_phase") or "").lower() == "before":
            self._apply_sql_query(config.get("sql_query"), ctx)
        files = self.processor.handler_files(file_path) if file_path else None

        crypto = RequestCryptoProcessor(self._encryption_config(), vars=self._merged_pool(ctx))
        headers, body, crypto_request_meta = crypto.apply_request(headers, body)

        # 2) 记录 Allure: Set up（变量池）+ Request（请求详情）
        result.action = f"{method} {url}"
        result.target = url
        result.input_data = {
            "method": method,
            "url": url,
            "headers": headers,
            "params": params_template,
            "body_template": params_template,
            "body": body,
        }
        if crypto_request_meta:
            result.input_data["crypto"] = crypto_request_meta
        set_allure_link(url)
        ctx_vars_display = {k: v for k, v in ctx.vars.items()
                            if not k.startswith("_")}
        add_allure_step(
            "Set up",
            ctx_vars_display or "(空)",
            attachment_name="变量池",
        )
        add_allure_step("Request", {
            "请求方法": method,
            "请求地址": url,
            "请求头": headers,
            "实际填写的": params_template,
            "请求填写的": body,
            "加解密": crypto_request_meta or "未启用",
        })

        # 3) 发请求
        timeout = float(step.get("timeout") or 30)
        response_body, status_code = self._send(method, url, headers, body, files, data_type, timeout)
        response_body, crypto_response_meta = crypto.apply_response(response_body)

        # 认证缓存只在单轮测试运行内生效。任意成功登录都刷新同凭据缓存；若缓存 token
        # 被服务端拒绝，或刚被登出/改密请求使用，则精准失效对应缓存项。
        auth_cache = ctx.get_var("_run_auth_cache")
        if isinstance(auth_cache, RunAuthCache):
            signature = build_auth_request_signature(config, ctx)
            if is_login_path(path) and 200 <= status_code < 400:
                auth_cache.put(signature, response_body)
            mutation_path = path.lower()
            invalidating_request = any(
                hint in mutation_path
                for hint in ("logout", "signout", "revoke", "password")
            ) and 200 <= status_code < 400
            if status_code in (401, 403) or invalidating_request:
                auth_cache.invalidate_if_used({"headers": headers, "body": body})

        result.output_data = response_body
        ctx.record("status_code", status_code)
        # 把请求/响应记进 record_property，pytest 钩子会落库到 TestStepReport，
        # 供「AI 分析执行结果」读真实响应（在断言之前记，断言失败也能拿到响应）。
        ctx.record("input_data", result.input_data)
        ctx.record("output_data", response_body)
        if crypto_response_meta:
            ctx.record("crypto_response", crypto_response_meta)
        add_allure_step(f"Response (HTTP {status_code})", {
            "响应体": response_body,
            "加解密": crypto_response_meta or "未启用",
        })

        # 4) extract：把响应里的值塞进 ctx.vars 和 processor.extra_pool
        # 多步骤 API 用例从 StepEditor 存的是 config.extract_data（v1 JSON），它是编辑器的
        # 唯一来源，优先级最高；顶层 step.extract 仅作为老数据 / AI 结构化步骤的兜底。
        # （历史坑：某些步骤同时残留了旧的顶层 extract，若顶层优先会覆盖用户在 config 里
        #  改的提取变量名，导致"改了没生效"。）
        if config.get("extract_data"):
            extract_rules = _v1_json_extract_to_rules(config.get("extract_data"))
        else:
            extract_rules = step.get("extract") or []
        extracted, extract_failures = self._apply_extract(
            extract_rules,
            response_body=response_body,
            status_code=status_code,
            ctx=ctx,
        )
        result.extracted = extracted
        ctx.record("extract_values", extracted)
        ctx.record("extract_errors", extract_failures)
        if extracted:
            ctx_vars_display = {k: v for k, v in ctx.vars.items()
                                if not k.startswith("_")}
            add_allure_step(
                "变量池（提取后）",
                ctx_vars_display or "(空)",
                attachment_name="变量池（提取后）",
            )
        if extract_failures:
            error_message = _format_extract_error(
                extract_failures,
                status_code,
                response_body,
            )
            error_details = {
                "错误信息": error_message,
                "HTTP状态码": status_code,
                "接口错误": _response_error_message(response_body),
                "失败项": extract_failures,
                "响应结构": _response_shape(response_body),
            }
            if step.get("_is_hook"):
                add_allure_failed_step(
                    "参数提取失败",
                    error_message,
                    error_details,
                    attachment_name="参数提取错误详情",
                )
            else:
                add_allure_step(
                    "参数提取未命中",
                    error_details,
                    attachment_name="参数提取错误详情",
                )
            # pre_hook 是主步骤的硬依赖，声明的变量缺失必须直接失败。普通 HTTP
            # 步骤继续维持原语义：负向用例即便带了可选 token 提取，也可按 401
            # 断言正常通过，但 Allure 中会留下明确的“参数提取未命中”信息。
            if step.get("_is_hook"):
                raise AssertionError(error_message)

        if str(config.get("sql_query_phase") or "after").lower() != "before":
            self._apply_sql_query(config.get("sql_query"), ctx)

        # 5) assertion（同 extract：config.assertion 有值时以它为准，顶层仅兜底）
        if config.get("assertion"):
            assertion_rules = _v1_json_assertion_to_rules(config.get("assertion"))
        else:
            assertion_rules = step.get("assertion") or []
        self._apply_assertions(
            assertion_rules,
            response_body=response_body,
            status_code=status_code,
            ctx=ctx,
        )

    @staticmethod
    def _latin1_safe_headers(headers: dict) -> dict:
        """把请求头的 key/value 转成 latin-1 可编码，避免 requests 编码头时崩。

        非 latin-1（中文等）→ 用 UTF-8 字节再按 latin-1 解码（mojibake 但可发送），
        服务端收到的就是无效 token，返回 401/403——不会让用例以 ERROR 形式崩掉。
        """
        def _safe(v: Any) -> Any:
            if not isinstance(v, str):
                return v
            try:
                v.encode("latin-1")
                return v
            except UnicodeEncodeError:
                return v.encode("utf-8").decode("latin-1")

        out = {}
        for k, val in (headers or {}).items():
            out[_safe(k) if isinstance(k, str) else k] = _safe(val)
        return out

    # ---------------------- 内部工具 ----------------------
    def _base_headers(self) -> dict:
        try:
            return dict(self.processor.base_header or {})
        except Exception:  # 离线单测时 processor 构造失败不阻断
            return {}

    def _encryption_config(self) -> dict:
        try:
            return dict(self.processor.encryption_decryption or {})
        except Exception:  # 离线单测时 fake processor 可能没有配置
            return {}

    def _resolve_url(self, path: str, ctx: ExecutionContext) -> str:
        # 先替换变量，再决定拼不拼 base_url
        replaced = self._resolve_value(path, ctx)
        if not isinstance(replaced, str):
            replaced = str(replaced)
        if replaced.startswith(("http://", "https://")):
            return replaced
        base = self._get_base_url()
        result = f"{base.rstrip('/')}/{replaced.lstrip('/')}" if base else replaced
        LOGGER.info(f"[_resolve_url] path={path} base={base} result={result}")
        return result

    def _get_base_url(self) -> str:
        # 从 config_center 配置中心拿
        try:
            url = (self.processor.base_url or {}).get("url", "")
            if url:
                LOGGER.info(f"[_get_base_url] 配置中心命中: {url}")
                return url
        except Exception as e:
            LOGGER.warning(f"[_get_base_url] 配置中心失败: {e}")
        # 兜底：环境变量
        import os
        return os.getenv("CONFIG_HOST_URL", "")

    def _resolve_dict(self, d: Any, ctx: ExecutionContext) -> dict:
        if not d:
            return {}
        d = self._decode_jsonish_value(d)
        if not isinstance(d, dict):
            return {}
        return {k: self._resolve_value(v, ctx) for k, v in d.items()}

    @staticmethod
    def _decode_jsonish_value(value: Any) -> Any:
        """快速编辑里常把 JSON 对象存成字符串；先还原，后续才能递归解析字段。"""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text or text[0] not in "{[":
            return value
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return value

    def _resolve_value(self, value: Any, ctx: ExecutionContext) -> Any:
        """递归解析三种语法：${var} / function:foo(...) / sql:select ...。

        历史坑：本方法以前只做 rep_expr（${var}），导致 HTTP 请求体里的
        function:generate_phone 之类被原样落进 params/headers，下游 server 看到
        字面字符串而不是真正的随机手机号。
        现在统一走 utils.value_resolver.resolve_value：

          - 字符串 → 走 resolve_value（含 ${var} / function: / sql:）
          - dict / list → 递归
          - 其它 → 原样

        变量池：rep_expr 部分仍按 ctx.vars + processor.extra_pool 合并；
        但 function: / sql: 是 ctx-only（resolve_value 内部从 ctx.vars 拿），
        想让 sql: 工作记得在 ctx.vars['_db'] 注入连接（CaseExecutor 会从
        config_center.target_db 自动注入；没配就走 actionable error 提示）。
        """
        decoded = self._decode_jsonish_value(value)
        if decoded is not value:
            return self._resolve_value(decoded, ctx)

        if isinstance(value, str):
            # 把 processor.extra_pool 临时合进 ctx.vars，让 ${var} 也能取到 pool 里的值
            pool = self._merged_pool(ctx)
            extra_keys = []
            for k, v in (pool or {}).items():
                if k not in ctx.vars:
                    ctx.vars[k] = v
                    extra_keys.append(k)
            try:
                return resolve_value(value, ctx)
            finally:
                # 借用完清理，避免 extra_pool 污染 ctx 影响其它步骤
                for k in extra_keys:
                    ctx.vars.pop(k, None)
        if isinstance(value, dict):
            return {k: self._resolve_value(v, ctx) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(v, ctx) for v in value]
        return value

    def _merged_pool(self, ctx: ExecutionContext) -> dict:
        pool = {}
        try:
            pool.update(self.processor.extra_pool or {})
        except Exception:
            pass
        pool.update(ctx.vars or {})
        return pool

    # ---------------------- 发请求（requests 调用细节） ----------------------
    def _send(
        self,
        method: str,
        url: str,
        headers: dict,
        body: Any,
        files: Any,
        data_type: str,
        timeout: float,
    ) -> tuple[Any, int]:
        # JSON 字符串自动转 dict，避免 requests 的 json= 参数双重序列化导致 422
        if data_type == "application/json" and isinstance(body, str):
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                pass

        kwargs: dict = {"method": method, "url": url, "headers": headers, "timeout": timeout}

        if data_type == "application/x-www-form-urlencoded":
            kwargs["params"] = body
        elif data_type == "multipart/form-data":
            kwargs["data"] = body
            kwargs["files"] = files
        else:  # application/json 或未知回退 json
            kwargs["json"] = body
            if files:
                kwargs["files"] = files

        try:
            res = self._session.request(**kwargs)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("HTTP 请求失败 %s: %s", type(exc).__name__, exc)
            raise

        # 尽力解析 JSON，失败就退回 text
        try:
            body_out = res.json()
        except JSONDecodeError:
            body_out = res.text

        return body_out, res.status_code

    # ---------------------- extract ----------------------
    def _apply_extract(
        self,
        rules: list[dict],
        response_body: Any,
        status_code: int,
        ctx: ExecutionContext,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        extracted: dict[str, Any] = {}
        failures: list[dict[str, Any]] = []
        for rule in rules or []:
            if not isinstance(rule, dict):
                continue
            name = rule.get("name")
            if not name:
                continue
            src = (rule.get("from") or "response.body").lower()
            default = rule.get("default")

            if src == "response.body":
                expr_raw = rule.get("jsonpath") or rule.get("path")
                expr = self._resolve_extract_expr(expr_raw, ctx)
                if self._is_value_expression(expr_raw):
                    val = expr
                else:
                    val = extractor(response_body, expr) if expr else response_body
            elif src == "response.status_code":
                val = status_code
            elif src == "response.text":
                val = response_body if isinstance(response_body, str) else json.dumps(response_body, ensure_ascii=False)
            else:
                val = None

            if val is None and default is not None:
                val = self._resolve_value(default, ctx)

            # 提取失败时不要用 None 覆盖已有变量。
            # 典型场景：登录失败返回 {"detail": "..."}，$.data.token 取不到；
            # 如果把 token 写成 None，后续依赖旧 token 的步骤会被连带污染。
            # 如确实需要显式清空变量，可在提取规则中配置 overwrite_empty=true。
            if val is None and not bool(rule.get("overwrite_empty")):
                expression = rule.get("jsonpath") or rule.get("path")
                failures.append({
                    "变量名": str(name),
                    "来源": src,
                    "表达式": str(expression) if expression else None,
                    "原因": "未匹配到值（JSONPath 无效、响应结构变化或接口返回失败）",
                })
                LOGGER.warning(
                    "HTTP extract skipped empty value: name=%s source=%s status=%s",
                    name,
                    rule.get("jsonpath") or rule.get("path") or src,
                    status_code,
                )
                continue

            extracted[name] = val
            ctx.set_var(name, val)
            # 同步到 processor 变量池，供老 RequestDataProcessor 逻辑使用
            try:
                self.processor.extra_pool[name] = val
            except Exception:
                pass

        if extracted:
            add_allure_step("Extracted", extracted)
        return extracted, failures

    # ---------------------- assertion ----------------------
    def _apply_assertions(
        self,
        asserts: list[dict],
        response_body: Any,
        status_code: int,
        ctx: ExecutionContext,
    ) -> None:
        """执行所有断言，收集全部失败后统一报告。"""
        passed: list[str] = []
        failures: list[str] = []
        results: list[dict[str, Any]] = []
        for item in asserts or []:
            if not isinstance(item, dict):
                continue
            t = (item.get("type") or "equal").lower()
            target = item.get("target") or ""
            expected = self._resolve_value(item.get("expected"), ctx)

            # 归一化"非空/为空"哨兵：AI / 历史用例常生成 {"$.x": "not_empty"}，
            # 被映射成 type=jsonpath/equal + expected="not_empty" 字面量，导致非空值
            # 反而判不相等而失败。这里把哨兵转成对应操作符，按语义判断。
            if isinstance(expected, str):
                _ev = expected.strip().lower()
                if _ev in _NOT_EMPTY_SENTINELS:
                    t, expected = "is_not_null", None
                elif _ev in _IS_EMPTY_SENTINELS:
                    t, expected = "is_null", None

            actual = self._resolve_assertion_actual(target, response_body, status_code, ctx)

            try:
                if t == "equal":
                    assert actual == expected, f"[equal] {target}: {actual!r} != {expected!r}"
                elif t in ("not_equal", "ne"):
                    assert actual != expected, f"[not_equal] {target}: {actual!r} == {expected!r}"
                elif t == "contains":
                    assert expected in (actual or ""), f"[contains] {target}: {expected!r} not in {actual!r}"
                elif t == "not_contains":
                    assert expected not in (actual or ""), f"[not_contains] {target}: {expected!r} in {actual!r}"
                elif t == "jsonpath":
                    assert actual == expected, f"[jsonpath] {target}: {actual!r} != {expected!r}"
                elif t in ("gt", "greater_than"):
                    assert actual is not None and actual > expected, f"[gt] {target}: {actual!r} <= {expected!r}"
                elif t in ("lt", "less_than"):
                    assert actual is not None and actual < expected, f"[lt] {target}: {actual!r} >= {expected!r}"
                elif t in ("in",):
                    assert actual in expected, f"[in] {target}: {actual!r} not in {expected!r}"
                elif t == "regex":
                    import re
                    assert expected and re.search(expected, str(actual or "")), \
                        f"[regex] {target}: pattern {expected!r} not matched in {actual!r}"
                elif t == "is_null":
                    assert actual is None, f"[is_null] {target}: {actual!r} is not None"
                elif t == "is_not_null":
                    assert actual not in (None, "", [], {}), f"[is_not_null] {target}: 空值 {actual!r}"
                elif t == "raw":
                    assert actual == expected, f"[raw] {target}: {actual!r} != {expected!r}"
                else:
                    failures.append(f"不支持的断言类型: {t!r}")
                    results.append({
                        "type": t,
                        "target": target,
                        "expected": expected,
                        "actual": actual,
                        "status": "failed",
                        "error": f"不支持的断言类型: {t!r}",
                    })
                    continue

                passed.append(f"[{t}] {target} OK")
                item_result = {
                    "type": t,
                    "target": target,
                    "expected": expected,
                    "actual": actual,
                    "status": "passed",
                }
                results.append(item_result)
                add_allure_step("Assertion", item_result)

            except AssertionError as e:
                msg = str(e)
                failures.append(msg)
                item_result = {
                    "type": t,
                    "target": target,
                    "expected": expected,
                    "actual": actual,
                    "status": "failed",
                    "error": msg,
                }
                results.append(item_result)
                add_allure_step("Assertion", item_result)

        if failures:
            ctx.record("assertion_results", results)
            add_allure_step("断言结果", {
                "通过": f"{len(passed)} 条",
                "失败": f"{len(failures)} 条",
                "详情": results,
            })
            raise AssertionError(
                f"断言失败 {len(failures)}/{len(passed) + len(failures)} 条:\n"
                + "\n".join(failures)
            )

        if passed:
            ctx.record("assertion_results", results)
            LOGGER.info("断言全部通过 %s 条", len(passed))
            add_allure_step("断言结果", {"通过": f"{len(passed)} 条", "状态": "全部通过", "详情": results})

    def _resolve_assertion_actual(
        self,
        target: Any,
        body: Any,
        status_code: int,
        ctx: ExecutionContext,
    ) -> Any:
        """把 target 字段解析成"实际值"。约定：
            - 'status_code' → HTTP status
            - 'body_text' → 响应体字符串
            - 以 'sql:' 开头 → 查 target_db，返回第一行/单值
            - 以 '$' 开头 → 把 target 当 jsonpath
            - 其他 → 直接当 body 的顶层键取（body.get(target)）
        """
        if isinstance(target, str):
            if target.startswith("sql:"):
                return self._resolve_value(target, ctx)
            target = self._resolve_value(target, ctx)

        if target == "status_code":
            return status_code
        if target == "body_text":
            return body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        if isinstance(target, str) and target.startswith("$"):
            return extractor(body, target)
        if isinstance(body, dict):
            return body.get(target)
        return None

    def _resolve_extract_expr(self, expr: Any, ctx: ExecutionContext) -> Any:
        if not isinstance(expr, str):
            return expr
        if self._is_value_expression(expr):
            return self._resolve_value(expr, ctx)
        return self._resolve_value(expr, ctx)

    @staticmethod
    def _is_value_expression(expr: Any) -> bool:
        return isinstance(expr, str) and expr.strip().startswith(("function:", "sql:"))

    def _apply_sql_query(self, raw_sql: Any, ctx: ExecutionContext) -> None:
        if raw_sql is None or str(raw_sql).strip() == "":
            return

        sql_text = self._resolve_sql_text(raw_sql, ctx)
        statements = [s.strip() for s in sql_text.split(";") if s.strip()]
        if not statements:
            return

        conn = ctx.vars.get("_db")
        if conn is None:
            LOGGER.warning("HTTP sql_query 未执行：未注入 target DB（ctx._db 为空）")
            return

        results = []
        for index, stmt in enumerate(statements, start=1):
            rows = self._query_sql(conn, stmt)
            first = rows[0] if rows else None
            results.append(first)
            ctx.set_var(f"sql_query_results_{index}", first)
            try:
                self.processor.extra_pool[f"sql_query_results_{index}"] = first
            except Exception:  # noqa: BLE001
                pass

        ctx.set_var("sql_query_results", results)
        try:
            self.processor.extra_pool["sql_query_results"] = results
        except Exception:  # noqa: BLE001
            pass
        add_allure_step("SQL Query", {"sql": sql_text, "results": results})

    @staticmethod
    def _select_target_db(raw_group: Any, ctx: ExecutionContext) -> None:
        """按 HTTP 步骤选择目标数据库；同一用例内按配置组复用连接。"""
        group = str(raw_group or "").strip()
        if not group or group == ctx.vars.get("_db_group"):
            return

        project_id = ctx.vars.get("_project_id")
        from utils.reload_config import config_center

        target = config_center.get(group, project_id=project_id) or {}
        if not target:
            raise ValueError(f"数据库连接配置组 {group!r} 不存在或没有配置项")

        connections = ctx.vars.get("_db_connections")
        if not isinstance(connections, dict):
            connections = {}
            ctx.set_var("_db_connections", connections)
        connection = connections.get(group)
        if connection is None:
            from database.db import DB
            connection = DB({**target, "password": target.get("password", "")})
            connections[group] = connection
        ctx.set_var("_db", connection)
        ctx.set_var("_db_group", group)
        LOGGER.info("HTTP 步骤已切换目标数据库配置组：%s", group)

    def _resolve_sql_text(self, raw_sql: Any, ctx: ExecutionContext) -> str:
        if isinstance(raw_sql, str) and raw_sql.strip().startswith("function:"):
            return str(self._resolve_value(raw_sql, ctx) or "")

        pool = self._merged_pool(ctx)
        sql_text = rep_expr(str(raw_sql), pool)
        leftovers = re.findall(r"\$\{[^}\n]+\}", sql_text)
        if leftovers:
            raise ValueError(
                "SQL 校验存在未解析变量："
                f"{', '.join(leftovers)}。请确认变量已在环境变量、用例变量或前序提取中写入。"
            )
        if sql_text.strip().startswith("sql:"):
            sql_text = sql_text.strip()[4:].strip()
        return sql_text

    @staticmethod
    def _query_sql(conn: Any, stmt: str) -> list[Any]:
        sql_handler = getattr(conn, "sql", None)
        if sql_handler is not None and hasattr(sql_handler, "query"):
            return sql_handler.query(stmt)
        if hasattr(conn, "query"):
            return conn.query(stmt)
        if sql_handler is not None and hasattr(sql_handler, "fetchone"):
            value = sql_handler.fetchone(stmt)
            return [] if value is None else [value]
        if hasattr(conn, "fetchone"):
            value = conn.fetchone(stmt)
            return [] if value is None else [value]
        raise RuntimeError(f"注入的 DB 连接不支持 query/fetchone：{type(conn).__name__}")
