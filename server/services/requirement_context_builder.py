"""需求上下文构建器 —— AI 分析的输入源（M6）。

把一个 requirement 的全部"分析素材"汇聚成一个结构化 dict：
  - 自己的元信息（标题 / 描述 / 优先级 / 时间 / 4 角色 assignee）
  - 关联模块（含该模块的功能点摘要）
  - depends_on 关联需求（自身已 M5 建模为 JSON list）
  - 子需求（M5 父子结构）
  - 附件：
      文档（PDF/DOCX/MD/TXT 用 doc_parser 提文，截 4000 字）
      图片（只记 abs_path，base64 由 gateway 在 vision 模式下现填）
      跳过的（kind=link 或后缀不在白名单）

每段都做了截断（标题/描述/附件），确保 prompt 总长可控；超出部分在
返回结构里以 `skipped` 字段保留可见性。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, selectinload

from database.models import (
    Attachment,
    ATTACHMENT_KIND_FILE,
    Module,
    Requirement,
    RequirementAssignee,
    User,
)


logger = logging.getLogger(__name__)


# 项目根，对齐 server/main.py / server/api/attachments.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ATTACHMENTS_DIR = _PROJECT_ROOT / "data" / "attachments"

# 截断阈值
MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 4000
MAX_RELATED_DESCRIPTION_CHARS = 600   # depends_on / children 的 description
MAX_DOC_EXCERPT_CHARS = 4000

DOC_EXTS = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt", ".text"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ContextImage:
    name: str
    mime: str
    abs_path: str
    size_bytes: int | None = None


@dataclass
class ContextDocument:
    name: str
    mime: str
    text_excerpt: str


@dataclass
class ContextSkipped:
    name: str
    reason: str  # "link" | "unsupported_type" | "file_missing" | "parse_failed"


@dataclass
class RequirementContext:
    requirement: dict[str, Any]
    module: dict[str, Any] | None
    depends_on: list[dict[str, Any]] = field(default_factory=list)
    children: list[dict[str, Any]] = field(default_factory=list)
    documents: list[ContextDocument] = field(default_factory=list)
    images: list[ContextImage] = field(default_factory=list)
    skipped: list[ContextSkipped] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "module": self.module,
            "depends_on": self.depends_on,
            "children": self.children,
            "attachments": {
                "documents": [asdict(d) for d in self.documents],
                "images": [asdict(i) for i in self.images],
                "skipped": [asdict(s) for s in self.skipped],
            },
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _truncate(text: str | None, n: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "\n…[truncated]"


def _user_label(u: User | None) -> str:
    if u is None:
        return ""
    return u.full_name or u.username or f"#{u.id}"


def _assignees_grouped(
    session: Session, requirement_id: int
) -> dict[str, list[str]]:
    rows = (
        session.query(RequirementAssignee)
        .filter(RequirementAssignee.requirement_id == requirement_id)
        .all()
    )
    user_ids = {r.user_id for r in rows if r.user_id}
    users = (
        session.query(User).filter(User.id.in_(user_ids)).all()
        if user_ids
        else []
    )
    name_by_id = {u.id: _user_label(u) for u in users}

    out: dict[str, list[str]] = {"dev": [], "test": [], "pm": [], "ui": []}
    for r in rows:
        label = name_by_id.get(r.user_id, f"#{r.user_id}")
        if r.role in out:
            out[r.role].append(label)
    return out


def _serialize_requirement(req: Requirement, assignees: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "id": req.id,
        "title": _truncate(req.title, MAX_TITLE_CHARS),
        "description": _truncate(req.description, MAX_DESCRIPTION_CHARS),
        "priority": req.priority,
        "system_status": req.system_status,
        "business_status": req.business_status,
        "planned_start_at": req.planned_start_at.isoformat()
        if req.planned_start_at
        else None,
        "planned_end_at": req.planned_end_at.isoformat()
        if req.planned_end_at
        else None,
        "acceptance_criteria": req.acceptance_criteria or [],
        "tags": req.tags or [],
        "assignees": assignees,
    }


def _serialize_related(req: Requirement) -> dict[str, Any]:
    """简版 —— depends_on / children 的精简表示。"""
    return {
        "id": req.id,
        "title": _truncate(req.title, MAX_TITLE_CHARS),
        "description": _truncate(req.description, MAX_RELATED_DESCRIPTION_CHARS),
        "priority": req.priority,
        "system_status": req.system_status,
    }


def _mime_of(path: Path) -> str:
    suf = path.suffix.lower()
    mapping = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".text": "text/plain",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }
    return mapping.get(suf, "application/octet-stream")


def _resolve_local_path(att: Attachment) -> Path | None:
    """Attachment.url 形如 '/attachments/req_42/uuid_name.pdf'。

    返回项目内绝对路径；找不到则 None。
    """
    url = att.url or ""
    if url.startswith("/attachments/"):
        rel = url[len("/attachments/"):]
        candidate = ATTACHMENTS_DIR / rel
        if candidate.is_file():
            return candidate
    # 兜底：直接 join 文件名
    candidate = ATTACHMENTS_DIR / f"req_{att.requirement_id}" / Path(att.url).name
    if candidate.is_file():
        return candidate
    return None


def _load_documents_and_images(
    attachments: list[Attachment],
) -> tuple[list[ContextDocument], list[ContextImage], list[ContextSkipped]]:
    docs: list[ContextDocument] = []
    imgs: list[ContextImage] = []
    skipped: list[ContextSkipped] = []

    for att in attachments:
        if att.kind != ATTACHMENT_KIND_FILE:
            skipped.append(ContextSkipped(name=att.name, reason="link"))
            continue
        path = _resolve_local_path(att)
        if path is None:
            skipped.append(ContextSkipped(name=att.name, reason="file_missing"))
            continue

        suf = path.suffix.lower()
        mime = _mime_of(path)

        if suf in DOC_EXTS:
            try:
                from server.services.doc_parser import parse_document

                parsed = parse_document(
                    str(path),
                    chunk=False,
                    max_total_chars=MAX_DOC_EXCERPT_CHARS,
                )
                docs.append(
                    ContextDocument(
                        name=att.name,
                        mime=mime,
                        text_excerpt=_truncate(parsed.plain_text, MAX_DOC_EXCERPT_CHARS),
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("解析附件文档失败 %s: %s", path, e)
                skipped.append(ContextSkipped(name=att.name, reason="parse_failed"))
        elif suf in IMAGE_EXTS:
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            imgs.append(
                ContextImage(
                    name=att.name,
                    mime=mime,
                    abs_path=str(path),
                    size_bytes=size,
                )
            )
        else:
            skipped.append(ContextSkipped(name=att.name, reason="unsupported_type"))

    return docs, imgs, skipped


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def build_requirement_context(
    session: Session, requirement_id: int
) -> RequirementContext:
    """汇聚一个 requirement 的全部分析输入。失败时抛 ValueError。"""
    req: Requirement | None = (
        session.query(Requirement)
        .options(
            selectinload(Requirement.children),
            selectinload(Requirement.attachments),
        )
        .filter(Requirement.id == requirement_id)
        .one_or_none()
    )
    if req is None:
        raise ValueError(f"需求 #{requirement_id} 不存在")

    assignees = _assignees_grouped(session, req.id)
    req_dict = _serialize_requirement(req, assignees)

    # 模块
    module_dict: dict[str, Any] | None = None
    if req.module_id:
        m: Module | None = (
            session.query(Module).filter(Module.id == req.module_id).one_or_none()
        )
        if m is not None:
            module_dict = {
                "id": m.id,
                "name": m.name,
                "description": _truncate(getattr(m, "description", None), 1000),
            }

    # depends_on
    depends_on_ids = [int(x) for x in (req.depends_on or []) if str(x).strip().isdigit()]
    depends_on: list[dict[str, Any]] = []
    if depends_on_ids:
        dep_rows = (
            session.query(Requirement)
            .filter(Requirement.id.in_(depends_on_ids))
            .all()
        )
        depends_on = [_serialize_related(r) for r in dep_rows]

    # children
    children = [_serialize_related(c) for c in (req.children or [])]

    # attachments
    documents, images, skipped = _load_documents_and_images(req.attachments or [])

    return RequirementContext(
        requirement=req_dict,
        module=module_dict,
        depends_on=depends_on,
        children=children,
        documents=documents,
        images=images,
        skipped=skipped,
    )


def render_context_as_text(ctx: RequirementContext) -> dict[str, str]:
    """把 RequirementContext 渲染成 prompt 占位符替换值字典。"""
    req = ctx.requirement

    def _kv_block(items: list[dict[str, Any]], header: str) -> str:
        if not items:
            return "（无）"
        lines = []
        for it in items:
            lines.append(
                f"- [#{it['id']}] **{it['title']}** "
                f"(优先级 {it.get('priority', 2)}, "
                f"状态 {it.get('system_status') or '未派生'})"
            )
            if it.get("description"):
                lines.append(f"  {it['description']}")
        return "\n".join(lines)

    def _module_block(m: dict[str, Any] | None) -> str:
        if not m:
            return "（无关联模块）"
        body = f"- ID: {m['id']}\n- 名称: {m['name']}"
        if m.get("description"):
            body += f"\n- 描述: {m['description']}"
        return body

    def _docs_block(docs: list[ContextDocument]) -> str:
        if not docs:
            return "（无附件文档）"
        out = []
        for d in docs:
            out.append(f"### {d.name}\n```\n{d.text_excerpt}\n```")
        return "\n\n".join(out)

    def _skipped_block(items: list[ContextSkipped]) -> str:
        if not items:
            return ""
        labels = ", ".join(f"{s.name} ({s.reason})" for s in items)
        return f"\n\n> 已跳过附件：{labels}"

    def _assignees_block(a: dict[str, list[str]]) -> str:
        parts = []
        for role_label, role in (
            ("开发", "dev"), ("测试", "test"), ("产品", "pm"), ("UI", "ui"),
        ):
            names = a.get(role) or []
            parts.append(f"{role_label}: {', '.join(names) if names else '未指派'}")
        return " | ".join(parts)

    def _list_block(items: list[Any], empty: str = "（无）") -> str:
        if not items:
            return empty
        return "\n".join(f"- {item}" for item in items if str(item).strip()) or empty

    return {
        "REQUIREMENT_ID": str(req["id"]),
        "REQUIREMENT_TITLE": req["title"],
        "REQUIREMENT_DESCRIPTION": req.get("description", "") or "（无描述）",
        "REQUIREMENT_PRIORITY": str(req.get("priority", 2)),
        "REQUIREMENT_SYSTEM_STATUS": req.get("system_status") or "未派生",
        "REQUIREMENT_BUSINESS_STATUS": req.get("business_status") or "未设置",
        "REQUIREMENT_TAGS": ", ".join(req.get("tags") or []) or "（无）",
        "REQUIREMENT_ACCEPTANCE_CRITERIA": _list_block(
            req.get("acceptance_criteria") or [], "（无现成验收标准）"
        ),
        "REQUIREMENT_PLANNED_START": req.get("planned_start_at") or "未设置",
        "REQUIREMENT_PLANNED_END": req.get("planned_end_at") or "未设置",
        "REQUIREMENT_ASSIGNEES": _assignees_block(req.get("assignees", {})),
        "MODULE_INFO": _module_block(ctx.module),
        "DEPENDS_ON": _kv_block(ctx.depends_on, "依赖需求"),
        "CHILDREN": _kv_block(ctx.children, "子需求"),
        "DOCUMENT_EXCERPTS": _docs_block(ctx.documents) + _skipped_block(ctx.skipped),
        "IMAGE_COUNT": str(len(ctx.images)),
    }
