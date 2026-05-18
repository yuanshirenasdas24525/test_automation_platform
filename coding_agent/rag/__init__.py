"""RAG 索引 + 检索子模块。

三个文件各司其职：
- ``indexer``  ：扫 working tree → 分块 → 产 (file, idx, content) 元数据
- ``embedder`` ：批量算 embedding，写入 ``code_chunks``
- ``retriever``：给定 query 文本，返 top-k 相关 chunk

不直接 import sqlalchemy session —— 调用方传入 session，便于 worker / 测试解耦。
"""
from .indexer import IndexChunk, scan_workspace, chunk_file
from .embedder import embed_and_persist, EmbedderStats
from .retriever import retrieve_relevant, RetrievedChunk

__all__ = [
    "IndexChunk",
    "scan_workspace",
    "chunk_file",
    "embed_and_persist",
    "EmbedderStats",
    "retrieve_relevant",
    "RetrievedChunk",
]
