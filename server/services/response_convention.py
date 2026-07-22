"""从测试报告的真实响应里提炼「响应结构约定」，回流记忆层(project_contexts)。

动机：AI 生成接口用例靠猜 JSONPath（如 $.access_token），真实响应却带信封
     （{status, data:{access_token}}）→ 首跑大面积 401/断言失败。真相就在报告里。
     本模块把真实 请求+响应 样本喂给 LLM 提炼成约定，回流记忆层；下次生成经
     PROJECT_CONTEXT 注入即写对路径。这是「运行暴露真相 → 真相入记忆 → 下次生成对」闭环。

被两处复用：
  - tasks/learn_convention_task.py：报告跑完自动学（样本取自 TestStepReport）
  - scripts/learn_response_convention.py：手动补学（样本取自 Allure 报告文件）
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

MAX_SAMPLES = 12
CONVENTION_SOURCE = "response_probe"  # 打这个 source_type，供节流识别


def collect_report_samples(session, report_id: int, limit: int = MAX_SAMPLES) -> list[dict]:
    """从 TestStepReport 取真实 请求+响应 样本（http 步骤）。"""
    from database.models import TestStepReport

    rows = (
        session.query(TestStepReport)
        .filter(
            TestStepReport.report_id == report_id,
            TestStepReport.step_type == "http_request",
        )
        .order_by(TestStepReport.id)
        .all()
    )
    # 成功/失败分开收集，最后按比例取 —— 否则一份"大部分挂了"的报告会让样本全是
    # 错误响应，模型只能提炼出错误结构，学不到"成功时返回什么状态码"。
    # （实测教训：项目 1 首次学习只产出了一条「错误响应结构约定」，
    #   缺少「POST 创建返回 200 而非 201」这类约定，导致后续生成的用例大批断言错状态码。）
    ok_samples: list[dict] = []
    err_samples: list[dict] = []
    for r in rows:
        resp = _loose_json(r.output_data)
        if resp is None:
            continue
        item = {
            "name": (r.step_name or "")[:60],
            "request": {
                "method": r.action,
                "url": r.target,
                "body": _loose_json(r.input_data),
            },
            "response": resp,
            "status": r.status_code,
        }
        code = r.status_code or 0
        (ok_samples if 200 <= code < 300 else err_samples).append(item)

    half = max(1, limit // 2)
    picked = ok_samples[:half] + err_samples[: limit - min(len(ok_samples), half)]
    return picked[:limit]


def _loose_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s[:500]  # 非 JSON 保留文本片段，也有诊断价值


def recently_learned(session, project_id: int, days: int = 7) -> bool:
    """节流：项目近 N 天内已自动学过响应约定就跳过（约定极少变，无需反复学）。"""
    from database.models import ProjectContext

    since = datetime.now() - timedelta(days=max(1, days))
    hit = (
        session.query(ProjectContext.id)
        .filter(
            ProjectContext.project_id == project_id,
            ProjectContext.source_type == CONVENTION_SOURCE,
            ProjectContext.created_at >= since,
        )
        .first()
    )
    return hit is not None


def _parse_items(raw: str) -> list[dict]:
    obj = None
    m = re.search(r"```json\s*(.+?)\s*```", raw, re.S)
    for cand in ([m.group(1)] if m else []) + [raw]:
        try:
            obj = json.loads(cand)
            break
        except Exception:
            obj = None
    if obj is None:
        s, e = raw.find("{"), raw.rfind("}")
        if 0 <= s < e:
            try:
                obj = json.loads(raw[s:e + 1])
            except Exception:
                obj = None
    items = (obj or {}).get("context_items") if isinstance(obj, dict) else None
    return [i for i in items if isinstance(i, dict)] if items else []


def distill_and_save(
    session, *, project_id: int, samples: list[dict], cfg, source_file: str = "",
) -> list[int]:
    """把真实样本喂 LLM 提炼响应约定并入库。返回新建 context id 列表。"""
    from ai_gateway.gateway import _load_prompt, _render_prompt, chat_markdown
    from server.services.context_service import save_contexts

    if not samples:
        return []
    prompt = _render_prompt(
        _load_prompt("response_convention"),
        {"SAMPLES": json.dumps(samples, ensure_ascii=False, indent=2)[:12000]},
    )
    raw, _a, _b = chat_markdown(prompt, cfg, timeout=120)
    items = _parse_items(raw)
    if not items:
        logger.info("[response_convention] project=%s 未提炼出约定", project_id)
        return []
    created = save_contexts(
        contexts=items, project_id=project_id,
        source_type=CONVENTION_SOURCE, source_file=source_file[:200],
        session=session,
    )
    logger.info(
        "[response_convention] project=%s 提炼 %d 条,新入库 %d 条",
        project_id, len(items), len(created),
    )
    return created
