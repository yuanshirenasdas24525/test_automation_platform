"""项目上下文服务（Context Service）。

职责：
  1. 从 AI 分析结果中提取上下文片段，持久化到 project_contexts 表
  2. 按查询文本检索最相关的历史上下文（关键词 + 文本相似度）
  3. 编排上下文片段，注入 AI Prompt（按优先级和相关性排序）

设计：
  - 不使用向量数据库（pgvector 为可选），先用关键词匹配 + Jaccard 相似度
  - 后续可平滑升级到 pgvector 语义检索
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 停用词（中文高频词 + 标点）
_STOP_WORDS = set(
    "的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 "
    "会 着 没有 看 好 自己 这 他 她 它 们 那 些 什么 怎么 如何 能 "
    "被 把 从 为 以 对 与 但 而 或 及 其 中 将 已 经 还 又 便 "
    "可以 需要 必须 应该 可能 如果 因为 所以 虽然 但是 然而 而且 "
    "这个 那个 这样 那样 等 等等 已 该 如 若 按 按照 根据"
    .split()
)

# 上下文检索默认参数
DEFAULT_RETRIEVAL_TOP_K = 20      # 标准分析
DEFAULT_RETRIEVAL_TOP_K_DEEP = 50  # 深度分析
DEFAULT_RETRIEVAL_TOP_K_QUICK = 5  # 快速扫描


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def retrieve_context(
    query_text: str,
    project_id: int,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    target_types: Optional[List[str]] = None,
) -> List[dict]:
    """检索与查询文本最相关的项目上下文。

    Args:
        query_text: 查询文本（新需求的文字）
        project_id: 项目 ID
        top_k: 返回前 K 个最相关的结果
        target_types: 限定上下文字段类型（如 ["business_rule", "data_model"]），None = 不限

    Returns:
        排序后的上下文列表，每项为 ProjectContext.to_dict() 格式 + score 字段
    """
    from database.db import DB
    from database.models import ProjectContext

    if not query_text.strip():
        return []

    db = DB()
    try:
        q = (
            db.session.query(ProjectContext)
            .filter(ProjectContext.project_id == project_id)
        )
        if target_types:
            q = q.filter(ProjectContext.context_type.in_(target_types))

        all_contexts = q.order_by(ProjectContext.importance.desc()).all()

        if not all_contexts:
            return []

        # 提取查询关键词
        query_kw = _extract_keywords(query_text)

        # 对每个上下文打分
        scored = []
        for ctx in all_contexts:
            score = _compute_similarity(
                query_kw,
                ctx.title + " " + (ctx.summary or "") + " " + ctx.content[:500],
                set(ctx.keywords or []),
                ctx.importance or 3,
            )
            if score > 0:
                d = ctx.to_dict()
                d["_score"] = round(score, 4)
                scored.append(d)

        # 按分数降序 + 重要性降序
        scored.sort(key=lambda x: (x["_score"], x.get("importance", 3)), reverse=True)
        result = scored[:top_k]

        # 移除内部排序字段
        for r in result:
            del r["_score"]

        return result
    finally:
        try:
            db.close()
        except Exception:
            pass


def build_context_summary(contexts: List[dict]) -> str:
    """从检索结果的上下文列表构建一个摘要文本，用于注入 Prompt。

    按上下文字段类型分组输出，方便 AI 理解项目结构。
    """
    if not contexts:
        return "（该项目暂无历史上下文信息）"

    # 按类型分组
    groups: Dict[str, List[dict]] = {}
    for ctx in contexts:
        ct = ctx.get("context_type", "unknown")
        if ct not in groups:
            groups[ct] = []
        groups[ct].append(ctx)

    # 类型友好名称
    type_names = {
        "business_rule": "业务规则",
        "data_model": "数据模型",
        "api_contract": "API 契约",
        "architecture": "架构信息",
        "term_definition": "术语定义",
        "requirement": "已有需求",
        "constraint": "约束条件",
        "user_scenario": "用户场景",
        "process_flow": "业务流程",
        "dependency": "模块依赖",
    }

    lines = ["## 项目已有信息概览\n"]
    for ct in [
        "architecture", "term_definition", "dependency",
        "business_rule", "requirement", "constraint",
        "data_model", "api_contract", "process_flow", "user_scenario",
    ]:
        items = groups.get(ct, [])
        if not items:
            continue
        name = type_names.get(ct, ct)
        lines.append(f"### {name}")
        for item in items[:10]:  # 每种类型最多 10 条
            title = item.get("title", "")
            summary = item.get("summary", "")
            module = f" [模块: {item.get('module_id')}]" if item.get("module_id") else ""
            line = f"- **{title}**{module}"
            if summary and summary != title:
                line += f"：{summary}"
            lines.append(line)
        lines.append("")

    return "\n".join(lines)


def save_contexts(
    contexts: List[dict],
    project_id: int,
    source_type: str = "document",
    source_file: str = "",
    ai_run_id: Optional[int] = None,
    session=None,  # 外部传入 session 避免 SQLite 写锁
) -> List[int]:
    """批量保存新的项目上下文片段。

    Args:
        contexts: 上下文片段列表，每项格式 {context_type, title, content, summary, keywords, ...}
        project_id: 项目 ID
        source_type: 来源类型
        source_file: 来源文件名
        ai_run_id: 关联的 AI 任务 ID
        session: 外部传入的 DB session（SQLite 必须共用连接，否则写锁冲突）

    Returns:
        新创建的 context IDs 列表
    """
    from database.models import ProjectContext

    if not contexts:
        return []

    close_session = False
    _db = None

    if session is None:
        from database.db import DB
        _db = DB()
        session = _db.session
        close_session = True

    try:
        created_ids = []
        for ctx_data in contexts:
            content = (ctx_data.get("content") or "").strip()
            title = (ctx_data.get("title") or "").strip()
            if not title or len(content) < 10:
                continue

            # 去重
            existing = (
                session.query(ProjectContext.id)
                .filter(
                    ProjectContext.project_id == project_id,
                    ProjectContext.title == title[:200],
                    ProjectContext.context_type == ctx_data.get("context_type", ""),
                )
                .first()
            )
            if existing:
                continue

            pc = ProjectContext(
                project_id=project_id,
                module_id=ctx_data.get("module_id"),
                source_type=source_type,
                source_file=source_file,
                context_type=ctx_data.get("context_type", ""),
                title=title[:255],
                content=content,
                summary=(ctx_data.get("summary") or "")[:500],
                tags=ctx_data.get("tags") or [],
                keywords=ctx_data.get("keywords") or _extract_keywords(content),
                importance=ctx_data.get("importance", 3),
            )
            session.add(pc)
            session.flush()
            created_ids.append(pc.id)

        session.flush()
        if close_session and _db:
            _db.commit()
        return created_ids
    except Exception:
        if close_session and _db:
            _db.session.rollback()
        raise
    finally:
        if close_session and _db:
            try:
                _db.close()
            except Exception:
                pass


def get_context_stats(project_id: int) -> dict:
    """获取项目上下文统计信息。"""
    from database.db import DB
    from database.models import ProjectContext
    from sqlalchemy import func as sa_func

    db = DB()
    try:
        total = (
            db.session.query(sa_func.count(ProjectContext.id))
            .filter(ProjectContext.project_id == project_id)
            .scalar()
            or 0
        )
        # 按类型统计
        by_type = (
            db.session.query(
                ProjectContext.context_type,
                sa_func.count(ProjectContext.id),
            )
            .filter(ProjectContext.project_id == project_id)
            .group_by(ProjectContext.context_type)
            .all()
        )
        return {
            "total": total,
            "by_type": {row[0]: row[1] for row in by_type},
        }
    finally:
        try:
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 文本工具（内部）
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """简单的中文 + 英文分词。"""
    # 提取中文字符 + 英文单词
    chinese_chars = re.findall(r"[一-鿿]{2,}", text)
    english_words = re.findall(r"[a-zA-Z_]{2,}", text.lower())
    # 合并
    tokens = chinese_chars + english_words
    # 去停用词 + 去重
    return [t for t in tokens if t not in _STOP_WORDS]


def _extract_keywords(text: str, max_kw: int = 10) -> List[str]:
    """从文本中提取关键词。"""
    tokens = _tokenize(text)
    if not tokens:
        return []
    counter = Counter(tokens)
    return [kw for kw, _ in counter.most_common(max_kw)]


def _compute_similarity(
    query_kw: List[str],
    ctx_text: str,
    ctx_kw: List[str],
    importance: int,
) -> float:
    """计算查询关键词与上下文的相似度分数。

    采用关键词命中率 + 文本 Jaccard 混合评分。
    """
    ctx_tokens = _tokenize(ctx_text)
    query_set = set(query_kw)
    ctx_set = set(ctx_tokens) | set(ctx_kw)

    if not query_set or not ctx_set:
        return 0.0

    # 关键词命中率
    hit_count = len(query_set & ctx_set)
    keyword_score = hit_count / max(len(query_set), 1)

    # 文本 Jaccard（取前 100 个 tokens）
    ctx_set_top = set(ctx_tokens[:100])
    jaccard = len(query_set & ctx_set_top) / max(len(query_set | ctx_set_top), 1)

    # 重要性加权
    importance_weight = min(importance, 5) / 5.0

    # 综合分数 = 关键词命中 (0.5) + Jaccard (0.3) + 重要性 (0.2)
    score = keyword_score * 0.5 + jaccard * 0.3 + importance_weight * 0.2
    return score
