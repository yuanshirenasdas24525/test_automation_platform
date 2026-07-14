"""RAG embedder —— 把 indexer 产出的 chunks 批量算 embedding → 落 ``code_chunks``。

设计：
- 调用方传入 ``IndexChunk`` 流 + project_id + git_sha + session，本函数负责：
    1. （可选）清掉旧索引：``project_id`` 这一行的所有 code_chunks 一把删
    2. 按 ``batch_size`` 切片，每片调一次 ``ai_gateway.embed_texts``
    3. ``session.bulk_save_objects`` 写一批
    4. 单批失败不阻断整体；记到 ``EmbedderStats.batches_failed``
- 失败策略宁可丢一批 chunk 也不让整次索引失败；写日志让人能 grep
- embedding 维度不在这里校验 —— provider 返回多少就存多少；维度变了由迁移层处理
- ``cfg`` 缺省自动 ``load_embedding_config()``；测试 / worker 可显式传入做隔离

为什么用 bulk_save_objects 而不是 ORM add：
- code_chunks 上没有任何 relationship 触发 / event listener，纯写入
- bulk_save_objects 在 5k 行级别比 add+flush 快 5-10x
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ai_gateway.embeddings import EmbeddingConfig, embed_texts, load_embedding_config
from database.models.code_chunk import CodeChunk

from .indexer import IndexChunk

logger = logging.getLogger(__name__)


# OpenAI 单批最多 2048 条 / 8192 token；这里取个稳的默认值，避免单批过大触发 timeout
DEFAULT_BATCH_SIZE = 64


@dataclass
class EmbedderStats:
    """一次 ``embed_and_persist`` 的结果汇总；调用方可写回 ai_runs 或日志。"""
    chunks_total: int = 0          # 收到的 chunk 数（input）
    chunks_indexed: int = 0        # 真正落库的 chunk 数
    batches_total: int = 0
    batches_failed: int = 0
    tokens_used: int = 0           # provider 返回的 prompt_tokens 累加
    failed_files: list[str] = field(default_factory=list)  # 失败批里涉及到的文件相对路径（去重前）


def embed_and_persist(
    session: Session,
    project_id: int,
    git_sha: str,
    chunks: Iterable[IndexChunk],
    *,
    cfg: Optional[EmbeddingConfig] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    replace_project_index: bool = True,
) -> EmbedderStats:
    """批量算 embedding 并写 ``code_chunks``。

    Args:
        session: SQLAlchemy session；调用方负责 commit / 回滚 / close
        project_id: 关联 ``projects.id``
        git_sha: 当前 HEAD commit；同 project 的旧索引按 sha 区分
        chunks: ``IndexChunk`` 生成器或列表
        cfg: 不传则从 ``config_store`` 加载
        batch_size: 单次 embedding HTTP 请求里塞多少条文本
        replace_project_index: True → 先把 ``project_id`` 现有 chunks 全删，再写新
                              一般首次索引 / 大 commit 切换走 True；小增量更新走 False

    Returns:
        ``EmbedderStats``；只要有一批成功，最终 commit 由调用方决定

    Notes:
        - 不在这里 commit —— Celery 任务里通常 1 个事务管 N 批，方便整体回滚
        - 整体不抛；只有 cfg 加载失败 / chunks 为空才会 early-return
    """
    if cfg is None:
        cfg = load_embedding_config(project_id)

    stats = EmbedderStats()
    buffered: list[IndexChunk] = list(chunks)
    stats.chunks_total = len(buffered)
    if not buffered:
        logger.info("embed_and_persist: 没有 chunk 可索引 project_id=%s", project_id)
        return stats

    if replace_project_index:
        deleted = (
            session.query(CodeChunk)
            .filter(CodeChunk.project_id == project_id)
            .delete(synchronize_session=False)
        )
        logger.info(
            "embed_and_persist: 清掉 project_id=%s 的旧索引 %d 行（replace_project_index=True）",
            project_id, deleted,
        )

    # 按 batch_size 切；每批一次 HTTP
    for batch_start in range(0, len(buffered), batch_size):
        batch = buffered[batch_start: batch_start + batch_size]
        stats.batches_total += 1
        texts = [c.content for c in batch]
        try:
            vectors, tokens = embed_texts(texts, cfg=cfg)
        except Exception as exc:  # noqa: BLE001 — provider 各种网络/格式异常都吞，单批失败不影响后续
            stats.batches_failed += 1
            stats.failed_files.extend(c.file_path for c in batch)
            logger.warning(
                "embed_and_persist: 第 %d 批 embedding 失败（%d 条），跳过 — %s",
                stats.batches_total, len(batch), exc,
            )
            continue

        if len(vectors) != len(batch):
            stats.batches_failed += 1
            stats.failed_files.extend(c.file_path for c in batch)
            logger.warning(
                "embed_and_persist: 第 %d 批返回向量数 (%d) 与请求 (%d) 不一致，跳过",
                stats.batches_total, len(vectors), len(batch),
            )
            continue

        stats.tokens_used += tokens

        # 组装 ORM 对象 → bulk insert
        rows = [
            CodeChunk(
                project_id=project_id,
                git_sha=git_sha,
                file_path=chunk.file_path,
                chunk_idx=chunk.chunk_idx,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content,
                embedding=vector,
            )
            for chunk, vector in zip(batch, vectors)
        ]
        try:
            session.bulk_save_objects(rows)
            session.flush()
        except Exception as exc:  # noqa: BLE001
            stats.batches_failed += 1
            stats.failed_files.extend(c.file_path for c in batch)
            logger.exception(
                "embed_and_persist: 第 %d 批落库失败（%d 行）— %s",
                stats.batches_total, len(rows), exc,
            )
            continue

        stats.chunks_indexed += len(rows)

    logger.info(
        "embed_and_persist done: project_id=%s sha=%s chunks=%d/%d batches=%d/%d tokens=%d",
        project_id, git_sha[:8],
        stats.chunks_indexed, stats.chunks_total,
        stats.batches_total - stats.batches_failed, stats.batches_total,
        stats.tokens_used,
    )
    return stats
