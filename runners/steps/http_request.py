"""HttpRequestStepRunner：执行 step_type='http_request' 这一步。

新版 step 的 JSON 结构约定（v2）：

    step.config = {
        "method": "POST",
        "path": "/api/login",                   # 支持完整 URL 或 相对路径（拼 base_url）
        "headers": {"X-Trace": "abc"},          # dict，不是 JSON 字符串
        "data_type": "application/json",        # json / form / multipart / x-www-form-urlencoded
        "params": {"username": "${USERNAME}"},  # dict / list，支持 ${var} 占位符
        "file_path": null,                      # multipart 用
        "sql_query": null,                      # 可选：步骤执行前先跑 SQL 再注入到变量池
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
  3. 执行 extract，把新变量塞进 ctx.vars 和 processor.extra_pool（便于后续 step 引用）
  4. 执行 assertion，失败 raise AssertionError 让 BaseStepRunner 走 FAILED 分支

实现上尽量复用 v1 的 `ApiClient._send_api` 内的 requests 细节，但走新的 step 字典，
避免与老字段（method/path/headers as 字符串）混淆。
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests
from requests.exceptions import JSONDecodeError

from runners.context.execution_context import ExecutionContext
from runners.protocol import BaseStepRunner, StepResult
from utils.allure_utils import add_allure_step, set_allure_link
from utils.logger import LOGGER
from utils.platform_utils import extractor
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


class HttpRequestStepRunner(BaseStepRunner):
    step_types = ("http_request",)

    def __init__(self, processor=None, session: requests.Session | None = None):
        """
        :param processor: 复用 v1 的 RequestDataProcessor，拿到 base_url / base_header / 加密 等配置。
                          为 None 时首次 execute 才惰性构造，避免 import 期连数据库。
        :param session: 可以从外面传一个已经塞好 Cookie 的 session（用例链场景）。
        """
        self._processor = processor
        self._session = session or requests.Session()

    # ---------------------- 惰性构造 processor ----------------------
    @property
    def processor(self):
        if self._processor is None:
            from runners.api.factory import create_request_data_processor
            self._processor = create_request_data_processor()
        return self._processor

    # ---------------------- 主逻辑 ----------------------
    def _run(self, step: dict, ctx: ExecutionContext, result: StepResult) -> None:
        config = step.get("config") or {}

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
        body = self._resolve_value(params_in, ctx)
        files = self.processor.handler_files(file_path) if file_path else None

        # 2) 记录 Allure: Set up（变量池）+ Test body（请求详情）
        result.action = f"{method} {url}"
        result.target = url
        result.input_data = {"method": method, "url": url, "headers": headers, "body": body}
        set_allure_link(url)
        ctx_vars_display = {k: v for k, v in ctx.vars.items()
                            if not k.startswith("_")}
        add_allure_step("Set up", {"变量池": ctx_vars_display or "(空)"})
        add_allure_step("Test body", {
            "请求方法": method,
            "请求地址": url,
            "请求头": headers,
            "请求参数": body,
        })

        # 3) 发请求
        timeout = float(step.get("timeout") or 30)
        response_body, status_code = self._send(method, url, headers, body, files, data_type, timeout)

        result.output_data = response_body
        ctx.record("status_code", status_code)
        add_allure_step(f"Response (HTTP {status_code})", response_body)

        # 4) extract：把响应里的值塞进 ctx.vars 和 processor.extra_pool
        extracted = self._apply_extract(
            step.get("extract") or [],
            response_body=response_body,
            status_code=status_code,
            ctx=ctx,
        )
        result.extracted = extracted

        # 5) assertion
        self._apply_assertions(
            step.get("assertion") or [],
            response_body=response_body,
            status_code=status_code,
        )

    # ---------------------- 内部工具 ----------------------
    def _base_headers(self) -> dict:
        try:
            return dict(self.processor.base_header or {})
        except Exception:  # 离线单测时 processor 构造失败不阻断
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
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:  # noqa: BLE001
                return {}
        if not isinstance(d, dict):
            return {}
        return {k: self._resolve_value(v, ctx) for k, v in d.items()}

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
    ) -> dict[str, Any]:
        extracted: dict[str, Any] = {}
        for rule in rules or []:
            if not isinstance(rule, dict):
                continue
            name = rule.get("name")
            if not name:
                continue
            src = (rule.get("from") or "response.body").lower()
            default = rule.get("default")

            if src == "response.body":
                expr = rule.get("jsonpath") or rule.get("path")
                val = extractor(response_body, expr) if expr else response_body
            elif src == "response.status_code":
                val = status_code
            elif src == "response.text":
                val = response_body if isinstance(response_body, str) else json.dumps(response_body, ensure_ascii=False)
            else:
                val = None

            if val is None and default is not None:
                val = default

            extracted[name] = val
            ctx.set_var(name, val)
            # 同步到 processor 变量池，供老 RequestDataProcessor 逻辑使用
            try:
                self.processor.extra_pool[name] = val
            except Exception:
                pass

        if extracted:
            add_allure_step("Extracted", extracted)
        return extracted

    # ---------------------- assertion ----------------------
    def _apply_assertions(
        self,
        asserts: list[dict],
        response_body: Any,
        status_code: int,
    ) -> None:
        """执行所有断言，收集全部失败后统一报告。"""
        passed: list[str] = []
        failures: list[str] = []
        for item in asserts or []:
            if not isinstance(item, dict):
                continue
            t = (item.get("type") or "equal").lower()
            target = item.get("target") or ""
            expected = item.get("expected")

            actual = self._resolve_assertion_actual(target, response_body, status_code)

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
                    assert actual is not None, f"[is_not_null] {target}: None"
                elif t == "raw":
                    assert actual == expected, f"[raw] {target}: {actual!r} != {expected!r}"
                else:
                    failures.append(f"不支持的断言类型: {t!r}")
                    continue

                passed.append(f"[{t}] {target} OK")
                add_allure_step("Assertion", {"type": t, "target": target,
                                              "expected": expected, "actual": actual,
                                              "status": "passed"})

            except AssertionError as e:
                msg = str(e)
                failures.append(msg)
                add_allure_step("Assertion", {"type": t, "target": target,
                                              "expected": expected, "actual": actual,
                                              "status": "failed", "error": msg})

        if failures:
            add_allure_step("断言结果", {
                "通过": f"{len(passed)} 条",
                "失败": f"{len(failures)} 条",
                "详情": failures,
            })
            raise AssertionError(
                f"断言失败 {len(failures)}/{len(passed) + len(failures)} 条:\n"
                + "\n".join(failures)
            )

        if passed:
            LOGGER.info("断言全部通过 %s 条", len(passed))
            add_allure_step("断言结果", {"通过": f"{len(passed)} 条", "状态": "全部通过"})

    @staticmethod
    def _resolve_assertion_actual(target: str, body: Any, status_code: int) -> Any:
        """把 target 字段解析成"实际值"。约定：
            - 'status_code' → HTTP status
            - 'body_text' → 响应体字符串
            - 以 '$' 开头 → 把 target 当 jsonpath
            - 其他 → 直接当 body 的顶层键取（body.get(target)）
        """
        if target == "status_code":
            return status_code
        if target == "body_text":
            return body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        if isinstance(target, str) and target.startswith("$"):
            return extractor(body, target)
        if isinstance(body, dict):
            return body.get(target)
        return None
