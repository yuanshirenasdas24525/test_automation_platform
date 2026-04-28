"""Anthropic Claude provider —— 走 /v1/messages REST。

跟 openai_provider 一样，不依赖 anthropic 官方 SDK，纯 requests 实现。
"""
from __future__ import annotations

import json
from typing import Optional

import requests

from ..gateway import ProviderError


def call_anthropic(
    api_key: str,
    model: str,
    prompt: str,
    base_url: Optional[str] = None,
    max_tokens: int = 4096,
    timeout: int = 60,
) -> tuple[str, int, int]:
    """调一次 Anthropic messages，返回 (raw_text, tokens_in, tokens_out)。

    Anthropic 不支持 OpenAI 那种 response_format=json_object，但可以通过
    system prompt 强约束 + assistant prefix 引导 JSON 起始。
    """
    if not api_key:
        raise ProviderError("anthropic api_key 未配置")

    url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": (
            "You are a structured JSON generator. "
            "Always respond with a single valid JSON object only. "
            "Do not include explanation, markdown, or code fences."
        ),
        "messages": [
            {"role": "user", "content": prompt},
            # 用 assistant 起始 "{" 引导模型直接输出 JSON 对象
            {"role": "assistant", "content": "{"},
        ],
        "temperature": 0.3,
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"anthropic 网络错误：{exc}") from exc

    if resp.status_code != 200:
        raise ProviderError(
            f"anthropic HTTP {resp.status_code}: {resp.text[:500]}"
        )

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise ProviderError(f"anthropic 响应不是 JSON：{resp.text[:500]}") from exc

    try:
        # content 是 list，取第一个 text block
        text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        # 因为 assistant prefix 是 "{"，这里要补回去
        content = "{" + "".join(text_blocks)
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
    except Exception as exc:
        raise ProviderError(
            f"anthropic 响应格式异常：{json.dumps(data)[:500]}"
        ) from exc

    return content, tokens_in, tokens_out
