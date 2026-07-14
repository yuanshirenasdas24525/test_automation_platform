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
    AI_FEATURE_FUNCTIONAL_CASE_ENHANCE,
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
    TestCase,
    User,
)
from database.models.edit_operation import ENTITY_TYPE_TEST_CASE
from server.services.edit_history_service import (
    record_test_case_create,
    record_test_case_delete,
    record_test_case_update,
    serialize_test_case_event,
    snapshot_test_case,
)

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


def _summarize_openapi(data: dict, operation_ids: set[str] | None = None) -> str:
    """OpenAPI/Swagger → 人读接口清单。"""
    lines = ["# OpenAPI/Swagger 接口清单"]
    wanted = {x for x in (operation_ids or set()) if x}
    paths = data.get("paths") or {}
    for path, methods in list(paths.items())[:200]:
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch", "head", "options"):
                continue
            op = op if isinstance(op, dict) else {}
            if wanted and str(op.get("operationId") or "") not in wanted:
                continue
            summary = op.get("summary") or op.get("operationId") or ""
            pdesc = []
            for p in op.get("parameters") or []:
                if isinstance(p, dict):
                    req = "必填" if p.get("required") else "可选"
                    typ = (p.get("schema") or {}).get("type") if isinstance(p.get("schema"), dict) else p.get("type")
                    pdesc.append(f"{p.get('name')}({p.get('in')},{req},{typ or ''})")
            resps = ",".join(str(k) for k in (op.get("responses") or {}).keys())
            line = f"- {method.upper()} {path}"
            if summary:
                line += f" — {summary}"
            if pdesc:
                line += f"；参数: {', '.join(pdesc)}"
            if isinstance(op.get("requestBody"), dict):
                line += "；有请求体"
            if resps:
                line += f"；响应码: {resps}"
            lines.append(line)
    if wanted and len(lines) == 1:
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


def _parse_api_spec(path: str, ext: str) -> str:
    """接口文件（OpenAPI/Swagger/Postman/任意 json·yaml）→ 喂给 AI 的接口清单文本。"""
    try:
        if ext in (".yaml", ".yml"):
            import yaml  # PyYAML 已在 requirements

            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
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


def _extract_json_list(raw: str):
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
    # 兜底：输出被截断 / 含少量噪声时，抢救已完整的用例对象
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
) -> tuple[list[str], list[str]]:
    """把上传文件落到临时目录。返回 (vision 用的图片绝对路径, 文本片段列表[文档正文/截图OCR])。"""
    import os
    from ai_gateway.gateway import ocr_extract
    from server.services.doc_parser import parse_document

    image_paths: list[str] = []
    text_chunks: list[str] = []

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
    return image_paths, text_chunks


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
        out.append({
            "type": "http_request",
            "config": {
                "method": method,
                "path": path,
                "data_type": cfg.get("data_type") or "application/json",
                "params": cfg.get("params") or cfg.get("body") or {},
                "headers": cfg.get("headers") or {},
                "extract_data": {str(k): str(v) for k, v in ext.items() if str(k).strip()},
            },
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
            "preconditions": [str(x).strip() for x in (it.get("preconditions") or []) if str(x).strip()],
            "steps": [str(x).strip() for x in (it.get("steps") or []) if str(x).strip()],
            "expected": [str(x).strip() for x in (it.get("expected") or []) if str(x).strip()],
            "after": str(it.get("after") or "").strip(),
        }
        # 接口模式的结构化字段（功能模式不会出现，透传给前端映射到 api 用例字段）
        for key in ("method", "path", "headers", "body", "extract", "assertion", "sql"):
            if key in it and it[key] not in (None, "", [], {}):
                item[key] = it[key]
        # 场景多步：requests 数组（每项一次接口调用）
        if isinstance(it.get("requests"), list) and it["requests"]:
            reqs = []
            for r in it["requests"]:
                if not isinstance(r, dict) or not r.get("path"):
                    continue
                rr = {
                    k: r[k]
                    for k in ("name", "method", "path", "headers", "body", "extract", "assertion", "sql")
                    if r.get(k) not in (None, "", [], {})
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
        return case["requests"]
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
_KNOWN_FUNCS_CACHE: set[str] | None = None


def _known_function_names() -> set[str]:
    global _KNOWN_FUNCS_CACHE
    if _KNOWN_FUNCS_CACHE is None:
        try:
            import inspect
            from utils.function_executor import function_name
            _KNOWN_FUNCS_CACHE = {
                n for n, f in function_name().items()
                if inspect.isfunction(f) and not n.startswith("_")
            }
        except Exception:
            _KNOWN_FUNCS_CACHE = set()
    return _KNOWN_FUNCS_CACHE


def _unknown_functions(case: dict) -> set[str]:
    """找出用例里引用了但平台没注册的 function 名（AI 瞎编的）。"""
    known = _known_function_names()
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
        body = req.get("body")
        if not isinstance(body, dict):
            continue
        for key in ("username", "user_name", "account"):
            val = body.get(key)
            if isinstance(val, str) and val.strip().startswith("function:unique"):
                return True
    return False


def _harden_generated_cases(cases: list[dict], var_pool_keys: set[str], carried_vars: set[str]) -> list[dict]:
    """生成后校验 + 加固（问题5/6/7 + 数据治理#1）：
      - 未解析 ${var}（无变量池来源、无前置用例 extract 产出）→ 记 warnings 引导用户/下一轮 AI。
      - 缺断言 → 记 warnings。
      - 正向写入用例的留库字段是写死字面量 → 自动改成 function:unique 命名空间。
    `carried_vars`：跨批次带过来的、前面批次已产出的变量名。
    """
    produced_so_far = set(var_pool_keys) | set(carried_vars)
    for case in cases:
        warnings: list[str] = []
        negative = _is_negative_case(case.get("name") or "")
        reqs = _case_requests(case)

        # 0) 空壳用例兜底：没有任何可执行请求（常见于并发/性能/压测类——平台顺序执行
        #    引擎无法表达，AI 往往只给了名字、结构化字段全空）。打警告，不让它静默空白。
        if not reqs:
            warnings.append(
                "该用例没有可执行的请求（method/path 为空）。若是并发/性能/压测类需求，"
                "本平台顺序执行无法表达——建议改成「连续多次重复提交」的顺序多步用例，或用专门的压测工具；"
                "否则请手工补全请求或删除本用例。"
            )
            case["warnings"] = warnings
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
                rewritten = _namespace_write_data(req.get("body"))
                if rewritten:
                    ds = case.setdefault("data_safety", {})
                    ds.setdefault("rewritten_fields", []).extend(rewritten)
                    ds["cleanup_required"] = True

        # 1.4) 引用了不存在的动态函数（AI 瞎编函数名）→ 执行必报错
        bad_funcs = _unknown_functions(case)
        if bad_funcs:
            warnings.append(
                f"用到了平台不存在的动态函数：{', '.join(sorted('function:' + n for n in bad_funcs))}。"
                "动态值只能用已注册的函数（如 function:unique / unique_mobile / unique_email），不要自己造名字。"
            )

        # 1.5) 用没创建过的 function:unique 账号登录 → 必然 401
        if _login_with_uncreated_unique(case):
            warnings.append(
                "登录步骤用了 function:unique 用户名，但该账号没有先被创建——登录会 401。"
                "请改成：先调创建账号接口建号、提取真实用户名再登录；或直接用变量池账号 ${my_account} 登录。"
            )

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

        # 3) 变量解析校验（按用例数组顺序累积）
        #    pre_hook 登录会在跑 steps 前提取变量(如 token)进 ctx，视作本用例可满足
        refs = _referenced_vars({"reqs": reqs})
        producible = produced_so_far | _produced_vars(case) | _pre_hook_vars(case)
        for var in sorted(refs):
            base = var.split(".")[0]
            if base not in producible:
                warnings.append(
                    f"变量 ${{{var}}} 找不到来源：请补充能 extract 出它的前置用例，或确认变量池里是否有该变量"
                )

        produced_so_far |= _produced_vars(case)
        if warnings:
            case["warnings"] = warnings
    return cases


def _auto_repair_flawed_cases(
    db,
    cfg,
    cases: list[dict],
    var_pool_keys: set[str],
    carried_vars: set[str],
    variable_pool_block: str = "",
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
                    + "\n\n可用动态函数：function:unique / unique_mobile / unique_email"
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
        repaired = _shape_cases(_extract_json_list(raw))
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
    merged = _harden_generated_cases(merged, var_pool_keys, carried_vars)
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
    body = req.get("body") or {}
    return json.dumps(
        {"method": method, "path": path, "headers": headers, "body": body},
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


_ALL_DIMENSIONS = ("正常", "参数校验", "边界", "鉴权", "越权", "响应校验", "安全", "场景", "关联")


def _dimensions_block(raw: str) -> str:
    """把用户勾选的维度清单渲染成 prompt 文本；留空=不限制（按覆盖力度自动取舍）。"""
    picked = [d.strip() for d in re.split(r"[\s,，;；]+", raw or "") if d.strip() and d.strip() in _ALL_DIMENSIONS]
    if not picked:
        return "（未指定，按覆盖力度自动取舍全部维度）"
    return "只规划以下维度的测试点，其它维度不要生成：" + "、".join(picked)


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
    if level == "exhaustive":
        target_min = endpoint_count * min(dim_count, 9) + param_count * 4
        target_max = endpoint_count * min(dim_count + 4, 13) + param_count * 6
    elif level == "full":
        target_min = endpoint_count * min(dim_count, 6) + param_count * 2
        target_max = endpoint_count * min(dim_count + 2, 8) + param_count * 3
    else:
        target_min = endpoint_count + max(2, min(endpoint_count * 3, param_count + endpoint_count * 2))
        target_max = target_min + max(2, endpoint_count * 2)
    target_min = max(3, min(target_min, 180))
    target_max = max(target_min, min(target_max, 240))
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
        image_paths, text_chunks = _ingest_uploads(images, docs, tmpdir, use_vision=use_vision)
        parts: list[str] = []
        if (text or "").strip():
            parts.append((text or "").strip())
        parts.extend(text_chunks)
        # 接口文档链接：逐个拉取解析
        for u in re.split(r"[\s,，;；]+", doc_urls or ""):
            u = u.strip()
            if not u:
                continue
            fetched = _fetch_doc_url(u)
            if fetched:
                parts.append(f"## 接口文档（链接）：{u}\n{fetched}")
        if use_vision and image_paths:
            parts.append(f"（另附 {len(image_paths)} 张界面/原型截图，请结合图片内容规划测试点）")
        if setup_doc.strip():
            parts.append(
                "## 账号准备/注册接口（供前置链跨模块建一次性测试账号用，请写进 digest）：\n"
                + setup_doc.strip()[:2000]
            )
        requirement_text = "\n\n".join(parts) or "（未提供需求文本，请基于模块名与下方跨模块信息合理推断）"
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
            "PROJECT_CONTEXT": _project_context_block(
                module.project_id, f"{module.name}\n{requirement_text}"
            ),
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

    # 注意：这里**不再**自动把规划出的测试点落库 —— 否则每点一次“生成大纲”
    # 都会往大纲里灌一批还没有对应用例的 gap 点，积累成垃圾。大纲只保留“同步过的”
    # 数据：由「刷新对齐」按真实用例建立/更新，或用户在生成用例后由关联落库。
    image_strategy = "vision" if use_vision else ("ocr" if has_images else "none")
    return {
        "status": "success",
        "data": {"digest": digest, "points": points, "model": model_name, "image_strategy": image_strategy},
    }


class BatchPoint(pydantic.BaseModel):
    title: str
    category: str = ""


# ---------------------------------------------------------------------------
# 模块大纲：长期保存 + 刷新对齐（大纲 ↔ 当前用例）。设计见 docs/module_outline_design.md
# ---------------------------------------------------------------------------
@router.get("/module_outline")
def get_module_outline(db: DBDep, module_id: int = Query(...)):
    """读某模块的大纲（digest + 测试点 + 覆盖统计）。没有则返回 null。"""
    from server.services.module_outline_service import get_outline

    outline = get_outline(db.session, module_id)
    return {"status": "success", "data": outline.to_dict() if outline else None}


class OutlineAlignRequest(pydantic.BaseModel):
    module_id: int
    mode: str = "functional"


@router.post("/module_outline/align_preview")
def module_outline_align_preview(payload: OutlineAlignRequest, db: DBDep):
    """算大纲 ↔ 当前用例的 diff（不落库），给前端预览。"""
    from server.services.module_outline_service import compute_align_changes

    data = compute_align_changes(db.session, payload.module_id, payload.mode)
    return {"status": "success", "data": data}


@router.post("/module_outline/apply")
def module_outline_apply(payload: OutlineAlignRequest, db: DBDep):
    """按最新用例重算并应用对齐（幂等，服务端重算不信任陈旧 diff）。"""
    from server.services.module_outline_service import apply_align

    data = apply_align(db.session, payload.module_id, payload.mode)
    return {"status": "success", "data": data, "message": "大纲已对齐"}


@router.post("/module_outline/purge_gaps")
def module_outline_purge_gaps(payload: OutlineAlignRequest, db: DBDep):
    """清理没有关联用例的测试点（缺口垃圾），只保留同步自真实用例的点。"""
    from server.services.module_outline_service import purge_unlinked_points

    data = purge_unlinked_points(db.session, payload.module_id)
    return {"status": "success", "data": data, "message": f"已清理 {data['removed']} 个未覆盖测试点"}


class OutlineReplanRequest(pydantic.BaseModel):
    module_id: int
    model_name: str
    mode: str = "interface"
    change_text: str = ""          # 本次新增 / 变更的需求（增量模式只填 delta）
    incremental: bool = True       # True=在现有大纲上增量；False=按 change_text 全量重规划


def _run_outline_ai(db, module, mode: str, requirement_text: str, model_name: str, coverage: str = "full"):
    """复用 outline prompt 跑一次 AI，返回 (digest, points)。供增量重规划用。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options

    cfg = _resolve_model(db, model_name, module.project_id)
    call_options = model_task_options(cfg, "api_outline" if mode == "interface" else "functional_outline")
    placeholders = {
        "MODULE_NAME": module.name,
        "REQUIREMENT_TEXT": requirement_text,
        "PROJECT_CONTEXT": _project_context_block(
            module.project_id, f"{module.name}\n{requirement_text}"
        ),
        "CROSS_MODULE_CONTEXT": _build_cross_module_context(db, module),
        "EXISTING_CASES": "、".join(
            _existing_case_names(
                db, module.id,
                case_type=CASE_TYPE_API if mode == "interface" else CASE_TYPE_FUNCTIONAL,
            )
        ) or "（本模块暂无已有用例）",
        "VARIABLE_POOL": _variable_pool_block(db, module.project_id) if mode == "interface" else "",
        "COVERAGE_LEVEL": _coverage_text(coverage),
        "DIMENSIONS": "",
    }
    template = _load_prompt("interface_case_outline" if mode == "interface" else "functional_case_outline")
    prompt = _render_prompt(template, placeholders)
    raw, _tin, _tout = chat_markdown(
        prompt,
        cfg,
        timeout=call_options["timeout"],
        system_prompt=(
            "你是结构化 JSON 生成器。必须只输出一个合法 JSON 对象，"
            "对象必须包含 digest 字符串和 points 数组。"
            "不要输出 Markdown、解释、思考过程或代码块外文字。"
        ),
        enable_thinking=call_options["enable_thinking"],
        json_mode=call_options["json_mode"],
        max_tokens=call_options["max_tokens"],
        temperature=call_options["temperature"],
        reasoning_effort=call_options.get("reasoning_effort"),
    )

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
                obj = json.loads(raw[s:e + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        logger.warning(
            "module_outline replan parse failed module_id=%s mode=%s model=%s raw_prefix=%r",
            module.id,
            mode,
            model_name,
            (raw or "")[:1000],
        )
        raise HTTPException(status_code=502, detail="大纲解析失败，请重试或更换模型")
    digest = str(obj.get("digest") or "").strip()
    points = []
    for p in obj.get("points") or []:
        if isinstance(p, dict) and str(p.get("title") or "").strip():
            points.append({"title": str(p["title"]).strip()[:200], "category": str(p.get("category") or "").strip()})
        elif isinstance(p, str) and p.strip():
            points.append({"title": p.strip()[:200], "category": ""})
    return digest, points


@router.post("/module_outline/replan_preview")
def module_outline_replan_preview(payload: OutlineReplanRequest, db: DBDep):
    """AI 增量重规划预览：在现有大纲上，针对本次变更产出新测试点的 diff（不落库）。"""
    from server.services.module_outline_service import get_outline, diff_ai_points

    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")

    # 增量模式：把现有测试点作为上下文，让 AI 只针对变更补新点、不重复已有。
    outline = get_outline(db.session, payload.module_id)
    parts = []
    if payload.change_text.strip():
        parts.append("## 本次新增 / 变更的需求\n" + payload.change_text.strip())
    if payload.incremental and outline and outline.points:
        existing_block = "\n".join(
            f"- {p.title}" + (f"（{p.category}）" if p.category else "")
            for p in outline.points
        )
        parts.append(
            "## 该模块已有测试点（请勿重复；只针对上面的变更补充新测试点）：\n" + existing_block
        )
        if outline.digest:
            parts.append("## 已有需求摘要 digest（供参考，保持连贯）：\n" + outline.digest)
    requirement_text = "\n\n".join(parts) or "（未提供变更说明，请基于模块名与跨模块信息推断需要补充的测试点）"

    digest, points = _run_outline_ai(db, module, payload.mode, requirement_text, payload.model_name)
    diff = diff_ai_points(db.session, payload.module_id, points)
    diff["digest"] = digest
    diff["points"] = points  # 回传给 apply（避免再跑一次 AI）
    return {"status": "success", "data": diff}


class OutlineReplanApplyRequest(pydantic.BaseModel):
    module_id: int
    mode: str = "interface"
    digest: str = ""
    points: list[BatchPoint] = []


@router.post("/module_outline/replan_apply")
def module_outline_replan_apply(payload: OutlineReplanApplyRequest, db: DBDep):
    """应用 AI 增量重规划：把预览产出的新测试点写入大纲（按标题去重，不清空已有）。"""
    from server.services.module_outline_service import upsert_outline_from_ai

    outline = upsert_outline_from_ai(
        db.session,
        module_id=payload.module_id,
        mode=payload.mode,
        digest=payload.digest,
        points=[{"title": p.title, "category": p.category} for p in payload.points],
    )
    return {"status": "success", "data": outline.to_dict(), "message": "大纲已更新"}


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
    done_names: list[str] = []
    mode: str = "functional"
    # 跨批次已产出的变量名（前端把前面批次 extract 出的变量累积传过来，避免误报"找不到来源"）
    carried_vars: list[str] = []
    # 用户直接提供的"账号准备/注册"接口信息（文本），供前置链跨模块建账号用
    setup_doc: str = ""


class AiCaseEnhanceRequest(pydantic.BaseModel):
    module_id: int
    agent_model_name: str
    digest: str = ""
    requirement_text: str = ""
    cases: list[dict[str, Any]]
    mode: str = "functional"
    target_extra_count: int = 5


def _available_functions_block() -> str:
    """枚举平台已注册的动态函数（function:xxx），生成"名字+作用"清单喂给 AI，
    防止它瞎编不存在的函数名（如 function:random_username）。"""
    try:
        import inspect
        from utils.function_executor import function_name
        funcs = function_name()
    except Exception:
        return "function:unique(前缀)、function:unique_mobile()、function:unique_email()（无法读取完整列表）"
    lines: list[str] = []
    for name, fn in funcs.items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        doc = (inspect.getdoc(fn) or "").strip().splitlines()
        desc = doc[0].strip() if doc else "（无说明）"
        try:
            params = inspect.signature(fn).parameters
            req = [
                p.name for p in params.values()
                if p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)
                and p.default is p.empty and p.name not in ("args", "kwargs")
            ]
            arg_hint = "(" + ", ".join(req) + ")" if req else "()"
        except Exception:
            arg_hint = "()"
        lines.append(f"- function:{name}{arg_hint} —— {desc}")
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
def ai_generate_batch(payload: AiBatchRequest, db: DBDep):
    """第二步：基于 digest + 本批测试点 + 已生成用例名 → 生成这一批的控件级详细用例。
    每批都带 done_names 避免重复，带 digest 保证多批连贯。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options

    if not payload.points:
        raise HTTPException(status_code=400, detail="本批没有测试点")

    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    cfg = _resolve_model(db, payload.model_name, module.project_id)

    batch_points = "\n".join(
        f"- [{p.category or '未分类'}] {p.title}" for p in payload.points
    )
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
        "AVAILABLE_FUNCTIONS": _available_functions_block() if payload.mode == "interface" else "",
        # 真实响应结构 / API 约定（从记忆层 api_contract 检索）——这是写对
        # extract/assertion JSONPath 的关键:没它模型只能照 prompt 示例猜路径
        "PROJECT_CONTEXT": _project_context_block(
            module.project_id, f"{module.name}\n{payload.digest}\n{batch_points}"
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
        parsed_obj = _extract_json_list(raw_text)
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
        # 失败时把 6 条拆成单点逐条再问，既降低输出长度，也减少模型安全/截断导致的空响应。
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
        cases = _harden_generated_cases(cases, var_pool_keys, carried)
        # P0-2: 有 warnings 的用例先让模型自修一轮，仍有问题的保留 warnings 给人看
        cases = _auto_repair_flawed_cases(
            db, cfg, cases, var_pool_keys, carried,
            variable_pool_block=placeholders["VARIABLE_POOL"],
        )

    return {"status": "success", "data": {"cases": cases, "model": payload.model_name}}


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

    existing = _existing_case_names(
        db,
        payload.module_id,
        case_type=CASE_TYPE_API if payload.mode == "interface" else CASE_TYPE_FUNCTIONAL,
    )
    prompt = build_case_enhancement_prompt(
        module_name=module.name,
        mode=payload.mode,
        digest=payload.digest,
        requirement_text=payload.requirement_text,
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
            var_pool_keys = _variable_pool_keys(db, module.project_id)
            cases = _harden_generated_cases(cases, var_pool_keys, _module_produced_vars(db, payload.module_id))

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
    verify: bool = True    # 应用后自动重跑验证 + 绿变红自动回滚
    max_rounds: int = 3    # 多轮修复上限：仍失败的用例带新证据自动再诊断再修（1=只修一轮）


@router.post("/ai_report_fix/apply")
def ai_report_fix_apply(payload: ReportFixApplyRequest, db: DBDep, user: OptionalUserDep = None):
    """把 AI 诊断结果（预检通过的部分）应用到用例，并触发闭环验证。

    与旧的前端逐条 update 相比：
      1. 应用前跑 preflight（分类过滤 / 真实响应 JSONPath 预检 / 变量产出校验 / params 合并），
         坏修复直接拦掉，不落库；
      2. 每条用例一个编辑事件、共用一个 batch，支持按用例精准回滚；
      3. verify=True 时自动重跑原报告全部用例（保证 ${var} 依赖链完整），
         绿变红的用例自动回滚——修复率在机制上不再可能为负。

    返回 {batch_id, applied, skipped, verify_report_id}；闭环结果由
    tasks.verify_ai_fix 写入 ai_run.output_payload["verify"]，前端轮询即可。
    """
    from database.models import AiRun, TestReport, AI_FEATURE_API_REPORT_FIX
    from server.services.ai_fix_service import apply_report_fixes, prepare_verification_run

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

    items = output.get("items") or []
    result = apply_report_fixes(
        db.session, report_id, items,
        operator_id=user.id if user else None,
    )

    verify_report_id: int | None = None
    prepared: dict | None = None
    if payload.verify and result["applied"] and report.project_id is None:
        logger.warning("[ai_fix] 报告 %s 没有 project_id，跳过闭环验证", report_id)
    if payload.verify and result["applied"] and report.project_id is not None:
        prepared = prepare_verification_run(
            db.session,
            project_id=report.project_id,
            category=report.category,
            base_report_id=report_id,
        )
        if prepared:
            verify_report_id = prepared["report_id"]

    # apply 结果先写回 ai_run；多轮循环状态放 loop/rounds，闭环结果由 verify 任务追加
    max_rounds = max(1, min(int(payload.max_rounds or 3), 5))
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
        # 没有验证闭环（verify=False / 无 project / 无可应用修复）→ 立即按诊断分类打标：
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


# 会改动共享数据 / 状态的 HTTP 方法
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 用例名/路径里命中这些词，说明它很可能改了某个共享账号/资源的状态
_STATE_MUTATION_HINTS = (
    "修改密码", "改密", "重置密码", "password", "改用户名", "改资料", "更新资料",
    "删除", "delete", "禁用", "注销", "锁定", "logout", "登出", "吊销", "revoke",
)


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
        steps.append(
            {
                "step_id": step.id,
                "step_name": step.step_name,
                "method": config.get("method"),
                "path": config.get("path"),
                "headers": config.get("headers") or {},
                "data_type": config.get("data_type") or "application/json",
                "params": config.get("params") or {},
                "extract": step.extract or [],
                "assertion": step.assertion or [],
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
        "note": "params 是平台实际发送的请求体/请求参数模板；修请求参数时请返回完整 fix.params。",
    }


def _build_report_dependency_context(items_ordered: list[dict], case_map: dict) -> tuple[str, dict[str, str]]:
    """构建"全局执行上下文"，让 AI 修复时能看清接口的**上下依赖**与**参数产出/引用关系**。

    每个 chunk 都会带上这段（哪怕分块调用），所以即使 0005 改密码、0025 重登录被切到
    不同 chunk，模型也能看到完整链路。包含四部分：
      1. 按执行顺序的用例清单（顺序 + method + path）；
      2. 变量产出表：每个变量最早由哪条用例 extract 出来（token/test_token01/id …）；
      3. 变量引用表：每个变量被哪些用例 ${var} 引用；
      4. 状态变更用例：会改共享账号/资源的用例（改密码、删除等）——下游若复用同一账号
         却仍引用旧值（如 ${my_password}），会失败。

    返回 (context_text, producers)；producers 供程序化线索（hints）复用。
    """
    producers: dict[str, str] = {}          # var -> "0002 登录测试账号 (extract $.data.token)"
    consumers: dict[str, set[str]] = {}     # var -> {引用它的用例名}
    order_lines: list[str] = []
    mutations: list[str] = []

    for idx, it in enumerate(items_ordered, start=1):
        name = str(it.get("name") or f"case#{it.get('case_id')}")
        d = it.get("def") or {}
        results = it.get("result") or []
        # method / path：def 优先，缺了就从执行请求里兜底
        method = str(d.get("method") or "").upper()
        path = str(d.get("path") or "")
        if (not method or not path) and results:
            req = _parse_json_loose(results[0].get("request"))
            method = method or str(req.get("请求方法") or req.get("method") or "").upper()
            path = path or str(req.get("请求地址") or req.get("url") or req.get("path") or "")
        order_lines.append(f"{idx}. {name}  [{method or '?'} {path or '?'}]")

        # —— 产出：extract 定义 + 实际 extract_values ——
        produced_here: dict[str, str] = {}
        extract_def = d.get("extract_data")
        if isinstance(extract_def, dict):
            for var, jp in extract_def.items():
                produced_here[str(var)] = str(jp)
        elif isinstance(extract_def, list):
            for rule in extract_def:
                if isinstance(rule, dict) and rule.get("name"):
                    produced_here[str(rule.get("name"))] = str(rule.get("jsonpath") or rule.get("path") or "")
        for step_def in d.get("steps") or []:
            if not isinstance(step_def, dict):
                continue
            ex = step_def.get("extract")
            if isinstance(ex, dict):
                for var, jp in ex.items():
                    produced_here.setdefault(str(var), str(jp))
            elif isinstance(ex, list):
                for rule in ex:
                    if isinstance(rule, dict) and rule.get("name"):
                        produced_here.setdefault(
                            str(rule.get("name")),
                            str(rule.get("jsonpath") or rule.get("path") or ""),
                        )
        for r in results:
            for var in _parse_json_loose(r.get("extract_values")).keys():
                produced_here.setdefault(str(var), "")
        for var, jp in produced_here.items():
            if var and var not in producers:   # 只记最早的产出方
                tail = f" (extract {jp})" if jp else ""
                producers[var] = f"{name}{tail}"

        # —— 引用：扫描 def + 实际请求里的 ${var} ——
        ref_blob = json.dumps(d, ensure_ascii=False, default=str)
        for r in results:
            ref_blob += " " + str(r.get("request") or "")
        for var in set(_VAR_REF_RE.findall(ref_blob)):
            consumers.setdefault(str(var).split(".")[0], set()).add(name)

        # —— 状态变更标记 ——
        hay = f"{name} {path}".lower()
        if method in _MUTATING_METHODS and any(h.lower() in hay for h in _STATE_MUTATION_HINTS):
            mutations.append(f"- {name} [{method} {path}] —— 会改共享账号/资源状态")

    lines: list[str] = []
    lines.append("## 全局执行上下文（本报告所有用例，按执行顺序；用于判断接口上下依赖与参数关系）")
    lines.append("### 执行顺序")
    lines.extend(order_lines)
    lines.append("### 变量产出表（变量 → 最早产出它的用例；下游要用某变量，必须有产出方且排在它前面）")
    if producers:
        for var in sorted(producers):
            lines.append(f"- ${{{var}}} ← {producers[var]}")
    else:
        lines.append("- （无用例 extract 出任何变量）")
    lines.append("### 变量引用表（变量 → 引用它的用例）")
    if consumers:
        for var in sorted(consumers):
            who = "、".join(sorted(consumers[var])[:8])
            lines.append(f"- ${{{var}}}: {who}")
    else:
        lines.append("- （无用例引用 ${变量}）")
    lines.append("### 状态变更用例（会改共享数据；其后再用同一账号的旧密码/旧 token 等会失败）")
    lines.extend(mutations or ["- （无）"])
    return "\n".join(lines), producers


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
    report_context, producers = _build_report_dependency_context(items_in, case_map)

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
        if hints:
            it["hints"] = hints
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
            return {"items": [], "total": 0}

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

    for chunk in chunks:
        prompt = _render_prompt(template, {
            "REPORT_CONTEXT": report_context[:6000],
            "CASES": json.dumps(chunk, ensure_ascii=False),
        })
        try:
            raw, _tin, _tout = chat_markdown(
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
        parsed = _extract_json_list(raw)
        if isinstance(parsed, list):
            for x in parsed:
                if not isinstance(x, dict):
                    continue
                fix = x.get("fix") if isinstance(x.get("fix"), dict) else {}
                try:
                    _cid = int(x.get("case_id"))
                except Exception:
                    _cid = None
                reorder = fix.get("reorder") if isinstance(fix.get("reorder"), dict) else {}
                # 场景多步用例的按步修复：fix.steps=[{step_id, params, headers}]
                step_fixes = []
                for sf in fix.get("steps") or []:
                    if not isinstance(sf, dict):
                        continue
                    try:
                        _sid = int(sf.get("step_id"))
                    except Exception:
                        continue
                    sf_params = sf.get("params") if isinstance(sf.get("params"), dict) else {}
                    sf_headers = sf.get("headers") if isinstance(sf.get("headers"), dict) else {}
                    if sf_params or sf_headers:
                        step_fixes.append({"step_id": _sid, "params": sf_params, "headers": sf_headers})
                out.append({
                    "case_id": _cid,
                    "module_id": case_map[_cid].module_id if _cid in case_map else None,
                    "name": str(x.get("name") or "").strip(),
                    "classification": str(x.get("classification") or "").strip(),
                    "findings": [str(f).strip() for f in (x.get("findings") or []) if str(f).strip()],
                    "fix": {
                        "extract": fix.get("extract") if isinstance(fix.get("extract"), dict) else {},
                        "assertion": fix.get("assertion") if isinstance(fix.get("assertion"), dict) else {},
                        "params": fix.get("params") if isinstance(fix.get("params"), dict) else {},
                        "headers": fix.get("headers") if isinstance(fix.get("headers"), dict) else {},
                        "steps": step_fixes,
                        "reorder": {"before_case_name": str(reorder.get("before_case_name") or "").strip()}
                        if reorder.get("before_case_name")
                        else {},
                    },
                })

    return {"items": out, "total": len(items_in)}


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
    data = [serialize_test_case_event(row) for row in event_rows]
    data.extend(r.to_dict() for r in rows)
    data.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"status": "success", "data": data[:limit]}


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
    """Excel 单元格里的多行文本（"\n" 分隔）→ 列表，去空行。"""
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


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
            spec = {
                "preconditions": _split_lines(row.get("preconditions")),
                "steps": _split_lines(row.get("steps")),
                "expected": (str(row.get("expected")).strip()
                             if pd.notna(row.get("expected")) and str(row.get("expected")).strip()
                             else None),
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
            "preconditions": "\n".join(spec.get("preconditions") or []),
            "steps": "\n".join(spec.get("steps") or []),
            "expected": spec.get("expected") or "",
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
