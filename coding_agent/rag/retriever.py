"""RAG retriever —— 给定 query 文本，从 ``code_chunks`` 拉 top-k 相关代码段。

双路径设计（自动检测 ``CodeChunk.embedding`` 列实际类型）：
- **pgvector** 装了 → SQL 里走 ``embedding <=> :query_vec``（cosine distance），
  数据库侧 ORDER BY + LIMIT，10W 行也是亚秒级
- **JSON 回退** → 把 project 全部 chunks 拉到内存做 Python cosine；
  适合 <5K chunks，第 1 期单机部署够用。后期切 pgvector 后无需改 retriever 调用方

Hybrid 混合检索（BM25 + Embedding + RRF）：
- embedding 搜索负责语义匹配
- BM25 搜索负责关键词精确命中（文件名、API 名、变量名）
- Reciprocal Rank Fusion 融合两种排序，优先返回两种方法都命中的结果

为啥不在 retriever 里管"换 embedding 模型 → 维度对不上"：
- 切模型必须重建索引（plan 风险表已写）；
- 这里碰到维度不匹配直接抛 RuntimeError，让用户去运维流程；
- 静默截断 / 补零会让召回质量神秘暴跌，反而难排查
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ai_gateway.embeddings import EmbeddingConfig, embed_texts, load_embedding_config
from database.models.code_chunk import CodeChunk

from .bm25 import Bm25Index

logger = logging.getLogger(__name__)


DEFAULT_TOP_K = 8


@dataclass(frozen=True)
class RetrievedChunk:
    """retrieve_relevant 的返回元素。``score`` 是 cosine 相似度（1 = 完全一致）。"""
    file_path: str
    chunk_idx: int
    start_line: int
    end_line: int
    content: str
    score: float


def retrieve_relevant(
    session: Session,
    project_id: int,
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    cfg: Optional[EmbeddingConfig] = None,
    git_sha: Optional[str] = None,
    hybrid: bool = True,
) -> list[RetrievedChunk]:
    """对单条 query 取 top-k 相关 chunk。

    Args:
        session: SQLAlchemy session
        project_id: 限定项目
        query: 自然语言或代码片段
        top_k: 返回条数
        cfg: 不传则从 ``config_store`` 自动加载
        git_sha: 限定到某个 commit
        hybrid: True → embedding + BM25 混合检索（RRF 融合）；False → 纯 embedding

    Returns:
        按 score 降序的 ``RetrievedChunk`` 列表
    """
    if not query or not query.strip():
        return []
    if top_k <= 0:
        raise ValueError(f"top_k 必须 > 0，当前 {top_k}")

    if hybrid:
        return _retrieve_hybrid(session, project_id, query, top_k, cfg, git_sha)

    if cfg is None:
        cfg = load_embedding_config()

    vectors, _tokens = embed_texts([query], cfg=cfg)
    if not vectors:
        logger.warning("retrieve_relevant: query embedding 为空 — query=%r", query[:100])
        return []
    query_vec = vectors[0]

    if _has_pgvector_column():
        return _retrieve_pgvector(session, project_id, query_vec, top_k, git_sha)
    return _retrieve_json_fallback(session, project_id, query_vec, top_k, git_sha)


# ---------------------------------------------------------------------------
# pgvector 路径：SQL 排序 + LIMIT
# ---------------------------------------------------------------------------
def _retrieve_pgvector(
    session: Session,
    project_id: int,
    query_vec: list[float],
    top_k: int,
    git_sha: Optional[str],
) -> list[RetrievedChunk]:
    # pgvector 的 ``<=>`` 是 cosine distance（0=同向，2=反向）；score = 1 - distance
    sql = """
        SELECT file_path, chunk_idx, start_line, end_line, content,
               1 - (embedding <=> CAST(:qv AS vector)) AS score
        FROM code_chunks
        WHERE project_id = :pid
          AND embedding IS NOT NULL
    """
    params: dict = {"pid": project_id, "qv": _format_pgvector(query_vec)}
    if git_sha:
        sql += " AND git_sha = :sha"
        params["sha"] = git_sha
    sql += " ORDER BY embedding <=> CAST(:qv AS vector) LIMIT :k"
    params["k"] = top_k

    rows = session.execute(text(sql), params).all()
    return [
        RetrievedChunk(
            file_path=r.file_path,
            chunk_idx=r.chunk_idx,
            start_line=r.start_line or 0,
            end_line=r.end_line or 0,
            content=r.content,
            score=float(r.score),
        )
        for r in rows
    ]


def _format_pgvector(vec: list[float]) -> str:
    """pgvector 的文本字面量格式：``[0.1,0.2,0.3]``，没有空格。"""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# ---------------------------------------------------------------------------
# JSON 回退路径：内存里算 cosine
# ---------------------------------------------------------------------------
def _retrieve_json_fallback(
    session: Session,
    project_id: int,
    query_vec: list[float],
    top_k: int,
    git_sha: Optional[str],
) -> list[RetrievedChunk]:
    q = session.query(CodeChunk).filter(
        CodeChunk.project_id == project_id,
        CodeChunk.embedding.isnot(None),
    )
    if git_sha:
        q = q.filter(CodeChunk.git_sha == git_sha)

    rows = q.all()
    if not rows:
        return []

    q_norm = math.sqrt(sum(x * x for x in query_vec)) or 1.0
    expected_dim = len(query_vec)

    scored: list[tuple[float, CodeChunk]] = []
    for row in rows:
        vec = row.embedding
        if not isinstance(vec, list) or not vec:
            continue
        if len(vec) != expected_dim:
            # 维度不一致 = embedding 模型已经换过，老 chunk 留着只会污染召回
            raise RuntimeError(
                f"code_chunks.embedding 维度 {len(vec)} 与 query {expected_dim} 不一致，"
                f"project_id={project_id} 需要重建 RAG 索引"
            )
        dot = sum(a * b for a, b in zip(query_vec, vec))
        v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        scored.append((dot / (q_norm * v_norm), row))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        RetrievedChunk(
            file_path=row.file_path,
            chunk_idx=row.chunk_idx,
            start_line=row.start_line or 0,
            end_line=row.end_line or 0,
            content=row.content,
            score=float(score),
        )
        for score, row in scored[:top_k]
    ]



# ---------------------------------------------------------------------------
# Hybrid 混合检索：Embedding + BM25 → 加权组合排序
# ---------------------------------------------------------------------------
def _retrieve_hybrid(
    session: Session,
    project_id: int,
    query: str,
    top_k: int,
    cfg: Optional[EmbeddingConfig],
    git_sha: Optional[str],
) -> list[RetrievedChunk]:
    """Embedding 语义检索 + BM25 关键词检索 → 归一化加权组合排序。

    权重：BM25 0.6（关键词精确匹配优先），Embedding 0.4（语义兜底）。
    """
    # 1. 加载所有 chunks
    q = session.query(CodeChunk).filter(
        CodeChunk.project_id == project_id,
        CodeChunk.embedding.isnot(None),
    )
    if git_sha:
        q = q.filter(CodeChunk.git_sha == git_sha)
    all_chunks = q.all()
    if not all_chunks:
        return []

    # 2. Embedding 检索
    if cfg is None:
        cfg = load_embedding_config()
    vectors, _tokens = embed_texts([query], cfg=cfg)
    if not vectors:
        logger.warning("retrieve_hybrid: query embedding 为空 — query=%r", query[:100])
        return []
    query_vec = vectors[0]
    q_norm = math.sqrt(sum(x * x for x in query_vec)) or 1.0

    emb_scores: dict[int, float] = {}
    for row in all_chunks:
        vec = row.embedding
        if not isinstance(vec, list) or not vec:
            continue
        dot = sum(a * b for a, b in zip(query_vec, vec))
        v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        emb_scores[row.id] = dot / (q_norm * v_norm)

    # 3. BM25 检索
    bm25 = Bm25Index([(c.id, c.content or "") for c in all_chunks])
    bm25_hits = bm25.search(query, top_k=max(top_k * 5, 50))
    bm25_scores: dict[int, float] = {h.chunk_id: h.score for h in bm25_hits}

    # 4. min-max 归一化 + 加权组合
    combined: dict[int, float] = {}
    emb_max = max(emb_scores.values()) if emb_scores else 1.0
    emb_min = min(emb_scores.values()) if emb_scores else 0.0
    emb_range = emb_max - emb_min or 1.0

    bm25_max = max(bm25_scores.values()) if bm25_scores else 1.0
    bm25_min = min(bm25_scores.values()) if bm25_scores else 0.0
    bm25_range = bm25_max - bm25_min or 1.0

    EMB_WEIGHT = 0.4
    BM25_WEIGHT = 0.6

    id_to_row = {c.id: c for c in all_chunks}

    for cid in {*emb_scores, *bm25_scores}:
        emb_norm = (emb_scores.get(cid, emb_min) - emb_min) / emb_range
        bm25_norm = (bm25_scores.get(cid, 0.0) - bm25_min) / bm25_range
        score = EMB_WEIGHT * emb_norm + BM25_WEIGHT * bm25_norm

        # 文件名匹配加权：query 中提取的英文词命中文件路径片段
        import re as _re
        fp = id_to_row.get(cid)
        if fp:
            fname_lower = fp.file_path.lower()
            query_en_words = set(_re.findall(r'[a-z]{3,}', query.lower()))
            hits = sum(1 for w in query_en_words if w in fname_lower)
            score += min(hits * 0.20, 0.40)

        combined[cid] = score

    merged = sorted(combined.items(), key=lambda x: -x[1])

    # 同文件加分：排名靠前的 chunk 所在文件，其所有 chunk 获得小量加权（最多 5 个文件）
    file_bonus: dict[str, float] = {}
    for rank, (cid, score) in enumerate(merged[:20]):
        fp = id_to_row[cid].file_path
        if fp not in file_bonus:
            file_bonus[fp] = max(file_bonus.get(fp, 0.0), 0.15 * (1.0 - rank / 20))
        if len(file_bonus) >= 8:
            break
    for fp, bonus in file_bonus.items():
        for row in all_chunks:
            if row.file_path == fp:
                old = combined.get(row.id, 0.0)
                combined[row.id] = old + bonus * (1.0 if row.id not in {c for c, _ in merged[:top_k]} else 0.5)

    # 重新排序，限制总量
    merged = sorted(combined.items(), key=lambda x: -x[1])
    final_sorted = merged[:top_k * 3]

    return [
        RetrievedChunk(
            file_path=id_to_row[cid].file_path,
            chunk_idx=id_to_row[cid].chunk_idx,
            start_line=id_to_row[cid].start_line or 0,
            end_line=id_to_row[cid].end_line or 0,
            content=id_to_row[cid].content,
            score=round(max(score, 0), 4),
        )
        for cid, score in final_sorted
    ]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _has_pgvector_column() -> bool:
    """检测 ``CodeChunk.embedding`` 是否绑定到 pgvector.Vector 类型。

    Vector 类来自 ``pgvector.sqlalchemy``，class name 就叫 'Vector'；JSON 列叫 'JSON'。
    这是导入期就定下的 —— 切换 pgvector 要重启进程，不存在 cache 不一致。
    """
    try:
        col_type = type(CodeChunk.__table__.c.embedding.type).__name__
        return col_type == "Vector"
    except Exception:  # noqa: BLE001
        return False
