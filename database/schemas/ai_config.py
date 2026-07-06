"""AiModelConfig Pydantic schema —— 配置中心存的 AI 模型连接参数。

不新建表，序列化为 JSON 落进 config_store 的
  (config_group="ai_models", config_key=<name>, config_value=<json>) 行。

`provider` 决定调用哪个 gateway 实现；`supports_vision=True` 时分析含图片附件
的需求会直接走 base64 多模态调用。
"""
from typing import Any

from pydantic import BaseModel, Field


PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OLLAMA = "ollama"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_ZAI = "zai"
PROVIDER_AZURE = "azure"
PROVIDER_CUSTOM = "custom"
ALL_AI_PROVIDERS = {
    PROVIDER_OPENAI,
    PROVIDER_ANTHROPIC,
    PROVIDER_OLLAMA,
    PROVIDER_DEEPSEEK,
    PROVIDER_ZAI,
    PROVIDER_AZURE,
    PROVIDER_CUSTOM,
}


class AiModelConfig(BaseModel):
    name: str = Field(..., description="用户起的别名，唯一")
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    supports_vision: bool = False
    is_default: bool = False
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class AiModelConfigUpsert(BaseModel):
    """upsert 入参；不接收 name（name 走 URL 路径）。"""

    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    supports_vision: bool = False
    is_default: bool = False
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class AiModelTestResult(BaseModel):
    ok: bool
    latency_ms: int
    sample: str
    error: str | None = None
