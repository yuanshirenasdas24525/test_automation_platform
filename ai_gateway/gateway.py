"""LLM 网关主入口。

职责：
  1. 从配置中心读 provider 配置
  2. 加载 prompt 模板（system + few-shot examples + user）
  3. 调对应 Provider，强制 JSON 输出
  4. 解析 + JSON Schema 校验
  5. 计算 token / 成本，返回结构化结果

不做：
  - 持久化 ai_run 记录（由 tasks/ai_tasks.py 负责）
  - HTTP 路由（由 server/api/ai.py 负责）
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """LLM 调用本身失败（网络 / 认证 / 限流 / 输出解析）。"""


class NoProviderConfiguredError(ProviderError):
    """配置中心里没配 AI provider。"""


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
def _load_ai_config() -> dict:
    """从配置中心读 ai 分组；没配抛 NoProviderConfiguredError。"""
    from utils.reload_config import config_center

    cfg = config_center.get("ai") or {}

    # 缓存里没有就尝试主动 reload 一次
    if not cfg:
        try:
            from database.db import DB

            db = DB()
            try:
                config_center.reload(db.sql, category=None)
            finally:
                db.close()
            cfg = config_center.get("ai") or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai 配置 reload 失败：%s", exc)

    if not cfg:
        raise NoProviderConfiguredError(
            "配置中心 'ai' 分组为空。请在 /config 页面配置 provider / api_key / model。"
        )

    provider = (cfg.get("provider") or "").strip().lower()
    if not provider:
        raise NoProviderConfiguredError(
            "ai.provider 未配置（合法值：openai / anthropic / azure / ollama）"
        )
    return cfg


# ---------------------------------------------------------------------------
# Prompt 加载
# ---------------------------------------------------------------------------
PROMPTS_DIR = Path(__file__).parent / "prompts"
_PROMPT_VERSION = "v1"   # 改 prompt 时 +1，便于 ai_runs 区分


def _load_prompt(feature: str) -> str:
    """从 ai_gateway/prompts/<feature>.md 读 prompt 模板。

    模板里支持 {{KEY}} 占位符，会被 user_input 字典里的值替换。
    """
    f = PROMPTS_DIR / f"{feature}.md"
    if not f.exists():
        raise ProviderError(f"prompt 模板不存在：{f}")
    return f.read_text(encoding="utf-8")


def _render_prompt(template: str, user_input: dict) -> str:
    """简单的 {{KEY}} 替换；user_input 没传的 key 留空。"""
    out = template
    for k, v in (user_input or {}).items():
        placeholder = "{{" + str(k).upper() + "}}"
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, indent=2)
        out = out.replace(placeholder, str(v))
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def chat_json(
    feature: str,
    user_input: dict,
    project_id: Optional[int] = None,
    timeout: int = 60,
) -> dict:
    """调一次 LLM，强制 JSON 输出。

    返回：
        {
            "output": <已 JSON.parse 的 LLM 输出>,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "tokens_in": 1234,
            "tokens_out": 567,
            "cost_usd": 0.0042,
            "prompt_hash": "...",
            "prompt_version": "v1",
        }
    """
    cfg = _load_ai_config()
    provider_name = (cfg.get("provider") or "").strip().lower()
    model = (cfg.get("model") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    base_url = (cfg.get("base_url") or "").strip() or None
    max_tokens = int(cfg.get("max_tokens") or 4096)

    template = _load_prompt(feature)
    prompt = _render_prompt(template, user_input)

    # prompt_hash 用于将来缓存命中
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    # 路由到具体 provider
    if provider_name == "openai":
        from .providers.openai_provider import call_openai
        raw, tokens_in, tokens_out = call_openai(
            api_key=api_key,
            model=model or "gpt-4o-mini",
            prompt=prompt,
            base_url=base_url,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    elif provider_name == "anthropic":
        from .providers.anthropic_provider import call_anthropic
        raw, tokens_in, tokens_out = call_anthropic(
            api_key=api_key,
            model=model or "claude-3-5-sonnet-20241022",
            prompt=prompt,
            base_url=base_url,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    else:
        raise ProviderError(
            f"不支持的 provider: {provider_name!r}（合法：openai / anthropic）"
        )

    # 强制 JSON 解析
    try:
        output = json.loads(raw)
    except Exception as exc:
        # 有时 LLM 会把 JSON 包在 ```json ... ``` 里
        cleaned = _strip_code_fence(raw)
        try:
            output = json.loads(cleaned)
        except Exception:
            raise ProviderError(
                f"LLM 输出不是合法 JSON：{exc}\n输出前 500 字：\n{raw[:500]}"
            )

    # 简单成本估算（按 token 单价）—— 后面可以换成更精确的 model 价格表
    cost = _estimate_cost(provider_name, model, tokens_in, tokens_out)

    return {
        "output": output,
        "provider": provider_name,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "prompt_hash": prompt_hash,
        "prompt_version": _PROMPT_VERSION,
    }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _strip_code_fence(s: str) -> str:
    """去掉 ```json ... ``` 这种 markdown 代码块包装。"""
    s = s.strip()
    if s.startswith("```"):
        # ``` 或 ```json
        nl = s.find("\n")
        if nl > 0:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


# 简化版价格表（USD per 1K tokens, input / output）。新 model 后续往里加。
_PRICING = {
    # OpenAI
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    # Anthropic
    "claude-3-5-sonnet-20241022": (0.003, 0.015),
    "claude-3-5-haiku-20241022": (0.001, 0.005),
    "claude-3-opus-20240229": (0.015, 0.075),
}


def _estimate_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    """USD 成本估算。模型不在表里返 0（不阻断流程）。"""
    if not tokens_in and not tokens_out:
        return 0.0
    in_p, out_p = _PRICING.get(model, (0.0, 0.0))
    return round((tokens_in or 0) / 1000 * in_p + (tokens_out or 0) / 1000 * out_p, 6)
