"""轻量 BM25 关键词检索 —— 与 embedding 检索混合，提升源码文件名/关键词命中率。

设计：
- 纯 Python 实现，零外部依赖
- tokenizer：英文按空格/标点分词 + CJK 字符 2-gram
- 索引在内存构建（从 code_chunks 表读取，复用已有数据）
- 单项目索引量 < 10K chunk，秒级查询
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# 常见编程关键词权重提升（API 名称、框架术语等）
_CODE_KEYWORD_BOOST: dict[str, float] = {
    "task": 2.0,
    "bug": 2.0,
    "detail": 2.0,
    "page": 2.0,
    "component": 2.0,
    "render": 2.0,
    "html": 2.0,
    "description": 2.0,
    "innerhtml": 2.0,
    "dangerously": 2.0,
    "setinnerhtml": 2.0,
    "dangerouslysetinnerhtml": 3.0,
    "api": 1.5,
    "route": 1.5,
    "router": 1.5,
    "button": 1.5,
    "modal": 1.5,
    "dialog": 1.5,
    "form": 1.5,
    "table": 1.5,
    "list": 1.5,
    "query": 1.5,
    "mutation": 1.5,
    "fetch": 1.5,
    "axios": 1.5,
    "request": 1.5,
    "response": 1.5,
    "error": 1.5,
    "exception": 1.5,
    "config": 1.5,
    "service": 1.5,
    "model": 1.5,
    "schema": 1.5,
    "migration": 1.5,
    "celery": 1.5,
    "redis": 1.5,
    "docker": 1.5,
    "git": 1.5,
    "pg": 1.5,
    "sql": 1.5,
}


def tokenize(text: str) -> list[str]:
    """中英文混合分词。"""
    tokens: list[str] = []

    # 英文/数字词
    for match in _WORD_RE.finditer(text):
        token = match.group().lower()
        if token:
            tokens.append(token)

    # CJK 字符 2-gram
    cjk_chars: list[str] = []
    for match in _CJK_RE.finditer(text):
        cjk_chars.append(match.group())
    for i in range(len(cjk_chars) - 1):
        tokens.append(cjk_chars[i] + cjk_chars[i + 1])
    for c in cjk_chars:
        tokens.append(c)

    return tokens


# ---------------------------------------------------------------------------
# BM25 实现
# ---------------------------------------------------------------------------
# 经典 Okapi BM25 参数
K1 = 1.5
B = 0.75


@dataclass(frozen=True)
class Bm25Hit:
    chunk_id: int
    score: float


class Bm25Index:
    """BM25 检索索引 —— 为每个 project 独立构建。"""

    def __init__(self, chunks: Iterable[tuple[int, str]]):
        """chunks: [(chunk_id, content), ...]"""
        self._chunks: list[tuple[int, str]] = []
        self._doc_len: list[int] = []       # 每篇文档的 token 数
        self._avgdl: float = 0.0
        self._df: dict[str, int] = defaultdict(int)  # document frequency
        self._idf: dict[str, float] = {}

        docs_tokens: list[list[str]] = []
        total_len = 0

        for cid, content in chunks:
            tokens = tokenize(content)
            docs_tokens.append(tokens)
            self._chunks.append((cid, content))
            self._doc_len.append(len(tokens))
            total_len += len(tokens)
            unique = set(tokens)
            for t in unique:
                self._df[t] += 1

        n = len(docs_tokens)
        self._avgdl = total_len / n if n > 0 else 1.0

        for term, df in self._df.items():
            self._idf[term] = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

        # 为 BM25 计算预存 doc_tokens
        self._docs_tokens = docs_tokens

    def search(self, query: str, top_k: int = 30) -> list[Bm25Hit]:
        """检索 top_k 篇最相关文档。"""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores: list[float] = [0.0] * len(self._chunks)
        for term in query_tokens:
            idf = self._idf.get(term, 0.0)
            if idf == 0.0:
                continue
            boost = _CODE_KEYWORD_BOOST.get(term, 1.0)
            for i, doc_tokens in enumerate(self._docs_tokens):
                tf = doc_tokens.count(term)
                if tf == 0:
                    continue
                dl = self._doc_len[i]
                numerator = tf * (K1 + 1)
                denominator = tf + K1 * (1 - B + B * dl / self._avgdl)
                scores[i] += idf * numerator / denominator * boost

        ranked = sorted(
            [(s, self._chunks[i][0]) for i, s in enumerate(scores) if s > 0],
            key=lambda x: -x[0],
        )[:top_k]
        return [Bm25Hit(chunk_id=cid, score=s) for s, cid in ranked]

    def __len__(self) -> int:
        return len(self._chunks)
