"""OpenAI 兼容 provider —— 走 /v1/chat/completions（OpenAI 官方 / Azure / 各种反代都兼容）。

为什么不用 openai 官方 SDK？
  - SDK 版本变化大（v0 / v1 大改），跨服务器部署兼容性差；
  - 反代 / 自建 endpoint 通常只支持 REST，不支持 SDK 特殊鉴权；
  - 我们只用 chat.completions，REST + requests 一百行写完，零额外依赖。
"""
from __future__ import annotations

import json
from typing import Optional

import requests

from ..gateway import ProviderError


def call_openai(
    api_key: str,
    model: str,
    prompt: str,
    base_url: Optional[str] = None,
    max_tokens: int = 4096,
    timeout: int = 60,
) -> tuple[str, int, int]:
    """调一次 OpenAI chat.completions，返回 (raw_text, tokens_in, tokens_out)。

    强制 JSON mode（response_format={"type": "json_object"}）—— 让模型只输出 JSON，
    避免它瞎写 markdown 包装。
    """
    if not api_key:
        raise ProviderError("openai api_key 未配置")

    url = (base_url or "https://api.openai.com").rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a structured JSON generator. "
                    "Always respond with a single valid JSON object. "
                    "Do not include explanation, markdown, or code fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"openai 网络错误：{exc}") from exc

    if resp.status_code != 200:
        # 直接把 OpenAI 的错误信息透出来，方便排查
        raise ProviderError(
            f"openai HTTP {resp.status_code}: {resp.text[:500]}"
        )

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise ProviderError(f"openai 响应不是 JSON：{resp.text[:500]}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"openai 响应格式异常：{json.dumps(data)[:500]}") from exc

    return content, tokens_in, tokens_out


def embed_openai(
    api_key: str,
    model: str,
    texts: list[str],
    base_url: Optional[str] = None,
    timeout: int = 60,
) -> tuple[list[list[float]], int]:
    """调一次 OpenAI /v1/embeddings，返回 (vectors, tokens_used)。

    - ``texts`` 一次最多丢几十条；调用方需要按 token 上限自行分批
    - 走 ``/v1/embeddings`` 端点，跟 chat.completions 同 base_url 前缀
    - DeepSeek 现在没 embedding 路由，但 Azure / 自建反代 / 通义 / Together 都兼容这条
    """
    if not api_key:
        raise ProviderError("openai-embeddings: api_key 未配置")
    if not texts:
        return [], 0

    url = (base_url or "https://api.openai.com").rstrip("/") + "/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {"model": model, "input": texts}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"openai-embeddings 网络错误：{exc}") from exc

    if resp.status_code != 200:
        raise ProviderError(
            f"openai-embeddings HTTP {resp.status_code}: {resp.text[:500]}"
        )

    try:
        data = resp.json()
        # /v1/embeddings 返回顺序与 input 对齐，但保险起见按 index 重排
        items = sorted(data["data"], key=lambda x: x.get("index", 0))
        vectors = [item["embedding"] for item in items]
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or usage.get("prompt_tokens") or 0)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderError(
            f"openai-embeddings 响应格式异常：{resp.text[:500]}"
        ) from exc

    return vectors, tokens
