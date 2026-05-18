"""Embedding 入口 —— RAG 索引 / 检索阶段调它把文本变向量。

设计：
- 沿用 chat_json 的 config_store 套路，但走独立 group ``rag_embedding``：
    config_group=rag_embedding, config_key=provider,  config_value=openai|ollama
    config_group=rag_embedding, config_key=model,     config_value=text-embedding-3-small
    config_group=rag_embedding, config_key=api_key,   config_value=sk-...
    config_group=rag_embedding, config_key=base_url,  config_value=https://api.openai.com (可选)
    config_group=rag_embedding, config_key=dim,       config_value=1536
- 不走 ai 分组的 chat 模型，避免误把 deepseek-chat 当 embedding 用
- Anthropic **没有** embedding API；选了 anthropic 会抛 NotImplementedError，
  指引用户用 OpenAI-compatible 或本地 Ollama (nomic-embed-text)
- 调用方负责 chunk 批次切分；这里只做"一次 HTTP，一组向量"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .gateway import NoProviderConfiguredError, ProviderError


DEFAULT_EMBEDDING_DIM = 1536


@dataclass(frozen=True)
class EmbeddingConfig:
    """RAG 用的 embedding provider 配置（来自 config_store 的 ``rag_embedding`` 组）。"""
    provider: str            # openai | ollama | deepseek | azure | custom
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    dim: int = DEFAULT_EMBEDDING_DIM


def load_embedding_config() -> EmbeddingConfig:
    """从 config_store 加载 RAG embedding 配置；缺失抛 ``NoProviderConfiguredError``。"""
    from database.db import DB

    db = DB()
    try:
        rows = db.sql.query(
            "SELECT config_key, config_value FROM config_store "
            "WHERE category = 'ai' AND config_group = 'rag_embedding'"
        )
    finally:
        db.close()

    if not rows:
        raise NoProviderConfiguredError(
            "config_store 缺 RAG embedding 配置（category=ai, config_group=rag_embedding）。"
            "至少需要 provider + model；OpenAI 兼容还要 api_key。"
        )

    cfg = {r["config_key"]: r["config_value"] for r in rows}
    provider = (cfg.get("provider") or "").strip().lower()
    model = (cfg.get("model") or "").strip()
    if not provider or not model:
        raise NoProviderConfiguredError(
            f"rag_embedding 配置不完整：provider={provider!r}, model={model!r}"
        )
    try:
        dim = int(cfg.get("dim") or DEFAULT_EMBEDDING_DIM)
    except (TypeError, ValueError):
        dim = DEFAULT_EMBEDDING_DIM

    return EmbeddingConfig(
        provider=provider,
        model=model,
        api_key=(cfg.get("api_key") or None),
        base_url=(cfg.get("base_url") or None),
        dim=dim,
    )


def embed_texts(
    texts: list[str],
    cfg: Optional[EmbeddingConfig] = None,
    timeout: int = 60,
) -> tuple[list[list[float]], int]:
    """批量算 embedding。返回 ``(vectors, tokens_used)``。

    - ``texts`` 顺序对应返回 ``vectors`` 顺序
    - 调用方按 token 上限自行分批；本函数只走一次 HTTP
    - Anthropic 抛 ``NotImplementedError``（officially 不提供 embedding API）
    """
    if not texts:
        return [], 0
    if cfg is None:
        cfg = load_embedding_config()

    provider = cfg.provider.lower()
    if provider in ("openai", "deepseek", "azure", "custom"):
        from .providers.openai_provider import embed_openai
        return embed_openai(
            api_key=cfg.api_key or "",
            model=cfg.model,
            texts=texts,
            base_url=cfg.base_url,
            timeout=timeout,
        )
    if provider == "ollama":
        from .providers.ollama_provider import embed_ollama
        return embed_ollama(
            base_url=cfg.base_url or "http://localhost:11434",
            model=cfg.model,
            texts=texts,
            timeout=timeout,
        )
    if provider == "anthropic":
        raise NotImplementedError(
            "Anthropic 暂无公开 embedding API；请改用 OpenAI / Ollama / 自建反代"
        )
    raise ProviderError(f"未知 embedding provider: {provider!r}")
