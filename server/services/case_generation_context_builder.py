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


def _load_existing_case_titles(
    session: Session, requirement_id: int, limit: int
) -> list[str]:
    rows = (
        session.query(TestCase.name)
        .filter(
            TestCase.requirement_id == requirement_id,
            TestCase.case_type == CASE_TYPE_FUNCTIONAL,
        )
        .order_by(TestCase.id.desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows if r[0]]


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

    # ── 同需求已有 functional 用例标题 ─────────────────────
    existing_case_titles = _load_existing_case_titles(
        session, requirement_id, existing_case_excerpt_limit
    )

    return CaseGenerationContext(
        base=base,
        analysis_markdown=analysis_markdown,
        analysis_document_id=analysis_doc_id,
        analysis_model_label=analysis_model_label,
        ui_images=ui_images,
        ui_image_attachment_ids=list(ui_image_attachment_ids or []),
        existing_case_titles=existing_case_titles,
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
