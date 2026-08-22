"""/api/functional_cases/* —— 功能（人工）用例 + 执行记录路由。

设计动机
========
功能用例 (`TestCase.case_type == 'functional'`) 不走 dispatcher / pytest，
执行结果是测试人员"勾"出来的：通过 / 失败 / 阻塞 / 不适用。一条用例可以
被多次勾选，每次产出一行 `FunctionalCaseRun`。

为什么要单独开一组路由（不复用 /api/test_cases）？
  - schema 完全不同：functional 用例的"步骤 / 期望"是自由文本结构（preconditions /
    steps / expected），落在 `TestCase.functional_spec` JSON 列里，没有 step_type / config 这套；
  - 执行模型完全不同：自动化用例 = 一次 dispatcher → TestReport，功能用例 = 多次手动勾 →
    多行 FunctionalCaseRun，根本不重叠；
  - 入口分流后，前端编辑器（FunctionalCaseEditor）和"测试模式"（批量勾）页面
    可以围绕这一组路由直接建 React Query 键，不污染 /api/test_cases 的缓存。

本模块覆盖：
  - CRUD     : POST / PUT / GET / DELETE /api/functional_cases[/{id}]
  - 列表     : GET /api/functional_cases (按 module_id 过滤，附最近一次 run 状态)
  - 勾结果   : POST /api/functional_cases/{id}/mark
  - 批量勾   : POST /api/functional_cases/batch_mark
  - 历史     : GET /api/functional_cases/{id}/runs
  - 批次概览 : GET /api/functional_cases/batches
  - 导入导出 : POST /api/functional_cases/import & GET /api/functional_cases/export

字段冷清：
  TestCase 上跟功能用例真正相关的只有 name / description / module_id / sort_order /
  case_type / tags / priority / skip / functional_spec。其它兼容字段（method/path/...）
  对功能用例无意义，全部留空。
"""
from __future__ import annotations

import io
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Optional

import pydantic
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from server.api.deps import DBDep
from server.api.auth import _get_optional_user
from database.models import (
    ALL_RUN_STATUSES,
    AI_FEATURE_API_CASE_GEN,
    AI_FEATURE_FUNCTIONAL_CASE_ENHANCE,
    AI_FEATURE_FUNCTIONAL_CASE_GEN,
    AI_RUN_STATUS_FAILED,
    AI_RUN_STATUS_PENDING,
    AI_RUN_STATUS_RUNNING,
    AI_RUN_STATUS_SUCCESS,
    AiRun,
    CASE_TYPE_API,
    CASE_TYPE_FUNCTIONAL,
    ConfigStore,
    EditOperationEvent,
    TestStepReport,
    EDIT_ACTION_CREATE,
    EDIT_ACTION_DELETE,
    EDIT_ACTION_UPDATE,
    FunctionalCaseEditHistory,
    FunctionalCaseRun,
    Module,
    Project,
    ProjectContext,
    TestCase,
    User,
)
from database.models.edit_operation import ENTITY_TYPE_TEST_CASE
from server.services.edit_history_service import (
    record_test_case_create,
    record_test_case_delete,
    record_test_case_update,
    merge_test_case_edit_history,
    snapshot_test_case,
)
from utils.parameter_flow import infer_state_transition_extracts

# 可选当前用户：带了有效 token 就解出 User，否则 None（不强制登录，记录 operator 用）
OptionalUserDep = Annotated[Optional[User], Depends(_get_optional_user)]
logger = logging.getLogger(__name__)


def _operator_name(user: Optional[User]) -> Optional[str]:
    if user is None:
        return None
    return getattr(user, "username", None) or getattr(user, "name", None)


def _record_edit(
    db,
    *,
    case_id: Optional[int],
    module_id: Optional[int],
    case_name: Optional[str],
    action: str,
    operator: Optional[str],
    changes: Optional[list[dict]] = None,
    session_id: Optional[str] = None,
) -> None:
    """写一行功能用例编辑历史。失败不应影响主流程（best-effort）。"""
    db.session.add(
        FunctionalCaseEditHistory(
            case_id=case_id,
            module_id=module_id,
            case_name=case_name,
            action=action,
            operator=operator,
            changes=changes or None,
            session_id=session_id,
        )
    )


def _load_case_for_history(db, case_id: int) -> TestCase | None:
    """加载用于生成可回滚快照的功能用例。"""
    return (
        db.session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == case_id)
        .first()
    )


router = APIRouter(prefix="/functional_cases", tags=["functional_cases"])


# ---------------------------------------------------------------------------
# 请求 / 响应 schema
# ---------------------------------------------------------------------------
class FunctionalSpec(pydantic.BaseModel):
    """`TestCase.functional_spec` JSON 列里存的形状。

    - preconditions / steps 都是字符串列表，方便前端按行渲染（每条可勾"做完了"）；
    - expected 是单条字符串，足够覆盖 95% 场景；要分多条期望就换行写。

    这里不强制非空 —— 用例刚被创建时常常是"占位"，先有标题后补内容。
    """
    preconditions: list[str] = pydantic.Field(default_factory=list)
    steps: list[str] = pydantic.Field(default_factory=list)
    expected: Optional[str] = None


class FunctionalCaseCreate(pydantic.BaseModel):
    module_id: int
    name: str
    description: Optional[str] = None
    skip: bool = False
    priority: Optional[int] = None
    tags: Optional[list[str]] = None
    functional_spec: Optional[FunctionalSpec] = None
    sort_order: Optional[int] = None


class FunctionalCaseUpdate(pydantic.BaseModel):
    """PUT 用：所有字段都可选，None = 不动；
    `module_id` 允许改，让用户可以把功能用例搬到别的模块。"""
    module_id: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    skip: Optional[bool] = None
    priority: Optional[int] = None
    tags: Optional[list[str]] = None
    functional_spec: Optional[FunctionalSpec] = None


class FunctionalMarkPayload(pydantic.BaseModel):
    """单条勾结果。`batch_id` 由前端在"测试模式"开始前生成 UUID；
    单点勾不传 batch_id 也行。"""
    status: str  # passed | failed | blocked | na
    actual_result: Optional[str] = None
    note: Optional[str] = None
    operator: Optional[str] = None
    batch_id: Optional[str] = None


class FunctionalBatchItem(pydantic.BaseModel):
    case_id: int
    status: str
    actual_result: Optional[str] = None
    note: Optional[str] = None


class FunctionalBatchMark(pydantic.BaseModel):
    """`batch_id` 必填 —— 批量勾的全部点意义就是同一批的聚合查询。
    没有 batch_id 就退化成 N 次单点 mark，应该走 /mark 接口。"""
    batch_id: str
    operator: Optional[str] = None
    items: list[FunctionalBatchItem]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _validate_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s not in ALL_RUN_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法状态 {status!r}，合法值：{sorted(ALL_RUN_STATUSES)}",
        )
    return s


def _get_functional_case_or_404(db, case_id: int) -> TestCase:
    case = db.session.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="用例不存在")
    if case.case_type != CASE_TYPE_FUNCTIONAL:
        raise HTTPException(
            status_code=400,
            detail=f"用例 {case_id} 不是功能用例（case_type={case.case_type}），不能用本接口操作",
        )
    return case


def _serialize_case(c: TestCase, *, latest_run: Optional[FunctionalCaseRun] = None) -> dict:
    """统一序列化形状。`latest_run` 由调用方决定要不要传，避免重复查。"""
    spec = c.functional_spec or {}
    return {
        "id": c.id,
        "module_id": c.module_id,
        "name": c.name,
        "description": c.description,
        "skip": c.skip,
        "priority": c.priority,
        "tags": c.tags or [],
        "case_type": c.case_type,
        "sort_order": c.sort_order,
        "functional_spec": {
            "preconditions": spec.get("preconditions") or [],
            "steps": spec.get("steps") or [],
            "expected": spec.get("expected"),
        },
        "latest_run": latest_run.to_dict() if latest_run else None,
    }


def _latest_runs_map(db, case_ids: list[int]) -> dict[int, FunctionalCaseRun]:
    """一次拿一组 case 的"最近一次 run"，避免 N+1。

    实现：先 GROUP BY case_id 拿 max(executed_at)，再 join 回 FunctionalCaseRun 取整行。
    """
    if not case_ids:
        return {}
    latest_sq = (
        db.session.query(
            FunctionalCaseRun.case_id.label("cid"),
            func.max(FunctionalCaseRun.executed_at).label("ts"),
        )
        .filter(FunctionalCaseRun.case_id.in_(case_ids))
        .group_by(FunctionalCaseRun.case_id)
        .subquery()
    )
    rows = (
        db.session.query(FunctionalCaseRun)
        .join(
            latest_sq,
            (FunctionalCaseRun.case_id == latest_sq.c.cid)
            & (FunctionalCaseRun.executed_at == latest_sq.c.ts),
        )
        .all()
    )
    # 同 (case_id, executed_at) 极小概率撞上多行（同一秒勾两次）—— 取最大 id 兜底
    out: dict[int, FunctionalCaseRun] = {}
    for r in rows:
        prev = out.get(r.case_id)
        if prev is None or r.id > prev.id:
            out[r.case_id] = r
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_API_SPEC_EXTS = {".json", ".yaml", ".yml"}
_DOC_EXTS = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt", ".text"} | _API_SPEC_EXTS


def _operation_ids_from_doc_url(url: str) -> set[str]:
    """从 Swagger UI 锚点链接里提取 operationId。

    例如 /docs#/users/create_user_api_users_post 中真正有用的是最后一段
    create_user_api_users_post；浏览器不会把 # 后内容发给服务端，所以这里必须
    在后端请求前先从原始输入里留住它。
    """
    from urllib.parse import unquote, urlsplit

    fragment = unquote(urlsplit(url or "").fragment or "").strip("/")
    if not fragment:
        return set()
    parts = [p for p in fragment.split("/") if p]
    if not parts:
        return set()
    op_id = parts[-1].strip()
    return {op_id} if op_id else set()


def _extract_doc_urls(*values: str) -> list[str]:
    """从链接字段或自由文本中提取接口文档 URL，并修正常见的 ``ttp://`` 漏字。"""
    found: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"(?<![A-Za-z0-9])(?:https?://|ttp://)[^\s,，;；<>\"']+", re.I)
    for value in values:
        for match in pattern.finditer(value or ""):
            url = match.group(0).rstrip(".)]}>。；，,")
            if url.lower().startswith("ttp://"):
                url = "h" + url
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _summarize_openapi(data: dict, operation_ids: set[str] | None = None) -> str:
    """OpenAPI/Swagger → 人读接口清单。

    摘要不再只写「有请求体」：必填字段、枚举、参数位置、鉴权和响应结构都来自
    同一份结构化契约，供大纲阶段理解业务；批次阶段还会收到完整契约 JSON。
    """
    from server.services.api_case_contract import build_contract_catalog

    lines = ["# OpenAPI/Swagger 接口清单"]
    catalog = build_contract_catalog(data, operation_ids=operation_ids)
    for op in catalog.get("operations") or []:
        line = f"- {op['method']} {op['path']}"
        if op.get("operation_id"):
            line += f"（operationId={op['operation_id']}）"
        if op.get("summary"):
            line += f" — {op['summary']}"
        parameters = []
        for parameter in op.get("parameters") or []:
            schema = parameter.get("schema") or {}
            constraint = schema.get("type") or ""
            if schema.get("enum"):
                constraint += f",enum={schema['enum']}"
            parameters.append(
                f"{parameter['name']}({parameter['in']},{'必填' if parameter.get('required') else '可选'},{constraint})"
            )
        if parameters:
            line += "；参数: " + ", ".join(parameters)
        request = op.get("request") or {}
        request_schema = request.get("schema") or {}
        if request_schema:
            properties = request_schema.get("properties") or {}
            required = set(request_schema.get("required") or [])
            fields = []
            for name, schema in properties.items():
                detail = schema.get("type") or ""
                if schema.get("enum"):
                    detail += f",enum={schema['enum']}"
                fields.append(f"{name}({'必填' if name in required else '可选'},{detail})")
            line += f"；请求体[{request.get('content_type') or 'unknown'}]: " + ", ".join(fields)
        security = op.get("security") or []
        if security:
            line += "；鉴权: " + ", ".join(f"{item.get('in')}:{item.get('name')}" for item in security)
        if op.get("responses"):
            line += "；响应码: " + ",".join(op["responses"])
        lines.append(line)
    wanted = {x for x in (operation_ids or set()) if x}
    if wanted and not catalog.get("operations"):
        lines.append(f"（未在 OpenAPI paths 中找到 operationId：{', '.join(sorted(wanted))}）")
    return "\n".join(lines[:400])


def _summarize_postman(data: dict) -> str:
    """Postman collection → 人读接口清单。"""
    lines = ["# Postman 接口清单"]

    def walk(items):
        for it in items or []:
            if not isinstance(it, dict):
                continue
            if "item" in it:  # 文件夹
                walk(it.get("item"))
                continue
            req = it.get("request")
            if isinstance(req, dict):
                method = req.get("method", "")
                url = req.get("url")
                raw = url.get("raw") if isinstance(url, dict) else str(url or "")
                line = f"- {method} {raw}"
                if it.get("name"):
                    line += f" — {it.get('name')}"
                body = req.get("body")
                if isinstance(body, dict) and body.get("raw"):
                    line += f"；body: {str(body.get('raw'))[:200]}"
                lines.append(line)

    walk(data.get("item"))
    return "\n".join(lines[:400])


def _api_text_from_obj(data, operation_ids: set[str] | None = None) -> str:
    """已解析的 OpenAPI/Postman/任意结构 → 人读接口清单文本。"""
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)[:8000] if data is not None else ""
    if "openapi" in data or "swagger" in data or "paths" in data:
        return _summarize_openapi(data, operation_ids=operation_ids)
    if "item" in data:
        return _summarize_postman(data)
    return json.dumps(data, ensure_ascii=False)[:8000]


def _load_api_spec_data(path: str, ext: str) -> Any:
    """读取接口规范原始对象；解析失败返回 ``None``。"""
    try:
        if ext in (".yaml", ".yml"):
            import yaml  # PyYAML 已在 requirements

            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _parse_api_spec(path: str, ext: str) -> str:
    """接口文件（OpenAPI/Swagger/Postman/任意 json·yaml）→ 喂给 AI 的接口清单文本。"""
    data = _load_api_spec_data(path, ext)
    if data is None:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read()[:8000]
        except Exception as e:  # noqa: BLE001
            return f"（接口文件解析失败：{e}）"
    return _api_text_from_obj(data)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _discover_spec_url(html: str, base: str) -> Optional[str]:
    """从 Swagger UI / 文档 HTML 里找规范文件地址（json/yaml 或 *api-docs*）。"""
    from urllib.parse import urljoin

    m = re.search(r'url:\s*["\']([^"\']+\.(?:json|yaml|yml)[^"\']*)["\']', html)
    if m:
        return urljoin(base, m.group(1))
    m = re.search(r'["\'](/?[^"\']*(?:api-docs|openapi|swagger)[^"\']*\.(?:json|yaml|yml)[^"\']*)["\']', html)
    if m:
        return urljoin(base, m.group(1))
    m = re.search(r'["\'](/?[^"\']*(?:v\d+/api-docs|api-docs|openapi)[^"\']*)["\']', html)
    if m:
        return urljoin(base, m.group(1))
    return None


def _local_platform_openapi(url: str) -> dict[str, Any] | None:
    """识别平台自身的 Swagger 地址并直接读取 app schema，不发起环回 HTTP 请求。

    这只允许项目约定的本地开发/容器端口以及 docs/redoc/openapi 路径。任意其它
    私网 URL 仍继续走 ``_ssrf_check`` 并默认拒绝，不能借此探测内网服务。
    """
    import os
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    configured_port = os.getenv("API_PORT", "").strip()
    allowed_ports = {54351, 8000}
    if configured_port.isdigit():
        allowed_ports.add(int(configured_port))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in allowed_ports:
        return None
    if (parsed.path.rstrip("/") or "/") not in {"/docs", "/redoc", "/openapi.json"}:
        return None
    try:
        from server.main import app

        schema = app.openapi()
        return schema if isinstance(schema, dict) else None
    except Exception:  # noqa: BLE001
        logger.warning("[api-contract] 读取平台自身 OpenAPI 失败", exc_info=True)
        return None


def _ssrf_check(url: str) -> str | None:
    """SSRF 防护：解析 host 并拒绝私网 / 环回 / 链路本地 / 保留地址。

    平台部署在内网时，这个接口拿到的是「用户给的任意 URL 由服务端发起请求」，
    不校验的话可以用来探测内网服务或云 metadata 端点（169.254.169.254）。
    如果确实需要拉内网文档站，通过环境变量 DOC_FETCH_ALLOW_PRIVATE=1 显式放开。
    """
    import ipaddress
    import os
    import socket
    from urllib.parse import urlparse

    if os.getenv("DOC_FETCH_ALLOW_PRIVATE", "").strip() in {"1", "true", "yes"}:
        return None
    host = urlparse(url).hostname
    if not host:
        return f"（不是合法链接：{url}）"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"（域名解析失败：{host}）"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return f"（出于安全考虑，禁止拉取内网/保留地址：{host} → {ip}；如需放开请设置 DOC_FETCH_ALLOW_PRIVATE=1）"
    return None


def _fetch_doc_url(url: str, _depth: int = 0, operation_ids: set[str] | None = None) -> str:
    """拉取接口文档链接 → 接口清单/正文文字。支持规范文件直链、Swagger UI、普通文档页。"""
    import requests  # 已在 requirements

    url = (url or "").strip()
    operation_ids = set(operation_ids or set()) | _operation_ids_from_doc_url(url)
    if not url.lower().startswith(("http://", "https://")):
        return f"（不是合法链接：{url}）"
    local_schema = _local_platform_openapi(url)
    if local_schema is not None:
        return _api_text_from_obj(local_schema, operation_ids=operation_ids)
    if (deny := _ssrf_check(url)) is not None:
        return deny
    try:
        # 手动跟随重定向：每一跳都要重新过 SSRF 校验，防止公网 URL 302 到内网
        for _ in range(4):
            resp = requests.get(
                url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=False
            )
            if resp.is_redirect or resp.is_permanent_redirect:
                from urllib.parse import urljoin

                url = urljoin(url, resp.headers.get("location", ""))
                if (deny := _ssrf_check(url)) is not None:
                    return deny
                continue
            break
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"（链接拉取失败：{e}）"

    ctype = (resp.headers.get("content-type") or "").lower()
    text = resp.text or ""
    bare = url.lower().split("?", 1)[0]

    if "json" in ctype or text.lstrip().startswith(("{", "[")):
        try:
            return _api_text_from_obj(json.loads(text), operation_ids=operation_ids)
        except Exception:
            pass
    if "yaml" in ctype or bare.endswith((".yaml", ".yml")):
        try:
            import yaml

            return _api_text_from_obj(yaml.safe_load(text), operation_ids=operation_ids)
        except Exception:
            pass
    # HTML：先找规范地址，找到就拉它（最多再下钻一层）
    if _depth < 1:
        spec_url = _discover_spec_url(text, url)
        if spec_url and spec_url != url:
            return _fetch_doc_url(spec_url, _depth + 1, operation_ids=operation_ids)
    return _html_to_text(text)[:8000]


def _fetch_openapi_catalog_url(
    url: str,
    _depth: int = 0,
    operation_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """从规范直链或 Swagger UI 拉取结构化契约。

    与 ``_fetch_doc_url`` 使用同一套 SSRF 与逐跳重定向检查；普通 HTML/文档不是
    OpenAPI 时返回 ``None``，不影响原有文本输入。
    """
    import requests

    from server.services.api_case_contract import build_contract_catalog

    url = (url or "").strip()
    operation_ids = set(operation_ids or set()) | _operation_ids_from_doc_url(url)
    if not url.lower().startswith(("http://", "https://")):
        return None
    local_schema = _local_platform_openapi(url)
    if local_schema is not None:
        return build_contract_catalog(local_schema, operation_ids=operation_ids)
    if _ssrf_check(url) is not None:
        return None
    try:
        for _ in range(4):
            response = requests.get(
                url,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=False,
            )
            if response.is_redirect or response.is_permanent_redirect:
                from urllib.parse import urljoin

                url = urljoin(url, response.headers.get("location", ""))
                if _ssrf_check(url) is not None:
                    return None
                continue
            break
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        return None

    text = response.text or ""
    content_type = (response.headers.get("content-type") or "").lower()
    data = None
    try:
        if "json" in content_type or text.lstrip().startswith("{"):
            data = json.loads(text)
        elif "yaml" in content_type or url.lower().split("?", 1)[0].endswith((".yaml", ".yml")):
            import yaml

            data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        data = None
    if isinstance(data, dict) and ("openapi" in data or "swagger" in data or "paths" in data):
        return build_contract_catalog(data, operation_ids=operation_ids)
    if _depth < 1:
        spec_url = _discover_spec_url(text, url)
        if spec_url and spec_url != url:
            return _fetch_openapi_catalog_url(spec_url, _depth + 1, operation_ids=operation_ids)
    return None


def _salvage_json_objects(text: str) -> list | None:
    """从可能被**截断**的文本里抢救出数组内所有「完整的」对象。

    LLM 输出超过 max_tokens 被截断时，整段 JSON 缺尾 `]`、最后一个对象残缺，
    直接 json.loads 必失败。这里逐字符扫描，把每个配平的顶层 `{...}` 单独解析出来，
    丢掉末尾残缺的那个——能把"本来要 502 重试"的批次救回大部分用例。
    """
    i = text.find("[")
    if i < 0:
        return None
    objs: list = []
    depth = 0
    in_str = False
    esc = False
    start = None
    for k in range(i + 1, len(text)):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = k
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objs.append(json.loads(text[start : k + 1]))
                    except Exception:
                        pass
                    start = None
    return objs or None


def _extract_json_list(raw: str, *, allow_salvage: bool = True):
    """从 LLM 输出里抽 JSON 数组：```json``` 围栏 / 第一个 [...] / 整段直接 loads；
    全失败时按"截断容错"抢救已完整的对象。"""
    if not raw:
        return None

    def as_list(obj: Any):
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for key in ("cases", "test_cases", "items", "data"):
                val = obj.get(key)
                if isinstance(val, list):
                    return val
            if obj.get("name"):
                return [obj]
        return None

    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    if m:
        try:
            parsed = as_list(json.loads(m.group(1)))
            if parsed is not None:
                return parsed
        except Exception:
            pass
    start, end = raw.find("["), raw.rfind("]")
    if 0 <= start < end:
        try:
            parsed = as_list(json.loads(raw[start : end + 1]))
            if parsed is not None:
                return parsed
        except Exception:
            pass
    try:
        parsed = as_list(json.loads(raw))
        if parsed is not None:
            return parsed
    except Exception:
        pass
    if not allow_salvage:
        return None
    # 旧的非执行资产继续允许容错；接口执行用例和修复结果必须完整 JSON，截断就缩小批次重试。
    salvaged = _salvage_json_objects(raw)
    if salvaged:
        logger.warning(
            "ai_generate_batch 输出疑似被截断，已抢救 %d 条完整用例（原文 %d 字符）",
            len(salvaged), len(raw),
        )
    return salvaged


_ACCOUNT_PATH_HINTS = ("user", "register", "signup", "sign_up", "account", "member", "regist")
_ACCOUNT_NAME_HINTS = ("注册", "创建用户", "新建用户", "建账号", "创建账号", "添加用户", "register", "signup")


def _account_setup_endpoints(db, project_id: int) -> str:
    """挖掘本项目里"建账号/注册"类接口（POST），供「前置链」跨模块建一次性测试账号用。

    来源：项目下已有 api 用例的 http_request 步骤里，path 像 /users /register 之类、
    或用例名含"注册/创建用户"的 POST 接口。没有就返回空串。
    """
    cases = (
        db.session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .join(Module, Module.id == TestCase.module_id)
        .filter(Module.project_id == project_id, TestCase.case_type == CASE_TYPE_API)
        .all()
    )
    found: dict[tuple[str, str], list[str]] = {}
    for c in cases:
        cname = (c.name or "").lower()
        for s in (c.steps or []):
            if s.step_type != "http_request" or not isinstance(s.config, dict):
                continue
            method = str(s.config.get("method") or "").upper()
            path = str(s.config.get("path") or "")
            if method != "POST" or not path:
                continue
            lp = path.lower()
            if not (any(h in lp for h in _ACCOUNT_PATH_HINTS) or any(h in cname for h in _ACCOUNT_NAME_HINTS)):
                continue
            params = s.config.get("params")
            keys = sorted(params.keys()) if isinstance(params, dict) else []
            found.setdefault((method, path), keys)
    if not found:
        return ""
    lines = [
        f"- {m} {p}（请求字段：{', '.join(ks) if ks else '见文档'}）"
        for (m, p), ks in list(found.items())[:6]
    ]
    return (
        "【可用的『建账号/注册』接口】（**仅『前置链』用例可跨模块使用**，用它建一个 "
        "function:unique 的一次性测试账号、密码写固定字面量，再登录该账号拿 token）：\n"
        + "\n".join(lines)
    )


def _project_context_block(project_id: int, query_text: str) -> str:
    """检索项目记忆层（project_contexts），渲染为大纲 prompt 可注入的文本。

    失败/为空一律降级为占位文案，绝不阻断大纲生成。
    """
    from server.services.context_service import (
        build_context_summary,
        retrieve_context,
    )

    empty = "（项目暂无沉淀上下文）"
    try:
        matched = retrieve_context(
            query_text=(query_text or "")[:800],
            project_id=project_id,
            top_k=8,
            target_types=[
                "business_rule", "data_model", "api_contract",
                "term_definition", "process_flow", "constraint",
            ],
        )
        if not matched:
            return empty
        text = build_context_summary(matched)
        return text[:4000] + ("\n…[truncated]" if len(text) > 4000 else "")
    except Exception:
        logger.warning(
            "[outline] project=%s 记忆层检索失败，降级为空", project_id, exc_info=True
        )
        return empty


def _project_api_contract_rules_block(
    db,
    project_id: int,
    api_contract: dict[str, Any] | None,
) -> str:
    """按 operationId/path 精确加载项目记忆中的 API 规则原文。

    OpenAPI 往往不包含运行期角色代码、会话语义或统一响应信封等业务约束。此处不走
    Top-K 摘要，直接读取 ``api_contract`` 原文；命中当前 operation 的规则优先，同时
    保留响应信封/状态码等全局约定。
    """
    try:
        rows = (
            db.session.query(ProjectContext)
            .filter(
                ProjectContext.project_id == project_id,
                ProjectContext.context_type.in_(["api_contract", "business_rule", "constraint"]),
            )
            .order_by(ProjectContext.importance.desc(), ProjectContext.id.desc())
            .limit(100)
            .all()
        )
    except Exception:  # noqa: BLE001
        logger.warning("[api-contract] project=%s 精确规则读取失败", project_id, exc_info=True)
        return "（项目暂无可用的 API 规则原文）"
    operations = (api_contract or {}).get("operations") or []
    needles = {
        str(value).lower()
        for operation in operations
        for value in (operation.get("operation_id"), operation.get("path"))
        if value
    }
    global_hints = ("响应信封", "响应结构", "状态码约定", "错误响应", "jsonpath", "全局约定")

    def score(row: ProjectContext) -> int:
        text = f"{row.title}\n{row.summary or ''}\n{row.content or ''}".lower()
        exact = sum(20 for needle in needles if needle and needle in text)
        global_score = 5 if any(hint in text for hint in global_hints) else 0
        return exact + global_score + int(row.importance or 0)

    ranked = sorted(rows, key=score, reverse=True)
    selected = [row for row in ranked if score(row) >= 5][:24]
    if not selected:
        selected = ranked[:12]
    lines = ["【项目 API 规则原文（业务约束，不能被通用经验覆盖）】"]
    for row in selected:
        content = (row.content or row.summary or "").strip()
        if content:
            lines.append(f"- {row.title}：{content}")
    text = "\n".join(lines)
    return text[:16000] + ("\n…[truncated]" if len(text) > 16000 else "")


def _enrich_api_contract_from_project_rules(
    db,
    project_id: int,
    api_contract: dict[str, Any],
) -> dict[str, Any]:
    """把项目人工确认过的响应 JSONPath 附加到对应 operation。

    某些 FastAPI 路由只在 OpenAPI 声明 200，却没有 response_model。此时 schema 无法
    证明 token/id 路径；项目记忆中的 ``api_contract`` 原文是第二可信来源。这里仅提取
    明确写出的 JSONPath，不从自然语言猜新路径。
    """
    from copy import deepcopy

    from server.services.api_case_contract import contract_hash

    enriched = deepcopy(api_contract or {})
    operations = enriched.get("operations") or []
    if not operations:
        return enriched
    try:
        rows = (
            db.session.query(ProjectContext)
            .filter(
                ProjectContext.project_id == project_id,
                ProjectContext.context_type == "api_contract",
            )
            .order_by(ProjectContext.importance.desc(), ProjectContext.id.asc())
            .all()
        )
    except Exception:  # noqa: BLE001
        return enriched
    aliases = {
        "login": ("登录", "登陆"),
        "sessions": ("会话列表", "会话"),
        "refresh": ("刷新",),
        "logout": ("登出", "退出"),
        "password": ("密码", "改密"),
    }
    path_pattern = re.compile(r"\$\.[A-Za-z0-9_.*\[\]-]+(?:\.[A-Za-z0-9_.*\[\]-]+)*")

    def positive_paths(text: str) -> list[str]:
        """只提取肯定描述中的 JSONPath，排除“而非 $.x”一类反例。"""
        paths: list[str] = []
        for match in path_pattern.finditer(text):
            prefix = text[max(0, match.start() - 12):match.start()].lower()
            if any(marker in prefix for marker in ("而非", "禁止", "不能", "错误路径", "不要用", "非正确")):
                continue
            paths.append(match.group(0))
        return paths

    for operation in operations:
        operation_text = " ".join((
            str(operation.get("operation_id") or ""),
            str(operation.get("path") or ""),
            str(operation.get("summary") or ""),
        )).lower()
        trusted: set[str] = set(operation.get("trusted_response_paths") or [])
        for row in rows:
            row_text = f"{row.title}\n{row.summary or ''}\n{row.content or ''}"
            row_lower = row_text.lower()
            signature = f"{operation.get('method') or ''} {operation.get('path') or ''}".strip().lower()
            operation_id = str(operation.get("operation_id") or "").lower()
            exact = bool(
                (signature and signature in row_lower)
                or (operation_id and operation_id in row_lower)
            )
            operation_aliases = {
                alias
                for key, values in aliases.items()
                if key in operation_text
                for alias in values
            }
            global_rule = "响应信封" in row.title.lower()
            if exact:
                trusted.update(positive_paths(row_text))
                continue
            if global_rule:
                envelope_paths = {
                    path for path in positive_paths(row_text)
                    if path in {"$.status", "$.data", "$.data.*"}
                }
                # ``{status, data}`` 本身就是人工确认的显式响应结构，即使原文没有
                # 给 status 写成 JSONPath，也可以确定对应顶层 ``$.status``。
                if re.search(r"\{[^}]*\bstatus\b[^}]*\bdata\b[^}]*\}", row_text, re.I):
                    envelope_paths.update({"$.status", "$.data"})
                trusted.update(envelope_paths)
                continue
            if operation_aliases:
                # 一条项目规则可能同时描述登录和创建用户。只读取含当前接口关键词的
                # 句子，且关键词必须表达“接口/成功/响应”等操作语义。这样“常用字段：
                # 刷新令牌 $.data.refresh_token”不会被误当成 refresh 接口响应模型。
                body = str(row.content or row.summary or "")
                sentences = [part for part in re.split(r"[。\n]+", body) if part.strip()]
                title_is_operation_rule = any(
                    re.search(
                        re.escape(alias) + r".{0,10}(?:接口|成功|响应|返回|调用|行为|时)",
                        row.title,
                    )
                    for alias in operation_aliases
                )
                if title_is_operation_rule:
                    matched_sentences = sentences
                else:
                    matched_sentences = [
                        sentence for sentence in sentences
                        if any(
                            re.search(
                                re.escape(alias) + r".{0,10}(?:接口|成功|响应|返回|调用|行为|时)",
                                sentence,
                            )
                            for alias in operation_aliases
                        )
                    ]
                for sentence in matched_sentences:
                    trusted.update(positive_paths(sentence))
        if trusted:
            operation["trusted_response_paths"] = sorted(trusted)
    enriched["hash"] = contract_hash(enriched)
    return enriched


def _build_cross_module_context(db, module: "Module") -> str:
    """跨模块上下文：① 项目概览 + 与本模块相关的模块关联关系（来自 project.ai_overview，
    若已生成）；② 同项目其它模块 + 各自最多 8 个功能用例名；③ 建账号接口（供前置链跨模块）。
    给 AI 做跨模块联动设计。"""
    parts: list[str] = []

    project = db.session.query(Project).filter(Project.id == module.project_id).first()
    overview = (project.ai_overview if project else None) or {}
    if isinstance(overview, dict):
        summary = str(overview.get("summary") or "").strip()
        if summary:
            parts.append(f"【项目概览】{summary}")
        relations = [r for r in (overview.get("relations") or []) if isinstance(r, dict)]
        mine = [
            r for r in relations
            if r.get("from") == module.name or r.get("to") == module.name
        ]
        if mine:
            rel_lines = "\n".join(
                f"- {r.get('from')} → {r.get('to')}：{r.get('relation')}" for r in mine
            )
            parts.append(
                f"【与本模块「{module.name}」相关的模块关联】（请据此设计跨模块联动用例：在关联模块操作→回本模块验证，或反之）：\n{rel_lines}"
            )

    siblings = (
        db.session.query(Module)
        .filter(Module.project_id == module.project_id, Module.id != module.id)
        .limit(20)
        .all()
    )
    if siblings:
        lines = []
        for m in siblings:
            names = (
                db.session.query(TestCase.name)
                .filter(TestCase.module_id == m.id, TestCase.case_type == CASE_TYPE_FUNCTIONAL)
                .limit(8)
                .all()
            )
            names_str = "、".join(n[0] for n in names) if names else "（暂无用例）"
            lines.append(f"- 模块「{m.name}」：{names_str}")
        parts.append("【其它模块现有用例】（参考，避免重复、便于联动）：\n" + "\n".join(lines))

    account_block = _account_setup_endpoints(db, module.project_id)
    if account_block:
        parts.append(account_block)

    return "\n\n".join(parts) if parts else "（项目下暂无其它模块）"


def _ingest_uploads(
    images: list[UploadFile],
    docs: list[UploadFile],
    tmpdir: str,
    *,
    use_vision: bool,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """把上传文件落到临时目录。

    返回 vision 图片路径、文本片段、结构化接口契约目录。文本给大纲模型阅读，契约目录
    则贯穿后续编译和硬校验，二者不能再互相替代。
    """
    import os
    from ai_gateway.gateway import ocr_extract
    from server.services.doc_parser import parse_document
    from server.services.api_case_contract import build_contract_catalog

    image_paths: list[str] = []
    text_chunks: list[str] = []
    contracts: list[dict[str, Any]] = []

    # 文档：接口文件(OpenAPI/Postman/json/yaml)→ 接口清单；其它(PDF/Word/MD/TXT)→ 抽全文
    for up in docs or []:
        name = up.filename or "doc"
        ext = os.path.splitext(name)[1].lower()
        if ext not in _DOC_EXTS:
            continue
        fp = os.path.join(tmpdir, f"doc_{len(text_chunks)}{ext}")
        with open(fp, "wb") as f:
            f.write(up.file.read())
        if ext in _API_SPEC_EXTS:
            body = _parse_api_spec(fp, ext)
            label = "接口文档"
            data = _load_api_spec_data(fp, ext)
            if isinstance(data, dict) and ("openapi" in data or "swagger" in data or "paths" in data):
                contracts.append(build_contract_catalog(data))
        else:
            try:
                body = (parse_document(fp).plain_text or "").strip()
            except Exception as e:  # noqa: BLE001
                body = f"（文档解析失败：{e}）"
            label = "需求文档"
        if body:
            text_chunks.append(f"## {label}：{name}\n{body[:8000]}")

    # 图片：vision 优先收路径；否则 OCR 抽文字
    for up in images or []:
        name = up.filename or "img"
        ext = os.path.splitext(name)[1].lower()
        if ext not in _IMG_EXTS:
            continue
        fp = os.path.join(tmpdir, f"img_{len(image_paths) + len(text_chunks)}{ext}")
        with open(fp, "wb") as f:
            f.write(up.file.read())
        if use_vision:
            image_paths.append(fp)
        else:
            txt = ocr_extract(fp)
            text_chunks.append(
                f"## 界面/原型截图：{name}\n```\n{(txt or '（OCR 未识别到文本）')[:2000]}\n```"
            )
    return image_paths, text_chunks, contracts


def _norm_name(s: str) -> str:
    """用例名归一化（去空白/标点/大小写）用于重复判断。"""
    return re.sub(r"[\s\-_:：、，,。.（）()【】\[\]]+", "", str(s or "")).lower()


_VAR_POOL_DESC = {"my_account": "默认账号", "my_password": "默认密码", "mobile": "默认手机号"}


def _variable_pool_block(db, project_id: int) -> str:
    """读项目 default_parameters 变量池，喂给 AI 让接口用例优先用 ${变量}。"""
    rows = (
        db.session.query(ConfigStore)
        .filter(
            ConfigStore.config_group == "default_parameters",
            ConfigStore.project_id == project_id,
        )
        .all()
    )
    seen: dict[str, ConfigStore] = {}
    for r in rows:
        if r.config_key:
            seen[r.config_key] = r
    if not seen:
        return ""
    lines = []
    for key in seen:
        desc = _VAR_POOL_DESC.get(key, "")
        lines.append(f"- ${{{key}}}{('：' + desc) if desc else ''}")
    return "\n".join(lines)


def _existing_case_names(db, module_id: int, limit: int = 300, case_type: str = CASE_TYPE_FUNCTIONAL) -> list[str]:
    rows = (
        db.session.query(TestCase.name)
        .filter(TestCase.module_id == module_id, TestCase.case_type == case_type)
        .order_by(TestCase.sort_order)
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows if r[0]]


_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][\w.-]*)\}")
_DATA_NS_PREFIX = "AUTO_TEST_"
# 正向写入用例里、应走动态唯一函数的"会留库"字段
_WRITE_DATA_KEYS = {
    "username", "user_name", "account", "mobile", "phone", "tel",
    "email", "nickname", "real_name", "name", "title", "order_no", "orderno",
}
# 逆向/异常类别：这些用例的数据就是要畸形，不做命名空间改写
_NEGATIVE_CATS = ("参数校验", "边界", "鉴权", "越权", "安全", "响应校验")


def _normalize_jsonpath(expr: Any) -> Any:
    """提取/断言里 jsonpath 语法兜底：状态码关键字保持，其余确保 $ 开头。"""
    if not isinstance(expr, str):
        return expr
    s = expr.strip()
    if not s or s.lower() == "status_code":
        return s
    if s.startswith("$"):
        return s
    # 写成 data.token / .data.token 之类的，补成 $.data.token
    return "$." + s.lstrip(".")


def _normalize_pre_hook(raw: Any) -> list[dict]:
    """规整生成用例带的 pre_hook（会话隔离登录）。非法/空 → []。

    统一成 [{type:'http_request', config:{method,path,data_type,params,headers,extract_data}}]，
    与 runners/case_executor._run_hooks 期望格式对齐。必须 extract 出至少一个变量才算有效。
    """
    if not isinstance(raw, list) or not raw:
        return []
    out: list[dict] = []
    for hk in raw:
        if not isinstance(hk, dict):
            continue
        cfg = hk.get("config") if isinstance(hk.get("config"), dict) else hk
        method = str(cfg.get("method") or "").upper()
        path = str(cfg.get("path") or cfg.get("url") or "")
        ext = cfg.get("extract_data") or cfg.get("extract") or {}
        if not path or method not in ("POST", "GET", "PUT"):
            continue
        if not (isinstance(ext, dict) and ext):
            continue  # 登录 hook 不提取任何变量 = 没意义
        data_type = str(cfg.get("data_type") or "application/json")
        normalized_config = {
            "method": method,
            "path": path,
            "data_type": data_type,
            "headers": cfg.get("headers") or {},
            "extract_data": {str(k): str(v) for k, v in ext.items() if str(k).strip()},
        }
        # 兼容模型按新契约输出 json/form，也兼容旧 prompt 的 params/body。
        # 之前这里只读取 params/body，模型输出 json 时登录请求体会被静默变成 {}，
        # pre_hook 随即返回 422，并把主用例误标为探测失败。
        if "json" in cfg:
            normalized_config["json"] = cfg.get("json")
        elif "form" in cfg:
            normalized_config["form"] = cfg.get("form")
        elif "body" in cfg:
            if data_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
                normalized_config["form"] = cfg.get("body")
            else:
                normalized_config["json"] = cfg.get("body")
        else:
            normalized_config["params"] = cfg.get("params") or {}
        if isinstance(cfg.get("query_params"), dict):
            normalized_config["query_params"] = cfg["query_params"]
        out.append({
            "type": "http_request",
            "config": normalized_config,
        })
    return out


def _pre_hook_vars(case: dict) -> set[str]:
    """pre_hook 登录 hook 提取出的变量名（运行时会进 ctx，供本用例引用）。"""
    out: set[str] = set()
    for hk in case.get("pre_hook") or []:
        cfg = hk.get("config") if isinstance(hk, dict) else {}
        for k in (cfg.get("extract_data") or {}):
            if str(k).strip():
                out.add(str(k))
    return out


def _shape_cases(parsed) -> list[dict]:
    """把 LLM 解析出的 list 规整成 {name, preconditions[], steps[], expected[], ...}。

    透传接口模式结构化字段（含场景多步 requests、清理 teardown、data_safety）。
    """
    out: list[dict] = []
    if not isinstance(parsed, list):
        return out
    for it in parsed:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        # 把测试点类别拼进用例名：【参数校验】缺少 username 返回 422
        cat = str(it.get("category") or "").strip()
        if cat and not name.startswith("【"):
            name = f"【{cat}】{name}"
        item = {
            "name": name[:200],
            "category": cat,
            "preconditions": [str(x).strip() for x in (it.get("preconditions") or []) if str(x).strip()],
            "steps": [str(x).strip() for x in (it.get("steps") or []) if str(x).strip()],
            "expected": [str(x).strip() for x in (it.get("expected") or []) if str(x).strip()],
            "after": str(it.get("after") or "").strip(),
        }
        # 接口模式的结构化字段（功能模式不会出现，透传给前端映射到 api 用例字段）
        for key in (
            "operation_id", "scenario_type", "field", "mutation",
            "method", "path", "path_params", "query_params", "headers",
            "json", "form", "files", "body", "extract", "assertion", "sql",
        ):
            if key in {"json", "form", "body"} and key in it:
                # 空对象、空数组、空字符串、null 都可能正是参数校验场景，不能在规整时丢掉。
                item[key] = it[key]
            elif key in it and it[key] not in (None, "", [], {}):
                item[key] = it[key]
        # 场景多步：requests 数组（每项一次接口调用）
        if isinstance(it.get("requests"), list) and it["requests"]:
            reqs = []
            for r in it["requests"]:
                if not isinstance(r, dict) or not r.get("path"):
                    continue
                rr = {
                    k: r[k]
                    for k in (
                        "name", "operation_id", "method", "path", "path_params", "query_params",
                        "headers", "json", "form", "files", "body", "extract", "assertion", "sql",
                    )
                    if (
                        (k in {"json", "form", "body"} and k in r)
                        or r.get(k) not in (None, "", [], {})
                    )
                }
                if isinstance(rr.get("extract"), dict):
                    rr["extract"] = {k: _normalize_jsonpath(v) for k, v in rr["extract"].items()}
                if isinstance(rr.get("assertion"), dict):
                    rr["assertion"] = {_normalize_jsonpath(k): v for k, v in rr["assertion"].items()}
                reqs.append(rr)
            if reqs:
                item["requests"] = reqs
        # 会话隔离 pre_hook：用例级前置登录(免受前序用例登出/改密污染)。
        # 形态 [{type:'http_request', config:{method,path,params,extract_data}}]
        norm_pre = _normalize_pre_hook(it.get("pre_hook"))
        if norm_pre:
            item["pre_hook"] = norm_pre

        # 清理闭环：teardown_api（DELETE 调用数组）/ teardown_sql（删库兜底）
        if isinstance(it.get("teardown_api"), list) and it["teardown_api"]:
            item["teardown_api"] = [t for t in it["teardown_api"] if isinstance(t, dict) and t.get("path")]
        if isinstance(it.get("teardown_sql"), str) and it["teardown_sql"].strip():
            item["teardown_sql"] = it["teardown_sql"].strip()
        # 数据安全说明
        if isinstance(it.get("data_safety"), dict) and it["data_safety"]:
            item["data_safety"] = it["data_safety"]
        # jsonpath 兜底归一化
        if isinstance(item.get("extract"), dict):
            item["extract"] = {k: _normalize_jsonpath(v) for k, v in item["extract"].items()}
        if isinstance(item.get("assertion"), dict):
            item["assertion"] = {_normalize_jsonpath(k): v for k, v in item["assertion"].items()}
        out.append(item)
    return out


def _case_requests(case: dict) -> list[dict]:
    """统一取一条用例的所有请求：场景用例取 requests，单接口用例把顶层字段当一个请求。"""
    if isinstance(case.get("requests"), list) and case["requests"]:
        requests = [request for request in case["requests"] if isinstance(request, dict)]
        if requests:
            return requests
    if case.get("path") or case.get("method"):
        return [case]
    return []


def _produced_vars(case: dict) -> set[str]:
    out: set[str] = set()
    for req in _case_requests(case):
        ex = req.get("extract")
        if isinstance(ex, dict):
            out |= {str(k) for k in ex if str(k).strip()}
    return out


def _referenced_vars(obj: Any) -> set[str]:
    return set(_VAR_REF_RE.findall(json.dumps(obj, ensure_ascii=False, default=str)))


def _is_negative_case(name: str) -> bool:
    head = name[:10]
    return any(cat in head for cat in _NEGATIVE_CATS)


def _namespace_write_data(body: Any, path: str = "$") -> list[str]:
    """把正向写入用例 body 里留库字段的写死字面量改成 function:unique。返回被改写的字段说明。"""
    changed: list[str] = []
    if isinstance(body, dict):
        for key, val in list(body.items()):
            if isinstance(val, (dict, list)):
                changed.extend(_namespace_write_data(val, f"{path}.{key}"))
                continue
            if not isinstance(val, str):
                continue
            raw = val.strip()
            if (
                not raw
                or raw.startswith("${")
                or raw.startswith("function:")
                or raw.startswith(_DATA_NS_PREFIX)
                or raw.startswith("<TODO")
            ):
                continue
            lk = key.lower()
            if lk not in _WRITE_DATA_KEYS:
                continue
            if lk in {"username", "user_name", "account"}:
                body[key] = "function:unique(AUTO_TEST_user)"
            elif lk in {"mobile", "phone", "tel"}:
                body[key] = "function:unique_mobile()"
            elif lk == "email":
                body[key] = "function:unique_email()"
            else:
                body[key] = f"function:unique(AUTO_TEST_{lk})"
            changed.append(f"{key}->{body[key]}")
    elif isinstance(body, list):
        for i, child in enumerate(body):
            changed.extend(_namespace_write_data(child, f"{path}[{i}]"))
    return changed


_FUNC_REF_RE = re.compile(r"function:([A-Za-z_]\w*)")


def _known_function_names(project_id: int | None = None) -> set[str]:
    try:
        from utils.script_runtime import list_script_names

        return set(list_script_names("function", project_id=project_id))
    except Exception:
        return set()


def _unknown_functions(case: dict, project_id: int | None = None) -> set[str]:
    """找出用例里引用了但当前项目脚本库没注册的 function 名。"""
    known = _known_function_names(project_id)
    if not known:
        return set()
    text = json.dumps(_case_requests(case), ensure_ascii=False, default=str)
    used = set(_FUNC_REF_RE.findall(text))
    return {n for n in used if n not in known}


def _is_login_path(path: str) -> bool:
    lp = (path or "").lower()
    return ("login" in lp or "signin" in lp or "sign_in" in lp or lp.endswith("/auth/token") or "/token" in lp) and "register" not in lp


def _is_public_auth_path(path: str) -> bool:
    """公开、无需鉴权就能调的认证类接口：登录 / 注册 / 找回密码等。"""
    lp = (path or "").lower()
    return (
        _is_login_path(lp)
        or "register" in lp or "signup" in lp or "sign_up" in lp
        or "forgot" in lp or "reset" in lp or "captcha" in lp or "send_code" in lp
    )


def _req_has_auth(req: dict) -> bool:
    headers = req.get("headers") or {}
    if not isinstance(headers, dict):
        return False
    return any(str(k).lower() == "authorization" and str(v).strip() for k, v in headers.items())


# 管理类写接口路径特征（这些写操作通常需要 admin/登录态鉴权）
_ADMIN_WRITE_HINTS = ("user", "account", "member", "role", "admin", "permission")


def _write_without_auth_reqs(case: dict) -> list[str]:
    """检测：写操作(POST/PUT/PATCH/DELETE)打到管理类接口、却没带 Authorization。

    这类接口多半需要鉴权,漏 token 会 401「未提供认证 token」——但平台静态不知道
    哪个接口要鉴权,所以只对"看起来是账号/角色管理"的写接口做启发式提醒(排除注册等公开接口)。
    返回命中的 "METHOD PATH" 列表。
    """
    hits: list[str] = []
    for req in _case_requests(case):
        method = str(req.get("method") or "").upper()
        path = str(req.get("path") or "")
        lp = path.lower()
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            continue
        if _is_public_auth_path(lp):
            continue
        if not any(h in lp for h in _ADMIN_WRITE_HINTS):
            continue
        if not _req_has_auth(req):
            hits.append(f"{method} {path}")
    return hits


def _login_with_uncreated_unique(case: dict) -> bool:
    """检测：用一个 function:unique 用户名直接登录（该账号没被创建过 → 必然 401）。"""
    for req in _case_requests(case):
        if not _is_login_path(str(req.get("path") or "")):
            continue
        for payload_key in ("json", "form", "body"):
            body = req.get(payload_key)
            if not isinstance(body, dict):
                continue
            for key in ("username", "user_name", "account"):
                val = body.get(key)
                if isinstance(val, str) and val.strip().startswith("function:unique"):
                    return True
    return False


def _harden_generated_cases(
    cases: list[dict],
    var_pool_keys: set[str],
    carried_vars: set[str],
    project_id: int | None = None,
) -> list[dict]:
    """生成后校验 + 加固（问题5/6/7 + 数据治理#1）：
      - 未解析 ${var}（无变量池来源、无前置用例 extract 产出）→ 记 warnings 引导用户/下一轮 AI。
      - 缺断言 → 记 warnings。
      - 正向写入用例的留库字段是写死字面量 → 自动改成 function:unique 命名空间。
    `carried_vars`：跨批次带过来的、前面批次已产出的变量名。
    """
    produced_so_far = set(var_pool_keys) | set(carried_vars)
    for case in cases:
        warnings: list[str] = []
        # blocking：执行必挂类问题（不是"写得不够好"，是"跑起来一定失败"）。
        # 有实测依据：项目 1 的 163 条用例中，被变量校验标记的 95 条运行通过率为 0。
        # 这类会置 case["needs_fix"]=True，评审页默认不勾选，避免一键批量入库。
        blocking: list[str] = []
        negative = _is_negative_case(case.get("name") or "")
        reqs = _case_requests(case)

        # 0) 空壳用例兜底：没有任何可执行请求（常见于并发/性能/压测类——平台顺序执行
        #    引擎无法表达，AI 往往只给了名字、结构化字段全空）。打警告，不让它静默空白。
        if not reqs:
            msg = (
                "该用例没有可执行的请求（method/path 为空）。若是并发/性能/压测类需求，"
                "本平台顺序执行无法表达——建议改成「连续多次重复提交」的顺序多步用例，或用专门的压测工具；"
                "否则请手工补全请求或删除本用例。"
            )
            warnings.append(msg)
            blocking.append(msg)
            case["warnings"] = warnings
            case["needs_fix"] = True
            continue

        # 1) 命名空间加固（仅正向写入用例）
        if not negative:
            for req in reqs:
                if str(req.get("method") or "").upper() not in ("POST", "PUT", "PATCH"):
                    continue
                # 登录/认证接口绝不改写用户名：登录是"认证已存在账号"，把用户名换成
                # function:unique 随机值会变成"登录一个不存在的账号"→ 必然 401。
                if _is_login_path(str(req.get("path") or "")):
                    continue
                for payload_key in ("json", "form", "body"):
                    rewritten = _namespace_write_data(req.get(payload_key))
                    if rewritten:
                        ds = case.setdefault("data_safety", {})
                        ds.setdefault("rewritten_fields", []).extend(rewritten)
                        ds["cleanup_required"] = True

        # 1.4) 引用了不存在的动态函数（AI 瞎编函数名）→ 执行必报错
        bad_funcs = _unknown_functions(case, project_id)
        if bad_funcs:
            msg = (
                f"用到了平台不存在的动态函数：{', '.join(sorted('function:' + n for n in bad_funcs))}。"
                "动态值只能用已注册的函数（如 function:unique / unique_mobile / unique_email），不要自己造名字。"
            )
            warnings.append(msg)
            blocking.append(msg)          # 未注册函数 → 执行期直接报错

        # 1.5) 用没创建过的 function:unique 账号登录 → 必然 401
        if _login_with_uncreated_unique(case):
            msg = (
                "登录步骤用了 function:unique 用户名，但该账号没有先被创建——登录会 401。"
                "请改成：先调创建账号接口建号、提取真实用户名再登录；或直接用变量池账号 ${my_account} 登录。"
            )
            warnings.append(msg)
            blocking.append(msg)          # 登录必 401

        # 1.6) 管理类写接口（建/删用户、改角色等）没带 Authorization → 多半 401
        #      典型：建一次性账号的 POST /api/users 忘了带 admin token（先有鸡先有蛋）
        noauth = _write_without_auth_reqs(case)
        if noauth:
            warnings.append(
                f"写操作 {('、'.join(noauth))} 没带 Authorization 头——这类管理接口通常需要"
                "鉴权(admin/登录态),漏 token 会 401「未提供认证 token」。请给该请求补 "
                "`{\"Authorization\": \"Bearer ${token}\"}`(用管理员/前置链登录后的 token);"
                "若该接口确实公开无需鉴权,可忽略本提示。"
            )

        # 2) 断言完整性
        if reqs and not any(isinstance(r.get("assertion"), dict) and r["assertion"] for r in reqs):
            warnings.append("缺少断言：至少补一条状态码或业务码断言")

        # 3) 变量解析校验（用例间按数组顺序累积，用例内**再按步骤顺序累积**）
        #    pre_hook 登录会在跑 steps 前提取变量(如 token)进 ctx，视作本用例开始时即可用。
        #
        #    为什么必须按步骤顺序：整条用例粒度会把"第 3 步才 extract 出来的变量"也算成
        #    第 1 步可用，于是「step0 引用 ${admin_token} → step2 才产出它」这种用例能过
        #    校验、运行时却必然 401。实测中最坑的一类正是它的极端形态——用例声称去拿
        #    admin_token，第一步却先要 admin_token 才能建号（鸡生蛋），无人产出。
        available = produced_so_far | _pre_hook_vars(case)
        for idx, req in enumerate(reqs):
            for var in sorted(_referenced_vars(req)):
                base = var.split(".")[0]
                if base in available:
                    continue
                later = base in _produced_vars(case)
                where = f"第 {idx + 1} 步" if len(reqs) > 1 else "本用例"
                if later:
                    msg = (
                        f"{where}引用了 ${{{var}}}，但它要到本用例后面的步骤才 extract 出来——"
                        "执行时按步骤顺序解析，这一步取不到值。请把产出它的步骤调到前面。"
                    )
                else:
                    msg = (
                        f"{where}引用的变量 ${{{var}}} 找不到来源：请补充能 extract 出它的"
                        "前置步骤/前置用例，或确认变量池里是否有该变量"
                    )
                warnings.append(msg)
                blocking.append(msg)      # 变量悬空 → 实测通过率 0
            ex = req.get("extract")
            if isinstance(ex, dict):
                # 本步骤产出的变量，从下一步起才可用
                available |= {str(k) for k in ex if str(k).strip()}

        # case 级清理在 finally 立即执行；被清理资源的 id/token 不能继续作为后续
        # 用例的有效依赖，否则静态校验会假绿、运行时必然引用脏变量。
        if not case.get("teardown_api") and not case.get("teardown_sql"):
            produced_so_far |= _produced_vars(case)
        if warnings:
            case["warnings"] = warnings
        # needs_fix 只由"执行必挂"类问题触发；缺断言、疑似缺鉴权头这类提示不影响勾选，
        # 因为它们未必真的失败（如接口本就公开），拦下来反而干扰用户。
        if blocking:
            case["needs_fix"] = True
            case["blocking_warnings"] = blocking
        else:
            case.pop("needs_fix", None)
            case.pop("blocking_warnings", None)
    return cases


def _auto_repair_flawed_cases(
    db,
    cfg,
    cases: list[dict],
    var_pool_keys: set[str],
    carried_vars: set[str],
    variable_pool_block: str = "",
    contract_block: str = "",
    project_id: int | None = None,
) -> list[dict]:
    """P0-2 自修回路：把 _harden_generated_cases 标了 warnings 的用例发回给模型修一轮。

    - 只修一轮；修完重新 harden 全量重算 warnings（顺序相关的变量校验必须全量重算）
    - 修复失败/仍有问题 → 保留原用例和 warnings，行为与没有本函数时一致（绝不丢用例，
      接口用例由人最终决定去留,与 M7 草稿"丢弃"策略不同——这里用例马上要入库,人看得到）
    - 修好的标 auto_repaired=true,前端可展示"已自动修复"
    """
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown

    flawed = [c for c in cases if c.get("warnings")]
    if not flawed:
        return cases

    try:
        flawed_json = json.dumps(
            [{"case": {k: v for k, v in c.items() if k != "warnings"},
              "errors": c["warnings"]} for c in flawed],
            ensure_ascii=False, indent=2, default=str,
        )
        repair_prompt = _render_prompt(
            _load_prompt("case_repair"),
            {
                "FLAWED_ITEMS_JSON": flawed_json[:20000],
                "REPAIR_CONTEXT": (
                    "可用变量池：\n" + (variable_pool_block or "（无）")
                    + "\n\n当前项目脚本库可用动态函数：\n"
                    + (_available_functions_block(db, project_id) if project_id else "（无）")
                    + "\n\n接口契约（method/path/参数位置/required/enum/security/响应模型均以此为准）：\n"
                    + (contract_block or "（未解析到结构化契约，缺信息时必须省略该条，禁止猜测）")
                ),
            },
        )
        raw, _tin, _tout = chat_markdown(
            repair_prompt, cfg, timeout=120,
            system_prompt=(
                "你是结构化 JSON 生成器。只输出一个合法 JSON 数组，"
                "不要输出任何代码块外文字。"
            ),
        )
        repaired = _shape_cases(_extract_json_list(raw, allow_salvage=False))
    except Exception:
        logger.warning("[ai_batch] 自修调用失败,保留原 warnings", exc_info=True)
        return cases

    if not repaired:
        return cases

    # 按 name 对应回原列表（repair prompt 要求不改 name）
    repaired_by_name = {_norm_name(c["name"]): c for c in repaired}
    merged: list[dict] = []
    replaced = 0
    for c in cases:
        key = _norm_name(c.get("name") or "")
        if c.get("warnings") and key in repaired_by_name:
            fixed = repaired_by_name[key]
            fixed["duplicate"] = c.get("duplicate", False)
            fixed["auto_repaired"] = True
            merged.append(fixed)
            replaced += 1
        else:
            merged.append(c)

    if not replaced:
        return cases

    # 全量重算 warnings（先清掉旧的,harden 只在有问题时才写 warnings）
    for c in merged:
        c.pop("warnings", None)
    merged = _harden_generated_cases(
        merged,
        var_pool_keys,
        carried_vars,
        project_id=project_id,
    )
    logger.info(
        "[ai_batch] 自修回路: %d 条有 warnings,模型修复 %d 条,修后仍有 warnings %d 条",
        len(flawed), replaced, sum(1 for c in merged if c.get("warnings")),
    )
    return merged


def _request_signature(req: dict) -> str:
    """请求签名：用于识别“同一个请求只拆了响应字段断言”的重复用例。"""
    method = str(req.get("method") or "").upper()
    path = str(req.get("path") or "")
    headers = req.get("headers") or {}
    return json.dumps(
        {
            "method": method,
            "path": path,
            "path_params": req.get("path_params") or {},
            "query_params": req.get("query_params") or {},
            "headers": headers,
            "json": req.get("json"),
            "form": req.get("form"),
            "body": req.get("body"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _is_response_check_case(case: dict) -> bool:
    name = str(case.get("name") or "")
    return "【响应校验】" in name or name.startswith("响应校验")


def _merge_response_check_cases(cases: list[dict]) -> list[dict]:
    """把同请求的响应校验用例合并进主流程用例，避免生成重复 API 用例。

    典型重复：
      - 【正常】登录成功（已断言 200/token/user）
      - 【响应校验】登录成功返回 token 字段非空
      - 【响应校验】登录成功返回用户角色信息

    后两条应成为第一条的 assertion/extract，而不是独立用例。
    """
    merged: list[dict] = []
    by_sig: dict[str, dict] = {}

    for case in cases:
        reqs = _case_requests(case)
        sig = _request_signature(reqs[0]) if len(reqs) == 1 else ""
        target = by_sig.get(sig) if sig else None
        if sig and target is not None and _is_response_check_case(case):
            if isinstance(case.get("assertion"), dict):
                base_assertion = target.setdefault("assertion", {})
                if isinstance(base_assertion, dict):
                    base_assertion.update(case["assertion"])
            if isinstance(case.get("extract"), dict):
                base_extract = target.setdefault("extract", {})
                if isinstance(base_extract, dict):
                    base_extract.update(case["extract"])
            extras = [x for x in (case.get("expected") or []) if x not in (target.get("expected") or [])]
            if extras:
                target["expected"] = [*(target.get("expected") or []), *extras]
            warnings = target.setdefault("warnings", [])
            if isinstance(warnings, list):
                warnings.append(f"已合并重复响应校验用例：{case.get('name')}")
            continue

        merged.append(case)
        if sig and not _is_response_check_case(case):
            by_sig.setdefault(sig, case)

    return merged


_COVERAGE_TEXT = {
    "standard": """
标准覆盖（少而关键）：
- 每个功能/接口只出 1 条主流程成功用例。
- 只选择最关键的 2-4 类风险补充异常点：必填缺失、核心字段格式错误、鉴权/权限、关键边界。
- 不要对每个字段穷举缺失/空值/类型/边界；非核心字段只在明显高风险时覆盖。
- 目标是快速冒烟 + 主要风险验证，数量应明显少于“全面”和“穷尽”。
""".strip(),
    "full": """
全面覆盖（推荐，系统覆盖）：
- 每个功能/接口都要覆盖主流程、核心异常、权限/鉴权、响应/数据正确性。
- 对每个必填字段至少覆盖缺失或为空之一；对每个核心字段覆盖格式/类型错误；有明确范围的字段覆盖边界。
- 写操作要覆盖创建/查询/更新/删除或状态流转链路，并规划查库/数据一致性校验。
- 不要求把同一字段的每一种非法值都拆开，但不能只写主流程。
""".strip(),
    "exhaustive": """
穷尽覆盖（最细，宁多勿漏）：
- 对每个功能/接口、每个参数、每个状态、每个角色逐维度展开。
- 每个必填字段分别生成：缺失、为空、null、类型错误、非法字符；有长度/数值/枚举范围的字段分别生成最小、最大、刚好、超界。
- 分页/排序/过滤、幂等、重复提交、安全注入、越权、状态流转、数据链路、查库校验都要拆成独立测试点。
- 响应状态码、关键响应字段、错误响应结构、列表/分页结构、敏感字段脱敏可拆成独立响应校验点。
- 端到端场景要保留，同时允许把关键中间状态、失败分支、回滚/清理链路拆成多个场景点。
- 文档缺少明确边界时，可以补充探索性边界/鲁棒性测试点，但不要虚构不存在的业务规则。
- 数量由「接口 × 参数 × 维度」的覆盖矩阵决定：先建矩阵再逐项输出，同样的输入重复生成时数量必须基本一致，不要为了简洁合并场景，也不要凭感觉多写或少写。
""".strip(),
}


def _coverage_text(c: str) -> str:
    return _COVERAGE_TEXT.get((c or "standard").strip().lower(), _COVERAGE_TEXT["standard"])


# 功能大纲每次输出的测试点数量上限（C 层兜底）：即使模型不听 prompt 的"宁缺毋滥"，
# 也不让单次把某模块灌到几百条。上限按覆盖力度放宽；超出部分从**尾部**裁剪——大纲按
# 执行依赖排序，正向/前置在前、探索性/边角在后，尾部正是最该砍的注水区。
_FUNCTIONAL_OUTLINE_CAP = {"standard": 30, "full": 70, "exhaustive": 130}


def _functional_outline_cap(coverage: str) -> int:
    return _FUNCTIONAL_OUTLINE_CAP.get((coverage or "standard").strip().lower(), 70)


_ALL_DIMENSIONS = ("正常", "参数校验", "边界", "鉴权", "越权", "响应校验", "安全", "场景", "关联")


def _dimensions_block(raw: str) -> str:
    """把用户勾选的维度清单渲染成 prompt 文本；留空=不限制（按覆盖力度自动取舍）。"""
    picked = [d.strip() for d in re.split(r"[\s,，;；]+", raw or "") if d.strip() and d.strip() in _ALL_DIMENSIONS]
    if not picked:
        return "（未指定，按覆盖力度自动取舍全部维度）"
    return "只规划以下维度的测试点，其它维度不要生成：" + "、".join(picked)


_RAW_MALFORMED_BODY_HINTS = (
    "畸形json", "畸形 json", "非法json", "非法 json", "截断json", "截断 json",
    "json语法错误", "json 语法错误", "原始畸形请求体", "raw body", "raw请求体",
)
_WRONG_METHOD_HINTS = (
    "错误方法", "错误 method", "不支持的方法", "不支持 method", "method not allowed",
    "返回405", "返回 405", "http 405",
)
_TRUE_CONCURRENCY_HINTS = (
    "真正并发", "并发请求", "并发登录", "并发刷新", "多线程", "竞态", "race condition",
    "压力测试", "负载测试", "压测",
)
_SEQUENTIAL_REWRITE_HINTS = ("顺序", "连续调用", "连续请求", "依次", "重复调用", "快速连续")


def _outline_normalized_path(path: str) -> str:
    """只归一化 OpenAPI 路径参数，避免把真实静态路径误当成同一路由。"""
    raw = str(path or "").split("?", 1)[0].rstrip("/") or "/"
    return re.sub(r"\{[^}/]+\}|\$\{[^}/]+\}", "{}", raw)


def _filter_interface_outline_points(
    points: list[dict[str, Any]],
    api_contract: dict[str, Any],
    available_variables: set[str] | None = None,
) -> list[dict[str, str]]:
    """在大纲阶段剔除平台无法可靠落成可执行脚本的测试点。"""
    operations = (api_contract or {}).get("operations") or []
    declared = {
        (str(operation.get("method") or "GET").upper(), str(operation.get("path") or ""))
        for operation in operations
        if isinstance(operation, dict) and operation.get("path")
    }
    normalized_paths = {
        (method, _outline_normalized_path(path))
        for method, path in declared
    }
    available = {str(name).strip().lower() for name in (available_variables or set())}
    has_api_key_value = bool(
        available & {"api_key", "apikey", "x_api_key", "x-api-key", "tap_api_key"}
    )
    method_path_pattern = re.compile(
        r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(https?://[^\s]+)?(/[^\s，。；]*)",
        re.I,
    )

    accepted: list[dict[str, str]] = []
    for point in points:
        title = str(point.get("title") or "").strip()
        if not title:
            continue
        lower = title.lower()
        compact = re.sub(r"\s+", "", lower)
        if any(hint in lower or hint in compact for hint in _RAW_MALFORMED_BODY_HINTS):
            continue
        if any(hint in lower or hint in compact for hint in _WRONG_METHOD_HINTS):
            continue
        if any(hint in lower for hint in _TRUE_CONCURRENCY_HINTS) and not any(
            hint in lower for hint in _SEQUENTIAL_REWRITE_HINTS
        ):
            continue
        if ("x-api-key" in lower or "x_api_key" in lower) and not has_api_key_value:
            continue
        explicit = method_path_pattern.search(title)
        if explicit and declared:
            method = explicit.group(1).upper()
            path = explicit.group(3).rstrip("/,:：") or "/"
            if (method, path) not in declared and (method, _outline_normalized_path(path)) not in normalized_paths:
                continue
        accepted.append({
            "title": title[:200],
            "category": str(point.get("category") or "").strip(),
        })
    return accepted


def _interface_outline_contract(requirement_text: str, coverage: str, dimensions: str = "") -> tuple[str, int, int]:
    """给接口大纲一个可复算的数量预算，避免穷尽模式完全交给模型自由发挥。

    注意：数量预算只有在**确实从文档里识别出接口**时才下发。识别失败时（人写的
    非结构化文档、正则没匹配到），如果仍按 endpoint_count=1 给出 9~13 条这种小预算，
    会把大文档的输出错误封顶——比不给预算更糟。此时退化为"按矩阵展开"的定性合同。
    """
    # 兼容两种形态：`- GET /api/users`（OpenAPI 摘要）和 `- POST https://host/api/login`（Postman raw url）
    endpoints = re.findall(
        r"(?im)^\s*[-*]?\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(?:https?://[^\s/；,，]+)?(/[^\s；,，?#]*)",
        requirement_text or "",
    )
    unique_endpoints = {(m.upper(), p.rstrip("/") or "/") for m, p in endpoints}
    if not unique_endpoints:
        # 没识别出接口 → 不下发数字预算，只强调"矩阵展开、不许偷懒合并"。
        text = (
            "\n\n# 稳定覆盖要求（必须遵守）\n"
            "- 先从文档里列出全部接口和参数，建立「接口 × 维度」覆盖矩阵，再逐项输出 points。\n"
            "- 同样的输入重复生成时，测试点数量必须基本一致（由矩阵决定，而不是随机取舍）。\n"
            "- 不得因为输出太长而提前停在主流程；不得把不同字段/不同维度的点合并成一条。"
        )
        return text, 0, 0
    endpoint_count = len(unique_endpoints)

    param_names: set[str] = set()
    for line in (requirement_text or "").splitlines():
        if not re.search(r"(?i)\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", line):
            continue
        tail = line.split("参数", 1)[-1] if "参数" in line else line
        for name in re.findall(r"([A-Za-z_][\w.-]*)\s*\(", tail):
            if name.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                param_names.add(name)
    param_count = len(param_names)

    picked = [d.strip() for d in re.split(r"[\s,，;；]+", dimensions or "") if d.strip() in _ALL_DIMENSIONS]
    dim_count = len(picked) if picked else len(_ALL_DIMENSIONS)
    level = (coverage or "standard").strip().lower()
    # 风险驱动预算：先保证每个接口的主流程/关键认证/契约明确的校验点，避免为了
    # 数量把未声明规则扩成上百条猜测用例。用户仍可选穷尽，但上限保持可探测、可评审。
    constrained_params = min(param_count, endpoint_count * 6)
    if level == "exhaustive":
        target_min = endpoint_count * min(max(dim_count, 4), 7) + constrained_params * 2
        target_max = endpoint_count * min(max(dim_count + 2, 6), 10) + constrained_params * 3
    elif level == "full":
        target_min = endpoint_count * min(max(dim_count, 3), 5) + constrained_params
        target_max = endpoint_count * min(max(dim_count + 1, 4), 7) + constrained_params * 2
    else:
        target_min = endpoint_count + max(1, endpoint_count // 2)
        target_max = endpoint_count * 3 + min(constrained_params, endpoint_count)
    target_min = max(2, min(target_min, 80))
    target_max = max(target_min, min(target_max, 120))
    text = (
        "\n\n# 稳定覆盖预算（必须遵守）\n"
        f"- 识别到接口数约 {endpoint_count} 个、参数数约 {param_count} 个。\n"
        f"- 本次应输出 {target_min}~{target_max} 个测试点；同一输入重复生成时数量必须落在这个区间，"
        "不要一次 10 条、一次 100 多条这种失控波动。\n"
        "- 先按接口和参数建立覆盖矩阵，再逐项输出 points；不得因为输出太长而提前停在主流程。"
    )
    return text, target_min, target_max


def _resolve_model(db, model_name: str, project_id: int):
    from server.services.ai_model_service import get_ai_model

    cfg = get_ai_model(db.session, model_name, project_id=project_id)
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"AI 模型 {model_name!r} 未配置，请先到「项目配置 → AI」添加")
    if not cfg.enabled:
        raise HTTPException(status_code=400, detail=f"AI 模型 {model_name!r} 未启用")
    return cfg


class AiGenerationHistoryUpdate(pydantic.BaseModel):
    """AI 用例生成历史快照。"""

    module_id: int
    draft: dict[str, Any]


def _generation_history_draft(run: AiRun) -> dict[str, Any]:
    """读取生成历史快照，并兼容只有大纲结果的旧记录。"""
    source = run.input_payload if isinstance(run.input_payload, dict) else {}
    output = run.output_payload if isinstance(run.output_payload, dict) else {}
    saved = output.get("draft")
    if isinstance(saved, dict):
        return {**saved, "generationRunId": run.id}

    points = output.get("points") if isinstance(output.get("points"), list) else []
    cases = output.get("cases") if isinstance(output.get("cases"), list) else []
    mode = str(source.get("mode") or (
        "interface" if run.feature == AI_FEATURE_API_CASE_GEN else "functional"
    ))
    saved_at = int(run.created_at.timestamp() * 1000) if run.created_at else 0
    return {
        "version": 1,
        "savedAt": saved_at,
        "text": "",
        "mode": mode,
        "coverage": source.get("coverage") or "standard",
        "docUrls": "",
        "setupDoc": "",
        "dimensions": [
            item.strip()
            for item in str(source.get("dimensions") or "").split(",")
            if item.strip()
        ],
        "smartInsert": False,
        "modelName": run.model or "",
        "gapModelName": run.model or "",
        "stage": "cases" if cases else "outline",
        "digest": output.get("digest") or "",
        "apiContract": output.get("api_contract") or {},
        "generationRunId": run.id,
        "points": points,
        "pickedPoints": list(range(len(points))),
        "genQueue": points,
        "cursor": len(points) if cases else 0,
        "failedBatches": [],
        "cases": cases,
        "picked": list(range(len(cases))),
        "writtenNames": output.get("written_names") or [],
    }


def _generation_history_summary(run: AiRun) -> dict[str, Any]:
    """生成历史列表所需的轻量摘要。"""
    source = run.input_payload if isinstance(run.input_payload, dict) else {}
    draft = _generation_history_draft(run)
    points = draft.get("points") if isinstance(draft.get("points"), list) else []
    cases = draft.get("cases") if isinstance(draft.get("cases"), list) else []
    written_names = (
        draft.get("writtenNames") if isinstance(draft.get("writtenNames"), list) else []
    )
    digest = str(draft.get("digest") or "").strip()
    return {
        "run_id": run.id,
        "module_id": source.get("module_id"),
        "mode": draft.get("mode") or source.get("mode") or "functional",
        "status": run.status,
        "model": run.model,
        "provider": run.provider,
        "operator": run.operator,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "digest": digest[:300],
        "point_count": len(points),
        "case_count": len(cases),
        "written_count": len(written_names),
        "stage": draft.get("stage") or "outline",
    }


def _get_generation_history_run(db: DBDep, run_id: int, module: Module) -> AiRun:
    """按模块读取一次大纲主记录，避免把批次子记录当成独立历史。"""
    run = (
        db.session.query(AiRun)
        .filter(AiRun.id == run_id, AiRun.project_id == module.project_id)
        .first()
    )
    source = run.input_payload if run and isinstance(run.input_payload, dict) else {}
    if (
        run is None
        or run.feature not in {AI_FEATURE_API_CASE_GEN, AI_FEATURE_FUNCTIONAL_CASE_GEN}
        or source.get("stage") != "outline"
        or int(source.get("module_id") or 0) != module.id
    ):
        raise HTTPException(status_code=404, detail="生成历史不存在")
    return run


@router.get("/ai_generation_history")
def list_ai_generation_history(
    db: DBDep,
    module_id: int = Query(...),
    mode: str = Query("functional"),
    limit: int = Query(50, ge=1, le=100),
):
    """查询模块下可恢复的 AI 用例生成历史，只返回每次生成的大纲主记录。"""
    module = db.session.query(Module).filter(Module.id == module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    if mode not in {"functional", "interface"}:
        raise HTTPException(status_code=422, detail="mode 只能是 functional 或 interface")
    feature = AI_FEATURE_API_CASE_GEN if mode == "interface" else AI_FEATURE_FUNCTIONAL_CASE_GEN
    candidates = (
        db.session.query(AiRun)
        .filter(AiRun.project_id == module.project_id, AiRun.feature == feature)
        .order_by(AiRun.created_at.desc(), AiRun.id.desc())
        .limit(500)
        .all()
    )
    rows = []
    for run in candidates:
        source = run.input_payload if isinstance(run.input_payload, dict) else {}
        if source.get("stage") != "outline" or int(source.get("module_id") or 0) != module_id:
            continue
        rows.append(_generation_history_summary(run))
        if len(rows) >= limit:
            break
    return {"status": "success", "data": rows}


@router.get("/ai_generation_history/{run_id}")
def get_ai_generation_history(run_id: int, module_id: int, db: DBDep):
    """查看某一次生成的完整大纲、详细用例和写入状态。"""
    module = db.session.query(Module).filter(Module.id == module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    run = _get_generation_history_run(db, run_id, module)
    return {
        "status": "success",
        "data": {**_generation_history_summary(run), "draft": _generation_history_draft(run)},
    }


@router.put("/ai_generation_history/{run_id}")
def save_ai_generation_history(
    run_id: int,
    payload: AiGenerationHistoryUpdate,
    db: DBDep,
):
    """持久化前端审阅态，浏览器缓存只作为断网时的快速恢复副本。"""
    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    run = _get_generation_history_run(db, run_id, module)
    mode = "interface" if run.feature == AI_FEATURE_API_CASE_GEN else "functional"
    draft = {
        **payload.draft,
        "version": 1,
        "mode": mode,
        "generationRunId": run.id,
        "savedAt": int(datetime.now().timestamp() * 1000),
    }
    output = dict(run.output_payload or {})
    output["draft"] = draft
    output["digest"] = draft.get("digest") or output.get("digest") or ""
    output["points"] = draft.get("points") or []
    output["cases"] = draft.get("cases") or []
    output["written_names"] = draft.get("writtenNames") or []
    run.output_payload = output
    db.session.flush()
    return {"status": "success", "data": _generation_history_summary(run)}


@router.post("/ai_generate_outline")
def ai_generate_outline(
    db: DBDep,
    module_id: int = Form(...),
    model_name: str = Form(...),
    text: str = Form(""),
    mode: str = Form("functional"),
    coverage: str = Form("standard"),
    doc_urls: str = Form(""),
    dimensions: str = Form(""),
    setup_doc: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    docs: list[UploadFile] = File(default=[]),
    user: OptionalUserDep = None,
):
    """第一步：通读需求（文本 + 截图/原型图 + PDF/Word/接口文档）→ 输出测试点大纲 + 摘要 digest。
    mode=functional → 功能用例；mode=interface → 接口用例。
    digest 供后续分批生成复用，保证多批之间连贯。图片走 vision，不支持则 OCR 回退。"""
    import tempfile

    from ai_gateway.gateway import (
        _load_prompt,
        _render_prompt,
        chat_markdown,
        chat_markdown_with_images,
        model_task_options,
    )

    module = db.session.query(Module).filter(Module.id == module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    cfg = _resolve_model(db, model_name, module.project_id)

    has_images = bool(images) and any((im.filename or "") for im in images)
    use_vision = bool(cfg.supports_vision and has_images)

    with tempfile.TemporaryDirectory(prefix="ai_outline_") as tmpdir:
        from server.services.api_case_contract import merge_contract_catalogs

        image_paths, text_chunks, contract_catalogs = _ingest_uploads(
            images,
            docs,
            tmpdir,
            use_vision=use_vision,
        )
        parts: list[str] = []
        if (text or "").strip():
            parts.append((text or "").strip())
        parts.extend(text_chunks)
        # 接口文档链接：接口模式同时扫描专用字段、说明文本和账号准备文本。用户经常
        # 直接把 Swagger 链接贴在大文本框里，不能因此悄悄生成一个空契约。
        document_urls = _extract_doc_urls(
            doc_urls,
            text if mode == "interface" else "",
            setup_doc if mode == "interface" else "",
        )
        for u in document_urls:
            fetched = _fetch_doc_url(u)
            if fetched:
                parts.append(f"## 接口文档（链接）：{u}\n{fetched}")
            if mode == "interface":
                catalog = _fetch_openapi_catalog_url(u)
                if catalog and catalog.get("operations"):
                    contract_catalogs.append(catalog)
        if use_vision and image_paths:
            parts.append(f"（另附 {len(image_paths)} 张界面/原型截图，请结合图片内容规划测试点）")
        if setup_doc.strip():
            parts.append(
                "## 账号准备/注册接口（供前置链跨模块建一次性测试账号用，请写进 digest）：\n"
                + setup_doc.strip()[:2000]
            )
        requirement_text = "\n\n".join(parts) or "（未提供需求文本，请基于模块名与下方跨模块信息合理推断）"
        api_contract = merge_contract_catalogs(contract_catalogs)
        if mode == "interface":
            api_contract = _enrich_api_contract_from_project_rules(
                db,
                module.project_id,
                api_contract,
            )
            if document_urls and not api_contract.get("operations"):
                logger.warning(
                    "[api-contract] module=%s 提供 %d 个文档 URL，但未解析出 operation",
                    module_id,
                    len(document_urls),
                )
        coverage_contract, target_min, target_max = (
            _interface_outline_contract(requirement_text, coverage, dimensions)
            if mode == "interface"
            else ("", 0, 0)
        )

        existing = _existing_case_names(
            db,
            module_id,
            case_type=CASE_TYPE_API if mode == "interface" else CASE_TYPE_FUNCTIONAL,
        )
        existing_block = "、".join(existing) if existing else "（本模块暂无已有用例）"
        placeholders = {
            "MODULE_NAME": module.name,
            "REQUIREMENT_TEXT": requirement_text,
            "PROJECT_CONTEXT": "\n\n".join([
                _project_api_contract_rules_block(db, module.project_id, api_contract),
                _project_context_block(module.project_id, f"{module.name}\n{requirement_text}"),
            ]),
            "CROSS_MODULE_CONTEXT": _build_cross_module_context(db, module),
            "EXISTING_CASES": existing_block,
            "VARIABLE_POOL": _variable_pool_block(db, module.project_id) if mode == "interface" else "",
            "COVERAGE_LEVEL": _coverage_text(coverage) + coverage_contract,
            "DIMENSIONS": _dimensions_block(dimensions),
        }
        template = _load_prompt(
            "interface_case_outline" if mode == "interface" else "functional_case_outline"
        )
        prompt = _render_prompt(template, placeholders)
        call_options = model_task_options(cfg, "api_outline" if mode == "interface" else "functional_outline")
        outline_system_prompt = (
            "你是结构化 JSON 生成器。必须只输出一个合法 JSON 对象，"
            "对象必须包含 digest 字符串和 points 数组。"
            "不要输出 Markdown、解释、思考过程或代码块外文字。"
        )

        try:
            if use_vision and image_paths:
                raw, _tin, _tout = chat_markdown_with_images(prompt, image_paths, cfg, timeout=240)
            else:
                raw, _tin, _tout = chat_markdown(
                    prompt,
                    cfg,
                    timeout=call_options["timeout"],
                    system_prompt=outline_system_prompt,
                    enable_thinking=call_options["enable_thinking"],
                    json_mode=call_options["json_mode"],
                    max_tokens=call_options["max_tokens"],
                    temperature=call_options["temperature"],
                    reasoning_effort=call_options.get("reasoning_effort"),
                )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"AI 调用失败：{e}")

    # 解析 {digest, points}
    obj = None
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            break
        except Exception:
            obj = None
    if not isinstance(obj, dict):
        # 退一步：截取第一个 {...}
        s, e = raw.find("{"), raw.rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads(raw[s : e + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        logger.warning(
            "ai_generate_outline parse failed module_id=%s mode=%s model=%s raw_prefix=%r",
            module_id,
            mode,
            model_name,
            (raw or "")[:1000],
        )
        raise HTTPException(status_code=502, detail="大纲解析失败，请重试或更换模型")

    digest = str(obj.get("digest") or "").strip()
    points = []
    for p in obj.get("points") or []:
        if isinstance(p, dict):
            title = str(p.get("title") or "").strip()
            if title:
                points.append({"title": title[:200], "category": str(p.get("category") or "").strip()})
        elif isinstance(p, str) and p.strip():
            points.append({"title": p.strip()[:200], "category": ""})
    if mode == "interface":
        points = _filter_interface_outline_points(
            points,
            api_contract,
            _variable_pool_keys(db, module.project_id),
        )
    if not points:
        raise HTTPException(status_code=502, detail="未规划出测试点，请补充需求或更换模型")

    if mode == "interface" and target_min and len(points) < target_min:
        gap_points = _run_outline_gap_ai(
            db,
            cfg,
            module,
            digest=digest,
            existing_points=points,
            mode=mode,
            contract=f"当前只有 {len(points)} 个测试点，低于稳定覆盖预算下限 {target_min}。请只补缺口，补到 {target_min}~{target_max} 区间。",
            requirement_text=requirement_text,
        ) or []  # 自动补齐失败不阻断首轮大纲
        have = {_norm_name(p["title"]) for p in points}
        for p in gap_points:
            key = _norm_name(p.get("title") or "")
            if key and key not in have:
                have.add(key)
                points.append(p)
            if len(points) >= target_min:
                break
        points = _filter_interface_outline_points(
            points,
            api_contract,
            _variable_pool_keys(db, module.project_id),
        )
    if mode == "interface" and target_max and len(points) > target_max:
        logger.info(
            "[api-outline] module=%s coverage=%s 输出 %d 条，按稳定预算上限裁剪为 %d 条",
            module_id,
            coverage,
            len(points),
            target_max,
        )
        points = points[:target_max]

    # 功能大纲「接地过滤」：把系统没有的能力 / 运维基建 / 合规 / 过程审计等越界测试点
    # 在大纲阶段就剔除（B 层），再按覆盖力度上限兜底裁剪（C 层）。接口模式另有自己的
    # _filter_interface_outline_points，不走这里。
    scope_filter: dict[str, Any] | None = None
    if mode == "functional":
        # B-1 关键词 + B-2 LLM 二判 + 去重（与查缺补漏共用同一套，见 _apply_functional_scope_filter）
        points, scope_filter = _apply_functional_scope_filter(
            db, cfg, module, points, requirement_text
        )
        # C 覆盖力度上限兜底（尾部裁剪）——只在首轮 outline 对单次总量兜底
        cap = _functional_outline_cap(coverage)
        cap_dropped = []
        if len(points) > cap:
            cap_dropped = points[cap:]
            points = points[:cap]
        scope_filter["cap"] = cap
        scope_filter["cap_dropped"] = [d.get("title") for d in cap_dropped]
        scope_filter["kept"] = len(points)
        if cap_dropped:
            logger.info(
                "[functional-scope] module=%s coverage=%s 超上限尾裁 %d 条(cap=%d)",
                module_id, coverage, len(cap_dropped), cap,
            )
        if not points:
            raise HTTPException(
                status_code=502,
                detail="规划出的测试点均判定为越界（系统无对应能力），请调整需求描述或覆盖力度后重试",
            )

    # 注意：这里**不再**自动把规划出的测试点落库 —— 否则每点一次“生成大纲”
    # 都会往大纲里灌一批还没有对应用例的 gap 点，积累成垃圾。大纲只保留“同步过的”
    # 数据：由「刷新对齐」按真实用例建立/更新，或用户在生成用例后由关联落库。
    image_strategy = "vision" if use_vision else ("ocr" if has_images else "none")
    run = AiRun(
        feature=AI_FEATURE_API_CASE_GEN if mode == "interface" else AI_FEATURE_FUNCTIONAL_CASE_GEN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=module.project_id,
        input_payload={
            "module_id": module_id,
            "mode": mode,
            "coverage": coverage,
            "dimensions": dimensions,
            "contract_hash": api_contract.get("hash"),
            "operation_count": len(api_contract.get("operations") or []),
            "stage": "outline",
        },
        output_payload={
            "digest": digest,
            "points": points,
            "api_contract": api_contract if mode == "interface" else None,
            "scope_filter": scope_filter,
        },
        provider=cfg.provider,
        model=cfg.model,
        tokens_in=_tin,
        tokens_out=_tout,
        prompt_version="if_contract_out_v2" if mode == "interface" else "functional_out_v1",
        operator=_operator_name(user),
        started_at=datetime.now(),
        ended_at=datetime.now(),
    )
    db.session.add(run)
    db.session.flush()
    return {
        "status": "success",
        "data": {
            "digest": digest,
            "points": points,
            "model": model_name,
            "image_strategy": image_strategy,
            "api_contract": api_contract,
            "generation_run_id": run.id,
            "scope_filter": scope_filter,
        },
    }


class BatchPoint(pydantic.BaseModel):
    title: str
    category: str = ""


class OutlineGapRequest(pydantic.BaseModel):
    module_id: int
    model_name: str
    mode: str = "functional"
    digest: str = ""
    points: list[BatchPoint] = []
    # 原始需求/接口文档（生成大纲时用户填的 text）。查漏只靠 digest 会信息不对称：
    # digest 是压缩摘要，字段级约束经常丢失，模型拿不到新材料自然"找不到漏"。
    text: str = ""
    # 接口文档链接，服务端按大纲同款逻辑拉取解析后注入
    doc_urls: str = ""
    api_contract: dict[str, Any] = pydantic.Field(default_factory=dict)


def _parse_outline_gap_response(raw: str) -> list[dict[str, str]]:
    obj = None
    m = re.search(r"```json\s*(.+?)\s*```", raw or "", re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            break
        except Exception:
            obj = None
    if not isinstance(obj, dict):
        s, e = (raw or "").find("{"), (raw or "").rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads((raw or "")[s : e + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict) or not isinstance(obj.get("points"), list):
        # None = 解析失败（与"模型确认没有遗漏、返回空数组"是两回事）。
        # 以前这里静默返回 []，前端 toast"没找到遗漏，大纲已比较全面"，把故障伪装成结论。
        return None
    points = []
    for p in obj.get("points") or []:
        if isinstance(p, dict):
            title = str(p.get("title") or "").strip()
            if title:
                points.append({"title": title[:200], "category": str(p.get("category") or "").strip()})
    return points


def _run_outline_gap_ai(
    db,
    cfg,
    module: "Module",
    *,
    digest: str,
    existing_points: list[dict[str, Any]],
    mode: str,
    contract: str = "",
    requirement_text: str = "",
) -> list[dict[str, str]] | None:
    """查漏补缺共用调用；让首次大纲过少时也能自动补齐一轮。

    返回 None 表示模型输出解析失败（调用方应报错/忽略，而不是当成"没有遗漏"）。
    requirement_text: 原始需求/接口文档。查漏必须拿到和生成大纲时同级的材料，
    只靠 digest 找不出字段级遗漏。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options

    call_options = model_task_options(cfg, "api_outline_gap")
    existing_block = (
        "\n".join(f"- [{p.get('category') or '未分类'}] {p.get('title')}" for p in existing_points)
        if existing_points
        else "（暂无）"
    )
    existing = _existing_case_names(
        db,
        module.id,
        case_type=CASE_TYPE_API if mode == "interface" else CASE_TYPE_FUNCTIONAL,
    )
    placeholders = {
        "REQUIREMENT_TEXT": (requirement_text or "").strip() or "（未提供原始文档，只能依据下方摘要排查）",
        "DIGEST": (digest.strip() or "（无摘要，请按已规划测试点合理推断）") + (f"\n\n{contract}" if contract else ""),
        "CROSS_MODULE_CONTEXT": _build_cross_module_context(db, module),
        "EXISTING_POINTS": existing_block,
        "EXISTING_CASES": "、".join(existing) if existing else "（无）",
    }
    template = _load_prompt("outline_gaps")
    prompt = _render_prompt(template, placeholders)
    raw, _tin, _tout = chat_markdown(
        prompt,
        cfg,
        timeout=call_options["timeout"],
        system_prompt="你是结构化 JSON 生成器。必须只输出一个合法 JSON 对象，包含 points 数组。",
        enable_thinking=call_options["enable_thinking"],
        json_mode=call_options["json_mode"],
        max_tokens=call_options["max_tokens"],
        temperature=call_options["temperature"],
        reasoning_effort=call_options.get("reasoning_effort"),
    )
    points = _parse_outline_gap_response(raw)
    if points is None:
        logger.warning(
            "outline_gaps parse failed module_id=%s mode=%s raw_prefix=%r",
            module.id, mode, (raw or "")[:500],
        )
    return points


def _run_functional_scope_judge(
    cfg,
    module: "Module",
    points: list[dict[str, Any]],
    capability_context: str,
) -> set[int]:
    """LLM 相关性二判（B 层第二道）：给定本系统真实能力，挑出**越界**测试点。

    返回要剔除的 1-based 序号集合（相对传入的 points 列表）。
    调用失败 / 解析失败 → 返回空集合（**不误删**，把不确定留给人工）。
    """
    if not points:
        return set()
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options

    points_list = "\n".join(
        f"{i + 1}. [{p.get('category') or '未分类'}] {p.get('title')}"
        for i, p in enumerate(points)
    )
    placeholders = {
        "MODULE_NAME": module.name,
        "CAPABILITY_CONTEXT": (capability_context or "").strip()
        or "（未提供额外能力上下文，只能依据测试点本身与模块名判断，倾向保留）",
        "POINTS_LIST": points_list,
    }
    template = _load_prompt("functional_scope_judge")
    prompt = _render_prompt(template, placeholders)
    call_options = model_task_options(cfg, "functional_outline")
    try:
        raw, _tin, _tout = chat_markdown(
            prompt,
            cfg,
            timeout=call_options["timeout"],
            system_prompt=(
                "你是结构化 JSON 生成器。必须只输出一个合法 JSON 对象，"
                "包含 out_of_scope 数组。"
            ),
            enable_thinking=call_options["enable_thinking"],
            json_mode=call_options["json_mode"],
            max_tokens=call_options["max_tokens"],
            temperature=call_options["temperature"],
            reasoning_effort=call_options.get("reasoning_effort"),
        )
    except Exception:  # noqa: BLE001 — 二判失败不阻断主流程
        logger.warning("[functional-scope-judge] LLM 调用失败，跳过二判", exc_info=True)
        return set()

    obj = None
    m = re.search(r"```json\s*(.+?)\s*```", raw or "", re.S)
    for cand in ([m.group(1)] if m else []) + [raw or ""]:
        try:
            obj = json.loads(cand)
            break
        except Exception:
            obj = None
    if not isinstance(obj, dict):
        s, e = (raw or "").find("{"), (raw or "").rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads((raw or "")[s : e + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict) or not isinstance(obj.get("out_of_scope"), list):
        logger.warning(
            "[functional-scope-judge] 解析失败 module_id=%s raw_prefix=%r",
            module.id, (raw or "")[:500],
        )
        return set()

    out: set[int] = set()
    for entry in obj.get("out_of_scope") or []:
        idx = entry.get("index") if isinstance(entry, dict) else entry
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(points):
            out.add(i)
    return out


def _apply_functional_scope_filter(
    db,
    cfg,
    module: "Module",
    points: list[dict[str, Any]],
    requirement_text: str,
    *,
    run_llm_judge: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """功能大纲测试点的接地过滤核心：B-1 关键词+白名单 → B-2 LLM 二判 → 去重。

    首轮 ai_generate_outline 与查缺补漏 ai_outline_gaps(_cli) 共用这一套，杜绝任何
    一条路径绕过过滤把注水/越界点灌回来。**不含**覆盖上限裁剪——那是首轮 outline 对
    "单次总量"的兜底，补漏是增量补充，由调用方自行决定是否再限量。

    run_llm_judge=False 用于 CLI Agent 路径（provider 不是 chat_markdown 模型，跑二判
    没意义）——此时只做 B-1 + 去重。
    """
    from server.services.functional_scope_filter import (
        dedup_points,
        filter_out_of_scope_points,
    )

    project_ctx = _project_context_block(
        module.project_id, f"{module.name}\n{requirement_text}"
    )
    capability_context = "\n".join(filter(None, [project_ctx, requirement_text]))
    before_n = len(points)

    points, kw_dropped = filter_out_of_scope_points(points, project_ctx, requirement_text)

    llm_dropped: list[dict] = []
    if run_llm_judge:
        judge_out = _run_functional_scope_judge(cfg, module, points, capability_context)
        llm_dropped = [p for i, p in enumerate(points) if (i + 1) in judge_out]
        points = [p for i, p in enumerate(points) if (i + 1) not in judge_out]

    points, dup_dropped = dedup_points(points)

    stats = {
        "before": before_n,
        "kept": len(points),
        "keyword_dropped": [d.get("title") for d in kw_dropped],
        "llm_dropped": [d.get("title") for d in llm_dropped],
        "dup_dropped": [d.get("title") for d in dup_dropped],
    }
    if kw_dropped or llm_dropped or dup_dropped:
        logger.info(
            "[functional-scope] module=%s %d→%d 剔除(关键词=%d LLM=%d 重复=%d)",
            module.id, before_n, len(points),
            len(kw_dropped), len(llm_dropped), len(dup_dropped),
        )
    return points, stats


def _outline_gap_requirement_text(payload: OutlineGapRequest) -> str:
    """重建查漏所需的原始需求材料。"""
    req_parts: list[str] = []
    if (payload.text or "").strip():
        req_parts.append(payload.text.strip())
    for u in re.split(r"[\s,，;；]+", payload.doc_urls or ""):
        u = u.strip()
        if not u:
            continue
        try:
            fetched = _fetch_doc_url(u)
        except Exception:  # noqa: BLE001 — 查漏时文档拉取失败不阻断，退化为 digest
            fetched = ""
        if fetched:
            req_parts.append(f"## 接口文档（链接）：{u}\n{fetched}")
    return "\n\n".join(req_parts)


def _dedupe_gap_points(
    raw_points: list[dict[str, Any]],
    *,
    payload: OutlineGapRequest,
    existing_case_names: list[str],
) -> list[dict[str, str]]:
    """过滤已存在的大纲点和用例名。"""
    have = {_norm_name(p.title) for p in payload.points} | {_norm_name(n) for n in existing_case_names}
    points = []
    for p in raw_points:
        title = str(p.get("title") or "").strip()
        if title and _norm_name(title) not in have:
            have.add(_norm_name(title))
            points.append({"title": title[:200], "category": str(p.get("category") or "").strip()})
    return points


@router.post("/ai_outline_gaps")
def ai_outline_gaps(payload: OutlineGapRequest, db: DBDep):
    """查漏补缺：给已有大纲找遗漏的测试点，返回补充点（已去重已有点/已有用例）。"""
    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    cfg = _resolve_model(db, payload.model_name, module.project_id)

    existing_points = [{"title": p.title, "category": p.category} for p in payload.points]
    existing = _existing_case_names(
        db,
        payload.module_id,
        case_type=CASE_TYPE_API if payload.mode == "interface" else CASE_TYPE_FUNCTIONAL,
    )
    requirement_text = _outline_gap_requirement_text(payload)
    try:
        points_raw = _run_outline_gap_ai(
            db,
            cfg,
            module,
            digest=payload.digest,
            existing_points=existing_points,
            mode=payload.mode,
            contract="请按接口/参数/鉴权/越权/响应校验/数据链路/安全/场景逐项核对，只输出真正缺失的点。",
            requirement_text=requirement_text,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 调用失败：{e}")
    if points_raw is None:
        raise HTTPException(status_code=502, detail="查漏结果解析失败，请重试或更换模型（不代表没有遗漏）")

    points = _dedupe_gap_points(points_raw, payload=payload, existing_case_names=existing)
    if payload.mode == "interface":
        points = _filter_interface_outline_points(
            points,
            payload.api_contract,
            _variable_pool_keys(db, module.project_id),
        )
    elif payload.mode == "functional":
        # 查缺补漏也必须过四层，否则注水会从这个口子灌回来
        points, _ = _apply_functional_scope_filter(
            db, cfg, module, points, requirement_text
        )
    return {"status": "success", "data": {"points": points}}


@router.post("/ai_outline_gaps_cli")
def ai_outline_gaps_cli(payload: OutlineGapRequest, db: DBDep, user: OptionalUserDep = None):
    """CLI Agent 查漏补缺：用 Codex / Claude Code 审查当前大纲并补测试点。"""
    from server.services.ai_model_service import get_ai_model
    from server.services.cli_case_enhancement_service import (
        build_outline_gap_prompt,
        is_cli_case_provider,
        run_cli_case_enhancement,
    )

    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")

    cfg = get_ai_model(db.session, payload.model_name, project_id=module.project_id)
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"CLI Agent {payload.model_name!r} 不存在")
    if not cfg.enabled:
        raise HTTPException(status_code=400, detail=f"CLI Agent {payload.model_name!r} 未启用")
    if not is_cli_case_provider(cfg.provider):
        raise HTTPException(status_code=400, detail="CLI 查漏请选择 Codex CLI 或 Claude Code 类型的 AI 配置")

    existing_points = [{"title": p.title, "category": p.category} for p in payload.points]
    existing = _existing_case_names(
        db,
        payload.module_id,
        case_type=CASE_TYPE_API if payload.mode == "interface" else CASE_TYPE_FUNCTIONAL,
    )
    prompt = build_outline_gap_prompt(
        module_name=module.name,
        mode=payload.mode,
        digest=payload.digest,
        requirement_text=_outline_gap_requirement_text(payload),
        existing_points=existing_points,
        existing_case_names=existing,
        target_extra_count=max(5, min(len(payload.points) // 3, 40)),
    )

    run = AiRun(
        feature=AI_FEATURE_FUNCTIONAL_CASE_ENHANCE,
        status=AI_RUN_STATUS_PENDING,
        project_id=module.project_id,
        input_payload={
            "module_id": payload.module_id,
            "agent_model_name": cfg.name,
            "mode": payload.mode,
            "point_count": len(payload.points),
            "action": "outline_gaps_cli",
        },
        operator=_operator_name(user),
        provider=cfg.provider,
        model=cfg.model,
    )
    db.session.add(run)
    db.session.flush()

    run.status = AI_RUN_STATUS_RUNNING
    run.started_at = datetime.now()
    try:
        result = run_cli_case_enhancement(
            cfg=cfg,
            prompt=prompt,
            timeout=int((cfg.extra or {}).get("timeout_seconds") or 900),
        )
        parsed = result["parsed"]
        raw_points = parsed.get("points") if isinstance(parsed.get("points"), list) else []
        points = _dedupe_gap_points(raw_points, payload=payload, existing_case_names=existing)
        if payload.mode == "interface":
            points = _filter_interface_outline_points(
                points,
                payload.api_contract,
                _variable_pool_keys(db, module.project_id),
            )
        elif payload.mode == "functional":
            # CLI Agent 补漏也过接地过滤；CLI provider 不走 chat_markdown，跳过 LLM 二判
            points, _ = _apply_functional_scope_filter(
                db, cfg, module, points,
                _outline_gap_requirement_text(payload),
                run_llm_judge=False,
            )
        output = {
            "points": points,
            "agent_model_name": cfg.name,
            "summary": str(parsed.get("summary") or "").strip(),
        }
        run.output_payload = output
        run.prompt_hash = result["prompt_hash"]
        run.prompt_version = "cli_outline_gap_v1"
        run.status = AI_RUN_STATUS_SUCCESS
        run.ended_at = datetime.now()
        return {"status": "success", "data": {**output, "run_id": run.id}}
    except Exception as exc:  # noqa: BLE001
        run.status = AI_RUN_STATUS_FAILED
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.ended_at = datetime.now()
        return {
            "status": "error",
            "message": f"CLI 查漏失败：{exc}",
            "data": {"run_id": run.id},
        }


class AiBatchRequest(pydantic.BaseModel):
    module_id: int
    model_name: str
    digest: str = ""
    points: list[BatchPoint]
    done_names: list[str] = pydantic.Field(default_factory=list)
    # 已生成用例的结构化依赖摘要，不能只靠名称猜跨批变量/清理关系。
    done_cases: list[dict[str, Any]] = pydantic.Field(default_factory=list)
    mode: str = "functional"
    # 跨批次已产出的变量名（前端把前面批次 extract 出的变量累积传过来，避免误报"找不到来源"）
    carried_vars: list[str] = pydantic.Field(default_factory=list)
    # 用户直接提供的"账号准备/注册"接口信息（文本），供前置链跨模块建账号用
    setup_doc: str = ""
    # 兜底重建契约：前端草稿丢失 api_contract 时仍可从原始文档链接恢复。
    doc_urls: str = ""
    # 大纲阶段解析的完整 OpenAPI 紧凑契约，贯穿编译、硬校验、探测和入库。
    api_contract: dict[str, Any] = pydantic.Field(default_factory=dict)
    generation_run_id: int | None = None


class AiCaseEnhanceRequest(pydantic.BaseModel):
    module_id: int
    agent_model_name: str
    digest: str = ""
    requirement_text: str = ""
    cases: list[dict[str, Any]]
    mode: str = "functional"
    target_extra_count: int = 5
    api_contract: dict[str, Any] = pydantic.Field(default_factory=dict)
    generation_run_id: int | None = None


class AiCaseRevalidateRequest(pydantic.BaseModel):
    module_id: int
    cases: list[dict[str, Any]]
    api_contract: dict[str, Any] = pydantic.Field(default_factory=dict)
    generation_run_id: int | None = None


def _revalidate_interface_cases(
    db,
    module: Module,
    cases: list[dict[str, Any]],
    api_contract: dict[str, Any],
    *,
    generation_run_id: int | None,
    provider: str = "deterministic",
    model: str = "contract-compiler",
    prompt_version: str = "contract_revalidate_v2",
) -> list[dict[str, Any]]:
    """用当前契约规则重编译整批草稿，旧红标不能直接沿用或绕过。"""
    from copy import deepcopy

    from server.services.api_case_contract import compile_generated_case
    from server.services.generation_probe_refine import validate_isolation

    def messages(values: Any) -> list[str]:
        if not isinstance(values, list):
            values = [values] if values not in (None, "") else []
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    def mark_internal_error(case: dict[str, Any], exc: Exception) -> None:
        message = f"重新校验异常：{type(exc).__name__}: {exc}"
        case["needs_fix"] = True
        case["warnings"] = [message]
        case["blocking_warnings"] = [message]
        case["compiled_case"] = {
            "module_id": module.id,
            "name": str(case.get("name") or "AI 接口用例")[:200],
            "case_type": "api",
            "source": "ai_interface",
            "steps": [],
            "generation_metadata": {
                "generator": "interface_contract_compiler",
                "compiler_version": "2.1",
                "contract_hash": api_contract.get("hash"),
                "generation_run_id": generation_run_id,
                "model": model,
                "provider": provider,
                "prompt_version": prompt_version,
                "preflight": {"passed": False, "errors": [message], "warnings": []},
            },
        }

    prepared = deepcopy(cases)
    for case in prepared:
        for key in ("compiled_case", "needs_fix", "blocking_warnings", "warnings"):
            case.pop(key, None)
    var_pool_keys = _variable_pool_keys(db, module.project_id)
    carried = _module_produced_vars(db, module.id)
    available = set(carried)
    for case in prepared:
        try:
            # 单条加固、单条编译：某一条历史草稿字段畸形时只把该条标红，不能让
            # 168 条整批请求一起 500。
            _harden_generated_cases(
                [case],
                var_pool_keys,
                available,
                project_id=module.project_id,
            )
            harden_blocking = messages(case.get("blocking_warnings"))
            harden_warnings = messages(case.get("warnings"))
            compiled, compile_issues = compile_generated_case(
                case,
                module.id,
                api_contract,
                generation_metadata={
                    "generation_run_id": generation_run_id,
                    "model": model,
                    "provider": provider,
                    "prompt_version": prompt_version,
                    "available_variables": sorted(var_pool_keys | available),
                    "persistent_variables": sorted(var_pool_keys),
                    "carried_variables": sorted(available),
                },
            )
            # 隔离校验必须读取契约编译后的结构化步骤；草稿自身的 steps 是给人看的
            # 字符串列表，不能当作 {config, assertion} HTTP 步骤处理。
            case["compiled_case"] = compiled
            isolation_errors = validate_isolation(case)
        except Exception as exc:  # noqa: BLE001
            logger.exception("接口草稿重新校验失败 case=%r", case.get("name"))
            mark_internal_error(case, exc)
            continue
        blocking = messages([
            *harden_blocking,
            *(compiled.get("generation_metadata", {}).get("preflight", {}).get("errors") or []),
            *isolation_errors,
        ])
        warnings = messages([*harden_warnings, *compile_issues, *isolation_errors])
        preflight = compiled.setdefault("generation_metadata", {}).setdefault("preflight", {})
        preflight["errors"] = blocking
        preflight["warnings"] = [warning for warning in warnings if warning not in blocking]
        preflight["passed"] = not blocking
        if warnings:
            case["warnings"] = warnings
        if blocking:
            case["needs_fix"] = True
            case["blocking_warnings"] = blocking
        else:
            case.pop("needs_fix", None)
            case.pop("blocking_warnings", None)
            if not case.get("teardown_api") and not case.get("teardown_sql"):
                available.update(_produced_vars(case))
    return prepared


@router.post("/ai_revalidate_cases")
def ai_revalidate_cases(payload: AiCaseRevalidateRequest, db: DBDep):
    """不调用模型，按当前 OpenAPI 契约重新校验整批草稿。"""
    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    if not (payload.api_contract.get("operations") or []):
        raise HTTPException(status_code=422, detail="重新校验前必须先读取结构化 OpenAPI 契约")
    cases = _revalidate_interface_cases(
        db,
        module,
        payload.cases,
        payload.api_contract,
        generation_run_id=payload.generation_run_id,
    )
    writable = sum(1 for case in cases if not case.get("needs_fix") and not case.get("duplicate"))
    return {
        "status": "success",
        "data": {
            "cases": cases,
            "total": len(cases),
            "writable": writable,
            "blocked": len(cases) - writable,
        },
    }


@router.get("/ai_generation_quality")
def ai_generation_quality(
    db: DBDep,
    project_id: int = Query(...),
):
    """返回 AI 接口用例的契约门禁、探测和首轮真实执行质量。

    只统计 ``source=ai_interface``，避免人工用例混入后让通过率看起来虚高。首轮通过
    以该用例最早出现的 report 为准；最新通过用于观察修复后的最终效果。
    """
    cases = (
        db.session.query(TestCase)
        .join(Module, Module.id == TestCase.module_id)
        .filter(Module.project_id == project_id, TestCase.source == "ai_interface")
        .all()
    )
    case_ids = [case.id for case in cases]
    reports_by_case: dict[int, dict[int, list[str]]] = {}
    if case_ids:
        rows = (
            db.session.query(
                TestStepReport.case_id,
                TestStepReport.report_id,
                TestStepReport.status,
            )
            .filter(TestStepReport.case_id.in_(case_ids))
            .order_by(TestStepReport.report_id.asc(), TestStepReport.id.asc())
            .all()
        )
        for case_id, report_id, status in rows:
            if case_id is None or report_id is None:
                continue
            reports_by_case.setdefault(int(case_id), {}).setdefault(int(report_id), []).append(
                str(status or "").lower()
            )

    def report_passed(statuses: list[str]) -> bool:
        return bool(statuses) and all(status in {"passed", "pass", "success"} for status in statuses)

    contract_bound = 0
    preflight_count = 0
    probe_attempted = 0
    probe_passed = 0
    first_run_total = 0
    first_run_passed = 0
    latest_run_passed = 0
    versions: dict[str, dict[str, int]] = {}
    for case in cases:
        metadata = case.generation_metadata if isinstance(case.generation_metadata, dict) else {}
        preflight = metadata.get("preflight") if isinstance(metadata.get("preflight"), dict) else {}
        has_contract = bool(metadata.get("contract_hash"))
        if has_contract:
            contract_bound += 1
        if preflight.get("passed"):
            preflight_count += 1
        probe = metadata.get("probe") if isinstance(metadata.get("probe"), dict) else {}
        probe_status = str(probe.get("status") or "")
        if probe_status in {"passed", "failed"}:
            probe_attempted += 1
        if probe.get("status") == "passed":
            probe_passed += 1
        prompt_version = str(metadata.get("prompt_version") or "unknown")
        version_stats = versions.setdefault(
            prompt_version,
            {
                "cases": 0,
                "contract_bound": 0,
                "preflight_passed": 0,
                "probe_attempted": 0,
                "probe_passed": 0,
                "first_run_total": 0,
                "first_run_passed": 0,
            },
        )
        version_stats["cases"] += 1
        version_stats["contract_bound"] += int(has_contract)
        version_stats["preflight_passed"] += int(bool(preflight.get("passed")))
        version_stats["probe_attempted"] += int(probe_status in {"passed", "failed"})
        version_stats["probe_passed"] += int(probe_status == "passed")
        case_reports = reports_by_case.get(case.id) or {}
        if case_reports:
            report_ids = sorted(case_reports)
            first_ok = report_passed(case_reports[report_ids[0]])
            latest_ok = report_passed(case_reports[report_ids[-1]])
            first_run_total += 1
            first_run_passed += int(first_ok)
            latest_run_passed += int(latest_ok)
            version_stats["first_run_total"] += 1
            version_stats["first_run_passed"] += int(first_ok)

    total = len(cases)
    return {
        "status": "success",
        "data": {
            "source": "ai_interface",
            "total_cases": total,
            "contract_bound": contract_bound,
            "contract_rate": round(contract_bound / total, 4) if total else None,
            "preflight_passed": preflight_count,
            "preflight_rate": round(preflight_count / total, 4) if total else None,
            "probe_attempted": probe_attempted,
            "probe_coverage_rate": round(probe_attempted / total, 4) if total else None,
            "probe_passed": probe_passed,
            "probe_pass_rate": round(probe_passed / probe_attempted, 4) if probe_attempted else None,
            "first_run_total": first_run_total,
            "first_run_passed": first_run_passed,
            "first_run_pass_rate": round(first_run_passed / first_run_total, 4) if first_run_total else None,
            "latest_run_passed": latest_run_passed,
            "latest_run_pass_rate": round(latest_run_passed / first_run_total, 4) if first_run_total else None,
            "by_prompt_version": versions,
        },
    }


def _available_functions_block(db: DBDep, project_id: int) -> str:
    """从脚本库读取当前项目可用函数，禁止再扫描平台 Python 函数。"""
    try:
        from database.models import ScriptStore

        rows = (
            db.session.query(ScriptStore)
            .filter(
                ScriptStore.kind == "function",
                ScriptStore.enabled.is_(True),
                (ScriptStore.project_id == project_id) | ScriptStore.project_id.is_(None),
            )
            .order_by(ScriptStore.project_id.asc().nullsfirst(), ScriptStore.name.asc())
            .all()
        )
    except Exception:
        return "function:unique(前缀)、function:unique_mobile()、function:unique_email()（无法读取完整列表）"
    by_name = {row.name: row for row in rows}
    lines = [
        f"- function:{name}() —— {row.description or '脚本库动态函数'}"
        for name, row in by_name.items()
    ]
    return "\n".join(sorted(lines)) if lines else ""


def _variable_pool_keys(db, project_id: int) -> set[str]:
    """项目可用变量池的 key 集合。"""
    rows = (
        db.session.query(ConfigStore.config_key)
        .filter(
            ConfigStore.config_group == "default_parameters",
            ConfigStore.project_id == project_id,
        )
        .all()
    )
    return {r[0] for r in rows if r[0]}


def _module_produced_vars(db, module_id: int) -> set[str]:
    """本模块已有 api 用例能 extract 出的变量名（供跨用例依赖校验，避免对已有登录 token 误报）。"""
    out: set[str] = set()
    cases = (
        db.session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.module_id == module_id, TestCase.case_type == CASE_TYPE_API)
        .all()
    )
    for c in cases:
        # v1 兼容字段 extract_data（JSON 文本：{var: jsonpath}）
        if c.extract_data:
            try:
                d = json.loads(c.extract_data)
                if isinstance(d, dict):
                    out |= {str(k) for k in d if str(k).strip()}
            except Exception:
                pass
        for s in (c.steps or []):
            ex = s.extract
            if isinstance(ex, list):
                out |= {str(i.get("name")) for i in ex if isinstance(i, dict) and i.get("name")}
            elif isinstance(ex, dict):
                out |= {str(k) for k in ex if str(k).strip()}
    return out


@router.post("/ai_generate_batch")
def ai_generate_batch(payload: AiBatchRequest, db: DBDep, user: OptionalUserDep = None):
    """第二步：基于 digest + 本批测试点 + 已生成用例名 → 生成这一批的控件级详细用例。
    每批都带 done_names 避免重复，带 digest 保证多批连贯。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options

    if not payload.points:
        raise HTTPException(status_code=400, detail="本批没有测试点")

    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")

    if payload.mode == "interface" and not (payload.api_contract.get("operations") or []):
        from server.services.api_case_contract import merge_contract_catalogs

        recovered_catalogs: list[dict[str, Any]] = []
        # 优先读取大纲阶段持久化的契约，避免浏览器草稿升级/恢复时丢字段。
        if payload.generation_run_id:
            outline_run = (
                db.session.query(AiRun)
                .filter(
                    AiRun.id == payload.generation_run_id,
                    AiRun.project_id == module.project_id,
                    AiRun.feature == AI_FEATURE_API_CASE_GEN,
                )
                .first()
            )
            stored_contract = (
                (outline_run.output_payload or {}).get("api_contract")
                if outline_run and isinstance(outline_run.output_payload, dict)
                else None
            )
            if isinstance(stored_contract, dict) and stored_contract.get("operations"):
                recovered_catalogs.append(stored_contract)
        for url in _extract_doc_urls(payload.doc_urls, payload.setup_doc):
            catalog = _fetch_openapi_catalog_url(url)
            if catalog and catalog.get("operations"):
                recovered_catalogs.append(catalog)
        payload.api_contract = _enrich_api_contract_from_project_rules(
            db,
            module.project_id,
            merge_contract_catalogs(recovered_catalogs),
        )
        if not payload.api_contract.get("operations"):
            raise HTTPException(
                status_code=422,
                detail=(
                    "没有解析到结构化 OpenAPI 契约，已在调用 AI 前停止。请把 Swagger/OpenAPI "
                    "链接放入“接口文档链接”或说明文本，或上传 OpenAPI JSON/YAML 后重新生成大纲。"
                ),
            )
    cfg = _resolve_model(db, payload.model_name, module.project_id)

    batch_points = "\n".join(
        f"- [{p.category or '未分类'}] {p.title}" for p in payload.points
    )
    from server.services.api_case_contract import contract_prompt

    session_done = [n.strip() for n in (payload.done_names or []) if n.strip()]
    existing = _existing_case_names(
        db,
        payload.module_id,
        case_type=CASE_TYPE_API if payload.mode == "interface" else CASE_TYPE_FUNCTIONAL,
    )
    # 喂给模型的「不要重复」清单：模块现有用例 + 本次已生成（截断防 prompt 过长）
    avoid = (existing[:200] + session_done)[-300:]
    done_names = "、".join(avoid) if avoid else "（暂无，这是第一批）"
    # 现有用例的有序清单（供 AI 决定每条新用例插在谁后面）
    existing_ordered = (
        "\n".join(f"{i + 1}. {n}" for i, n in enumerate(existing[:200]))
        if existing
        else "（本模块暂无已有用例，新用例按本批顺序排列即可）"
    )

    cross_ctx = _build_cross_module_context(db, module)
    if payload.done_cases:
        cross_ctx += (
            "\n\n【本次前序批次的结构化依赖摘要】（只能引用其中明确产出的变量；"
            "cleanup_scope=case 的数据不能给后续批次使用）：\n"
            + json.dumps(payload.done_cases[-80:], ensure_ascii=False, default=str)[:12000]
        )
    if payload.setup_doc.strip():
        cross_ctx += (
            "\n\n【用户提供的『账号准备/注册』接口信息】（**仅前置链可跨模块使用**，"
            "据此建一个 function:unique 的一次性测试账号、密码固定字面量，再登录拿 token）：\n"
            + payload.setup_doc.strip()[:2000]
        )
    placeholders = {
        "MODULE_NAME": module.name,
        "DIGEST": payload.digest.strip() or "（无摘要，请按测试点标题合理推断）",
        "CROSS_MODULE_CONTEXT": cross_ctx,
        "BATCH_POINTS": batch_points,
        "DONE_NAMES": done_names,
        "EXISTING_ORDERED": existing_ordered,
        "VARIABLE_POOL": _variable_pool_block(db, module.project_id) if payload.mode == "interface" else "",
        "AVAILABLE_FUNCTIONS": _available_functions_block(db, module.project_id) if payload.mode == "interface" else "",
        # 真实响应结构 / API 约定（从记忆层 api_contract 检索）——这是写对
        # extract/assertion JSONPath 的关键:没它模型只能照 prompt 示例猜路径
        "PROJECT_CONTEXT": "\n\n".join([
            _project_api_contract_rules_block(db, module.project_id, payload.api_contract),
            _project_context_block(
                module.project_id, f"{module.name}\n{payload.digest}\n{batch_points}"
            ),
        ]) if payload.mode == "interface" else "",
        "API_CONTRACT": contract_prompt(
            payload.api_contract,
            f"{batch_points}\n{payload.digest}",
        ) if payload.mode == "interface" else "",
    }
    template = _load_prompt(
        "interface_case_batch" if payload.mode == "interface" else "functional_case_batch"
    )
    call_options = model_task_options(cfg, "api_batch" if payload.mode == "interface" else "functional_batch")

    system_prompt = (
        "你是结构化 JSON 生成器。必须只输出一个合法 JSON 数组，数组元素是接口或功能测试用例对象。"
        "不要输出 Markdown 说明、标题、自然语言解释或代码块外文字。"
        "如果用户提示要求 ```json``` 代码块，也只能在代码块内放合法 JSON。"
    )
    token_usage = {"in": 0, "out": 0}

    def call_and_shape(point_lines: str, *, timeout: int = 180) -> tuple[str, list[dict]]:
        one_prompt = _render_prompt(template, {**placeholders, "BATCH_POINTS": point_lines})
        raw_text, _tin, _tout = chat_markdown(
            one_prompt,
            cfg,
            timeout=timeout or call_options["timeout"],
            system_prompt=system_prompt,
            enable_thinking=call_options["enable_thinking"],
            json_mode=call_options["json_mode"],
            max_tokens=call_options["max_tokens"],
            temperature=call_options["temperature"],
            reasoning_effort=call_options.get("reasoning_effort"),
        )
        token_usage["in"] += int(_tin or 0)
        token_usage["out"] += int(_tout or 0)
        parsed_obj = _extract_json_list(raw_text, allow_salvage=False)
        return raw_text, _shape_cases(parsed_obj)

    try:
        raw, shaped = call_and_shape(batch_points, timeout=call_options["timeout"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 调用失败：{e}")

    if not shaped:
        logger.warning(
            "ai_generate_batch parse failed module_id=%s mode=%s points=%s raw_prefix=%r",
            payload.module_id,
            payload.mode,
            [p.title for p in payload.points],
            (raw or "")[:1000],
        )
        # DeepSeek 等兼容 OpenAI 网关偶发 HTTP 200 但 content 为空；复杂场景批次更容易触发。
        # 失败时把当前小批拆成单点逐条再问，既降低输出长度，也避免接受截断的半批 JSON。
        recovered: list[dict] = []
        for p in payload.points:
            point_line = f"- [{p.category or '未分类'}] {p.title}"
            try:
                one_raw, one_shaped = call_and_shape(point_line, timeout=120)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ai_generate_batch single-point retry failed module_id=%s point=%r error=%s",
                    payload.module_id,
                    p.title,
                    exc,
                )
                continue
            if one_shaped:
                recovered.extend(one_shaped)
                continue
            logger.warning(
                "ai_generate_batch single-point parse failed module_id=%s point=%r raw_prefix=%r",
                payload.module_id,
                p.title,
                (one_raw or "")[:1000],
            )
        shaped = recovered

    if not shaped:
        raise HTTPException(status_code=502, detail="本批解析失败或无有效用例，请重试")

    # 去重 + 标记：本次已生成的直接丢；与模块现有用例同名的保留但标 duplicate=true
    existing_norm = {_norm_name(n) for n in existing}
    seen = {_norm_name(n) for n in session_done}
    cases = []
    for c in shaped:
        key = _norm_name(c["name"])
        if key in seen:
            continue  # 本批/本次重复，丢弃
        seen.add(key)
        c["duplicate"] = key in existing_norm
        cases.append(c)

    # 接口模式：生成后校验 + 加固（变量来源校验、缺断言提示、写入数据命名空间）
    if payload.mode == "interface":
        cases = _merge_response_check_cases(cases)
        var_pool_keys = _variable_pool_keys(db, module.project_id)
        carried = set(payload.carried_vars or []) | _module_produced_vars(db, payload.module_id)
        cases = _harden_generated_cases(
            cases,
            var_pool_keys,
            carried,
            project_id=module.project_id,
        )
        # P0-2: 有 warnings 的用例先让模型自修一轮，仍有问题的保留 warnings 给人看
        cases = _auto_repair_flawed_cases(
            db, cfg, cases, var_pool_keys, carried,
            variable_pool_block=placeholders["VARIABLE_POOL"],
            contract_block=(
                placeholders["API_CONTRACT"]
                + "\n\n"
                + placeholders["PROJECT_CONTEXT"]
            ),
            project_id=module.project_id,
        )

        from server.services.api_case_contract import compile_generated_case
        from server.services.generation_probe_refine import probe_and_refine, validate_isolation

        generation_meta = {
            "generation_run_id": payload.generation_run_id,
            "model": cfg.model,
            "provider": cfg.provider,
            "prompt_version": "if_contract_bat_v2",
        }

        def compile_all(items: list[dict]) -> list[dict]:
            external_variables = set(carried)
            for item in items:
                previous_contract_issues = set(item.pop("_contract_issues", []) or [])
                existing_warnings = [
                    warning for warning in (item.get("warnings") or [])
                    if warning not in previous_contract_issues
                ]
                blocking = [
                    warning for warning in (item.get("blocking_warnings") or [])
                    if warning not in previous_contract_issues
                ]
                compiled, compile_issues = compile_generated_case(
                    item,
                    payload.module_id,
                    payload.api_contract,
                    generation_metadata={
                        **generation_meta,
                        "available_variables": sorted(var_pool_keys | external_variables),
                        "persistent_variables": sorted(var_pool_keys),
                        "carried_variables": sorted(external_variables),
                        "probe": item.get("probe"),
                        "probe_refined": bool(item.get("probe_refined")),
                    },
                )
                item["compiled_case"] = compiled
                for warning in compile_issues:
                    if warning not in existing_warnings:
                        existing_warnings.append(warning)
                isolation_errors = validate_isolation(item)
                for warning in isolation_errors:
                    if warning not in existing_warnings:
                        existing_warnings.append(warning)
                preflight_errors = compiled.get("generation_metadata", {}).get("preflight", {}).get("errors") or []
                for warning in [*preflight_errors, *isolation_errors]:
                    if warning not in blocking:
                        blocking.append(warning)
                if existing_warnings:
                    item["warnings"] = existing_warnings
                else:
                    item.pop("warnings", None)
                if blocking:
                    item["needs_fix"] = True
                    item["blocking_warnings"] = blocking
                else:
                    item.pop("needs_fix", None)
                    item.pop("blocking_warnings", None)
                preflight = compiled.get("generation_metadata", {}).setdefault("preflight", {})
                preflight["errors"] = blocking
                preflight["warnings"] = [warning for warning in existing_warnings if warning not in blocking]
                preflight["passed"] = not blocking
                item["_contract_issues"] = list(dict.fromkeys([*compile_issues, *isolation_errors]))
                if preflight["passed"] and not item.get("teardown_api") and not item.get("teardown_sql"):
                    external_variables.update(_produced_vars(item))
            return items

        cases = compile_all(cases)

        # 先编译和硬校验，再只对安全且可执行的草稿做小流量探测。探测状态码与契约
        # 不一致时不会把错误响应学习成新的正确断言，而是继续保持阻断。
        import os as _os
        if _os.getenv("GEN_PROBE_REFINE", "1") != "0":
            try:
                cases = probe_and_refine(cases, module.project_id, cfg)
                cases = compile_all(cases)
            except Exception:  # noqa: BLE001
                logger.warning("[gen-probe] 精修接入失败，保留契约编译结果", exc_info=True)

        preflight_passed = sum(
            1 for item in cases
            if item.get("compiled_case", {}).get("generation_metadata", {}).get("preflight", {}).get("passed")
            and not item.get("needs_fix")
        )
        run = AiRun(
            feature=AI_FEATURE_API_CASE_GEN,
            status=AI_RUN_STATUS_SUCCESS,
            project_id=module.project_id,
            input_payload={
                "module_id": payload.module_id,
                "stage": "batch",
                "outline_run_id": payload.generation_run_id,
                "contract_hash": payload.api_contract.get("hash"),
                "points": [point.model_dump() for point in payload.points],
            },
            output_payload={
                "case_count": len(cases),
                "preflight_passed": preflight_passed,
                "preflight_blocked": len(cases) - preflight_passed,
            },
            provider=cfg.provider,
            model=cfg.model,
            tokens_in=token_usage["in"],
            tokens_out=token_usage["out"],
            prompt_version="if_contract_bat_v2",
            operator=_operator_name(user),
            started_at=datetime.now(),
            ended_at=datetime.now(),
        )
        db.session.add(run)
        db.session.flush()
        for item in cases:
            metadata = item.get("compiled_case", {}).get("generation_metadata")
            if isinstance(metadata, dict):
                metadata["generation_run_id"] = run.id
            item.pop("_contract_issues", None)
            item.pop("_probe_verified_paths", None)
            item.pop("_probe_response", None)

    return {
        "status": "success",
        "data": {
            "cases": cases,
            "model": payload.model_name,
            "generation_run_id": run.id if payload.mode == "interface" else payload.generation_run_id,
        },
    }


@router.post("/ai_enhance_cases")
def ai_enhance_cases(payload: AiCaseEnhanceRequest, db: DBDep, user: OptionalUserDep = None):
    """高级补全：用 Codex / Claude Code CLI Agent 审稿并补充当前草稿。

    这是一个低频高价值的同步入口：平台准备 prompt，CLI Agent 只输出结构化建议；
    服务端校验/规整后返回给前端继续人工审核，绝不直接入库。
    """
    from server.services.ai_model_service import get_ai_model
    from server.services.cli_case_enhancement_service import (
        build_case_enhancement_prompt,
        is_cli_case_provider,
        run_cli_case_enhancement,
    )

    if not payload.cases:
        raise HTTPException(status_code=400, detail="当前没有可补全的草稿用例")

    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")

    cfg = get_ai_model(db.session, payload.agent_model_name, project_id=module.project_id)
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"CLI Agent {payload.agent_model_name!r} 不存在")
    if not cfg.enabled:
        raise HTTPException(status_code=400, detail=f"CLI Agent {payload.agent_model_name!r} 未启用")
    if not is_cli_case_provider(cfg.provider):
        raise HTTPException(status_code=400, detail="高级补全请选择 Codex CLI 或 Claude Code 类型的 AI 配置")
    if payload.mode == "interface" and not (payload.api_contract.get("operations") or []):
        raise HTTPException(status_code=422, detail="高级补全前必须先读取结构化 OpenAPI 契约")

    existing = _existing_case_names(
        db,
        payload.module_id,
        case_type=CASE_TYPE_API if payload.mode == "interface" else CASE_TYPE_FUNCTIONAL,
    )
    requirement_text = payload.requirement_text
    if payload.mode == "interface":
        from server.services.api_case_contract import contract_prompt

        requirement_text += "\n\n【结构化 OpenAPI 契约（最高优先级）】\n" + contract_prompt(
            payload.api_contract,
            f"{payload.digest}\n{payload.requirement_text}",
        )
    prompt = build_case_enhancement_prompt(
        module_name=module.name,
        mode=payload.mode,
        digest=payload.digest,
        requirement_text=requirement_text,
        existing_case_names=existing,
        cases=payload.cases,
        target_extra_count=max(1, min(int(payload.target_extra_count or 5), 20)),
    )

    run = AiRun(
        feature=AI_FEATURE_FUNCTIONAL_CASE_ENHANCE,
        status=AI_RUN_STATUS_PENDING,
        project_id=module.project_id,
        input_payload={
            "module_id": payload.module_id,
            "agent_model_name": cfg.name,
            "mode": payload.mode,
            "case_count": len(payload.cases),
            "target_extra_count": payload.target_extra_count,
            "contract_hash": payload.api_contract.get("hash"),
        },
        operator=_operator_name(user),
        provider=cfg.provider,
        model=cfg.model,
    )
    db.session.add(run)
    db.session.flush()

    run.status = AI_RUN_STATUS_RUNNING
    run.started_at = datetime.now()
    try:
        result = run_cli_case_enhancement(
            cfg=cfg,
            prompt=prompt,
            timeout=int((cfg.extra or {}).get("timeout_seconds") or 900),
        )
        parsed = result["parsed"]
        shaped = _shape_cases(parsed.get("cases") or [])
        if not shaped:
            raise ValueError("CLI Agent 没有返回有效 cases")

        existing_norm = {_norm_name(n) for n in existing}
        seen: set[str] = set()
        cases: list[dict] = []
        for c in shaped:
            key = _norm_name(c["name"])
            if not key or key in seen:
                continue
            seen.add(key)
            c["duplicate"] = key in existing_norm
            cases.append(c)

        if payload.mode == "interface":
            cases = _merge_response_check_cases(cases)
            cases = _revalidate_interface_cases(
                db,
                module,
                cases,
                payload.api_contract,
                generation_run_id=payload.generation_run_id,
                provider=cfg.provider,
                model=cfg.model,
                prompt_version="cli_enhance_contract_v2",
            )

        output = {
            "cases": cases,
            "summary": str(parsed.get("summary") or "").strip(),
            "issues_found": parsed.get("issues_found") if isinstance(parsed.get("issues_found"), list) else [],
            "quality_score": parsed.get("quality_score"),
            "agent_model_name": cfg.name,
        }
        run.output_payload = output
        run.prompt_hash = result["prompt_hash"]
        run.prompt_version = "cli_enhance_v1"
        run.status = AI_RUN_STATUS_SUCCESS
        run.ended_at = datetime.now()
        return {"status": "success", "data": {**output, "run_id": run.id}}
    except Exception as exc:  # noqa: BLE001
        run.status = AI_RUN_STATUS_FAILED
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        run.ended_at = datetime.now()
        return {
            "status": "error",
            "message": f"高级补全失败：{exc}",
            "data": {"run_id": run.id},
        }


class DiagnoseRunRequest(pydantic.BaseModel):
    case_id: int
    model_name: str


@router.post("/ai_diagnose_run")
def ai_diagnose_run(payload: DiagnoseRunRequest, db: DBDep):
    """分析一条接口用例最近一次执行结果：分类(用例问题/接口问题/环境其他)+原因+建议，
    用例问题给出修正后的 extract/assertion 供「一键修复」。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options

    case = (
        db.session.query(TestCase)
        .options(selectinload(TestCase.steps))
        .filter(TestCase.id == payload.case_id)
        .first()
    )
    if case is None:
        raise HTTPException(status_code=404, detail="用例不存在")
    module = db.session.query(Module).filter(Module.id == case.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="用例所属模块不存在")
    cfg = _resolve_model(db, payload.model_name, module.project_id)
    call_options = model_task_options(cfg, "api_run_diagnose")
    if case.case_type != CASE_TYPE_API:
        raise HTTPException(status_code=400, detail="只支持分析接口(API)用例")

    latest = (
        db.session.query(TestStepReport.report_id)
        .filter(TestStepReport.case_id == payload.case_id)
        .order_by(TestStepReport.report_id.desc())
        .first()
    )
    if not latest:
        raise HTTPException(status_code=400, detail="该用例还没有执行记录，请先运行一次再分析")
    rows = (
        db.session.query(TestStepReport)
        .filter(TestStepReport.case_id == payload.case_id, TestStepReport.report_id == latest[0])
        .order_by(TestStepReport.id)
        .all()
    )
    run_result = [
        {
            "step_name": r.step_name,
            "request": (r.input_data or "")[:2000],
            "status_code": r.status_code,
            "response": (r.output_data or "")[:3000],
            "assertion_results": (r.assertion_results or "")[:1500],
            "extract_values": (r.extract_values or "")[:800],
            "error_message": (r.error_message or "")[:1500],
            "status": r.status,
        }
        for r in rows
    ]
    case_def = _serialize_api_case_definition(case)
    placeholders = {
        "CASE_DEF": json.dumps(case_def, ensure_ascii=False)[:4000],
        "RUN_RESULT": json.dumps(run_result, ensure_ascii=False)[:9000],
    }
    template = _load_prompt("api_run_diagnose")
    prompt = _render_prompt(template, placeholders)
    try:
        raw, _tin, _tout = chat_markdown(
            prompt,
            cfg,
            timeout=call_options["timeout"],
            system_prompt=(
                "你是接口测试诊断器。必须只输出一个合法 JSON 对象，"
                "不要输出 Markdown、解释或思考过程。"
            ),
            enable_thinking=call_options["enable_thinking"],
            json_mode=call_options["json_mode"],
            max_tokens=call_options["max_tokens"],
            temperature=call_options["temperature"],
            reasoning_effort=call_options.get("reasoning_effort"),
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 调用失败：{e}")

    obj = None
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            break
        except Exception:
            obj = None
    if not isinstance(obj, dict):
        s, e = raw.find("{"), raw.rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads(raw[s : e + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        raise HTTPException(status_code=502, detail="分析结果解析失败，请重试")
    fix = obj.get("fix") if isinstance(obj.get("fix"), dict) else {}
    return {
        "status": "success",
        "data": {
            "classification": str(obj.get("classification") or "").strip(),
            "reason": str(obj.get("reason") or "").strip(),
            "suggestion": str(obj.get("suggestion") or "").strip(),
            "fix": {
                "extract": fix.get("extract") if isinstance(fix.get("extract"), dict) else {},
                "assertion": fix.get("assertion") if isinstance(fix.get("assertion"), dict) else {},
                "params": fix.get("params") if isinstance(fix.get("params"), dict) else {},
            },
        },
    }


class ReportDiagnoseRequest(pydantic.BaseModel):
    report_id: int
    model_name: str
    # L1 确定性分诊先分掉能算的（变量悬空、限流、5xx…），只把判不了的交给 LLM。
    # 实测报告 8：126 条失败里 121 条规则可定性，送模型的从 126 降到 5，省 96% token。
    # 关掉则退回全量送模型（老行为）。
    skip_l1_triaged: bool = True


@router.post("/ai_diagnose_report")
def ai_diagnose_report(payload: ReportDiagnoseRequest, db: DBDep):
    """异步提交：对一份测试报告里所有接口用例执行结果做 AI 全面诊断 + 参数修复。

    早期版本是同步阻塞接口（一个请求里串行把所有用例喂给 AI），任务既不进全局任务看板、
    也无法终止。现在改成建一行 AiRun(feature=api_report_fix) + 派 Celery 任务，立即返回
    ai_run_id；任务因此出现在 /tasks-overview/in-progress，可查看进度、可终止。
    前端轮询 /api/ai/runs/{id}，success 后从 output_payload.items 取诊断结果再应用。"""
    from database.models import (
        AiRun,
        TestReport,
        AI_RUN_STATUS_PENDING,
        AI_FEATURE_API_REPORT_FIX,
    )
    from tasks.ai_tasks import dispatch_ai_task

    report = db.session.query(TestReport).filter(TestReport.id == payload.report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.project_id is None:
        raise HTTPException(status_code=400, detail="报告缺少 project_id，无法解析项目级 AI 模型")

    # 早校验模型，给前端明确报错（handler 里还会再 resolve 一次）
    _resolve_model(db, payload.model_name, report.project_id)

    run = AiRun(
        feature=AI_FEATURE_API_REPORT_FIX,
        status=AI_RUN_STATUS_PENDING,
        project_id=report.project_id,
        input_payload={
            "report_id": payload.report_id,
            "model_name": payload.model_name,
            "skip_l1_triaged": payload.skip_l1_triaged,
        },
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    db.commit()

    async_result = dispatch_ai_task.delay(run.id)
    run.celery_task_id = async_result.id
    db.commit()

    return {
        "status": "success",
        "data": {
            "ai_run_id": run.id,
            "feature": run.feature,
            "celery_task_id": async_result.id,
        },
    }


class ReportFixApplyRequest(pydantic.BaseModel):
    ai_run_id: int
    verify: bool = True    # 应用后自动重跑验证；未转绿或无法验证都会回滚
    max_rounds: int = 2    # 多轮修复上限：仍失败的用例带新证据自动再诊断再修（1=只修一轮）


@router.post("/ai_report_fix/apply")
def ai_report_fix_apply(payload: ReportFixApplyRequest, db: DBDep, user: OptionalUserDep = None):
    """把 AI 诊断结果（预检通过的部分）应用到用例，并触发闭环验证。

    与旧的前端逐条 update 相比：
      1. 应用前跑 preflight（分类过滤 / 真实响应 JSONPath 预检 / 变量产出校验 / params 合并），
         坏修复直接拦掉，不落库；
      2. 每条用例一个编辑事件、共用一个 batch，支持按用例精准回滚；
      3. verify=True 时自动重跑原报告全部用例（保证 ${var} 依赖链完整），
         绿变红、红仍红或验证超时都自动回滚——只有验证转绿的修改才永久保留。

    返回 {batch_id, applied, skipped, verify_report_id}；闭环结果由
    tasks.verify_ai_fix 写入 ai_run.output_payload["verify"]，前端轮询即可。
    """
    from database.models import AiRun, TestReport, AI_FEATURE_API_REPORT_FIX
    from server.services.ai_fix_service import (
        apply_report_fixes,
        prepare_verification_run,
        rollback_applied_fixes,
    )

    if not payload.verify:
        raise HTTPException(status_code=422, detail="AI 参数修复必须开启验证，禁止未验证直接落库")

    run = db.session.query(AiRun).filter(AiRun.id == payload.ai_run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="AI 诊断任务不存在")
    if run.feature != AI_FEATURE_API_REPORT_FIX:
        raise HTTPException(status_code=400, detail=f"ai_run {run.id} 不是报告修复任务")
    if run.status != "success":
        raise HTTPException(status_code=400, detail=f"诊断任务状态为 {run.status}，尚不能应用")

    output = run.output_payload or {}
    if output.get("apply"):
        raise HTTPException(status_code=400, detail="该诊断结果已应用过；请重新诊断后再应用，避免重复修改")

    report_id = int((run.input_payload or {}).get("report_id") or 0)
    report = db.session.query(TestReport).filter(TestReport.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="原测试报告不存在")
    if report.project_id is None:
        raise HTTPException(status_code=422, detail="报告缺少 project_id，无法安全重跑验证")

    items = output.get("items") or []
    result = apply_report_fixes(
        db.session, report_id, items,
        operator_id=user.id if user else None,
    )

    verify_report_id: int | None = None
    prepared: dict | None = None
    if result["applied"]:
        prepared = prepare_verification_run(
            db.session,
            project_id=report.project_id,
            category=report.category,
            base_report_id=report_id,
        )
        if prepared:
            verify_report_id = prepared["report_id"]
        else:
            rollback_result = rollback_applied_fixes(
                db.session,
                batch_id=result["batch_id"],
                applied=result["applied"],
                reason="AI 修复无法装配验证执行，候选修改自动回滚",
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "无法装配验证执行，候选修改已回滚"
                    f"（回滚 {rollback_result.get('rolled_back', 0)} 条）"
                ),
            )

    # apply 结果先写回 ai_run；多轮循环状态放 loop/rounds，闭环结果由 verify 任务追加
    max_rounds = max(1, min(int(payload.max_rounds or 2), 2))
    round_entry = {
        "round": 1,
        "batch_id": result["batch_id"],
        "applied": result["applied"],
        "skipped": result["skipped"],
        "base_report_id": report_id,
        "verify_report_id": verify_report_id,
        "status": "verifying" if verify_report_id else "no_verify",
    }
    run.output_payload = {
        **output,
        "apply": {   # 兼容旧读法：首轮信息
            "batch_id": result["batch_id"],
            "applied": result["applied"],
            "skipped": result["skipped"],
            "verify_report_id": verify_report_id,
            "applied_at": datetime.now().isoformat(),
        },
        "loop": {"max_rounds": max_rounds},
        "rounds": [round_entry],
    }
    db.commit()

    if verify_report_id is not None and prepared:
        from tasks import run_test_task
        from tasks.ai_fix_verify_task import verify_ai_fix_task

        run_test_task.delay(prepared["task_id"], verify_report_id, prepared["cases_to_run"], report.category)
        verify_ai_fix_task.delay(run.id, report_id, verify_report_id, result["batch_id"], 1)
    else:
        # 无可应用修复时不创建验证报告，直接按诊断分类打标：
        # 接口问题/环境照常标；用例问题因无验证背书一律标"需人工"提示复核。
        try:
            from server.services.ai_flag_service import (
                derive_outcomes_from_items,
                upsert_flags_from_outcomes,
            )

            applied_ids = {a["case_id"] for a in result["applied"] if a.get("case_id") is not None}
            skip_reasons = {
                s["case_id"]: (s.get("reasons") or [])
                for s in result["skipped"] if s.get("case_id") is not None
            }
            outcomes = derive_outcomes_from_items(
                items,
                unverified=True,
                applied_ids=applied_ids,
                skip_reasons_by_case=skip_reasons,
            )
            upsert_flags_from_outcomes(db.session, outcomes, ai_run_id=run.id, report_id=report_id)
            db.commit()
        except Exception as exc:  # noqa: BLE001 —— 标记是提示层，失败不影响应用结果
            logger.warning("[ai_fix] 无验证路径打标失败（忽略）：%s", exc)

    return {
        "status": "success",
        "data": {
            "batch_id": result["batch_id"],
            "applied": result["applied"],
            "skipped": result["skipped"],
            "verify_report_id": verify_report_id,
        },
    }


def _parse_json_loose(text):
    """尽力把字符串解析成 dict；失败返回 {}。"""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _serialize_api_case_definition(case: TestCase) -> dict[str, Any]:
    """把 API 用例真实执行定义序列化给 AI；执行定义只取 steps。"""
    steps = []
    for step in sorted(case.steps or [], key=lambda s: (int(s.step_order or 0), s.id)):
        if step.step_type != "http_request":
            continue
        config = step.config if isinstance(step.config, dict) else {}
        config_extract = _parse_json_loose(config.get("extract_data"))
        config_assertion = _parse_json_loose(config.get("assertion"))
        steps.append(
            {
                "step_id": step.id,
                "step_name": step.step_name,
                "method": config.get("method"),
                "path": config.get("path"),
                "headers": config.get("headers") or {},
                "data_type": config.get("data_type") or "application/json",
                "params": config.get("params") or {},
                # 与 HttpRequestStepRunner 的真实优先级一致：快速编辑器字段优先。
                "extract": config_extract or step.extract or [],
                "assertion": config_assertion or step.assertion or [],
            }
        )
    first = steps[0] if steps else {}
    return {
        "name": case.name,
        "method": first.get("method"),
        "path": first.get("path"),
        "headers": first.get("headers") or {},
        "params": first.get("params") or {},
        "extract_data": first.get("extract") or [],
        "assertion": first.get("assertion") or [],
        "steps": steps,
        "pre_hook": case.pre_hook or [],
        "note": "params 是平台实际发送的请求体/请求参数模板；修请求参数时请返回完整 fix.params。",
    }


def _extract_rule_map(raw: Any) -> dict[str, str]:
    """把提取规则统一成 ``变量名 -> JSONPath``。"""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if str(k).strip()}
    for rule in raw or []:
        if isinstance(rule, dict) and rule.get("name"):
            out[str(rule["name"])] = str(rule.get("jsonpath") or rule.get("path") or "")
    return out


def _auth_subject_signature(step: dict) -> str | None:
    """按登录请求里的账号字段生成会话主体签名；不包含密码和真实值。"""
    params = step.get("params")
    if not isinstance(params, dict):
        return None
    subject_keys = {
        "username", "user_name", "account", "email", "mobile", "phone", "user_id", "userid",
    }
    subjects = {
        str(k).lower(): str(v)
        for k, v in params.items()
        if str(k).lower() in subject_keys and v not in (None, "")
    }
    if not subjects:
        return None
    return json.dumps(subjects, ensure_ascii=False, sort_keys=True)


def _result_is_success(result: dict) -> bool:
    """判断状态变更请求是否实际成功，避免 401/422 负向请求误作废变量。"""
    if str(result.get("status") or "").lower() in {"failed", "error", "broken"}:
        return False
    try:
        status_code = int(result.get("status_code"))
    except (TypeError, ValueError):
        return False
    if status_code < 200 or status_code >= 400:
        return False
    body = _parse_json_loose(result.get("response"))
    if body.get("success") is False:
        return False
    if str(body.get("status") or "").lower() in {"error", "failed", "failure"}:
        return False
    return True


def _expects_auth_rejection(case_name: str, step: dict) -> bool:
    """识别“故意拿失效 token 验证 401/403”的负向步骤，避免把它误判成流转错误。"""
    expected_codes: set[int] = set()
    assertion = step.get("assertion")
    if isinstance(assertion, dict):
        values = [assertion.get("status_code")]
    else:
        values = [
            rule.get("expected")
            for rule in assertion or []
            if isinstance(rule, dict) and rule.get("target") == "status_code"
        ]
    for value in values:
        try:
            expected_codes.add(int(value))
        except (TypeError, ValueError):
            continue
    negative_name = any(hint in case_name.lower() for hint in ("失效", "过期", "无效", "登出后", "作废"))
    return bool(expected_codes & {401, 403}) and negative_name


def _mutation_kind(case_name: str, step_name: str, path: str) -> str | None:
    hay = f"{case_name} {step_name} {path}".lower()
    if any(h in hay for h in ("logout-all", "logout_all", "全部登出", "登出所有", "revoke-all")):
        return "all_sessions"
    if any(h in hay for h in ("logout", "signout", "sign-out", "登出", "退出登录", "revoke", "吊销")):
        return "current_session"
    if any(h in hay for h in ("password", "修改密码", "改密", "重置密码", "注销账号", "注销账户")):
        return "account_state"
    if any(h in hay for h in ("删除", "delete", "禁用", "停用")):
        return "resource_state"
    return None


def _build_report_dependency_context(
    items_ordered: list[dict],
    case_map: dict,
) -> tuple[str, dict[str, str], dict[int, list[str]]]:
    """按真实 HTTP 步骤构建变量生命周期，而不是只记录“最早产出方”。

    返回 ``(上下文文本, 最早产出表, 用例级确定性线索)``。生命周期会区分：
    实际提取成功、同账号后续登录带来的单会话替换风险、登出/改密后的明确作废，
    从而让 token1/token2/token3 不再被视为三个同等可用的字符串。
    """
    del case_map  # 保留参数兼容调用方；所需定义已在 items_ordered.def 中。
    producers: dict[str, str] = {}
    production_lines: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}
    issues_by_case: dict[int, list[str]] = {}
    token_states: dict[str, dict[str, Any]] = {}
    value_states: dict[str, dict[str, Any]] = {}
    latest_by_subject: dict[str, str] = {}
    order_lines: list[str] = []
    mutation_lines: list[str] = []
    sequence = 0

    for case_index, item in enumerate(items_ordered, start=1):
        case_id = int(item.get("case_id") or 0)
        case_name = str(item.get("name") or f"case#{case_id}")
        definition = item.get("def") or {}
        results = item.get("result") or []
        steps = [s for s in (definition.get("steps") or []) if isinstance(s, dict)]
        if not steps:
            steps = [{
                "step_id": None,
                "step_name": case_name,
                "method": definition.get("method"),
                "path": definition.get("path"),
                "headers": definition.get("headers") or {},
                "params": definition.get("params") or {},
                "extract": definition.get("extract_data") or [],
                "assertion": definition.get("assertion") or [],
            }]
        result_by_step = {
            r.get("step_id"): r for r in results
            if isinstance(r, dict) and r.get("step_id") is not None
        }
        endpoint_labels: list[str] = []

        for step_index, step in enumerate(steps, start=1):
            sequence += 1
            step_id = step.get("step_id")
            result = result_by_step.get(step_id)
            if result is None and step_index <= len(results):
                result = results[step_index - 1]
            result = result if isinstance(result, dict) else {}
            method = str(step.get("method") or "").upper()
            path = str(step.get("path") or "")
            step_name = str(step.get("step_name") or f"步骤{step_index}")
            label = f"{case_name} / {step_name} [{method or '?'} {path or '?'}]"
            endpoint_labels.append(f"{method or '?'} {path or '?'}")

            refs = {
                str(var).split(".")[0]
                for var in _referenced_vars({
                    "path": path,
                    "headers": step.get("headers") or {},
                    "params": step.get("params") or {},
                })
            }
            intentional_stale_check = _expects_auth_rejection(case_name, step)
            auth_failed = str(result.get("status_code") or "") in {"401", "403"}
            for var in sorted(refs):
                consumers.setdefault(var, []).append(label)
                replacement = value_states.get(var)
                if replacement is not None and auth_failed:
                    issues_by_case.setdefault(case_id, []).append(
                        f"本步骤仍引用 ${{{var}}}，但它已被「{replacement['reason']}」更新；"
                        f"当前值应改为 {json.dumps(replacement['value'], ensure_ascii=False)}。"
                        "修复该请求后，平台会自动把新值赋回同名变量。"
                    )
                state = token_states.get(var)
                if state is None or intentional_stale_check:
                    continue
                if state["status"] == "invalid" and auth_failed:
                    latest = latest_by_subject.get(state.get("subject") or "")
                    replacement = (
                        f"；同账号当前最新候选是 ${{{latest}}}" if latest and latest != var
                        and token_states.get(latest, {}).get("status") == "valid"
                        else "；需要在用例编辑器中人工添加可见的登录前置步骤并覆盖该变量"
                    )
                    issues_by_case.setdefault(case_id, []).append(
                        f"本步骤引用 ${{{var}}}，但它已被「{state['reason']}」明确作废{replacement}。"
                    )
                elif state["status"] == "superseded_risk" and auth_failed:
                    latest = latest_by_subject.get(state.get("subject") or "")
                    if latest and latest != var:
                        issues_by_case.setdefault(case_id, []).append(
                            f"本步骤引用 ${{{var}}}，同一账号后来又登录并产出 ${{{latest}}}；"
                            f"单会话系统只有最后一个 token 可用，应优先改用 ${{{latest}}}，"
                            "或在用例编辑器中人工添加可见的登录前置步骤。"
                        )

            defined_extracts = _extract_rule_map(step.get("extract"))
            actual_extracts = _parse_json_loose(result.get("extract_values"))
            for var, jsonpath in defined_extracts.items():
                actual_ok = var in actual_extracts and actual_extracts.get(var) not in (None, "", [], {})
                produced_desc = f"{label} (extract {jsonpath or '?'}, {'成功' if actual_ok else '未提取到值'})"
                production_lines.setdefault(var, []).append(produced_desc)
                producers.setdefault(var, produced_desc)
                if "token" not in var.lower() or "refresh" in var.lower() or not actual_ok:
                    continue

                subject = _auth_subject_signature(step) if _is_login_path(path) else None
                if subject:
                    for previous, previous_state in token_states.items():
                        if previous == var or previous_state.get("subject") != subject:
                            continue
                        if previous_state.get("status") != "invalid":
                            previous_state["status"] = "superseded_risk"
                            previous_state["reason"] = f"同账号后续登录产出 ${{{var}}}"
                    latest_by_subject[subject] = var
                token_states[var] = {
                    "status": "valid",
                    "reason": "",
                    "subject": subject,
                    "produced_at": sequence,
                    "producer": label,
                }

            mutation = _mutation_kind(case_name, step_name, path)
            if mutation and _result_is_success(result):
                state_updates = infer_state_transition_extracts(step.get("params") or {})
                for var, value in state_updates.items():
                    value_states[var] = {"value": value, "reason": label}
                referenced_tokens = [
                    var for var in refs
                    if var in token_states and "token" in var.lower()
                    and mutation != "resource_state"
                ]
                invalidated: set[str] = set(referenced_tokens)
                if mutation in {"all_sessions", "account_state"}:
                    subjects = {token_states[var].get("subject") for var in referenced_tokens}
                    for var, state in token_states.items():
                        if state.get("subject") and state.get("subject") in subjects:
                            invalidated.add(var)
                for var in invalidated:
                    token_states[var]["status"] = "invalid"
                    token_states[var]["reason"] = label
                changes = [
                    f"${{{var}}} 更新为 {json.dumps(value, ensure_ascii=False)}"
                    for var, value in state_updates.items()
                ]
                suffix_parts = []
                if invalidated:
                    suffix_parts.append(
                        f"作废 {', '.join('${' + v + '}' for v in sorted(invalidated))}"
                    )
                if changes:
                    suffix_parts.extend(changes)
                suffix = f"，{'；'.join(suffix_parts)}" if suffix_parts else ""
                mutation_lines.append(f"- {label}{suffix}")

        order_lines.append(
            f"{case_index}. {case_name}  " + " → ".join(endpoint_labels[:4])
            + (" → …" if len(endpoint_labels) > 4 else "")
        )

    lifecycle_lines: list[str] = []
    for var in sorted(production_lines):
        state = token_states.get(var)
        state_text = ""
        if state:
            if state["status"] == "valid":
                state_text = "；当前候选=有效"
            elif state["status"] == "invalid":
                state_text = f"；当前候选=已作废（{state['reason']}）"
            else:
                state_text = f"；当前候选=有单会话替换风险（{state['reason']}）"
        sources = "；".join(production_lines[var][:3])
        lifecycle_lines.append(f"- ${{{var}}}: {sources}{state_text}")

    issue_lines = [
        f"- case_id={case_id}: {issue}"
        for case_id, issues in issues_by_case.items()
        for issue in issues[:4]
    ]
    consumer_lines = [
        f"- ${{{var}}}: " + "；".join(labels[:6])
        for var, labels in sorted(consumers.items())
    ]
    lines = [
        "## 全局执行上下文（按真实 HTTP 步骤推导的参数生命周期）",
        "### 确定性参数冲突（优先处理）",
        *(issue_lines or ["- （未发现明确的失效参数复用）"]),
        "### 状态变更与会话作废步骤",
        *(mutation_lines or ["- （无）"]),
        "### 变量生命周期（不是普通字符串池）",
        *(lifecycle_lines or ["- （无用例 extract 出变量）"]),
        "### 变量引用",
        *(consumer_lines or ["- （无用例引用 ${变量}）"]),
        "### 执行顺序（多步用例按箭头展开）",
        *order_lines,
    ]
    return "\n".join(lines), producers, issues_by_case


def _normalize_report_diagnosis_item(raw: dict, case_map: dict[int, TestCase]) -> dict:
    """规整模型返回的一条报告诊断，并保留所有可执行修复字段。"""
    fix = raw.get("fix") if isinstance(raw.get("fix"), dict) else {}
    try:
        case_id = int(raw.get("case_id"))
    except (TypeError, ValueError):
        case_id = None
    reorder = fix.get("reorder") if isinstance(fix.get("reorder"), dict) else {}
    pre_hook = fix.get("pre_hook") if isinstance(fix.get("pre_hook"), list) else []
    step_fixes = []
    for step_fix in fix.get("steps") or []:
        if not isinstance(step_fix, dict):
            continue
        try:
            step_id = int(step_fix.get("step_id"))
        except (TypeError, ValueError):
            continue
        params = step_fix.get("params") if isinstance(step_fix.get("params"), dict) else {}
        headers = step_fix.get("headers") if isinstance(step_fix.get("headers"), dict) else {}
        extract = step_fix.get("extract") if isinstance(step_fix.get("extract"), dict) else {}
        if params or headers or extract:
            step_fixes.append({
                "step_id": step_id,
                "params": params,
                "headers": headers,
                "extract": extract,
            })
    return {
        "case_id": case_id,
        "module_id": case_map[case_id].module_id if case_id in case_map else None,
        "name": str(raw.get("name") or "").strip(),
        "classification": str(raw.get("classification") or "").strip(),
        "findings": [str(f).strip() for f in (raw.get("findings") or []) if str(f).strip()],
        "fix": {
            "extract": fix.get("extract") if isinstance(fix.get("extract"), dict) else {},
            "assertion": fix.get("assertion") if isinstance(fix.get("assertion"), dict) else {},
            "params": fix.get("params") if isinstance(fix.get("params"), dict) else {},
            "headers": fix.get("headers") if isinstance(fix.get("headers"), dict) else {},
            "steps": step_fixes,
            "pre_hook": pre_hook,
            "reorder": {"before_case_name": str(reorder.get("before_case_name") or "").strip()}
            if reorder.get("before_case_name")
            else {},
        },
    }


def diagnose_report_items(
    session,
    report_id: int,
    cfg,
    only_case_ids: set[int] | None = None,
    attempt_notes: dict[int, str] | None = None,
) -> dict:
    """报告级 AI 诊断核心逻辑（同步执行体，供 Celery handler / 多轮修复循环调用）。

    读报告里所有接口用例的执行结果，分块喂给 AI，逐条返回分类 + 发现 + 修复建议。
    每条用例附带程序化预分析线索（hints）：JSONPath 候选、未解析变量、断言 actual 等
    确定性可算的信息，让模型从"猜"变成"确认"。

    only_case_ids：只诊断这些用例（多轮修复的第 2+ 轮用，全局上下文仍覆盖整份报告）。
    attempt_notes：case_id → 上一轮修复情况说明，注入 previous_attempt 字段避免模型原地打转。

    返回 {"items": [...], "total": N}。无执行记录抛 ValueError（handler 兜底成 failed）。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options
    from server.services.ai_fix_service import build_case_hints

    rows = (
        session.query(TestStepReport)
        .filter(TestStepReport.report_id == report_id)
        .order_by(TestStepReport.case_id, TestStepReport.id)
        .all()
    )
    if not rows:
        raise ValueError("该报告没有执行记录")

    by_case: dict[int, list] = {}
    for r in rows:
        if r.case_id is None:
            continue
        by_case.setdefault(r.case_id, []).append(r)

    case_map = {
        c.id: c
        for c in (
            session.query(TestCase)
            .options(selectinload(TestCase.steps))
            .filter(TestCase.id.in_(list(by_case.keys())))
            .all()
        )
    }

    items_in = []
    for cid, rs in by_case.items():
        c = case_map.get(cid)
        if c is None:
            continue
        items_in.append({
            "case_id": cid,
            "name": c.name,
            "def": _serialize_api_case_definition(c),
            "result": [
                {
                    "step_id": r.step_id,
                    "step_order": getattr(r, "step_order", None),
                    "step_name": r.step_name,
                    "step_type": r.step_type,
                    "request": (r.input_data or "")[:1200],
                    "status_code": r.status_code,
                    "response": (r.output_data or "")[:1800],
                    "assertion_results": (r.assertion_results or "")[:800],
                    "extract_values": (r.extract_values or "")[:500],
                    "error_message": (r.error_message or "")[:800],
                    "status": r.status,
                }
                for r in rs
            ],
        })

    # 按执行顺序（用例 sort_order）排好，让"上下依赖/产出在前、引用在后"的判断成立
    def _sort_key(it):
        c = case_map.get(it.get("case_id"))
        so = getattr(c, "sort_order", None)
        return (so if so is not None else 1 << 30, str(it.get("name") or ""))
    items_in.sort(key=_sort_key)

    # 全局上下文：变量产出/引用表 + 状态变更用例。整份报告算一次，注入到每个 chunk。
    report_context, producers, flow_hints_by_case = _build_report_dependency_context(items_in, case_map)

    # 用户历史反馈：清除标记时留下的更正/经验，按用例注入（权威性最高，防止重复误判）
    from server.services.ai_flag_service import get_case_feedback
    try:
        feedback_by_case = get_case_feedback(session, [it.get("case_id") for it in items_in])
    except Exception as exc:  # noqa: BLE001 —— 反馈是增强项，失败不阻塞诊断
        logger.warning("[diagnose] get_case_feedback 失败：%s", exc)
        feedback_by_case = {}

    # 程序化线索：用未截断的原始执行行做确定性预分析（截断后的 item 数据可能丢证据）
    for it in items_in:
        cid = it.get("case_id")
        try:
            hints = build_case_hints(it.get("def") or {}, by_case.get(cid) or [], producers)
        except Exception as exc:  # noqa: BLE001 —— 线索是增强项，失败不阻塞诊断
            logger.warning("[diagnose] build_case_hints case=%s 失败：%s", cid, exc)
            hints = []
        # 生命周期分析来自完整报告的真实步骤顺序，优先级高于模型推测。
        hints.extend(flow_hints_by_case.get(cid) or [])
        if hints:
            it["hints"] = hints[:10]
        fb = feedback_by_case.get(cid)
        if fb:
            it["user_feedback"] = fb
        note = (attempt_notes or {}).get(cid)
        if note:
            it["previous_attempt"] = note

    # 多轮修复：第 2+ 轮只重诊断指定用例（上下文仍是全报告）
    if only_case_ids is not None:
        items_in = [it for it in items_in if it.get("case_id") in only_case_ids]
        if not items_in:
            return {"items": [], "total": 0, "tokens_in": 0, "tokens_out": 0}

    out = []
    template = _load_prompt("api_report_diagnose")
    call_options = model_task_options(cfg, "api_report_fix")
    diagnose_system_prompt = (
        "你是接口测试诊断器。必须只输出一个合法 JSON 数组（可包在 ```json``` 代码块里），"
        "每条用例一个对象。不要输出 Markdown 标题、解释或思考过程。"
    )
    # 按字符预算装箱分块，替代旧的 chunk_size=6 + json.dumps(chunk)[:14000] 硬截断——
    # 那会把后半个 chunk 的用例数据拦腰切成非法 JSON，模型对这些用例只能瞎猜。
    # 预算按“每 chunk 输入体量 ≈ 24k 字符”估（约 1.2 万 token），单条超预算的用例独立成块。
    chunk_char_budget = 24000
    max_cases_per_chunk = 6
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_chars = 0
    for it in items_in:
        item_chars = len(json.dumps(it, ensure_ascii=False))
        if cur and (cur_chars + item_chars > chunk_char_budget or len(cur) >= max_cases_per_chunk):
            chunks.append(cur)
            cur, cur_chars = [], 0
        cur.append(it)
        cur_chars += item_chars
    if cur:
        chunks.append(cur)

    tokens_in = tokens_out = 0
    for chunk in chunks:
        prompt = _render_prompt(template, {
            "REPORT_CONTEXT": report_context[:6000],
            "CASES": json.dumps(chunk, ensure_ascii=False),
        })
        try:
            raw, call_tin, call_tout = chat_markdown(
                prompt,
                cfg,
                timeout=call_options["timeout"],
                system_prompt=diagnose_system_prompt,
                enable_thinking=call_options["enable_thinking"],
                json_mode=call_options["json_mode"],
                max_tokens=call_options["max_tokens"],
                temperature=call_options["temperature"],
                reasoning_effort=call_options.get("reasoning_effort"),
            )
        except Exception as e:  # noqa: BLE001
            # 在 Celery handler 里执行：抛普通异常即可，dispatch 兜底成 ai_run.status=failed。
            raise RuntimeError(f"AI 调用失败：{e}") from e
        # 累计 token 用量：分块调用会调多次，逐次累加后随结果返回，
        # 供 AiRun 记账（此前这里用 _tin/_tout 丢弃了，导致成本看板恒为空）
        tokens_in += int(call_tin or 0)
        tokens_out += int(call_tout or 0)

        parsed = _extract_json_list(raw)
        if isinstance(parsed, list):
            for x in parsed:
                if not isinstance(x, dict):
                    continue
                out.append(_normalize_report_diagnosis_item(x, case_map))

    return {
        "items": out, "total": len(items_in),
        "tokens_in": tokens_in, "tokens_out": tokens_out,
    }


@router.post("")
def create_functional_case(
    payload: FunctionalCaseCreate,
    db: DBDep,
    user: OptionalUserDep = None,
    session_id: Optional[str] = Query(None, description="快速编辑会话 id（同会话改动聚合成一条编辑记录）"),
):
    """创建功能用例：往 test_cases 写一行 case_type='functional'。"""
    if not payload.name:
        raise HTTPException(status_code=400, detail="名称不能为空")

    explicit_order = payload.sort_order
    if explicit_order is not None:
        # 指定位置插入：把同模块里 >= 该位置的所有用例（含其它栈）整体后移
        db.session.query(TestCase).filter(
            TestCase.module_id == payload.module_id,
            TestCase.sort_order >= explicit_order,
        ).update(
            {TestCase.sort_order: TestCase.sort_order + 1},
            synchronize_session=False,
        )
        sort_order = explicit_order
    else:
        max_order = (
            db.session.query(func.max(TestCase.sort_order))
            .filter(TestCase.module_id == payload.module_id)
            .scalar()
            or 0
        )
        sort_order = max_order + 1

    spec_dict = payload.functional_spec.model_dump() if payload.functional_spec else None
    new_case = TestCase(
        module_id=payload.module_id,
        name=payload.name,
        description=payload.description,
        skip=payload.skip,
        priority=payload.priority,
        tags=payload.tags,
        case_type=CASE_TYPE_FUNCTIONAL,
        sort_order=sort_order,
        functional_spec=spec_dict,
    )
    db.session.add(new_case)
    db.session.flush()
    db.session.refresh(new_case)
    hist_case = _load_case_for_history(db, new_case.id)
    if hist_case is not None:
        record_test_case_create(
            db.session,
            hist_case,
            operator_id=user.id if user else None,
            summary=f"新增功能用例：{hist_case.name}",
        )
    _record_edit(
        db,
        case_id=new_case.id,
        module_id=new_case.module_id,
        case_name=new_case.name,
        action=EDIT_ACTION_CREATE,
        operator=_operator_name(user),
        session_id=session_id,
    )
    return {"status": "success", "data": _serialize_case(new_case)}


@router.put("/{case_id}")
def update_functional_case(
    case_id: int,
    payload: FunctionalCaseUpdate,
    db: DBDep,
    user: OptionalUserDep = None,
    session_id: Optional[str] = Query(None, description="快速编辑会话 id"),
):
    """部分更新。Pydantic 字段为 None 视为"用户没碰它"，保留旧值。"""
    case = _get_functional_case_or_404(db, case_id)
    hist_case = _load_case_for_history(db, case_id)
    before_snapshot = snapshot_test_case(hist_case) if hist_case is not None else {}

    data = payload.model_dump(exclude_unset=True)
    if "functional_spec" in data and data["functional_spec"] is not None:
        # FunctionalSpec 对象会被 Pydantic 自动转 dict（exclude_unset 导致它来时已是 dict）
        data["functional_spec"] = data["functional_spec"]

    # —— 编辑历史：在应用改动前算出每个被改字段的 old→new ——
    changes: list[dict] = []
    for key, value in data.items():
        if key == "functional_spec":
            old_spec = case.functional_spec or {}
            new_spec = value or {}
            for sub in ("preconditions", "steps", "expected"):
                ov, nv = old_spec.get(sub), new_spec.get(sub)
                old_s = "\n".join(ov) if isinstance(ov, list) else (ov or "")
                new_s = "\n".join(nv) if isinstance(nv, list) else (nv or "")
                if old_s != new_s:
                    changes.append({"field": sub, "old": old_s, "new": new_s})
        elif key == "tags":
            ov, nv = case.tags or [], value or []
            if list(ov) != list(nv):
                changes.append({"field": "tags", "old": ", ".join(ov), "new": ", ".join(nv)})
        else:
            old_v = getattr(case, key)
            if old_v != value:
                changes.append({
                    "field": key,
                    "old": "" if old_v is None else str(old_v),
                    "new": "" if value is None else str(value),
                })

    for key, value in data.items():
        setattr(case, key, value)

    db.session.flush()
    db.session.refresh(case)
    hist_case = _load_case_for_history(db, case_id)
    if hist_case is not None:
        record_test_case_update(
            db.session,
            hist_case,
            before_snapshot=before_snapshot,
            field_changes=[
                {"field": ch["field"], "label": ch.get("field", ""), "old": ch["old"], "new": ch["new"]}
                for ch in changes
            ],
            operator_id=user.id if user else None,
            summary=f"修改功能用例：{hist_case.name}",
        )
    if changes:
        _record_edit(
            db,
            case_id=case.id,
            module_id=case.module_id,
            case_name=case.name,
            action=EDIT_ACTION_UPDATE,
            operator=_operator_name(user),
            changes=changes,
            session_id=session_id,
        )
    latest_map = _latest_runs_map(db, [case.id])
    return {
        "status": "success",
        "data": _serialize_case(case, latest_run=latest_map.get(case.id)),
    }


@router.delete("/{case_id}")
def delete_functional_case(
    case_id: int,
    db: DBDep,
    user: OptionalUserDep = None,
    session_id: Optional[str] = Query(None, description="快速编辑会话 id"),
):
    """删除功能用例。`functional_runs` 关系上挂了 cascade='all, delete-orphan'，
    历史勾结果会一起被删掉（如果要保留审计就改 cascade 策略）。
    编辑历史用 ON DELETE SET NULL，删除后仍保留（靠 module_id/case_name 快照查询）。"""
    case = _get_functional_case_or_404(db, case_id)
    hist_case = _load_case_for_history(db, case_id)
    _record_edit(
        db,
        case_id=case.id,
        module_id=case.module_id,
        case_name=case.name,
        action=EDIT_ACTION_DELETE,
        operator=_operator_name(user),
        session_id=session_id,
    )
    event = None
    if hist_case is not None:
        event = record_test_case_delete(
            db.session,
            hist_case,
            operator_id=user.id if user else None,
            summary=f"删除功能用例：{hist_case.name}",
        )
    db.session.flush()  # 确保历史行先落库拿到 case_id，再删 case 触发 SET NULL
    db.session.delete(case)
    return {
        "status": "success",
        "message": "用例已删除",
        "data": {"batch_id": event.batch_id if event is not None else None},
    }


@router.get("/batches")
def list_recent_batches(
    db: DBDep,
    project_id: int = Query(..., description="哪个项目下的批次"),
    limit: int = Query(20, ge=1, le=200),
):
    """按批次（batch_id）聚合的执行汇总。

    用途：给"回归测试"看板用 —— 一次回归把全部功能用例跑完后，能在卡片上
    显示"x/y 通过 / z 失败 / w 阻塞"。

    项目维度过滤：通过 case 的 module → project 反查（不强 FK，挂在 batch_id 上）。
    """
    project = db.session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 拿到该项目所有 functional case 的 id
    case_ids_q = (
        db.session.query(TestCase.id)
        .join(Module, TestCase.module_id == Module.id)
        .filter(
            Module.project_id == project_id,
            TestCase.case_type == CASE_TYPE_FUNCTIONAL,
        )
    )
    case_ids = [r[0] for r in case_ids_q.all()]
    if not case_ids:
        return {"status": "success", "data": []}

    # 按 batch_id 聚合：起止时间 + 各状态 count
    rows = (
        db.session.query(
            FunctionalCaseRun.batch_id,
            func.min(FunctionalCaseRun.executed_at).label("started_at"),
            func.max(FunctionalCaseRun.executed_at).label("finished_at"),
            func.count(FunctionalCaseRun.id).label("total"),
        )
        .filter(
            FunctionalCaseRun.case_id.in_(case_ids),
            FunctionalCaseRun.batch_id.isnot(None),
        )
        .group_by(FunctionalCaseRun.batch_id)
        .order_by(func.max(FunctionalCaseRun.executed_at).desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return {"status": "success", "data": []}

    batch_ids = [r[0] for r in rows]

    # 每个 batch 的状态分布：再来一次 group by
    status_rows = (
        db.session.query(
            FunctionalCaseRun.batch_id,
            FunctionalCaseRun.status,
            func.count(FunctionalCaseRun.id),
        )
        .filter(
            FunctionalCaseRun.case_id.in_(case_ids),
            FunctionalCaseRun.batch_id.in_(batch_ids),
        )
        .group_by(FunctionalCaseRun.batch_id, FunctionalCaseRun.status)
        .all()
    )
    status_map: dict[str, dict[str, int]] = {}
    for bid, st, n in status_rows:
        status_map.setdefault(bid, {s: 0 for s in ALL_RUN_STATUSES})
        status_map[bid][st] = int(n or 0)

    data = []
    for bid, started_at, finished_at, total in rows:
        sm = status_map.get(bid, {s: 0 for s in ALL_RUN_STATUSES})
        passed = sm.get("passed", 0)
        pass_rate = round((passed / total) * 100, 1) if total else 0.0
        data.append({
            "batch_id": bid,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
            "total": int(total or 0),
            "passed": passed,
            "failed": sm.get("failed", 0),
            "blocked": sm.get("blocked", 0),
            "na": sm.get("na", 0),
            "pass_rate": pass_rate,
        })
    return {"status": "success", "data": data}


# /export 必须在 /{case_id} 之前注册：
#   `case_id: int` 类型校验失败时 FastAPI 返 422，不会回退到下一条路由 ——
#   如果先注册了 /{case_id}，对 GET /functional_cases/export 的请求会被它拦截，
#   把 "export" 拿去转 int 失败 → 422。所以静态路径必须排在动态路径前面。
#   函数体放在文件下面"Excel 导入 / 导出"那块（代码就近），这里只是路由顺序锚点。
@router.get("/export")
def export_functional_cases(  # noqa: F811  真正实现在下方
    db: DBDep,
    project_id: int = Query(..., description="导出该项目下的功能用例"),
    module_id: Optional[int] = Query(
        None, description="可选：只导这个模块及其子模块；不传 = 整个项目",
    ),
):
    return _export_functional_cases_impl(db, project_id, module_id)


# 静态路径，必须排在 /{case_id} 之前（同 /export 的理由）
@router.get("/edit_history")
def list_edit_history(
    db: DBDep,
    module_id: int = Query(..., description="按模块查编辑历史"),
    limit: int = Query(100, ge=1, le=500),
):
    """某模块下功能用例的编辑历史（新建/修改/删除），按时间倒序。"""
    rows = (
        db.session.query(FunctionalCaseEditHistory)
        .filter(FunctionalCaseEditHistory.module_id == module_id)
        .order_by(
            FunctionalCaseEditHistory.created_at.desc(),
            FunctionalCaseEditHistory.id.desc(),
        )
        .limit(limit)
        .all()
    )
    case_ids = [
        case_id for (case_id,) in db.session.query(TestCase.id).filter(
            TestCase.module_id == module_id,
            TestCase.case_type == CASE_TYPE_FUNCTIONAL,
        ).all()
    ]
    event_rows = []
    if case_ids:
        event_rows = (
            db.session.query(EditOperationEvent)
            .filter(
                EditOperationEvent.entity_type == ENTITY_TYPE_TEST_CASE,
                EditOperationEvent.entity_id.in_(case_ids),
            )
            .order_by(EditOperationEvent.created_at.desc(), EditOperationEvent.id.desc())
            .limit(limit)
            .all()
        )
    data = merge_test_case_edit_history(event_rows, rows, limit=limit)
    return {"status": "success", "data": data}


@router.get("/test_history")
def list_test_history(
    db: DBDep,
    module_id: int = Query(..., description="按模块查测试（勾结果）历史"),
    limit: int = Query(300, ge=1, le=1000),
):
    """某模块下所有功能用例的勾结果，带用例名，按时间倒序（给"测试记录"按批次聚合用）。"""
    rows = (
        db.session.query(FunctionalCaseRun, TestCase.name)
        .join(TestCase, FunctionalCaseRun.case_id == TestCase.id)
        .filter(
            TestCase.module_id == module_id,
            TestCase.case_type == CASE_TYPE_FUNCTIONAL,
        )
        .order_by(
            FunctionalCaseRun.executed_at.desc(),
            FunctionalCaseRun.id.desc(),
        )
        .limit(limit)
        .all()
    )
    data = []
    for run, case_name in rows:
        d = run.to_dict()
        d["case_name"] = case_name
        data.append(d)
    return {"status": "success", "data": data}


@router.get("/{case_id}")
def get_functional_case(case_id: int, db: DBDep):
    """单个用例详情 + 最近一次执行结果。"""
    case = _get_functional_case_or_404(db, case_id)
    latest_map = _latest_runs_map(db, [case.id])
    return {
        "status": "success",
        "data": _serialize_case(case, latest_run=latest_map.get(case.id)),
    }


# ---------------------------------------------------------------------------
# 列表（分页 + 状态过滤）
# ---------------------------------------------------------------------------
@router.get("")
def list_functional_cases(
    db: DBDep,
    module_id: Optional[int] = Query(None, description="只列该模块下的用例"),
    project_id: Optional[int] = Query(None, description="跨模块按项目列；与 module_id 互斥"),
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description=(
            "按最近一次执行状态过滤（多值逗号分隔，如 failed,blocked）；"
            "包含 'pending' 表示还没执行过的用例"
        ),
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=0, le=500),
):
    """列功能用例，附带最近一次执行结果。

    `status_filter` 在 Python 侧过滤（因为状态来自 latest_run，原始查询拿不到）。
    分页针对"过滤后"做，不是"过滤前" —— 否则带 status 过滤时分页计数会失真。
    """
    if module_id is None and project_id is None:
        raise HTTPException(status_code=400, detail="必须指定 module_id 或 project_id")
    if module_id is not None and project_id is not None:
        raise HTTPException(status_code=400, detail="module_id 与 project_id 互斥")

    q = db.session.query(TestCase).filter(TestCase.case_type == CASE_TYPE_FUNCTIONAL)
    if module_id is not None:
        q = q.filter(TestCase.module_id == module_id)
    else:
        # project 维度：跨模块，用 join Module 反查
        q = q.join(Module, TestCase.module_id == Module.id).filter(Module.project_id == project_id)

    cases = q.order_by(TestCase.sort_order, TestCase.id).all()
    if not cases:
        return {"status": "success", "data": {"items": [], "total": 0, "page": page, "page_size": page_size}}

    latest_map = _latest_runs_map(db, [c.id for c in cases])

    # 状态过滤
    wanted: Optional[set[str]] = None
    if status_filter:
        wanted = {s.strip().lower() for s in status_filter.split(",") if s.strip()}

    items: list[dict] = []
    for c in cases:
        latest = latest_map.get(c.id)
        if wanted is not None:
            current_status = latest.status if latest else "pending"
            if current_status not in wanted:
                continue
        items.append(_serialize_case(c, latest_run=latest))

    total = len(items)
    if page_size == 0:
        paged_items = items
    else:
        start = (page - 1) * page_size
        end = start + page_size
        paged_items = items[start:end]
    return {
        "status": "success",
        "data": {
            "items": paged_items,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


# ---------------------------------------------------------------------------
# 勾结果（单条 / 批量）
# ---------------------------------------------------------------------------
@router.post("/{case_id}/mark")
def mark_functional_case(case_id: int, payload: FunctionalMarkPayload, db: DBDep):
    """单条勾结果，写一行 FunctionalCaseRun。"""
    case = _get_functional_case_or_404(db, case_id)
    status = _validate_status(payload.status)
    run = FunctionalCaseRun(
        case_id=case.id,
        status=status,
        actual_result=payload.actual_result,
        note=payload.note,
        operator=payload.operator,
        batch_id=payload.batch_id,
    )
    db.session.add(run)
    db.session.flush()
    db.session.refresh(run)
    return {"status": "success", "data": run.to_dict()}


@router.post("/batch_mark")
def batch_mark(payload: FunctionalBatchMark, db: DBDep):
    """批量勾结果。

    实现策略：
      - 单事务里循环写入；某一条失败不回滚整批，转为 errors 列表返回，
        让前端能给"哪几条没成功"提示，符合"测试模式"边勾边走的体感；
      - case_id 不存在 / 不是 functional → 单条 error，跳过；
      - 状态非法 → 单条 error，跳过。
    """
    if not payload.batch_id:
        raise HTTPException(status_code=400, detail="batch_id 不能为空（批量勾必须有批次标识）")
    if not payload.items:
        raise HTTPException(status_code=400, detail="items 为空")

    # 先一次性把这批 case 拉出来，验证类型
    case_ids = [it.case_id for it in payload.items]
    cases = (
        db.session.query(TestCase)
        .filter(TestCase.id.in_(case_ids))
        .all()
    )
    case_map = {c.id: c for c in cases}

    created: list[dict] = []
    errors: list[dict] = []
    for it in payload.items:
        c = case_map.get(it.case_id)
        if c is None:
            errors.append({"case_id": it.case_id, "error": "case_not_found"})
            continue
        if c.case_type != CASE_TYPE_FUNCTIONAL:
            errors.append({
                "case_id": it.case_id,
                "error": f"not_functional (case_type={c.case_type})",
            })
            continue
        s = (it.status or "").strip().lower()
        if s not in ALL_RUN_STATUSES:
            errors.append({"case_id": it.case_id, "error": f"invalid_status:{it.status}"})
            continue

        run = FunctionalCaseRun(
            case_id=it.case_id,
            status=s,
            actual_result=it.actual_result,
            note=it.note,
            operator=payload.operator,
            batch_id=payload.batch_id,
        )
        db.session.add(run)
        # 不在循环里 flush 每一条 —— 一次 flush 在循环外更省 IO；refresh 也可省（用 dict 而非 ORM 回写）
        created.append({
            "case_id": it.case_id,
            "status": s,
            "batch_id": payload.batch_id,
        })

    db.session.flush()
    return {
        "status": "success",
        "data": {
            "batch_id": payload.batch_id,
            "created": len(created),
            "errors": errors,
            "items": created,
        },
    }


# ---------------------------------------------------------------------------
# 历史 / 批次
# ---------------------------------------------------------------------------
@router.get("/{case_id}/runs")
def list_runs(case_id: int, db: DBDep, limit: int = Query(20, ge=1, le=200)):
    """某条用例的最近 N 次勾结果（按时间倒序）。"""
    case = _get_functional_case_or_404(db, case_id)
    rows = (
        db.session.query(FunctionalCaseRun)
        .filter(FunctionalCaseRun.case_id == case.id)
        .order_by(FunctionalCaseRun.executed_at.desc(), FunctionalCaseRun.id.desc())
        .limit(limit)
        .all()
    )
    return {"status": "success", "data": [r.to_dict() for r in rows]}


# ---------------------------------------------------------------------------
# Excel 导入 / 导出
# ---------------------------------------------------------------------------
# 列定义（与导出严格对齐，导出 → 编辑 → 导入是个闭环）
# 这里故意保持"简单的 7 列"，user 决策："功能用例导入 Excel 的列简单一点，后期再扩展"。
_IMPORT_COLUMNS = [
    "name",            # 必填
    "description",
    "preconditions",   # 多行用 "\n" 分隔
    "steps",           # 多行用 "\n" 分隔
    "expected",
    "priority",        # int 0/1/2/3，留空 = 默认
    "tags",            # 逗号分隔
]


def _split_lines(value: Any) -> list[str]:
    """Excel 单元格里的多行文本转列表，并去掉导出时添加的有序序号。"""
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lines.append(re.sub(r"^\d+[.)、]\s+", "", line).strip())
    return [line for line in lines if line]


def _join_numbered_lines(values: Any) -> str:
    """把列表或多行字符串格式化为 Excel 单元格中的有序文本。"""
    if isinstance(values, list):
        raw_lines = values
    elif isinstance(values, str):
        raw_lines = values.splitlines()
    else:
        raw_lines = []
    lines = [str(value).strip() for value in raw_lines if str(value).strip()]
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))


def _split_csv(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [t.strip() for t in text.split(",") if t.strip()]


@router.post("/import")
async def import_functional_cases(
    db: DBDep,
    module_id: int = Query(..., description="导入到哪个模块下"),
    file: UploadFile = File(...),
):
    """从 Excel 批量导入功能用例。模板列：name / description / preconditions / steps /
    expected / priority / tags。

    解析失败按 400 返回原因。每行成功就 +1，整体一次性 flush；中途某行
    失败不会回滚之前已经入库的（前端会显示成功 N 条 + 失败列表）。
    """
    import pandas as pd

    contents = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{exc}") from exc

    missing = [c for c in ("name", "steps") if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Excel 缺少必需列：{missing}（最少要 name + steps）",
        )

    # 当前模块下最大 sort_order，新导入的追加到末尾
    base_order = (
        db.session.query(func.max(TestCase.sort_order))
        .filter(TestCase.module_id == module_id)
        .scalar()
        or 0
    )

    imported = 0
    errors: list[dict] = []
    for idx, row in df.iterrows():
        try:
            name = str(row.get("name") or "").strip()
            if not name:
                errors.append({"row": int(idx) + 2, "error": "name 为空"})
                continue
            expected_raw = row.get("expected")
            expected_lines = _split_lines(expected_raw) if pd.notna(expected_raw) else []
            spec = {
                "preconditions": _split_lines(row.get("preconditions")),
                "steps": _split_lines(row.get("steps")),
                "expected": "\n".join(expected_lines) or None,
            }
            priority_raw = row.get("priority")
            try:
                priority = int(priority_raw) if pd.notna(priority_raw) and str(priority_raw).strip() else None
            except (TypeError, ValueError):
                priority = None
            tags = _split_csv(row.get("tags"))

            db.session.add(TestCase(
                module_id=module_id,
                name=name,
                description=str(row.get("description") or "").strip() or None,
                case_type=CASE_TYPE_FUNCTIONAL,
                priority=priority,
                tags=tags or None,
                functional_spec=spec,
                sort_order=base_order + imported + 1,
            ))
            imported += 1
        except Exception as exc:
            errors.append({"row": int(idx) + 2, "error": str(exc)})

    db.session.flush()
    return {
        "status": "success",
        "data": {"imported": imported, "errors": errors},
    }


def _export_functional_cases_impl(
    db,
    project_id: int,
    module_id: Optional[int],
):
    """导出功能用例为 xlsx。列与 import 对齐 + 多带几列只读信息（最近状态 / 时间）。

    实现独立成函数是因为 `@router.get("/export")` 装饰器需要在 /{case_id} 之前
    注册（参见上方注释），而具体实现就近放在 Excel 导入下方更易读。
    """
    import pandas as pd

    project = db.session.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 模块范围：传了 module_id 就 BFS 收子树
    module_ids: Optional[list[int]] = None
    if module_id is not None:
        root = (
            db.session.query(Module)
            .filter(Module.id == module_id, Module.project_id == project_id)
            .first()
        )
        if root is None:
            raise HTTPException(status_code=404, detail=f"模块 {module_id} 不属于项目 {project_id}")
        module_ids = [root.id]
        frontier = [root.id]
        while frontier:
            children = (
                db.session.query(Module.id)
                .filter(Module.parent_id.in_(frontier))
                .all()
            )
            next_ids = [r[0] for r in children]
            if not next_ids:
                break
            module_ids.extend(next_ids)
            frontier = next_ids

    q = (
        db.session.query(TestCase, Module.name.label("module_name"))
        .join(Module, TestCase.module_id == Module.id)
        .filter(
            Module.project_id == project_id,
            TestCase.case_type == CASE_TYPE_FUNCTIONAL,
        )
    )
    if module_ids is not None:
        q = q.filter(TestCase.module_id.in_(module_ids))
    rows = q.order_by(TestCase.module_id, TestCase.sort_order, TestCase.id).all()

    # 一次拿所有最近 run，避免 N+1
    latest_map = _latest_runs_map(db, [c.id for c, _ in rows])

    records = []
    for case, module_name in rows:
        spec = case.functional_spec or {}
        latest = latest_map.get(case.id)
        records.append({
            "module_name": module_name or "",       # 只读
            "name": case.name or "",
            "description": case.description or "",
            "preconditions": _join_numbered_lines(spec.get("preconditions")),
            "steps": _join_numbered_lines(spec.get("steps")),
            "expected": _join_numbered_lines(spec.get("expected")),
            "priority": case.priority if case.priority is not None else "",
            "tags": ", ".join(case.tags or []),
            # 只读的执行情况列：导入侧不识别，导出仅供查看
            "latest_status": latest.status if latest else "pending",
            "latest_executed_at": latest.executed_at.isoformat() if latest and latest.executed_at else "",
            "latest_operator": latest.operator if latest else "",
        })
    df = pd.DataFrame(records)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = (project.name or f"project_{project_id}").replace("/", "_").replace("\\", "_")
    filename = f"{safe_name}_functional_cases_{ts}.xlsx"

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="functional_cases")
    buf.seek(0)

    from urllib.parse import quote
    disposition = (
        f'attachment; filename="{quote(filename)}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


# ---------------------------------------------------------------------------
# 工具：生成一个新 batch_id（前端"测试模式"开始时调用，避免前后端各生成一份）
# ---------------------------------------------------------------------------
@router.post("/new_batch_id")
def new_batch_id():
    """返回一个供"测试模式"使用的新 batch_id。

    放在后端是因为：
      - 前端生成 UUID 也行，但放后端可以未来加上"项目+用户+时间"前缀做易读化；
      - 单点接口好测，前端直接 POST 就能拿。
    """
    return {"status": "success", "data": {"batch_id": uuid.uuid4().hex}}
