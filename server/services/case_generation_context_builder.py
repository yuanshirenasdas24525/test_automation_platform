"""AI 一键生成测试用例的上下文构建器（M7）。

直接复用 `requirement_context_builder.build_requirement_context` 拿到需求 +
模块 + 依赖 + 子需求 + 附件文档 / 图片 / skipped；再额外注入：

  - analysis_document：M6 产出的最新（或用户指定的）分析 markdown，截 8000 字
  - ui_images：PM 在 Launcher 单独"标记为 UI 截图"的 attachment 列表
                （这些图片走 Vision/OCR，跟附件里的文档图片合流；
                 task handler 在选 vision/ocr 分支时只会读 ctx.ui_images，
                 而不是把所有 ctx.images 都一股脑塞进去）
  - existing_cases_excerpt：同需求下已有 functional 用例的 title 列表（避免 AI 产重复）
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.models import (
    AI_CASE_DRAFT_STATUS_ACCEPTED,
    AiCaseDraft,
    Attachment,
    ATTACHMENT_KIND_FILE,
    CASE_TYPE_FUNCTIONAL,
    Requirement,
    RequirementAnalysisDocument,
    TestCase,
)
from server.services.requirement_context_builder import (
    ATTACHMENTS_DIR,
    ContextImage,
    RequirementContext,
    _mime_of,
    _resolve_local_path,
    build_requirement_context,
    render_context_as_text,
)


logger = logging.getLogger(__name__)


# 同需求已存在的 functional 用例标题最多带几条（防 prompt 爆）
DEFAULT_EXISTING_CASE_LIMIT = 30
# 分析文档 markdown 截断阈值
MAX_ANALYSIS_MARKDOWN_CHARS = 8000
# 项目记忆层（project_contexts）注入截断阈值
MAX_PROJECT_CONTEXT_CHARS = 4000
# few-shot 样例条数与总长度上限
DEFAULT_EXEMPLAR_LIMIT = 3
MAX_EXEMPLAR_CHARS = 3500
# 记忆层检索条数
DEFAULT_PROJECT_CONTEXT_TOP_K = 8
# 用例生成关心的上下文字段类型（跳过 architecture 等对用例无直接价值的）
CASE_GEN_CONTEXT_TYPES = [
    "business_rule",
    "data_model",
    "api_contract",
    "term_definition",
    "process_flow",
    "constraint",
]


@dataclass
class CaseGenerationContext:
    """build_case_generation_context 的返回结构。

    `base` 是 M6 已有的 RequirementContext —— renderer 可以直接复用；
    其它字段是 M7 在 M6 基础上加的：分析文档 markdown / UI 截图 / 已有用例标题。
    """

    base: RequirementContext
    analysis_markdown: str = ""
    analysis_document_id: Optional[int] = None
    analysis_model_label: Optional[str] = None
    ui_images: list[ContextImage] = field(default_factory=list)
    ui_image_attachment_ids: list[int] = field(default_factory=list)
    existing_case_titles: list[str] = field(default_factory=list)
    # 项目记忆层（project_contexts）检索结果，已渲染为可注入文本
    project_context_text: str = ""
    matched_context_ids: list[int] = field(default_factory=list)
    # few-shot 样例（同模块已采纳草稿），已渲染为可注入文本
    exemplar_cases_text: str = ""
    # 反例（被拒草稿标题 + 拒因），已渲染为可注入文本
    rejected_examples_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = self.base.to_dict()
        d["analysis_document"] = {
            "id": self.analysis_document_id,
            "model_label": self.analysis_model_label,
            "markdown": self.analysis_markdown,
        }
        d["ui_images"] = [asdict(i) for i in self.ui_images]
        d["ui_image_attachment_ids"] = list(self.ui_image_attachment_ids)
        d["existing_case_titles"] = list(self.existing_case_titles)
        d["project_context_text"] = self.project_context_text
        d["matched_context_ids"] = list(self.matched_context_ids)
        d["exemplar_cases_text"] = self.exemplar_cases_text
        return d


def _truncate_md(text: str, n: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "\n…[truncated]"


def _resolve_ui_image(att: Attachment) -> Optional[ContextImage]:
    if att.kind != ATTACHMENT_KIND_FILE:
        return None
    p = _resolve_local_path(att)
    if p is None:
        return None
    return ContextImage(
        name=att.name,
        mime=_mime_of(p),
        abs_path=str(p),
        size_bytes=p.stat().st_size if p.is_file() else None,
    )


def _load_ui_images(
    session: Session,
    requirement_id: int,
    attachment_ids: list[int],
) -> list[ContextImage]:
    if not attachment_ids:
        return []
    rows = (
        session.query(Attachment)
        .filter(
            Attachment.id.in_(attachment_ids),
            # 限定在同一需求下，防止前端误传其它需求的附件 id
            Attachment.requirement_id == requirement_id,
        )
        .all()
    )
    images: list[ContextImage] = []
    for att in rows:
        img = _resolve_ui_image(att)
        if img is None:
            logger.warning(
                "[case_gen_ctx] attachment %s 不可用（kind/path 不对）",
                att.id,
            )
            continue
        images.append(img)
    return images


def _load_latest_analysis_doc(
    session: Session, requirement_id: int
) -> Optional[RequirementAnalysisDocument]:
    return (
        session.query(RequirementAnalysisDocument)
        .filter(RequirementAnalysisDocument.requirement_id == requirement_id)
        .order_by(RequirementAnalysisDocument.created_at.desc())
        .first()
    )


def _load_project_context(
    session: Session, requirement_id: int
) -> tuple[str, list[int]]:
    """检索项目记忆层（project_contexts），渲染为可注入 prompt 的文本。

    失败绝不阻断用例生成 —— 记忆层是增强项不是依赖项，
    项目没跑过 M6 需求解析时记忆层为空，返回占位文案即可。
    """
    from server.services.context_service import (
        build_context_summary,
        retrieve_context,
    )

    empty = "（项目暂无沉淀上下文，跳过此节）"
    try:
        req = (
            session.query(Requirement)
            .filter(Requirement.id == requirement_id)
            .one_or_none()
        )
        if req is None or not req.project_id:
            return empty, []

        query_text = f"{req.title or ''}\n{(req.description or '')[:500]}"
        matched = retrieve_context(
            query_text=query_text,
            project_id=req.project_id,
            top_k=DEFAULT_PROJECT_CONTEXT_TOP_K,
            target_types=CASE_GEN_CONTEXT_TYPES,
        )
        if not matched:
            return empty, []

        text = _truncate_md(
            build_context_summary(matched), MAX_PROJECT_CONTEXT_CHARS
        )
        ids = [int(c["id"]) for c in matched if c.get("id")]
        logger.info(
            "[case_gen_ctx] requirement=%s 记忆层命中 %d 条上下文",
            requirement_id, len(ids),
        )
        return text, ids
    except Exception:
        logger.warning(
            "[case_gen_ctx] requirement=%s 记忆层检索失败，降级为空",
            requirement_id, exc_info=True,
        )
        return empty, []


def _load_existing_case_titles(
    session: Session, requirement_id: int, limit: int,
    module_id: Optional[int] = None,
) -> list[str]:
    """已有用例标题。有模块时按模块取（防重复的范围才有意义），否则退回同需求。"""
    q = session.query(TestCase.name).filter(
        TestCase.case_type == CASE_TYPE_FUNCTIONAL
    )
    if module_id:
        q = q.filter(TestCase.module_id == module_id)
    else:
        q = q.filter(TestCase.requirement_id == requirement_id)
    rows = q.order_by(TestCase.id.desc()).limit(limit).all()
    return [r[0] for r in rows if r[0]]


def _load_exemplar_cases(
    session: Session, requirement_id: int,
    module_id: Optional[int] = None,
    limit: int = DEFAULT_EXEMPLAR_LIMIT,
) -> str:
    """few-shot 样例：被人工采纳的草稿是本项目的"质量金标准"。

    优先同模块最近 accepted 的完整草稿（title/preconditions/steps/expected/step_template），
    渲染为可注入文本；没有则返回占位文案。
    """
    q = (
        session.query(AiCaseDraft)
        .filter(AiCaseDraft.status == AI_CASE_DRAFT_STATUS_ACCEPTED)
    )
    if module_id:
        q = q.join(
            Requirement, AiCaseDraft.requirement_id == Requirement.id
        ).filter(Requirement.module_id == module_id)
    else:
        q = q.filter(AiCaseDraft.requirement_id == requirement_id)

    drafts = q.order_by(AiCaseDraft.id.desc()).limit(limit).all()
    if not drafts:
        return "（暂无已采纳的样例）"

    return _render_draft_blocks(drafts)


def _load_rejected_examples(
    session: Session, requirement_id: int,
    module_id: Optional[int] = None,
    limit: int = 5,
) -> str:
    """反例：最近被拒且填了原因的草稿（标题 + 拒因），让模型别再犯同样的错。"""
    from database.models import AI_CASE_DRAFT_STATUS_REJECTED

    q = (
        session.query(AiCaseDraft.title, AiCaseDraft.reject_reason)
        .filter(
            AiCaseDraft.status == AI_CASE_DRAFT_STATUS_REJECTED,
            AiCaseDraft.reject_reason.isnot(None),
        )
    )
    if module_id:
        q = q.join(
            Requirement, AiCaseDraft.requirement_id == Requirement.id
        ).filter(Requirement.module_id == module_id)
    else:
        q = q.filter(AiCaseDraft.requirement_id == requirement_id)

    rows = q.order_by(AiCaseDraft.id.desc()).limit(limit).all()
    if not rows:
        return "（暂无被拒记录）"
    return "\n".join(
        f"- 《{(t or '')[:60]}》被拒，原因：{(r or '').strip()[:150]}"
        for t, r in rows
    )


def _render_draft_blocks(drafts: list) -> str:
    import json as _json

    blocks: list[str] = []
    for d in drafts:
        step_tpl = ""
        if d.step_template:
            try:
                step_tpl = _json.dumps(
                    d.step_template, ensure_ascii=False
                )[:600]
            except Exception:
                step_tpl = ""
        blocks.append(
            f"### {d.title}\n"
            f"- 前置条件: {(d.preconditions or '（无）')[:200]}\n"
            f"- 步骤:\n{(d.steps_text or '（无）')[:500]}\n"
            f"- 预期:\n{(d.expected or '（无）')[:300]}"
            + (f"\n- step_template: `{step_tpl}`" if step_tpl else "")
        )
    return _truncate_md("\n\n".join(blocks), MAX_EXEMPLAR_CHARS)


def build_case_generation_context(
    session: Session,
    requirement_id: int,
    analysis_document_id: Optional[int] = None,
    ui_image_attachment_ids: Optional[list[int]] = None,
    existing_case_excerpt_limit: int = DEFAULT_EXISTING_CASE_LIMIT,
) -> CaseGenerationContext:
    """汇聚 AI 用例生成的输入上下文。

    - analysis_document_id 给了就强制用这个；不给就拉最新的；都没就空
    - ui_image_attachment_ids 列表里的 attachment 必须属于该 requirement
    - 已有用例标题最多带 limit 条，让 AI 知道"哪些已经覆盖了"
    """
    base = build_requirement_context(session, requirement_id)

    # ── 分析文档 ───────────────────────────────────────────
    doc: Optional[RequirementAnalysisDocument] = None
    if analysis_document_id:
        doc = (
            session.query(RequirementAnalysisDocument)
            .filter(
                RequirementAnalysisDocument.id == analysis_document_id,
                RequirementAnalysisDocument.requirement_id == requirement_id,
            )
            .one_or_none()
        )
        if doc is None:
            logger.warning(
                "[case_gen_ctx] analysis_document_id=%s 不存在或不属于需求 %s，回退为最新",
                analysis_document_id, requirement_id,
            )
    if doc is None:
        doc = _load_latest_analysis_doc(session, requirement_id)

    analysis_markdown = ""
    analysis_doc_id = None
    analysis_model_label = None
    if doc is not None:
        analysis_markdown = _truncate_md(
            doc.current_markdown or "", MAX_ANALYSIS_MARKDOWN_CHARS
        )
        analysis_doc_id = doc.id
        analysis_model_label = doc.model_label

    # ── UI 截图 ────────────────────────────────────────────
    ui_images = _load_ui_images(
        session, requirement_id, ui_image_attachment_ids or []
    )

    # ── 已有 functional 用例标题（有模块按模块，否则同需求）────
    module_id = (base.module or {}).get("id")
    existing_case_titles = _load_existing_case_titles(
        session, requirement_id, existing_case_excerpt_limit,
        module_id=module_id,
    )

    # ── 项目记忆层（business_rule / data_model / api_contract …）──
    project_context_text, matched_context_ids = _load_project_context(
        session, requirement_id
    )

    # ── few-shot 样例（同模块已采纳草稿 = 质量金标准）──────────
    exemplar_cases_text = _load_exemplar_cases(
        session, requirement_id, module_id=module_id
    )

    # ── 反例（被拒草稿 + 拒因,数据飞轮回填）─────────────────
    rejected_examples_text = _load_rejected_examples(
        session, requirement_id, module_id=module_id
    )

    return CaseGenerationContext(
        base=base,
        analysis_markdown=analysis_markdown,
        analysis_document_id=analysis_doc_id,
        analysis_model_label=analysis_model_label,
        ui_images=ui_images,
        ui_image_attachment_ids=list(ui_image_attachment_ids or []),
        existing_case_titles=existing_case_titles,
        project_context_text=project_context_text,
        matched_context_ids=matched_context_ids,
        exemplar_cases_text=exemplar_cases_text,
        rejected_examples_text=rejected_examples_text,
    )


def render_case_generation_placeholders(
    ctx: CaseGenerationContext,
    count: int,
    scenario_mix: str,
    user_prompt: str = "",
) -> dict[str, str]:
    """渲染 prompt 占位符。同 M6 的 render_context_as_text，再加 M7 独有的几个。"""
    base_placeholders = render_context_as_text(ctx.base)

    base_placeholders.update(
        {
            "ANALYSIS_MARKDOWN": ctx.analysis_markdown or "（暂无 AI 分析文档）",
            "ANALYSIS_MODEL_LABEL": ctx.analysis_model_label or "未指定",
            "PROJECT_CONTEXT": ctx.project_context_text
            or "（项目暂无沉淀上下文，跳过此节）",
            "EXEMPLAR_CASES": ctx.exemplar_cases_text
            or "（暂无已采纳的样例）",
            "REJECTED_EXAMPLES": ctx.rejected_examples_text
            or "（暂无被拒记录）",
            "UI_IMAGE_COUNT": str(len(ctx.ui_images)),
            "EXISTING_CASE_TITLES": _render_titles(ctx.existing_case_titles),
            "SCENARIO_MIX": scenario_mix,
            "SCENARIO_MIX_DESC": _scenario_mix_desc(scenario_mix),
            "TARGET_CASE_COUNT": str(count),
            "USER_PROMPT": user_prompt.strip() or "（用户未补充）",
        }
    )
    return base_placeholders


def _render_titles(titles: list[str]) -> str:
    if not titles:
        return "（同需求暂无已有用例）"
    return "\n".join(f"- {t}" for t in titles)


def _scenario_mix_desc(mix: str) -> str:
    return {
        "positive_only": "只产正向（happy path）用例",
        "positive_and_negative": "正向 + 常见异常分支，比例约 2:1",
        "all_scenarios": "正向 + 异常 + 边界 + 安全/权限，尽可能覆盖",
    }.get(mix, "正向 + 异常")
