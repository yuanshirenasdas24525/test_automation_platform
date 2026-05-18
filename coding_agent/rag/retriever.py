"""RAG retriever —— 给定 query 文本，从 ``code_chunks`` 拉 top-k 相关代码段。

双路径设计（自动检测 ``CodeChunk.embedding`` 列实际类型）：
- **pgvector** 装了 → SQL 里走 ``embedding <=> :query_vec``（cosine distance），
  数据库侧 ORDER BY + LIMIT，10W 行也是亚秒级
- **JSON 回退** → 把 project 全部 chunks 拉到内存做 Python cosine；
  适合 <5K chunks，第 1 期单机部署够用。后期切 pgvector 后无需改 retriever 调用方

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
) -> list[RetrievedChunk]:
    """对单条 query 取 top-k 相关 chunk。

    Args:
        session: SQLAlchemy session
        project_id: 限定项目
        query: 自然语言或代码片段
        top_k: 返回条数
        cfg: 不传则从 ``config_store`` 自动加载（embedding model 必须跟索引时一致）
        git_sha: 限定到某个 commit；不传则匹配 project 下所有 sha

    Returns:
        按 score 降序的 ``RetrievedChunk`` 列表；可能为空（项目还没索引）
    """
    if not query or not query.strip():
        return []
    if top_k <= 0:
        raise ValueError(f"top_k 必须 > 0，当前 {top_k}")

    if cfg is None:
        cfg = load_embedding_config()

    vectors, _tokens = embed_texts([query], cfg=cfg)
    if not vectors:
        logger.warning("retrieve_relevant: query embedding 为空，跳过 — query=%r", query[:100])
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
