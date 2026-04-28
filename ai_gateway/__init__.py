"""AI Gateway —— 统一 LLM Provider 抽象。

核心入口：

    from ai_gateway import chat_json, ProviderError

    result = chat_json(
        feature="requirement_parse",     # 决定用哪个 prompt 模板
        user_input={"text": "..."},
        project_id=42,
    )
    # result = {
    #   "output": <已 JSON.parse 的 LLM 输出>,
    #   "provider": "openai",
    #   "model": "gpt-4o-mini",
    #   "tokens_in": 1234, "tokens_out": 567,
    #   "cost_usd": 0.0042,
    #   "prompt_hash": "...",
    #   "prompt_version": "v1",
    # }

Provider 配置走配置中心 category="ai"：
    provider          openai | anthropic | azure | ollama
    api_key           xxx
    model             gpt-4o-mini / claude-3-5-sonnet / ...
    base_url          自定义 endpoint（azure / 自建反代用）
    max_tokens        4096
"""
from .gateway import chat_json, ProviderError, NoProviderConfiguredError

__all__ = ["chat_json", "ProviderError", "NoProviderConfiguredError"]
