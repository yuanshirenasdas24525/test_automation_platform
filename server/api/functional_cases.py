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


def _summarize_openapi(data: dict) -> str:
    """OpenAPI/Swagger → 人读接口清单。"""
    lines = ["# OpenAPI/Swagger 接口清单"]
    paths = data.get("paths") or {}
    for path, methods in list(paths.items())[:200]:
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch", "head", "options"):
                continue
            op = op if isinstance(op, dict) else {}
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


def _api_text_from_obj(data) -> str:
    """已解析的 OpenAPI/Postman/任意结构 → 人读接口清单文本。"""
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)[:8000] if data is not None else ""
    if "openapi" in data or "swagger" in data or "paths" in data:
        return _summarize_openapi(data)
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


def _fetch_doc_url(url: str, _depth: int = 0) -> str:
    """拉取接口文档链接 → 接口清单/正文文字。支持规范文件直链、Swagger UI、普通文档页。"""
    import requests  # 已在 requirements

    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return f"（不是合法链接：{url}）"
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"（链接拉取失败：{e}）"

    ctype = (resp.headers.get("content-type") or "").lower()
    text = resp.text or ""
    bare = url.lower().split("?", 1)[0]

    if "json" in ctype or text.lstrip().startswith(("{", "[")):
        try:
            return _api_text_from_obj(json.loads(text))
        except Exception:
            pass
    if "yaml" in ctype or bare.endswith((".yaml", ".yml")):
        try:
            import yaml

            return _api_text_from_obj(yaml.safe_load(text))
        except Exception:
            pass
    # HTML：先找规范地址，找到就拉它（最多再下钻一层）
    if _depth < 1:
        spec_url = _discover_spec_url(text, url)
        if spec_url and spec_url != url:
            return _fetch_doc_url(spec_url, _depth + 1)
    return _html_to_text(text)[:8000]


def _extract_json_list(raw: str):
    """从 LLM 输出里抽 JSON 数组：```json``` 围栏 / 第一个 [...] / 整段直接 loads。"""
    if not raw:
        return None
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start, end = raw.find("["), raw.rfind("]")
    if 0 <= start < end:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            pass
    try:
        return json.loads(raw)
    except Exception:
        return None


def _build_cross_module_context(db, module: "Module") -> str:
    """跨模块上下文：① 项目概览 + 与本模块相关的模块关联关系（来自 project.ai_overview，
    若已生成）；② 同项目其它模块 + 各自最多 8 个功能用例名。给 AI 做跨模块联动设计。"""
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
    """读项目 default_parameters 变量池（项目专属覆盖全局），喂给 AI 让接口用例优先用 ${变量}。"""
    rows = (
        db.session.query(ConfigStore)
        .filter(
            ConfigStore.config_group == "default_parameters",
            (ConfigStore.project_id == project_id) | (ConfigStore.project_id.is_(None)),
        )
        .all()
    )
    seen: dict[str, ConfigStore] = {}
    for r in sorted(rows, key=lambda x: 0 if x.project_id is None else 1):
        if r.config_key:
            seen[r.config_key] = r
    if not seen:
        return ""
    lines = []
    for key in seen:
        desc = _VAR_POOL_DESC.get(key, "")
        lines.append(f"- ${{{key}}}{('：' + desc) if desc else ''}")
    return "\n".join(lines)


def _existing_case_names(db, module_id: int, limit: int = 300) -> list[str]:
    rows = (
        db.session.query(TestCase.name)
        .filter(TestCase.module_id == module_id, TestCase.case_type == CASE_TYPE_FUNCTIONAL)
        .order_by(TestCase.sort_order)
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows if r[0]]


def _shape_cases(parsed) -> list[dict]:
    """把 LLM 解析出的 list 规整成 {name, preconditions[], steps[], expected[]}。"""
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
        out.append(item)
    return out


_COVERAGE_TEXT = {
    "standard": "标准覆盖：主流程 + 主要的异常/边界/权限场景即可，控制数量、别太碎。",
    "full": "全面覆盖：按下方维度清单，对每个功能点/每个参数逐项系统出点，适用维度都要覆盖，不要只写主流程。",
    "exhaustive": "穷尽覆盖：把每个输入字段/参数的每个维度都拆成独立测试点（等价类的每个无效类、每个边界值、每种格式错误都单列），并覆盖组合场景。宁可几十上百条，越细越好。",
}


def _coverage_text(c: str) -> str:
    return _COVERAGE_TEXT.get((c or "standard").strip().lower(), _COVERAGE_TEXT["standard"])


def _resolve_model(db, model_name: str):
    from server.services.ai_model_service import get_ai_model

    cfg = get_ai_model(db.session, model_name)
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"AI 模型 {model_name!r} 未配置，请先到「配置中心 → AI 模型」添加")
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
    )

    cfg = _resolve_model(db, model_name)
    module = db.session.query(Module).filter(Module.id == module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")

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
        requirement_text = "\n\n".join(parts) or "（未提供需求文本，请基于模块名与下方跨模块信息合理推断）"

        existing = _existing_case_names(db, module_id)
        existing_block = "、".join(existing) if existing else "（本模块暂无已有用例）"
        placeholders = {
            "MODULE_NAME": module.name,
            "REQUIREMENT_TEXT": requirement_text,
            "CROSS_MODULE_CONTEXT": _build_cross_module_context(db, module),
            "EXISTING_CASES": existing_block,
            "VARIABLE_POOL": _variable_pool_block(db, module.project_id) if mode == "interface" else "",
            "COVERAGE_LEVEL": _coverage_text(coverage),
        }
        template = _load_prompt(
            "interface_case_outline" if mode == "interface" else "functional_case_outline"
        )
        prompt = _render_prompt(template, placeholders)

        try:
            if use_vision and image_paths:
                raw, _tin, _tout = chat_markdown_with_images(prompt, image_paths, cfg, timeout=240)
            else:
                raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=180)
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

    image_strategy = "vision" if use_vision else ("ocr" if has_images else "none")
    return {
        "status": "success",
        "data": {"digest": digest, "points": points, "model": model_name, "image_strategy": image_strategy},
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


@router.post("/ai_outline_gaps")
def ai_outline_gaps(payload: OutlineGapRequest, db: DBDep):
    """查漏补缺：给已有大纲找遗漏的测试点，返回补充点（已去重已有点/已有用例）。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown

    cfg = _resolve_model(db, payload.model_name)
    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")

    existing_points = (
        "\n".join(f"- [{p.category or '未分类'}] {p.title}" for p in payload.points)
        if payload.points
        else "（暂无）"
    )
    existing = _existing_case_names(db, payload.module_id)
    placeholders = {
        "DIGEST": payload.digest.strip() or "（无摘要，请按已规划测试点合理推断）",
        "CROSS_MODULE_CONTEXT": _build_cross_module_context(db, module),
        "EXISTING_POINTS": existing_points,
        "EXISTING_CASES": "、".join(existing) if existing else "（无）",
    }
    template = _load_prompt("outline_gaps")
    prompt = _render_prompt(template, placeholders)
    try:
        raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=180)
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
    have = {_norm_name(p.title) for p in payload.points} | {_norm_name(n) for n in existing}
    points = []
    for p in (obj or {}).get("points") or []:
        if isinstance(p, dict):
            title = str(p.get("title") or "").strip()
            if title and _norm_name(title) not in have:
                have.add(_norm_name(title))
                points.append({"title": title[:200], "category": str(p.get("category") or "").strip()})
    return {"status": "success", "data": {"points": points}}


class AiBatchRequest(pydantic.BaseModel):
    module_id: int
    model_name: str
    digest: str = ""
    points: list[BatchPoint]
    done_names: list[str] = []
    mode: str = "functional"


@router.post("/ai_generate_batch")
def ai_generate_batch(payload: AiBatchRequest, db: DBDep):
    """第二步：基于 digest + 本批测试点 + 已生成用例名 → 生成这一批的控件级详细用例。
    每批都带 done_names 避免重复，带 digest 保证多批连贯。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown

    if not payload.points:
        raise HTTPException(status_code=400, detail="本批没有测试点")

    cfg = _resolve_model(db, payload.model_name)
    module = db.session.query(Module).filter(Module.id == payload.module_id).first()
    if module is None:
        raise HTTPException(status_code=404, detail="模块不存在")

    batch_points = "\n".join(
        f"- [{p.category or '未分类'}] {p.title}" for p in payload.points
    )
    session_done = [n.strip() for n in (payload.done_names or []) if n.strip()]
    existing = _existing_case_names(db, payload.module_id)
    # 喂给模型的「不要重复」清单：模块现有用例 + 本次已生成（截断防 prompt 过长）
    avoid = (existing[:200] + session_done)[-300:]
    done_names = "、".join(avoid) if avoid else "（暂无，这是第一批）"
    # 现有用例的有序清单（供 AI 决定每条新用例插在谁后面）
    existing_ordered = (
        "\n".join(f"{i + 1}. {n}" for i, n in enumerate(existing[:200]))
        if existing
        else "（本模块暂无已有用例，新用例按本批顺序排列即可）"
    )

    placeholders = {
        "MODULE_NAME": module.name,
        "DIGEST": payload.digest.strip() or "（无摘要，请按测试点标题合理推断）",
        "CROSS_MODULE_CONTEXT": _build_cross_module_context(db, module),
        "BATCH_POINTS": batch_points,
        "DONE_NAMES": done_names,
        "EXISTING_ORDERED": existing_ordered,
        "VARIABLE_POOL": _variable_pool_block(db, module.project_id) if payload.mode == "interface" else "",
    }
    template = _load_prompt(
        "interface_case_batch" if payload.mode == "interface" else "functional_case_batch"
    )
    prompt = _render_prompt(template, placeholders)

    try:
        raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=180)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 调用失败：{e}")

    parsed = _extract_json_list(raw)
    shaped = _shape_cases(parsed)
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
    return {"status": "success", "data": {"cases": cases, "model": payload.model_name}}


class DiagnoseRunRequest(pydantic.BaseModel):
    case_id: int
    model_name: str


@router.post("/ai_diagnose_run")
def ai_diagnose_run(payload: DiagnoseRunRequest, db: DBDep):
    """分析一条接口用例最近一次执行结果：分类(用例问题/接口问题/环境其他)+原因+建议，
    用例问题给出修正后的 extract/assertion 供「一键修复」。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown

    cfg = _resolve_model(db, payload.model_name)
    case = db.session.query(TestCase).filter(TestCase.id == payload.case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="用例不存在")
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
    case_def = {
        "name": case.name,
        "method": case.method,
        "path": case.path,
        "params": case.params,
        "extract_data": case.extract_data,
        "assertion": case.assertion,
    }
    placeholders = {
        "CASE_DEF": json.dumps(case_def, ensure_ascii=False)[:4000],
        "RUN_RESULT": json.dumps(run_result, ensure_ascii=False)[:9000],
    }
    template = _load_prompt("api_run_diagnose")
    prompt = _render_prompt(template, placeholders)
    try:
        raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=180)
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
            },
        },
    }


class ReportDiagnoseRequest(pydantic.BaseModel):
    report_id: int
    model_name: str


@router.post("/ai_diagnose_report")
def ai_diagnose_report(payload: ReportDiagnoseRequest, db: DBDep):
    """对一份测试报告里所有接口用例的执行结果做全面分析（分块调 AI），逐条返回分类+发现+修正。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown

    cfg = _resolve_model(db, payload.model_name)
    rows = (
        db.session.query(TestStepReport)
        .filter(TestStepReport.report_id == payload.report_id)
        .order_by(TestStepReport.case_id, TestStepReport.id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="该报告没有执行记录")

    by_case: dict[int, list] = {}
    for r in rows:
        if r.case_id is None:
            continue
        by_case.setdefault(r.case_id, []).append(r)

    case_map = {
        c.id: c
        for c in db.session.query(TestCase).filter(TestCase.id.in_(list(by_case.keys()))).all()
    }

    items_in = []
    for cid, rs in by_case.items():
        c = case_map.get(cid)
        if c is None:
            continue
        items_in.append({
            "case_id": cid,
            "name": c.name,
            "def": {
                "method": c.method,
                "path": c.path,
                "params": c.params,
                "extract_data": c.extract_data,
                "assertion": c.assertion,
            },
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

    out = []
    template = _load_prompt("api_report_diagnose")
    chunk_size = 6
    for i in range(0, len(items_in), chunk_size):
        chunk = items_in[i : i + chunk_size]
        prompt = _render_prompt(template, {"CASES": json.dumps(chunk, ensure_ascii=False)[:14000]})
        try:
            raw, _tin, _tout = chat_markdown(prompt, cfg, timeout=240)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"AI 调用失败：{e}")
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
                out.append({
                    "case_id": _cid,
                    "module_id": case_map[_cid].module_id if _cid in case_map else None,
                    "name": str(x.get("name") or "").strip(),
                    "classification": str(x.get("classification") or "").strip(),
                    "findings": [str(f).strip() for f in (x.get("findings") or []) if str(f).strip()],
                    "fix": {
                        "extract": fix.get("extract") if isinstance(fix.get("extract"), dict) else {},
                        "assertion": fix.get("assertion") if isinstance(fix.get("assertion"), dict) else {},
                    },
                })

    return {"status": "success", "data": {"items": out, "total": len(items_in)}}


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
    page_size: int = Query(50, ge=1, le=500),
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
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "status": "success",
        "data": {
            "items": items[start:end],
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
