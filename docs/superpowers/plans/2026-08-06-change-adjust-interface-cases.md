# 变更调整（接口变更驱动的用例增改删）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「AI 生成接口用例」抽屉里的旧「模块大纲」Tab 改造成「变更调整」：用户描述本次接口变更（可附文档/链接），AI 产出用例级调整大纲（新增/修改/删除），审阅确认后直接写入真实模块用例。

**Architecture:** 两阶段（规划→审阅→应用）。后端新增薄编排层，最大化复用现有能力：OpenAPI 解析用 `api_case_contract.build_contract_catalog`，文本文档用 `doc_parser.parse_document`，AI JSON 调用照抄 `_run_outline_ai` 模板，新增/修改用例的详情生成复用 `ai_generate_batch`，计划态用一条 `AiRun` 落库。旧模块大纲整套移除。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Celery（本功能同步即可）、pydantic v1 风格、React 19 + Vite + TS strict + Tailwind + shadcn。后端测试用 pytest（`tests/api/`），前端无测试框架 → typecheck + lint + build + 手动验证。

**Spec:** `docs/superpowers/specs/2026-08-06-change-adjust-interface-cases-design.md`

---

## 复用清单（已存在，勿重写）

这些是既有函数/类型，后续任务直接引用：

- `server/services/api_case_contract.py`
  - `build_contract_catalog(document: dict, operation_ids: list[str] = None) -> dict` — OpenAPI/Swagger dict → 紧凑契约 catalog（operations: method/path/params/schema）。
  - `merge_contract_catalogs(catalogs: list[dict]) -> dict`、`empty_contract_catalog() -> dict`、`contract_hash(catalog) -> str`、`contract_prompt(catalog) -> str`（渲染进 prompt 的接口清单文本）。
- `server/services/doc_parser.py`
  - `parse_document(path, ...) -> ParsedDocument`（.pdf/.docx/.doc/.md/.txt）；`ParsedDocument` 有 `.text`/`.chunks`。
- `server/api/functional_cases.py`
  - `_run_outline_ai(db, module, mode, requirement_text, model_name, coverage="full")`（约 2445 行）— AI JSON 调用 + 健壮解析模板，**照它写新的 plan AI 调用**。
  - `_resolve_model(db, model_name, project_id) -> cfg`、`_project_context_block(...)`、`_existing_case_names(db, module_id, case_type)`、`_operator_name(user)`。
  - `ai_generate_batch(payload: AiBatchRequest, db, user=None) -> dict`（3199 行，普通函数可直接调）— 详情生成 + 契约加固。返回形如 `{"status","data":{...}}`；**实现前先读 3199–3364 确认 data 里生成用例的键名**。
  - `AiBatchRequest`（2845 行）：`module_id, model_name, digest, points:list[BatchPoint], done_names, done_cases, mode, carried_vars, setup_doc, doc_urls, api_contract`。`BatchPoint{title, category}`。
  - 从 URL/本地 schema 建契约的既有代码在 587–631；实现 doc_ingest 时先读它，能直接抽出/复用"抓 URL→build_contract_catalog"。
- `database/models`：`TestCase(id, module_id, case_type, name, sort_order, steps, ...)`、`Module(id, name, project_id)`、`AiRun(...)`、常量 `CASE_TYPE_API`；`AiRun` 状态常量与 `AI_FEATURE_*` 在 `database/models/ai_run.py`。
- `database/schemas/test_case_create.py`：`TestCaseCreate{module_id, name, sort_order?, case_type?, steps?}`、`StepPayload`。
- `server/api/authz.py`：`assert_project_access(db, user, project_id)`。
- `server/api/deps.py`：`DBDep`、`OptionalUserDep`。
- 前端 `frontend/src/lib/api.ts` 的 `request<T>()`、`ApiError`；类型集中在 `frontend/src/types/domain.ts`。

## 新增文件结构

- `ai_gateway/prompts/change_plan.md` — 变更调整规划 prompt。
- `server/services/doc_ingest.py` — 文档/链接 → `IngestResult{contract, text_blocks, warnings}` 的编排器。
- `server/services/change_plan_service.py` — `plan_preview()` / `plan_apply()` 编排。
- `server/api/change_adjust.py` — 两个 REST 端点。
- `tests/services/test_doc_ingest.py`、`tests/services/test_change_plan_service.py`、`tests/api/test_change_adjust.py` — 后端测试。
- 前端：改写 `frontend/src/components/case/module-outline-drawer.tsx`（保留文件名，内容换成变更调整面板）；`frontend/src/lib/api.ts` 加 `changeAdjustApi`；`frontend/src/types/domain.ts` 加类型。

---

## Task 1: 规划 prompt 文件

**Files:**
- Create: `ai_gateway/prompts/change_plan.md`

- [ ] **Step 1: 写 prompt 文件**

内容（占位符用 `{{NAME}}`，与 `_render_prompt` 一致）：

```markdown
# 角色
你是接口测试用例的变更规划器。根据「本次变更说明」「接口文档结构」「模块现有用例」，
产出一份**用例级调整大纲**：这次应该新增、修改、删除哪些接口用例。

# 输入
## 模块
{{MODULE_NAME}}

## 本次变更说明
{{CHANGE_TEXT}}

## 接口文档结构（可能为空）
{{CONTRACT_BLOCK}}

## 接口文档补充文本（可能为空）
{{DOC_TEXT}}

## 模块现有用例（id 与名称，用于定位修改/删除）
{{EXISTING_CASES}}

# 输出要求
只输出一个合法 JSON 对象，形如：
{"ops":[
  {"action":"add","title":"...","endpoint":{"method":"POST","path":"/x"},"reason":"..."},
  {"action":"modify","target_case_id":12,"title":"...","endpoint":{"method":"PUT","path":"/x/{id}"},"reason":"..."},
  {"action":"delete","target_case_id":34,"title":"...","reason":"..."}
]}
规则：
- modify / delete 必须给出 target_case_id，取值只能来自「模块现有用例」里列出的 id。
- add 不要给 target_case_id。
- 只针对「本次变更说明」涉及的接口产出 op，不要动无关用例。
- 不确定是否删除的，宁可用 modify 或不产出，不要乱删。
- 不要输出 Markdown、解释、思考过程或代码块外文字。
```

- [ ] **Step 2: 提交**

```bash
git add ai_gateway/prompts/change_plan.md
git commit -m "feat(change-adjust): 变更调整规划 prompt"
```

---

## Task 2: doc_ingest 编排器

把上传文件 + 链接解析成「接口契约 + 补充文本 + 警告」。OpenAPI 走 `build_contract_catalog`，文本走 `doc_parser`，链接复用既有 URL 抓取逻辑。

**Files:**
- Create: `server/services/doc_ingest.py`
- Test: `tests/services/test_doc_ingest.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/services/test_doc_ingest.py
import json
from server.services.doc_ingest import ingest_sources, IngestResult

MINIMAL_OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "t", "version": "1"},
    "paths": {"/login": {"post": {"summary": "登录", "responses": {"200": {"description": "ok"}}}}},
}

def test_ingest_openapi_json_bytes_builds_contract():
    files = [("api.json", json.dumps(MINIMAL_OPENAPI).encode("utf-8"))]
    res = ingest_sources(files=files, links=[])
    assert isinstance(res, IngestResult)
    ops = res.contract.get("operations") or []
    assert any(o.get("path") == "/login" and o.get("method", "").lower() == "post" for o in ops)
    assert res.warnings == []

def test_ingest_bad_json_falls_back_to_text_with_warning():
    files = [("junk.json", b"{not json")]
    res = ingest_sources(files=files, links=[])
    assert res.warnings                      # 记录了警告
    assert (res.contract.get("operations") or []) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/services/test_doc_ingest.py -v`
Expected: FAIL（`ModuleNotFoundError: server.services.doc_ingest`）

- [ ] **Step 3: 实现 doc_ingest.py**

先读 `server/api/functional_cases.py:587-631` 里既有的"抓 URL→build_contract_catalog"逻辑，能抽公共函数就抽；下面是自包含实现：

```python
# server/services/doc_ingest.py
"""把上传文件 / 链接编排成：接口契约(catalog) + 补充文本 + 警告。

OpenAPI/Swagger(json/yaml) → api_case_contract.build_contract_catalog
其它文档(pdf/docx/md) → doc_parser 抽文本
链接 → 抓取后按同样规则分流
Postman collection 本期：识别到就抽 request 列表塞进文本，识别不了回退纯文本。
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.services.api_case_contract import (
    build_contract_catalog,
    empty_contract_catalog,
    merge_contract_catalogs,
)
from server.services.doc_parser import parse_document

_OPENAPI_SUFFIXES = {".json", ".yaml", ".yml"}
_TEXT_SUFFIXES = {".pdf", ".docx", ".doc", ".md", ".txt"}
_MAX_LINK_BYTES = 5 * 1024 * 1024


@dataclass
class IngestResult:
    contract: dict[str, Any] = field(default_factory=empty_contract_catalog)
    text_blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _load_openapi_document(raw: bytes) -> dict[str, Any] | None:
    text = raw.decode("utf-8", errors="replace")
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    try:
        import yaml  # PyYAML 已随 openapi 依赖存在；没有则回退
        obj = yaml.safe_load(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _catalog_from_openapi_bytes(name: str, raw: bytes, warnings: list[str]) -> dict[str, Any] | None:
    doc = _load_openapi_document(raw)
    if not isinstance(doc, dict):
        warnings.append(f"{name}: 不是合法的 OpenAPI/JSON/YAML，已按纯文本处理")
        return None
    if "paths" not in doc and "openapi" not in doc and "swagger" not in doc:
        # 可能是 Postman collection 或别的 JSON
        warnings.append(f"{name}: 非 OpenAPI 文档，已按纯文本处理")
        return None
    try:
        return build_contract_catalog(doc)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{name}: 契约解析失败({exc})，已按纯文本处理")
        return None


def _text_from_bytes(name: str, raw: bytes, warnings: list[str]) -> str | None:
    suffix = Path(name).suffix.lower()
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tf:
            tf.write(raw)
            tf.flush()
            parsed = parse_document(tf.name)
        return (parsed.text or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{name}: 文本解析失败({exc})，已跳过")
        return None


def ingest_sources(
    files: list[tuple[str, bytes]] | None = None,
    links: list[str] | None = None,
) -> IngestResult:
    result = IngestResult()
    catalogs: list[dict[str, Any]] = []

    for name, raw in files or []:
        suffix = Path(name).suffix.lower()
        if suffix in _OPENAPI_SUFFIXES:
            cat = _catalog_from_openapi_bytes(name, raw, result.warnings)
            if cat is not None:
                catalogs.append(cat)
                continue
            # 回退纯文本
            txt = raw.decode("utf-8", errors="replace").strip()
            if txt:
                result.text_blocks.append(f"# {name}\n{txt[:20000]}")
        elif suffix in _TEXT_SUFFIXES:
            txt = _text_from_bytes(name, raw, result.warnings)
            if txt:
                result.text_blocks.append(f"# {name}\n{txt[:20000]}")
        else:
            result.warnings.append(f"{name}: 不支持的类型，已跳过")

    for url in links or []:
        _ingest_link(url, catalogs, result)

    if catalogs:
        result.contract = merge_contract_catalogs(catalogs)
    return result


def _ingest_link(url: str, catalogs: list[dict[str, Any]], result: IngestResult) -> None:
    url = (url or "").strip()
    if not url:
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        result.warnings.append(f"{url}: 仅支持 http/https 链接，已跳过")
        return
    if _is_blocked_host(url):
        result.warnings.append(f"{url}: 拒绝访问内网/环回地址")
        return
    try:
        import requests
        resp = requests.get(url, timeout=15, stream=True)
        resp.raise_for_status()
        raw = resp.raw.read(_MAX_LINK_BYTES + 1, decode_content=True)
        if len(raw) > _MAX_LINK_BYTES:
            result.warnings.append(f"{url}: 内容过大，已截断")
            raw = raw[:_MAX_LINK_BYTES]
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"{url}: 抓取失败({exc})，已跳过")
        return
    cat = _catalog_from_openapi_bytes(url, raw, result.warnings)
    if cat is not None:
        catalogs.append(cat)
    else:
        txt = raw.decode("utf-8", errors="replace").strip()
        if txt:
            result.text_blocks.append(f"# {url}\n{txt[:20000]}")


def _is_blocked_host(url: str) -> bool:
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    if host in {"localhost", ""}:
        return True
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except Exception:  # noqa: BLE001
        return True  # 解析不了也拒
    return False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/services/test_doc_ingest.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add server/services/doc_ingest.py tests/services/test_doc_ingest.py
git commit -m "feat(change-adjust): 文档/链接编排器 doc_ingest（复用契约解析）"
```

---

## Task 3: change_plan_service.plan_preview（AI → 调整大纲，落 AiRun）

**Files:**
- Create: `server/services/change_plan_service.py`
- Test: `tests/services/test_change_plan_service.py`

- [ ] **Step 1: 写失败测试（AI 调用打桩）**

```python
# tests/services/test_change_plan_service.py
import types
from server.services import change_plan_service as svc

def test_normalize_ops_filters_bad_target(monkeypatch):
    existing_ids = {12, 34}
    raw = {"ops": [
        {"action": "add", "title": "新增登录成功"},
        {"action": "modify", "target_case_id": 12, "title": "改登录"},
        {"action": "delete", "target_case_id": 999, "title": "删不存在"},  # 非法 id → 丢弃
        {"action": "delete", "title": "缺 id"},                          # 缺 id → 丢弃
        {"action": "weird", "title": "非法动作"},                         # 非法 action → 丢弃
    ]}
    ops = svc._normalize_ops(raw, existing_ids)
    actions = [(o["action"], o.get("target_case_id")) for o in ops]
    assert ("add", None) in actions
    assert ("modify", 12) in actions
    assert all(not (o["action"] == "delete" and o["target_case_id"] == 999) for o in ops)
    assert all(o["action"] != "weird" for o in ops)
    # 每个 op 有稳定 id
    assert [o["id"] for o in ops] == list(range(len(ops)))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/services/test_change_plan_service.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 plan_preview + _normalize_ops**

```python
# server/services/change_plan_service.py
"""变更调整：规划(plan_preview) 与 应用(plan_apply) 编排。

plan_preview：变更文本 + 接口契约/文本 + 现有用例 → AI 产出用例级调整大纲(ops)，
              落一条 AiRun 持久化 ops + 上下文，返回 plan_id + ops。
plan_apply：见 Task 4。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from database.models import CASE_TYPE_API, Module, TestCase
from database.models.ai_run import AiRun, AI_RUN_STATUS_SUCCESS
from server.services.api_case_contract import contract_prompt, contract_hash
from server.services.doc_ingest import IngestResult

AI_FEATURE_CHANGE_PLAN = "change_plan"
_VALID_ACTIONS = {"add", "modify", "delete"}


def _existing_interface_cases(db, module_id: int) -> list[TestCase]:
    return (
        db.session.query(TestCase)
        .filter(TestCase.module_id == module_id, TestCase.case_type == CASE_TYPE_API)
        .order_by(TestCase.sort_order.asc(), TestCase.id.asc())
        .all()
    )


def _normalize_ops(raw: dict[str, Any], existing_ids: set[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (raw or {}).get("ops") or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip().lower()
        if action not in _VALID_ACTIONS:
            continue
        title = str(item.get("title") or "").strip()[:200]
        if not title:
            continue
        target = item.get("target_case_id")
        if action in {"modify", "delete"}:
            if not isinstance(target, int) or target not in existing_ids:
                continue
        else:
            target = None
        ep = item.get("endpoint") if isinstance(item.get("endpoint"), dict) else None
        endpoint = None
        if ep:
            endpoint = {
                "method": str(ep.get("method") or "").strip().upper(),
                "path": str(ep.get("path") or "").strip(),
            }
        out.append({
            "id": len(out),
            "action": action,
            "target_case_id": target,
            "title": title,
            "endpoint": endpoint,
            "reason": str(item.get("reason") or "").strip()[:500],
        })
    return out


def _parse_ai_json(raw: str) -> dict[str, Any] | None:
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    s, e = raw.find("{"), raw.rfind("}")
    if 0 <= s < e:
        try:
            obj = json.loads(raw[s:e + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def plan_preview(
    db,
    module: Module,
    model_name: str,
    change_text: str,
    ingest: IngestResult,
    operator: str | None = None,
) -> dict[str, Any]:
    """跑 AI 产出调整大纲，落 AiRun，返回 {plan_id, ops, warnings}。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown, model_task_options
    from server.api.functional_cases import _resolve_model  # 复用模型解析

    cases = _existing_interface_cases(db, module.id)
    existing_ids = {c.id for c in cases}
    existing_block = "\n".join(f"- #{c.id} {c.name}" for c in cases) or "（本模块暂无接口用例）"
    contract_block = contract_prompt(ingest.contract) if (ingest.contract.get("operations") or []) else "（无结构化接口）"
    doc_text = "\n\n".join(ingest.text_blocks)[:20000] or "（无补充文本）"

    cfg = _resolve_model(db, model_name, module.project_id)
    call_options = model_task_options(cfg, "api_outline")
    template = _load_prompt("change_plan")
    prompt = _render_prompt(template, {
        "MODULE_NAME": module.name,
        "CHANGE_TEXT": change_text.strip(),
        "CONTRACT_BLOCK": contract_block,
        "DOC_TEXT": doc_text,
        "EXISTING_CASES": existing_block,
    })
    raw, tin, tout = chat_markdown(
        prompt, cfg,
        timeout=call_options["timeout"],
        system_prompt="你只输出一个合法 JSON 对象，含 ops 数组。不要输出任何其它文字。",
        enable_thinking=call_options["enable_thinking"],
        json_mode=call_options["json_mode"],
        max_tokens=call_options["max_tokens"],
        temperature=call_options["temperature"],
        reasoning_effort=call_options.get("reasoning_effort"),
    )
    obj = _parse_ai_json(raw or "")
    if obj is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="调整大纲解析失败，请重试或更换模型")
    ops = _normalize_ops(obj, existing_ids)

    run = AiRun(
        feature=AI_FEATURE_CHANGE_PLAN,
        status=AI_RUN_STATUS_SUCCESS,
        project_id=module.project_id,
        provider=cfg.provider,
        model=cfg.model,
        tokens_in=tin,
        tokens_out=tout,
        input_payload={
            "module_id": module.id,
            "mode": "interface",
            "model_name": model_name,
            "change_text": change_text,
            "warnings": ingest.warnings,
        },
        output_payload={
            "ops": ops,
            "contract": ingest.contract,
            "contract_hash": contract_hash(ingest.contract),
            "doc_text": doc_text,
        },
        operator=operator,
        started_at=datetime.now(),
        ended_at=datetime.now(),
    )
    db.session.add(run)
    db.session.flush()
    return {"plan_id": run.id, "ops": ops, "warnings": ingest.warnings}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/services/test_change_plan_service.py -v`
Expected: PASS（`_normalize_ops` 那条）

- [ ] **Step 5: 提交**

```bash
git add server/services/change_plan_service.py tests/services/test_change_plan_service.py
git commit -m "feat(change-adjust): plan_preview 产出调整大纲并落 AiRun"
```

---

## Task 4: change_plan_service.plan_apply（写入真实用例）

新增/修改复用 `ai_generate_batch` 产出详情；删除按确认真删。

**Files:**
- Modify: `server/services/change_plan_service.py`
- Test: `tests/services/test_change_plan_service.py`（追加）

- [ ] **Step 1: 追加失败测试（删除路径，不触达 AI）**

```python
def test_apply_deletes_confirmed_only(monkeypatch, db_session_factory):
    # db_session_factory: 复用现有测试夹具（见 tests/api/conftest.py 里已有的 db 夹具）
    # 构造：一个模块 + 两条 API 用例；plan 里删两条，仅确认一条 → 只删一条
    ...
```

> 实现前先看 `tests/api/conftest.py` / 现有 `tests/api/test_*.py` 用的 db 夹具与建数据 helper，照抄夹具风格；上面用 `...` 占位的建数据部分按夹具补全（建 Module、两条 `TestCase(case_type=CASE_TYPE_API)`、一条 AiRun 存 ops）。断言：`apply` 后被确认的用例已删、未确认的仍在，返回 `{"deleted":1}`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/services/test_change_plan_service.py::test_apply_deletes_confirmed_only -v`
Expected: FAIL（`plan_apply` 未定义）

- [ ] **Step 3: 实现 plan_apply**

```python
# 追加到 server/services/change_plan_service.py

def _load_plan(db, plan_id: int) -> AiRun:
    run = db.session.query(AiRun).filter(
        AiRun.id == plan_id, AiRun.feature == AI_FEATURE_CHANGE_PLAN
    ).first()
    from fastapi import HTTPException
    if run is None:
        raise HTTPException(status_code=404, detail="调整计划不存在或已过期")
    return run


def plan_apply(
    db,
    plan_id: int,
    selected_op_ids: list[int],
    confirmed_delete_ids: list[int],
    user=None,
) -> dict[str, Any]:
    """按选中的 op 写入真实用例。返回 {added, modified, deleted, errors}。"""
    run = _load_plan(db, plan_id)
    payload = run.output_payload or {}
    module_id = (run.input_payload or {}).get("module_id")
    model_name = (run.input_payload or {}).get("model_name") or ""
    contract = payload.get("contract") or {}
    ops = {o["id"]: o for o in payload.get("ops") or []}
    selected = [ops[i] for i in selected_op_ids if i in ops]
    confirmed_del = set(confirmed_delete_ids or [])

    added = modified = deleted = 0
    errors: list[dict[str, Any]] = []

    module = db.session.query(Module).filter(Module.id == module_id).first()
    if module is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="模块不存在")

    # 1) 删除（仅确认项）
    for op in selected:
        if op["action"] != "delete":
            continue
        if op["id"] not in confirmed_del:
            continue
        case = db.session.query(TestCase).filter(
            TestCase.id == op["target_case_id"], TestCase.module_id == module_id
        ).first()
        if case is None:
            errors.append({"op_id": op["id"], "error": "用例不存在，已跳过"})
            continue
        db.session.delete(case)
        deleted += 1

    # 2) 新增 / 修改 → 复用 ai_generate_batch 产出详情
    gen_ops = [o for o in selected if o["action"] in {"add", "modify"}]
    if gen_ops:
        generated = _generate_details(db, module, model_name, contract, gen_ops, user)
        # generated: dict[op_id -> 生成的 case dict]
        for op in gen_ops:
            gen = generated.get(op["id"])
            if gen is None:
                errors.append({"op_id": op["id"], "error": "详情生成失败，已跳过"})
                continue
            try:
                if op["action"] == "add":
                    _create_case_from_generated(db, module_id, gen)
                    added += 1
                else:
                    _overwrite_case_from_generated(db, op["target_case_id"], module_id, gen)
                    modified += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"op_id": op["id"], "error": str(exc)})

    db.session.flush()
    return {"added": added, "modified": modified, "deleted": deleted, "errors": errors}
```

`_generate_details` / `_create_case_from_generated` / `_overwrite_case_from_generated` 的实现说明（实现前必须先读 `functional_cases.py:3199-3364` 的 `ai_generate_batch` 返回结构 + `database/schemas/test_case_create.py` 的建用例入口）：

```python
def _generate_details(db, module, model_name, contract, gen_ops, user) -> dict[int, dict]:
    """把 add/modify 的 title 组成 BatchPoint，调 ai_generate_batch 一次拿回带步骤的用例，
    按标题对回 op_id。"""
    from server.api.functional_cases import ai_generate_batch, AiBatchRequest, BatchPoint

    points = [BatchPoint(title=o["title"], category="") for o in gen_ops]
    req = AiBatchRequest(
        module_id=module.id,
        model_name=model_name,
        points=points,
        mode="interface",
        api_contract=contract,
    )
    resp = ai_generate_batch(req, db, user)
    # ↓ 键名以 3199-3364 实际返回为准，实现时核对后替换
    cases = (resp.get("data") or {}).get("cases") or []
    by_title = {str(c.get("name") or "").strip(): c for c in cases}
    return {o["id"]: by_title.get(o["title"]) for o in gen_ops if by_title.get(o["title"])}


def _create_case_from_generated(db, module_id: int, gen: dict) -> None:
    """用现有建用例路径写库。复用 database/schemas/test_case_create.py::TestCaseCreate
    + 现有 create 服务；gen 已含 name/steps/case_type。实现时对齐 ai_generate_batch
    产出的字段到 TestCaseCreate。"""
    from server.services... import create_test_case  # 实现时定位真实建用例函数（见 POST "" 端点 4383）
    ...

def _overwrite_case_from_generated(db, case_id: int, module_id: int, gen: dict) -> None:
    """定位 case_id（校验 module 归属），用 gen 覆盖 name/steps（复用 PUT /{case_id} 4449 的更新逻辑）。"""
    ...
```

> 说明：`_create_case_from_generated` / `_overwrite_case_from_generated` 需在实现时把 `ai_generate_batch` 产出的用例字段映射到既有建/改用例函数（POST `""` @4383、PUT `/{case_id}` @4449）。**这是本计划里唯一依赖"读现有返回结构后补全"的地方**，Task 4 实现的第一步就是读这两段并把上面 `...` 补成真实调用。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/services/test_change_plan_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/services/change_plan_service.py tests/services/test_change_plan_service.py
git commit -m "feat(change-adjust): plan_apply 写入真实用例（增改删）"
```

---

## Task 5: REST 路由 change_adjust.py

**Files:**
- Create: `server/api/change_adjust.py`
- Modify: `server/api/__init__.py`（导出）、`server/main.py`（注册进 router 循环）
- Test: `tests/api/test_change_adjust.py`

- [ ] **Step 1: 写失败测试（授权 + apply 删除，AI 打桩）**

```python
# tests/api/test_change_adjust.py
# 复用 tests/api/ 既有 client / db / auth 夹具（照抄同目录其它 test 的夹具用法）
def test_apply_requires_project_access(client, other_user_token, seeded_plan):
    r = client.post("/api/change_plan/apply",
                    json={"plan_id": seeded_plan.id, "selected_op_ids": [], "confirmed_delete_ids": []},
                    headers={"Authorization": f"Bearer {other_user_token}"})
    assert r.status_code == 403
```

> `seeded_plan` 夹具：建 project+module+一条 `AiRun(feature="change_plan")`。`other_user_token`：非本项目用户。授权在路由里对 `module.project_id` 调 `assert_project_access` 实现。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/api/test_change_adjust.py -v`
Expected: FAIL（路由不存在 → 404 而非 403）

- [ ] **Step 3: 实现路由**

```python
# server/api/change_adjust.py
from typing import Optional

import pydantic
from fastapi import APIRouter, File, Form, UploadFile

from database.models import Module
from database.models.ai_run import AiRun
from server.api.authz import assert_project_access
from server.api.deps import DBDep, OptionalUserDep
from server.services.change_plan_service import (
    AI_FEATURE_CHANGE_PLAN, plan_preview, plan_apply,
)
from server.services.doc_ingest import ingest_sources
from server.api.functional_cases import _operator_name

router = APIRouter(prefix="/change_plan", tags=["change-adjust"])


def _module_or_404(db, module_id: int) -> Module:
    from fastapi import HTTPException
    m = db.session.query(Module).filter(Module.id == module_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="模块不存在")
    return m


@router.post("/preview")
async def change_plan_preview(
    db: DBDep,
    user: OptionalUserDep = None,
    module_id: int = Form(...),
    change_text: str = Form(""),
    model_name: str = Form(""),
    links: str = Form(""),
    files: Optional[list[UploadFile]] = File(None),
):
    module = _module_or_404(db, module_id)
    assert_project_access(db, user, module.project_id)
    file_tuples = [(f.filename or "upload", await f.read()) for f in (files or [])]
    link_list = [s.strip() for s in links.replace(",", "\n").splitlines() if s.strip()]
    ingest = ingest_sources(files=file_tuples, links=link_list)
    data = plan_preview(db, module, model_name, change_text, ingest, operator=_operator_name(user))
    return {"status": "success", "data": data}


class ApplyRequest(pydantic.BaseModel):
    plan_id: int
    selected_op_ids: list[int] = pydantic.Field(default_factory=list)
    confirmed_delete_ids: list[int] = pydantic.Field(default_factory=list)


@router.post("/apply")
def change_plan_apply(payload: ApplyRequest, db: DBDep, user: OptionalUserDep = None):
    run = db.session.query(AiRun).filter(
        AiRun.id == payload.plan_id, AiRun.feature == AI_FEATURE_CHANGE_PLAN
    ).first()
    from fastapi import HTTPException
    if run is None:
        raise HTTPException(status_code=404, detail="调整计划不存在或已过期")
    module_id = (run.input_payload or {}).get("module_id")
    module = _module_or_404(db, module_id)
    assert_project_access(db, user, module.project_id)
    data = plan_apply(db, payload.plan_id, payload.selected_op_ids, payload.confirmed_delete_ids, user)
    return {"status": "success", "data": data}
```

- [ ] **Step 4: 注册路由**

`server/api/__init__.py` 导出 `change_adjust`；`server/main.py` 的 `for router in (...)` 循环里加入 `change_adjust.router`（照现有其它 router 写法；自动挂 `/api` 前缀）。

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/api/test_change_adjust.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add server/api/change_adjust.py server/api/__init__.py server/main.py tests/api/test_change_adjust.py
git commit -m "feat(change-adjust): preview/apply 路由 + 授权"
```

---

## Task 6: 前端类型 + API 客户端

**Files:**
- Modify: `frontend/src/types/domain.ts`、`frontend/src/lib/api.ts`

- [ ] **Step 1: 加类型**

```typescript
// frontend/src/types/domain.ts
export type ChangeOpAction = "add" | "modify" | "delete";
export interface ChangeOp {
  id: number;
  action: ChangeOpAction;
  target_case_id: number | null;
  title: string;
  endpoint: { method: string; path: string } | null;
  reason: string;
}
export interface ChangePlan { plan_id: number; ops: ChangeOp[]; warnings: string[]; }
export interface ApplySummary {
  added: number; modified: number; deleted: number;
  errors: { op_id: number; error: string }[];
}
```

- [ ] **Step 2: 加 API 客户端**

```typescript
// frontend/src/lib/api.ts —— 参照现有 request<T>() 用法
import type { ChangePlan, ApplySummary } from "@/types/domain";

export const changeAdjustApi = {
  async preview(args: {
    moduleId: number; changeText: string; modelName: string;
    links: string; files: File[];
  }): Promise<ChangePlan> {
    const fd = new FormData();
    fd.append("module_id", String(args.moduleId));
    fd.append("change_text", args.changeText);
    fd.append("model_name", args.modelName);
    fd.append("links", args.links);
    args.files.forEach((f) => fd.append("files", f));
    // 用现有上传型 request（multipart）；若 request<T> 不支持 FormData，参照现有上传接口写法
    return request<ChangePlan>("/change_plan/preview", { method: "POST", body: fd });
  },
  apply(args: { planId: number; selectedOpIds: number[]; confirmedDeleteIds: number[] }) {
    return request<ApplySummary>("/change_plan/apply", {
      method: "POST",
      body: JSON.stringify({
        plan_id: args.planId,
        selected_op_ids: args.selectedOpIds,
        confirmed_delete_ids: args.confirmedDeleteIds,
      }),
    });
  },
};
```

> 实现前确认 `request<T>()` 对 multipart 的处理（是否自动设 headers）；若它强制 `Content-Type: application/json`，参照 `src/lib/api.ts` 里已有的文件上传接口改用其上传通道。

- [ ] **Step 3: typecheck**

Run: `cd frontend && npm run typecheck`
Expected: 无错误

- [ ] **Step 4: 提交**

```bash
git add frontend/src/types/domain.ts frontend/src/lib/api.ts
git commit -m "feat(change-adjust): 前端类型与 API 客户端"
```

---

## Task 7: 前端面板改写（调整大纲审阅 + 删除勾选）

**Files:**
- Modify: `frontend/src/components/case/module-outline-drawer.tsx`（当前已是变更调整表单骨架）

- [ ] **Step 1: 接线 preview + 渲染 ops**

在现有 `ModuleOutlinePanel`（已含 changeText/docFile/docLinks/model 四项 UI）基础上：
- 「规划调整」onClick → 收集 `docFile`（改成支持多文件数组 `File[]`）+ `docLinks` + `changeText` + `effectiveModel`，调 `changeAdjustApi.preview`，把返回 `ChangePlan` 存入 `plan` state。
- 新增 state：`const [plan, setPlan] = useState<ChangePlan | null>(null);` 和 `const [uncheckedDeletes, setUncheckedDeletes] = useState<Set<number>>(new Set());`
- 渲染 ops 列表：按 `action` 上色（add=绿 / modify=黄 / delete=红），删除项前置 `<input type="checkbox">`，默认勾选（即不在 `uncheckedDeletes` 里）。展示 `title`、`endpoint`（method+path）、`reason`。
- `warnings` 非空时在顶部黄条列出。

```tsx
// 关键片段
{plan?.ops.map((op) => (
  <div key={op.id} className={cn("flex items-start gap-2 rounded-md border px-2.5 py-2",
    op.action === "add" && "border-green-300 bg-green-50",
    op.action === "modify" && "border-amber-300 bg-amber-50",
    op.action === "delete" && "border-red-300 bg-red-50")}>
    {op.action === "delete" ? (
      <input type="checkbox"
        checked={!uncheckedDeletes.has(op.id)}
        onChange={(e) => setUncheckedDeletes((s) => {
          const n = new Set(s); e.target.checked ? n.delete(op.id) : n.add(op.id); return n;
        })}
        className="mt-0.5" />
    ) : <span className="w-4" />}
    <div className="min-w-0 flex-1">
      <div className="text-[12.5px] font-medium">
        <span className="mr-1 text-[10px] uppercase opacity-70">{op.action}</span>{op.title}
      </div>
      {op.endpoint ? <div className="text-[11px] text-muted-foreground">{op.endpoint.method} {op.endpoint.path}</div> : null}
      {op.reason ? <div className="text-[11px] text-muted-foreground">{op.reason}</div> : null}
    </div>
    {op.target_case_id ? <span className="text-[11px] text-primary">#{op.target_case_id}</span> : null}
  </div>
))}
```

- [ ] **Step 2: 接线 apply**

「应用变更」onClick：
- `selectedOpIds` = 全部 `plan.ops` 的 id **除去**被取消勾选的删除项（`uncheckedDeletes`）。
- `confirmedDeleteIds` = `plan.ops.filter(action==="delete" && !uncheckedDeletes.has(id)).map(id)`。
- 调 `changeAdjustApi.apply`，成功后 `toast.success("新增 x · 修改 y · 删除 z")`，有 `errors` 再 `toast.warning`，`setPlan(null)` 并 `onApplied?.()`。

- [ ] **Step 3: typecheck + lint + build**

Run: `cd frontend && npm run typecheck && npm run lint && npm run build`
Expected: 全绿；build 产出新 dist（后端 54351 即可看到）

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/case/module-outline-drawer.tsx
git commit -m "feat(change-adjust): 面板接线 preview/apply + 调整大纲审阅与删除勾选"
```

---

## Task 8: 移除旧「模块大纲」

**先确认无其它引用**，再删。

**Files:**
- Modify: `server/api/functional_cases.py`（删 6 个 module_outline 端点：get / align_preview / apply / purge_gaps / replan_preview / replan_apply，及仅它们用到的 `_run_outline_ai` 若无它用、`OutlineAlignRequest`/`OutlineReplanRequest`/`BatchPoint` 若无它用）
- Delete: `server/services/module_outline_service.py`
- Modify: `frontend/src/lib/api.ts`（删 `moduleOutlineApi`）、`frontend/src/types/domain.ts`（删 `OutlineAlign*`/`OutlineReplan*`）

- [ ] **Step 1: 查引用**

```bash
grep -rn "module_outline\|moduleOutlineApi\|OutlineAlign\|OutlineReplan\|_run_outline_ai\|module_outline_service" server frontend/src | grep -v "docs/"
```
逐一确认：除本次要删的位置外无其它使用（`_run_outline_ai` 若 replan 之外无引用则一并删；有则保留）。

- [ ] **Step 2: 删后端**

删除 `module_outline_service.py` 与上述 6 个端点及仅其使用的 schema/辅助函数。保留 `ai_generate_outline`（属「生成用例」流程）。

- [ ] **Step 3: 删前端 API/类型**

- [ ] **Step 4: 验证**

Run: `python -m compileall server && cd frontend && npm run typecheck && npm run lint`
Expected: 无错误、无未用引用告警（lint `--max-warnings 0`）

- [ ] **Step 5: 提交**

```bash
git add -A server frontend/src
git commit -m "refactor(change-adjust): 移除旧模块大纲(align/replan/purge)整套"
```

---

## Task 9: 端到端手动验证

- [ ] **Step 1: 起服务**

```bash
CELERY_TASK_ALWAYS_EAGER=1 python server/main.py
```
（前端已在 Task 7 build 进 dist；或另开 `cd frontend && npm run dev` 用 5173）

- [ ] **Step 2: 验证 preview**

打开某接口模块 →「AI 生成接口用例」→「变更调整」；填「新增一个 POST /login 登录接口用例；删除旧的 /ping 用例」，选模型，点「规划调整」。
预期：返回 ops 含 1 条 add(/login) + 1 条 delete(指向 /ping 用例 id，带勾选框)。

- [ ] **Step 3: 验证 apply**

取消勾选删除项 → 应用 → 预期只新增、不删除；库中出现新 /login 用例、/ping 仍在。再勾选删除 → 应用 → /ping 被删。
返回 toast 显示「新增 1 · 修改 0 · 删除 1」。

- [ ] **Step 4: 验证授权**

用非本项目用户 token 调 `/api/change_plan/apply` → 403。

- [ ] **Step 5: 验证文档解析**

传一个最小 OpenAPI json（含 /login POST）→ preview 的 ops 里 endpoint 能引用到该接口；传一个坏 json → 顶部 warnings 提示"已按纯文本处理"，流程不中断。

---

## 备注

- 后端 `python -m compileall .` 自查（无 ruff/black）。
- 平台"跑测试"指端到端执行用例，与本功能（生成侧）无关；本计划的 pytest 针对 `tests/services` / `tests/api`，照抄同目录既有夹具。
- 改前端后要让 54351 生效需 `cd frontend && npm run build`（见项目记忆）。
