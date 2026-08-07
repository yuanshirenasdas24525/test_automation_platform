"""接口用例契约解析、确定性编译和入库前校验。

AI 只负责提出测试场景；本模块负责把 OpenAPI 里的 method/path/security、参数位置、
请求体约束和响应模型编译成平台唯一执行结构。这样生成、试跑和正式执行看到的是同一份
步骤定义，避免模型自由猜状态码、JSONPath 或参数位置。
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_NEGATIVE_HINTS = (
    "缺失", "缺少", "为空", "空值", "null", "类型", "格式", "非法", "无效",
    "超长", "超界", "越界", "边界外", "错误", "重复", "不存在",
)
_UNAUTH_HINTS = (
    "未认证", "无认证", "无token", "无 token", "缺少token", "缺少 token", "未登录",
    "未带token", "未带 token", "不带token", "不带 token", "未携带token", "未携带 token",
    "无 authorization", "无authorization",
)
_CREDENTIAL_HINTS = (
    "密码错误", "错误密码", "账号不存在", "用户不存在", "用户名不存在", "不存在的用户名",
    "凭据错误", "无效 token", "无效token", "过期 token", "过期token",
)
_AUTH_SUCCESS_HINTS = (
    "鉴权成功", "认证成功", "有效 token", "有效token", "合法 token", "合法token",
    "登录成功", "刷新成功",
)
_FORBIDDEN_HINTS = ("无权限", "越权", "权限不足", "禁止访问")
_NOT_FOUND_HINTS = ("不存在", "未找到", "not found")
_VOLATILE_KEYS = {"token", "access_token", "refresh_token", "session_id", "created_at", "updated_at"}
_TYPE_KEYS = (
    "type", "format", "enum", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
    "minProperties", "maxProperties", "additionalProperties", "nullable",
)


def _resolve_ref(document: dict[str, Any], value: Any, seen: set[str] | None = None) -> Any:
    """递归解析 OpenAPI 本地 ``#/...`` 引用，并合并引用旁的覆盖字段。"""
    if isinstance(value, list):
        return [_resolve_ref(document, item, seen) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        seen = set(seen or set())
        if ref in seen:
            return {k: _resolve_ref(document, v, seen) for k, v in value.items() if k != "$ref"}
        target: Any = document
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            target = {}
        merged = deepcopy(target) if isinstance(target, dict) else {}
        merged.update({k: deepcopy(v) for k, v in value.items() if k != "$ref"})
        return _resolve_ref(document, merged, seen | {ref})
    return {k: _resolve_ref(document, v, seen) for k, v in value.items()}


def _merge_composed_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """把 allOf 合并为单一对象；oneOf/anyOf 保留候选摘要供校验与 Prompt 使用。"""
    schema = deepcopy(schema)
    all_of = schema.pop("allOf", None)
    if isinstance(all_of, list):
        required = set(schema.get("required") or [])
        properties = dict(schema.get("properties") or {})
        for child in all_of:
            child = _merge_composed_schema(child) if isinstance(child, dict) else {}
            required.update(child.get("required") or [])
            properties.update(child.get("properties") or {})
            for key, value in child.items():
                if key not in {"required", "properties"} and key not in schema:
                    schema[key] = value
        if properties:
            schema["properties"] = properties
        if required:
            schema["required"] = sorted(required)
    return schema


def _compact_schema(raw: Any, depth: int = 0) -> dict[str, Any]:
    """保留生成和静态校验需要的 schema 字段，限制深度避免契约快照无限膨胀。"""
    if not isinstance(raw, dict) or depth > 8:
        return {}
    raw = _merge_composed_schema(raw)
    out = {key: deepcopy(raw[key]) for key in _TYPE_KEYS if key in raw}
    for key in ("title", "description", "default", "example", "examples"):
        if key in raw:
            out[key] = deepcopy(raw[key])
    required = raw.get("required")
    if isinstance(required, list):
        out["required"] = [str(item) for item in required]
    properties = raw.get("properties")
    if isinstance(properties, dict):
        out["type"] = out.get("type") or "object"
        out["properties"] = {
            str(name): _compact_schema(value, depth + 1)
            for name, value in properties.items()
            if isinstance(value, dict)
        }
    items = raw.get("items")
    if isinstance(items, dict):
        out["items"] = _compact_schema(items, depth + 1)
    for keyword in ("oneOf", "anyOf"):
        values = raw.get(keyword)
        if isinstance(values, list):
            out[keyword] = [_compact_schema(item, depth + 1) for item in values if isinstance(item, dict)]
    return out


def _media_schema(document: dict[str, Any], content: Any) -> tuple[str | None, dict[str, Any], Any]:
    if not isinstance(content, dict) or not content:
        return None, {}, None
    preferred = next(
        (key for key in ("application/json", "application/x-www-form-urlencoded", "multipart/form-data") if key in content),
        next(iter(content)),
    )
    media = content.get(preferred) if isinstance(content.get(preferred), dict) else {}
    schema = _compact_schema(_resolve_ref(document, media.get("schema") or {}))
    example = media.get("example")
    if example is None and isinstance(media.get("examples"), dict):
        first = next(iter(media["examples"].values()), None)
        if isinstance(first, dict):
            example = first.get("value")
    return preferred, schema, example


def build_contract_catalog(
    document: dict[str, Any],
    operation_ids: set[str] | None = None,
) -> dict[str, Any]:
    """把 OpenAPI/Swagger 文档变成可传给前端和生成接口的紧凑契约目录。"""
    if not isinstance(document, dict):
        return empty_contract_catalog()
    wanted = {str(item) for item in (operation_ids or set()) if str(item).strip()}
    security_schemes = _resolve_ref(document, (document.get("components") or {}).get("securitySchemes") or {})
    if not security_schemes and isinstance(document.get("securityDefinitions"), dict):
        security_schemes = _resolve_ref(document, document["securityDefinitions"])
    operations: list[dict[str, Any]] = []
    global_security = document.get("security")
    for path, path_item_raw in (document.get("paths") or {}).items():
        if not isinstance(path_item_raw, dict):
            continue
        path_item = _resolve_ref(document, path_item_raw)
        path_parameters = path_item.get("parameters") or []
        for method, operation_raw in path_item.items():
            if str(method).lower() not in _HTTP_METHODS or not isinstance(operation_raw, dict):
                continue
            operation = _resolve_ref(document, operation_raw)
            operation_id = str(operation.get("operationId") or "").strip()
            if wanted and operation_id not in wanted:
                continue
            parameters: list[dict[str, Any]] = []
            seen_parameters: set[tuple[str, str]] = set()
            for raw_parameter in [*path_parameters, *(operation.get("parameters") or [])]:
                parameter = _resolve_ref(document, raw_parameter)
                if not isinstance(parameter, dict) or not parameter.get("name"):
                    continue
                location = str(parameter.get("in") or "query")
                key = (location, str(parameter["name"]))
                if key in seen_parameters:
                    continue
                seen_parameters.add(key)
                schema = parameter.get("schema") or {
                    name: parameter[name] for name in _TYPE_KEYS if name in parameter
                }
                parameters.append({
                    "name": str(parameter["name"]),
                    "in": location,
                    "required": bool(parameter.get("required") or location == "path"),
                    "description": str(parameter.get("description") or "")[:500],
                    "schema": _compact_schema(_resolve_ref(document, schema)),
                    "example": deepcopy(parameter.get("example")),
                })

            request_body = _resolve_ref(document, operation.get("requestBody") or {})
            content_type, request_schema, request_example = _media_schema(document, request_body.get("content"))
            if not request_schema and isinstance(operation.get("consumes"), list):
                body_parameter = next((p for p in parameters if p["in"] == "body"), None)
                if body_parameter:
                    content_type = operation["consumes"][0]
                    request_schema = body_parameter["schema"]

            responses: dict[str, Any] = {}
            for status, raw_response in (operation.get("responses") or {}).items():
                response = _resolve_ref(document, raw_response)
                response_type, response_schema, response_example = _media_schema(document, response.get("content"))
                if not response_schema and isinstance(response.get("schema"), dict):
                    response_schema = _compact_schema(_resolve_ref(document, response["schema"]))
                responses[str(status)] = {
                    "description": str(response.get("description") or "")[:500],
                    "content_type": response_type,
                    "schema": response_schema,
                    "example": response_example,
                }

            security = operation["security"] if "security" in operation else global_security
            security_requirements: list[dict[str, Any]] = []
            for requirement in security or []:
                if not isinstance(requirement, dict):
                    continue
                for scheme_name in requirement:
                    scheme = security_schemes.get(scheme_name) if isinstance(security_schemes, dict) else {}
                    scheme = scheme if isinstance(scheme, dict) else {}
                    security_requirements.append({
                        "scheme": str(scheme_name),
                        "type": str(scheme.get("type") or ""),
                        "in": str(scheme.get("in") or "header"),
                        "name": str(scheme.get("name") or ("Authorization" if scheme.get("type") in {"http", "oauth2"} else scheme_name)),
                        "scheme_type": str(scheme.get("scheme") or ""),
                    })

            operations.append({
                "operation_id": operation_id,
                "method": str(method).upper(),
                "path": str(path),
                "summary": str(operation.get("summary") or operation.get("description") or "")[:1000],
                "tags": [str(item) for item in (operation.get("tags") or [])],
                "security": security_requirements,
                "parameters": parameters,
                "request": {
                    "required": bool(request_body.get("required")),
                    "content_type": content_type,
                    "schema": request_schema,
                    "example": request_example,
                },
                "responses": responses,
            })
    catalog = {"version": 1, "operations": operations}
    catalog["hash"] = contract_hash(catalog)
    return catalog


def empty_contract_catalog() -> dict[str, Any]:
    return {"version": 1, "hash": contract_hash({"version": 1, "operations": []}), "operations": []}


def merge_contract_catalogs(catalogs: list[dict[str, Any]]) -> dict[str, Any]:
    """合并多个接口文档，同 method/path 后出现的定义覆盖先出现定义。"""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for catalog in catalogs:
        for operation in (catalog or {}).get("operations") or []:
            if isinstance(operation, dict) and operation.get("path"):
                merged[(str(operation.get("method") or "GET").upper(), str(operation["path"]))] = deepcopy(operation)
    result = {"version": 1, "operations": list(merged.values())}
    result["hash"] = contract_hash(result)
    return result


def contract_hash(catalog: dict[str, Any]) -> str:
    payload = {"version": catalog.get("version", 1), "operations": catalog.get("operations") or []}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def contract_prompt(
    catalog: dict[str, Any],
    query: str = "",
    max_chars: int = 50000,
) -> str:
    """给模型的精确契约；按 operationId/path/摘要精确选取，机器校验仍使用全量目录。"""
    if not (catalog or {}).get("operations"):
        return "（没有解析到结构化 OpenAPI 契约；不得猜测未声明字段和状态码）"
    operations = list(catalog.get("operations") or [])
    if query and len(operations) > 30:
        query_lower = query.lower()
        query_tokens = set(re.findall(r"[a-zA-Z_][\w-]*|[\u4e00-\u9fff]{2,}", query_lower))

        def score(operation: dict[str, Any]) -> tuple[int, int]:
            operation_id = str(operation.get("operation_id") or "").lower()
            path = str(operation.get("path") or "").lower()
            summary = str(operation.get("summary") or "").lower()
            exact = int(bool(operation_id and operation_id in query_lower)) * 20
            exact += int(bool(path and path in query_lower)) * 20
            haystack_tokens = set(re.findall(r"[a-zA-Z_][\w-]*|[\u4e00-\u9fff]{2,}", f"{operation_id} {path} {summary}"))
            overlap = len(query_tokens & haystack_tokens)
            return exact + overlap, -len(path)

        ranked = sorted(operations, key=score, reverse=True)
        matched = [operation for operation in ranked if score(operation)[0] > 0]
        operations = (matched or ranked)[:30]
    selected = {"version": catalog.get("version", 1), "hash": catalog.get("hash"), "operations": operations}
    return json.dumps(selected, ensure_ascii=False, separators=(",", ":"), default=str)[:max_chars]


def _normalized_path(path: str) -> str:
    path = urlsplit(str(path or "")).path or str(path or "")
    return re.sub(r"\{[^}/]+\}|\$\{[^}/]+\}|[^/]+(?=/|$)", lambda m: "{}" if m.group(0).startswith(("{", "${")) else m.group(0), path.rstrip("/") or "/")


def find_operation(catalog: dict[str, Any], method: str, path: str, operation_id: str = "") -> dict[str, Any] | None:
    operations = (catalog or {}).get("operations") or []
    if operation_id:
        hit = next((op for op in operations if op.get("operation_id") == operation_id), None)
        if hit:
            return hit
    method = str(method or "GET").upper()
    direct = next((op for op in operations if op.get("method") == method and op.get("path") == path), None)
    if direct:
        return direct
    normalized = _normalized_path(path)
    return next(
        (op for op in operations if op.get("method") == method and _normalized_path(str(op.get("path") or "")) == normalized),
        None,
    )


def _contains_hint(text: str, hints: tuple[str, ...]) -> bool:
    lower = text.lower().replace("_", " ")
    return any(hint.lower() in lower for hint in hints)


def _flatten_intent_text(value: Any) -> str:
    """把模型可能给出的字符串/列表/字典意图压成可做规则判断的文本。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(
            f"{key} {_flatten_intent_text(child)}"
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_intent_text(item) for item in value)
    return str(value)


def _request_intent_text(case: dict[str, Any], request: dict[str, Any]) -> str:
    """返回当前请求自己的测试意图，避免多步骤用例的负向标题污染所有前置步骤。"""
    step_text = " ".join(
        _flatten_intent_text(request.get(key))
        for key in ("name", "description", "category", "scenario_type", "expected")
    )
    case_text = " ".join(
        _flatten_intent_text(case.get(key))
        for key in ("name", "description", "category", "scenario_type")
    )
    expected_text = _flatten_intent_text(case.get("expected"))
    count = int(request.get("_case_step_count") or len(case.get("requests") or []) or 1)
    index = int(request.get("_case_step_index") or 0)
    if count <= 1:
        return " ".join((step_text, case_text, expected_text))
    if index == count - 1:
        # 多步骤大纲通常用用例标题和“最后一步……”描述最终负向验证；只把这些
        # 上下文交给最后一步，登录/建号/改密等前置步骤仍按各自名称判断为成功。
        last_expected = expected_text
        marker = expected_text.rfind("最后")
        if marker >= 0:
            last_expected = expected_text[marker:]
        return " ".join((step_text, case_text, last_expected))
    return step_text


def _auth_failure_intent(case: dict[str, Any], request: dict[str, Any]) -> bool:
    """识别无需依赖 OpenAPI 明示 401 的鉴权生命周期失败意图。"""
    text = _request_intent_text(case, request)
    path = str(request.get("path") or "").lower()
    if _contains_hint(text, _UNAUTH_HINTS + _CREDENTIAL_HINTS):
        return True
    if _contains_hint(text, _AUTH_SUCCESS_HINTS):
        return False
    if "refresh" in path and _contains_hint(
        text,
        (
            "无效refresh token", "无效 refresh token", "无效刷新令牌",
            "已登出", "登出后", "退出后", "已退出", "会话已失效",
            "refresh token失效", "refresh token 失效", "刷新令牌失效",
            "撤销", "注销后",
        ),
    ):
        return True
    if any(hint in path for hint in ("/login", "/signin", "/sign-in", "/auth/token")):
        return "旧密码" in text and _contains_hint(
            text,
            ("失败", "拒绝", "密码已改", "修改密码后", "改密后", "返回401", "返回 401"),
        )
    return False


def _documented_status(operation: dict[str, Any], preferred: tuple[int, ...], fallback: int) -> int:
    statuses = {int(key) for key in (operation.get("responses") or {}) if str(key).isdigit()}
    for status in preferred:
        if status in statuses:
            return status
    if statuses:
        if fallback < 400:
            success = sorted(status for status in statuses if 200 <= status < 300)
            if success:
                return success[0]
        # 没声明目标业务错误时使用语义 fallback，不能拿任意其它错误码顶替。
        # 典型例子：登录 OpenAPI 只自动声明 422，但错误密码真实返回 401；把 422
        # 当成“文档中最接近的错误码”会让在线探测把正确的 401 误判为失败。
    return fallback


def expected_status(case: dict[str, Any], request: dict[str, Any], operation: dict[str, Any] | None) -> int:
    """按测试意图和接口声明确定状态码，不让模型凭经验自由猜。"""
    assertions = request.get("assertion") if isinstance(request.get("assertion"), dict) else {}
    raw = assertions.get("status_code")
    text = _request_intent_text(case, request)
    if operation is None:
        return int(raw) if isinstance(raw, (int, float)) else 200
    path = str(request.get("path") or "").lower()
    # 修改密码接口把“旧密码不正确”定义为业务请求错误 400，不是未认证 401。
    # 必须放在通用凭据词义之前，否则“旧密码错误”会被误吃成登录凭据失败。
    if path.endswith("/auth/password") and _contains_hint(
        text,
        ("旧密码错误", "old password错误", "old password 错误", "错误old password", "错误 old password"),
    ):
        return _documented_status(operation, (400,), 400)
    if _auth_failure_intent(case, request):
        return _documented_status(operation, (401, 403), 401)
    if _contains_hint(text, _UNAUTH_HINTS):
        return _documented_status(operation, (401, 403), 401)
    if _contains_hint(text, _CREDENTIAL_HINTS):
        return _documented_status(operation, (401, 403), 401)
    if ("/login" in path or path.endswith("/auth/token")) and _contains_hint(
        text,
        ("安全", "sql 注入", "sql注入", "xss", "注入", "攻击"),
    ):
        return _documented_status(operation, (401, 403), 401)
    if _contains_hint(text, _FORBIDDEN_HINTS):
        return _documented_status(operation, (403, 401), 403)
    if _contains_hint(text, _NOT_FOUND_HINTS):
        return _documented_status(operation, (404, 422, 400), 404)
    if _request_violates_contract(request, operation):
        return _documented_status(operation, (422, 400, 409, 401, 403), 422)
    if _contains_hint(text, ("幂等", "重复调用", "重复提交")) and any(
        path.endswith(suffix)
        for suffix in ("/auth/login", "/auth/refresh", "/auth/logout", "/auth/logout-all")
    ):
        return _documented_status(operation, (200, 204), 200)
    if "重复" in text or "冲突" in text:
        return _documented_status(operation, (409, 422, 400), 409)
    # 模型给出的状态码即使“恰好在 responses 列表里”也不能直接采用：FastAPI 常为
    # 每个接口统一声明 422，AI 很容易把合法的 optional/null 场景误判成 422。
    # 未命中上面的认证/权限/契约违规/冲突意图时，一律选择文档中的成功响应。
    return _documented_status(operation, (200, 201, 202, 204), 200)


def _value_violates_schema(value: Any, schema: dict[str, Any]) -> bool:
    if isinstance(value, str) and value.startswith(("${", "function:")):
        return False
    any_of = [item for item in (schema.get("anyOf") or []) if isinstance(item, dict)]
    if any_of and not any(not _value_violates_schema(value, item) for item in any_of):
        return True
    one_of = [item for item in (schema.get("oneOf") or []) if isinstance(item, dict)]
    if one_of and sum(not _value_violates_schema(value, item) for item in one_of) != 1:
        return True
    if value is None:
        candidates = [*any_of, *one_of]
        type_value = schema.get("type")
        allows_null = bool(schema.get("nullable")) or type_value == "null" or (
            isinstance(type_value, list) and "null" in type_value
        ) or any(isinstance(item, dict) and item.get("type") == "null" for item in candidates)
        return not allows_null
    expected_type = schema.get("type")
    if expected_type == "null" and value is not None:
        return True
    if expected_type == "string" and not isinstance(value, str):
        return True
    if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return True
    if expected_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return True
    if expected_type == "boolean" and not isinstance(value, bool):
        return True
    if expected_type == "array":
        if not isinstance(value, list):
            return True
        if schema.get("minItems") is not None and len(value) < int(schema["minItems"]):
            return True
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            return True
        return any(_value_violates_schema(item, schema.get("items") or {}) for item in value)
    if expected_type == "object":
        if not isinstance(value, dict):
            return True
        properties = schema.get("properties") or {}
        if any(field not in value for field in (schema.get("required") or [])):
            return True
        if schema.get("additionalProperties") is False and any(field not in properties for field in value):
            return True
        if any(
            field in properties and _value_violates_schema(child, properties[field])
            for field, child in value.items()
        ):
            return True
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < int(schema["minLength"]):
            return True
        if schema.get("maxLength") is not None and len(value) > int(schema["maxLength"]):
            return True
        if schema.get("pattern") and re.search(str(schema["pattern"]), value) is None:
            return True
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None and value < schema["minimum"]:
            return True
        if schema.get("maximum") is not None and value > schema["maximum"]:
            return True
        if schema.get("exclusiveMinimum") is not None and value <= schema["exclusiveMinimum"]:
            return True
        if schema.get("exclusiveMaximum") is not None and value >= schema["exclusiveMaximum"]:
            return True
    return False


def _request_violates_contract(request: dict[str, Any], operation: dict[str, Any]) -> bool:
    """判断测试输入是否真的违反 OpenAPI，而不是根据“null/边界”等标题猜。"""
    headers = request.get("headers") if isinstance(request.get("headers"), dict) else {}
    supplied_content_type = next(
        (str(value).split(";", 1)[0].strip().lower() for key, value in headers.items()
         if str(key).lower() == "content-type" and value),
        None,
    )
    documented_content_type = str(
        operation.get("request", {}).get("content_type") or ""
    ).split(";", 1)[0].strip().lower()
    if supplied_content_type and documented_content_type and supplied_content_type != documented_content_type:
        return True

    body = request.get("json")
    if body is None:
        body = request.get("form")
    if body is None:
        body = request.get("body")
    schema = operation.get("request", {}).get("schema") or {}
    if operation.get("request", {}).get("required") and body is None:
        return True
    if body is not None and schema and _value_violates_schema(body, schema):
        return True
    if isinstance(body, dict):
        for field in schema.get("required") or []:
            if field not in body:
                return True
        properties = schema.get("properties") or {}
        for field, value in body.items():
            if field in properties and _value_violates_schema(value, properties[field]):
                return True
        if schema.get("additionalProperties") is False and any(field not in properties for field in body):
            return True
    locations = {
        "path": request.get("path_params") or {},
        "query": request.get("query_params") or {},
        "header": request.get("headers") or {},
    }
    for parameter in operation.get("parameters") or []:
        values = locations.get(parameter.get("in"), {})
        name = parameter.get("name")
        supplied_by_variable = (
            parameter.get("in") == "path"
            and f"${{{name}}}" in str(request.get("path") or "")
        )
        if parameter.get("required") and name not in values and not supplied_by_variable:
            return True
        if name in values and _value_violates_schema(values[name], parameter.get("schema") or {}):
            return True
    return False


def _schema_value(schema: dict[str, Any], field_name: str) -> Any:
    """只使用契约明确给出的安全值；没有 example/default/enum 时禁止凭字段名猜。"""
    if "example" in schema:
        return deepcopy(schema["example"])
    if "default" in schema:
        return deepcopy(schema["default"])
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return deepcopy(enum[0])
    for candidate in [*(schema.get("anyOf") or []), *(schema.get("oneOf") or [])]:
        if not isinstance(candidate, dict) or candidate.get("type") == "null":
            continue
        value = _schema_value(candidate, field_name)
        if value is not None:
            return value
    if schema.get("type") == "array":
        child = _schema_value(schema.get("items") or {}, field_name)
        return [child] if child is not None else []
    return None


def _coerce_positive_value(field: str, value: Any, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """正向场景按契约修正明显非法的枚举/类型；负向场景不会调用本函数。"""
    if isinstance(value, str) and value.startswith(("${", "function:")):
        return value, None
    candidates = [
        item
        for item in [*(schema.get("anyOf") or []), *(schema.get("oneOf") or [])]
        if isinstance(item, dict)
    ]
    if candidates:
        if not _value_violates_schema(value, schema):
            return value, None
        replacement = _schema_value(schema, field)
        if replacement is not None:
            return replacement, f"字段 {field} 不符合 anyOf/oneOf，已按契约生成有效值"
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        return deepcopy(enum[0]), f"字段 {field} 的非法枚举 {value!r} 已按契约改为 {enum[0]!r}"
    if schema.get("type") == "array" and isinstance(value, list):
        next_values = []
        messages = []
        for index, item in enumerate(value):
            next_item, message = _coerce_positive_value(f"{field}[{index}]", item, schema.get("items") or {})
            next_values.append(next_item)
            if message:
                messages.append(message)
        return next_values, "；".join(messages) if messages else None
    expected_type = schema.get("type")
    type_ok = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected_type, True)
    if not type_ok:
        replacement = _schema_value(schema, field)
        if replacement is not None:
            return replacement, f"字段 {field} 类型不符，已按契约生成有效值"
    return value, None


def _is_negative_case(case: dict[str, Any], request: dict[str, Any] | None = None) -> bool:
    request = request or case
    text = _request_intent_text(case, request)
    return _auth_failure_intent(case, request) or _contains_hint(
        text,
        _NEGATIVE_HINTS + _UNAUTH_HINTS + _FORBIDDEN_HINTS,
    )


def _split_request_payload(case: dict[str, Any], request: dict[str, Any], operation: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    headers = deepcopy(request.get("headers") or {}) if isinstance(request.get("headers"), dict) else {}
    path_params = deepcopy(request.get("path_params") or {}) if isinstance(request.get("path_params"), dict) else {}
    query_params = deepcopy(request.get("query_params") or {}) if isinstance(request.get("query_params"), dict) else {}
    json_body = deepcopy(request.get("json")) if "json" in request else None
    form = deepcopy(request.get("form")) if "form" in request else None
    files = deepcopy(request.get("files") or {}) if isinstance(request.get("files"), dict) else None
    legacy_body = deepcopy(request.get("body"))
    # 显式 Content-Type 是用例输入的一部分（尤其是安全/畸形场景），不能被 OpenAPI
    # 声明静默覆盖；若与契约不符，expected_status 会据此编译成 422/400。
    content_type = str(
        headers.get("Content-Type")
        or headers.get("content-type")
        or (operation or {}).get("request", {}).get("content_type")
        or "application/json"
    ).split(";", 1)[0].strip().lower()

    parameters = (operation or {}).get("parameters") or []
    query_names = {p["name"] for p in parameters if p.get("in") == "query"}
    path_names = {p["name"] for p in parameters if p.get("in") == "path"}
    header_names = {p["name"] for p in parameters if p.get("in") == "header"}
    if isinstance(legacy_body, dict):
        for key in list(legacy_body):
            if key in path_names and key not in path_params:
                path_params[key] = legacy_body.pop(key)
            elif key in query_names and key not in query_params:
                query_params[key] = legacy_body.pop(key)
            elif key in header_names and key not in headers:
                headers[key] = legacy_body.pop(key)
        if legacy_body:
            if content_type == "application/x-www-form-urlencoded":
                form = form or legacy_body
            elif content_type == "multipart/form-data":
                form = form or legacy_body
            elif json_body is None:
                json_body = legacy_body
    elif "body" in request:
        if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
            if form is None:
                form = legacy_body
        elif "json" not in request:
            json_body = legacy_body

    if operation:
        request_schema = operation.get("request", {}).get("schema") or {}
        required_fields = request_schema.get("required") or []
        properties = request_schema.get("properties") or {}
        target_body = form if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"} else json_body
        if not _is_negative_case(case, request):
            if target_body is None and required_fields:
                target_body = {}
            if isinstance(target_body, dict):
                for field in required_fields:
                    if field in target_body:
                        continue
                    value = _schema_value(properties.get(field) or {}, str(field))
                    if value is None:
                        warnings.append(f"必填字段 {field} 缺失，契约没有 example/default，无法安全补值")
                    else:
                        target_body[field] = value
                for field, value in list(target_body.items()):
                    if field not in properties:
                        continue
                    coerced, message = _coerce_positive_value(str(field), value, properties[field])
                    target_body[field] = coerced
                    if message:
                        warnings.append(message)
            if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
                form = target_body
            else:
                json_body = target_body

    return {
        "headers": headers,
        "path_params": path_params,
        "query_params": query_params,
        "json": json_body,
        "form": form,
        "files": files,
        "content_type": content_type,
    }, warnings


def _jsonpath_segments(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("$"):
        return []
    normalized = re.sub(r"\[(?:\d+|\*)\]", "", path[1:])
    return [part for part in normalized.lstrip(".").split(".") if part]


def jsonpath_in_schema(path: str, schema: dict[str, Any]) -> bool:
    """判断简单 JSONPath 是否存在于响应 schema；复杂过滤表达式保守放行。"""
    if not schema or "[?" in str(path):
        return True
    current = schema
    for segment in _jsonpath_segments(path):
        if current.get("type") == "array":
            current = current.get("items") or {}
        properties = current.get("properties") or {}
        if segment not in properties:
            return False
        current = properties[segment]
    return True


def _assertion_rules(
    assertions: Any,
    status: int,
    response_schema: dict[str, Any],
    verified_paths: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    rules = [{"type": "equal", "target": "status_code", "expected": status}]
    warnings: list[str] = []
    if not isinstance(assertions, dict):
        return rules, warnings
    for target, value in assertions.items():
        target = str(target)
        if target == "status_code":
            continue
        if target.startswith("$") and not response_schema and target not in verified_paths:
            warnings.append(f"HTTP {status} 没有响应 schema，已移除无法验证的 JSONPath 断言 {target}")
            continue
        if target.startswith("$") and target not in verified_paths and not jsonpath_in_schema(target, response_schema):
            warnings.append(f"断言 JSONPath {target} 不存在于 HTTP {status} 响应模型，已移除")
            continue
        if status >= 400 and target.startswith("$"):
            # 错误文案、校验细节和本地化内容通常不稳定；契约能证明字段存在即可，
            # 不把 AI 猜测的 None/中文文案编译成脆弱的精确比较。
            value = "not_empty"
        if target.startswith("$"):
            lower = target.rsplit(".", 1)[-1].lower()
            if lower in _VOLATILE_KEYS and (
                not isinstance(value, str)
                or value.strip().lower() not in {"not_empty", "not_null", "非空"}
            ) and value is not None:
                value = "not_empty"
        normalized = value.strip().lower() if isinstance(value, str) else ""
        if normalized.startswith("type:") and normalized.removeprefix("type:") in {
            "string", "number", "integer", "boolean", "array", "object", "null",
        }:
            rules.append({
                "type": "type",
                "target": target,
                "expected": normalized.removeprefix("type:"),
            })
        elif normalized in {"not_empty", "not_null", "notnull", "notempty", "非空"}:
            rules.append({"type": "is_not_null", "target": target, "expected": None})
        elif normalized in {"is_null", "为空", "空"}:
            rules.append({"type": "is_null", "target": target, "expected": None})
        else:
            rules.append({"type": "jsonpath" if target.startswith("$") else "equal", "target": target, "expected": value})
    return rules, warnings


def _extract_rules(
    extract: Any,
    response_schema: dict[str, Any],
    verified_paths: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    rules: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not isinstance(extract, dict):
        return rules, warnings
    for name, path in extract.items():
        path = str(path)
        if not response_schema and path not in verified_paths:
            warnings.append(f"响应没有 schema，变量 {name} 的 JSONPath {path} 禁止凭空提取")
            continue
        if path not in verified_paths and not jsonpath_in_schema(path, response_schema):
            warnings.append(f"提取 JSONPath {path} 不存在于响应模型，变量 {name} 无法产出")
            continue
        rules.append({"name": str(name), "from": "response.body", "jsonpath": path, "required": True})
    return rules, warnings


def _request_to_step(case: dict[str, Any], request: dict[str, Any], order: int, catalog: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, Any] | None]:
    method = str(request.get("method") or "GET").upper()
    path = str(request.get("path") or "")
    operation_id = str(request.get("operation_id") or case.get("operation_id") or "")
    operation = find_operation(catalog, method, path, operation_id)
    warnings: list[str] = []
    if operation and operation_id and (method != operation.get("method") or path != operation.get("path")):
        warnings.append(
            f"operationId={operation_id} 对应 {operation.get('method')} {operation.get('path')}，"
            f"已修正 AI 给出的 {method} {path}"
        )
        method = str(operation.get("method") or method).upper()
        path = str(operation.get("path") or path)
    if (catalog or {}).get("operations") and operation is None:
        warnings.append(f"接口契约中不存在 {method} {path}")
    parts, part_warnings = _split_request_payload(case, request, operation)
    warnings.extend(part_warnings)
    normalized_request = {
        **request,
        "path_params": parts["path_params"],
        "query_params": parts["query_params"],
        "headers": parts["headers"],
        "json": parts["json"],
        "form": parts["form"],
    }
    status = expected_status(case, normalized_request, operation)
    response = ((operation or {}).get("responses") or {}).get(str(status)) or {}
    response_schema = response.get("schema") or {}
    verified_paths = {
        str(path) for path in (case.get("_probe_verified_paths") or []) if str(path).startswith("$")
    }
    if status < 400:
        verified_paths.update(
            str(path)
            for path in ((operation or {}).get("trusted_response_paths") or [])
            if str(path).startswith("$")
        )
    assertions, assertion_warnings = _assertion_rules(
        request.get("assertion"),
        status,
        response_schema,
        verified_paths,
    )
    extracts, extract_warnings = _extract_rules(request.get("extract"), response_schema, verified_paths)
    warnings.extend(assertion_warnings)
    warnings.extend(extract_warnings)

    config = {
        "method": method,
        "path": path,
        "headers": parts["headers"],
        "path_params": parts["path_params"],
        "query_params": parts["query_params"],
        "json": parts["json"],
        "form": parts["form"],
        "files": parts["files"],
        "data_type": parts["content_type"],
    }
    if request.get("sql"):
        warnings.append("AI 生成的自由文本 SQL 未绑定数据库 schema，已禁止自动执行")
    step = {
        "step_order": order,
        "step_name": str(request.get("name") or case.get("name") or f"HTTP step {order + 1}"),
        "step_type": "http_request",
        "skip": False,
        "config": config,
        "extract": extracts or None,
        "assertion": assertions,
        "wait_before": 0,
        "timeout": 60,
        "retry": 0,
        "on_failure": "stop",
    }
    return step, warnings, operation


def _auth_present(case: dict[str, Any], step: dict[str, Any], operation: dict[str, Any]) -> bool:
    if not operation.get("security"):
        return True
    headers = step.get("config", {}).get("headers") or {}
    query = step.get("config", {}).get("query_params") or {}
    metadata = case.get("generation_metadata") if isinstance(case.get("generation_metadata"), dict) else {}
    available = {
        str(name).strip()
        for name in (metadata.get("available_variables") or [])
        if str(name).strip()
    }
    for requirement in operation["security"]:
        location = requirement.get("in") or "header"
        name = requirement.get("name") or "Authorization"
        value: Any = None
        if location == "query":
            value = query.get(name)
        elif location == "header":
            value = next(
                (item for key, item in headers.items() if str(key).lower() == str(name).lower()),
                None,
            )
        if value in (None, ""):
            continue
        if str(requirement.get("type") or "").lower() == "apikey":
            # OpenAPI 声明 X-API-Key 只表示服务端支持这种鉴权方式，不表示生成器手里
            # 自动拥有一把可用 key。AI 写死的占位字符串不能冒充真实凭据。
            refs = set(re.findall(r"\$\{([A-Za-z_][\w.-]*)\}", str(value)))
            if refs and all(ref.split(".")[0] in available for ref in refs):
                return True
            continue
        return True
    pre_hook_blob = json.dumps(case.get("pre_hook") or [], ensure_ascii=False, default=str)
    supports_bearer = any(
        str(requirement.get("type") or "").lower() in {"http", "oauth2", "openidconnect"}
        for requirement in operation["security"]
    )
    return supports_bearer and "extract_data" in pre_hook_blob and "token" in pre_hook_blob.lower()


def _validate_value(field: str, value: Any, schema: dict[str, Any], negative: bool) -> list[str]:
    if negative or isinstance(value, str) and value.startswith(("${", "function:")):
        return []
    if value is None and not _value_violates_schema(value, schema):
        return []
    errors: list[str] = []
    any_of = [item for item in (schema.get("anyOf") or []) if isinstance(item, dict)]
    if any_of and not any(not _value_violates_schema(value, item) for item in any_of):
        return [f"字段 {field} 不符合 anyOf 任一分支"]
    one_of = [item for item in (schema.get("oneOf") or []) if isinstance(item, dict)]
    if one_of and sum(not _value_violates_schema(value, item) for item in one_of) != 1:
        return [f"字段 {field} 不符合 oneOf 唯一分支"]
    expected_type = schema.get("type")
    valid_type = {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected_type, True)
    if not valid_type:
        errors.append(f"字段 {field} 类型应为 {expected_type}，实际为 {type(value).__name__}")
        return errors
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        errors.append(f"字段 {field}={value!r} 不在枚举 {enum!r} 中")
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < int(schema["minLength"]):
            errors.append(f"字段 {field} 长度小于 {schema['minLength']}")
        if schema.get("maxLength") is not None and len(value) > int(schema["maxLength"]):
            errors.append(f"字段 {field} 长度大于 {schema['maxLength']}")
        if schema.get("pattern") and re.search(str(schema["pattern"]), value) is None:
            errors.append(f"字段 {field} 不满足 pattern={schema['pattern']}")
    if isinstance(value, list):
        if schema.get("minItems") is not None and len(value) < int(schema["minItems"]):
            errors.append(f"字段 {field} 元素数小于 {schema['minItems']}")
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            errors.append(f"字段 {field} 元素数大于 {schema['maxItems']}")
        for index, item in enumerate(value):
            errors.extend(_validate_value(f"{field}[{index}]", item, schema.get("items") or {}, negative))
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for required_field in schema.get("required") or []:
            if required_field not in value:
                errors.append(f"字段 {field} 缺少嵌套必填字段 {required_field}")
        if schema.get("additionalProperties") is False:
            for child_field in value:
                if child_field not in properties:
                    errors.append(f"字段 {field}.{child_field} 未在契约中声明")
        for child_field, child_value in value.items():
            if child_field in properties:
                errors.extend(
                    _validate_value(f"{field}.{child_field}", child_value, properties[child_field], negative)
                )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None and value < schema["minimum"]:
            errors.append(f"字段 {field} 小于 minimum={schema['minimum']}")
        if schema.get("maximum") is not None and value > schema["maximum"]:
            errors.append(f"字段 {field} 大于 maximum={schema['maximum']}")
        if schema.get("exclusiveMinimum") is not None and value <= schema["exclusiveMinimum"]:
            errors.append(f"字段 {field} 不满足 exclusiveMinimum={schema['exclusiveMinimum']}")
        if schema.get("exclusiveMaximum") is not None and value >= schema["exclusiveMaximum"]:
            errors.append(f"字段 {field} 不满足 exclusiveMaximum={schema['exclusiveMaximum']}")
    return errors


def validate_compiled_case(compiled: dict[str, Any], catalog: dict[str, Any], original: dict[str, Any] | None = None) -> list[str]:
    """硬门禁：返回的每一条错误都代表用例不应默认入库或真实试跑。"""
    errors: list[str] = []
    original = original or {}
    auth_context = {
        **original,
        "generation_metadata": compiled.get("generation_metadata") or {},
    }
    if not (catalog or {}).get("operations"):
        errors.append("未解析到结构化 OpenAPI 契约，AI 猜测的接口用例禁止自动入库")
    steps = compiled.get("steps") or []
    if not steps:
        return ["没有可执行步骤"]
    original_requests = original.get("requests") if isinstance(original.get("requests"), list) else None
    if not original_requests:
        original_requests = [original] if original.get("path") else []
    produced: set[str] = set()
    for hook in compiled.get("pre_hook") or []:
        if not isinstance(hook, dict):
            continue
        config = hook.get("config") if isinstance(hook.get("config"), dict) else hook
        extract_data = config.get("extract_data") or config.get("extract") or {}
        if isinstance(extract_data, dict):
            produced.update(str(name) for name in extract_data if str(name).strip())
    for index, step in enumerate(steps, start=1):
        config = step.get("config") or {}
        method = str(config.get("method") or "GET").upper()
        path = str(config.get("path") or "")
        raw_request = deepcopy(original_requests[index - 1]) if index <= len(original_requests) else {}
        raw_request.setdefault("name", step.get("step_name") or "")
        raw_request.setdefault("method", method)
        raw_request.setdefault("path", path)
        raw_request["_case_step_index"] = index - 1
        raw_request["_case_step_count"] = len(steps)
        expected_step_status = next(
            (
                rule.get("expected")
                for rule in (step.get("assertion") or [])
                if isinstance(rule, dict) and rule.get("target") == "status_code"
            ),
            None,
        )
        step_negative = isinstance(expected_step_status, int) and expected_step_status >= 400
        if _auth_failure_intent(original, raw_request) and expected_step_status not in (401, 403):
            errors.append(
                f"第 {index} 步存在鉴权失败意图，但状态码断言被编译为 HTTP {expected_step_status}"
            )
        operation = find_operation(catalog, method, path)
        if (catalog or {}).get("operations") and operation is None:
            errors.append(f"第 {index} 步 {method} {path} 不在接口契约中")
            continue
        if operation and operation.get("security") and not _auth_present(auth_context, step, operation):
            if expected_step_status not in (401, 403):
                errors.append(f"第 {index} 步 {method} {path} 需要认证，但没有提供契约声明的认证信息")
        if operation:
            parameters = operation.get("parameters") or []
            location_values = {
                "path": config.get("path_params") or {},
                "query": config.get("query_params") or {},
                "header": config.get("headers") or {},
            }
            if not step_negative:
                for parameter in parameters:
                    if not parameter.get("required"):
                        continue
                    values = location_values.get(parameter.get("in"), {})
                    name = str(parameter.get("name"))
                    supplied_by_variable = parameter.get("in") == "path" and f"${{{name}}}" in path
                    if name not in values and not supplied_by_variable:
                        errors.append(f"第 {index} 步缺少 {parameter.get('in')} 必填参数 {parameter.get('name')}")
            for parameter in parameters:
                values = location_values.get(parameter.get("in"), {})
                if parameter.get("name") in values:
                    errors.extend(_validate_value(str(parameter["name"]), values[parameter["name"]], parameter.get("schema") or {}, step_negative))
            content_type = str(config.get("data_type") or "application/json")
            body = config.get("form") if content_type in {"application/x-www-form-urlencoded", "multipart/form-data"} else config.get("json")
            schema = operation.get("request", {}).get("schema") or {}
            if isinstance(body, dict):
                if schema.get("anyOf") or schema.get("oneOf"):
                    errors.extend(_validate_value(f"第 {index} 步请求体", body, schema, step_negative))
                else:
                    properties = schema.get("properties") or {}
                    if not step_negative:
                        for field in schema.get("required") or []:
                            if field not in body:
                                errors.append(f"第 {index} 步请求体缺少必填字段 {field}")
                    for field, value in body.items():
                        if field in properties:
                            errors.extend(_validate_value(str(field), value, properties[field], step_negative))
            elif operation.get("request", {}).get("required") and not step_negative:
                errors.append(f"第 {index} 步缺少必填请求体")
        for rule in step.get("extract") or []:
            if isinstance(rule, dict) and rule.get("name"):
                produced.add(str(rule["name"]))
        if not any(isinstance(rule, dict) and rule.get("target") == "status_code" for rule in (step.get("assertion") or [])):
            errors.append(f"第 {index} 步缺少状态码断言")

    for index, hook in enumerate(compiled.get("post_hook") or [], start=1):
        if not isinstance(hook, dict) or hook.get("type", "http_request") != "http_request":
            continue
        config = hook.get("config") if isinstance(hook.get("config"), dict) else hook
        method = str(config.get("method") or "DELETE").upper()
        path = str(config.get("path") or "")
        operation = find_operation(catalog, method, path)
        if (catalog or {}).get("operations") and operation is None:
            errors.append(f"第 {index} 个清理请求 {method} {path} 不在接口契约中")
        elif operation and operation.get("security") and not _auth_present(auth_context, {"config": config}, operation):
            errors.append(f"第 {index} 个清理请求 {method} {path} 需要认证，但没有认证信息")

    blob = json.dumps(compiled, ensure_ascii=False, default=str)
    refs = set(re.findall(r"\$\{([A-Za-z_][\w.-]*)\}", blob))
    metadata_vars = compiled.get("generation_metadata", {}).get("available_variables") or []
    allowed = produced | {str(name) for name in metadata_vars} | {
        "my_account", "my_password", "token", "admin_token", "base_url",
    }
    unresolved = sorted(ref.split(".")[0] for ref in refs if ref.split(".")[0] not in allowed)
    if unresolved:
        errors.append("存在未解析变量：" + "、".join(sorted(set(unresolved))))
    return list(dict.fromkeys(errors))


def compile_generated_case(
    case: dict[str, Any],
    module_id: int,
    catalog: dict[str, Any],
    generation_metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """把 AI 草稿编译成可直接 POST /api/test_cases 的唯一执行载荷。"""
    requests = case.get("requests") if isinstance(case.get("requests"), list) else None
    if not requests:
        requests = [case] if case.get("path") else []
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    for order, request in enumerate(requests):
        if not isinstance(request, dict):
            continue
        request_context = deepcopy(request)
        request_context["_case_step_index"] = order
        request_context["_case_step_count"] = len(requests)
        step, step_warnings, _operation = _request_to_step(case, request_context, order, catalog)
        steps.append(step)
        warnings.extend(step_warnings)

    metadata = {
        "generator": "interface_contract_compiler",
        "compiler_version": "2.1",
        "contract_hash": (catalog or {}).get("hash") or contract_hash(catalog or empty_contract_catalog()),
        **(generation_metadata or {}),
    }
    compiled = {
        "module_id": module_id,
        "name": str(case.get("name") or "AI 接口用例")[:200],
        "description": "\n".join(
            line for line in (
                f"前置条件：{'；'.join(str(item) for item in (case.get('preconditions') or []))}" if case.get("preconditions") else "",
                f"预期结果：{'；'.join(str(item) for item in (case.get('expected') or []))}" if case.get("expected") else "",
            ) if line
        ),
        "case_type": "api",
        "priority": 3,
        "source": "ai_interface",
        "generation_metadata": metadata,
        "pre_hook": deepcopy(case.get("pre_hook") or []),
        "post_hook": [],
        "steps": steps,
    }
    # API 清理可以安全编译；自由文本 SQL 没有 schema 映射时不执行。
    for teardown in case.get("teardown_api") or []:
        if not isinstance(teardown, dict) or not teardown.get("path"):
            continue
        compiled["post_hook"].append({
            "type": "http_request",
            "config": {
                "method": str(teardown.get("method") or "DELETE").upper(),
                "path": str(teardown["path"]),
                "headers": deepcopy(teardown.get("headers") or {}),
                "json": deepcopy(teardown.get("body")),
                "data_type": "application/json",
            },
        })
    errors = validate_compiled_case(compiled, catalog, case)
    metadata["preflight"] = {"passed": not errors, "errors": errors, "warnings": warnings}
    return compiled, list(dict.fromkeys([*errors, *warnings]))
