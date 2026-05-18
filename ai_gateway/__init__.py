"""AI Gateway —— 统一 LLM Provider 抽象。

核心入口：

    from ai_gateway import chat_json, ProviderError

    result = chat_json(
        feature="requirement_parse",     # 决定用哪个 prompt 模板
        user_input={"text": "..."},
        project_id=42,
        analysis_mode="standard",       # quick / standard / deep / multi_model
        context_text="...",             # 由 context_service 生成的项目上下文
    )
    # result = {
    #   "output": <已 JSON.parse 的 LLM 输出>,
    #   "provider": "openai",
    #   "model": "gpt-4o",
    #   "tokens_in": 1234, "tokens_out": 567,
    #   "cost_usd": 0.0042,
    #   "prompt_hash": "...",
    #   "prompt_version": "v2",
    #   "analysis_mode": "standard",
    # }

分析模式（analysis_mode）：
  - quick       → 轻量模型 + 简化 prompt
  - standard    → 平衡模型（默认）
  - deep        → 旗舰模型 + 大 token
  - multi_model → 2-3 个模型并行 + 投票聚合

Provider 配置走配置中心 category="ai"：
    provider          openai | anthropic | ollama
    api_key           xxx
    model             gpt-4o / claude-3-5-sonnet / ...
    base_url          自定义 endpoint（ollama / 自建反代）
    max_tokens        4096
    providers_config  JSON: 多 provider 配置（multi_model 使用）
"""
from .gateway import chat_json, ProviderError, NoProviderConfiguredError
from .embeddings import embed_texts, load_embedding_config, EmbeddingConfig

__all__ = [
    "chat_json",
    "ProviderError",
    "NoProviderConfiguredError",
    "embed_texts",
    "load_embedding_config",
    "EmbeddingConfig",
]
