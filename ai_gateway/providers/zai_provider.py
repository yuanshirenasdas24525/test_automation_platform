"""Z.AI / 智谱 provider —— 走 `/api/paas/v4/chat/completions` REST。"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import requests

from ..gateway import ProviderError

DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"


def build_zai_chat_url(base_url: Optional[str] = None) -> str:
    """构造 Z.AI chat completions URL，允许用户填到 v4 根路径或完整接口路径。"""
    raw = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if raw.endswith("/chat/completions"):
        return raw
    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    if not path:
        path = "/api/paas/v4"
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/chat/completions", parts.query, parts.fragment))


def call_zai(
    api_key: str,
    model: str,
    prompt: str,
    base_url: Optional[str] = None,
    max_tokens: int = 4096,
    timeout: int = 60,
    json_mode: bool = True,
    system_prompt: str | None = None,
    enable_thinking: bool = True,
    temperature: float = 0.3,
) -> tuple[str, int, int]:
    """调用 Z.AI chat.completions，返回 (raw_text, tokens_in, tokens_out)。

    enable_thinking: GLM-4.5+/GLM-5.x 是带思维链的推理模型，默认会先"思考"再回答，
      慢但质量高。传 False 时下发 thinking={"type":"disabled"} 关闭思考，用于连通性
      测试、结构化短输出等不需要推理的场景，能把单次耗时从十几秒降到 1~2 秒。
      （只在关闭时下发该参数，避免不支持 thinking 的旧模型报错。）"""
    if not api_key:
        raise ProviderError("zai api_key 未配置")

    url = build_zai_chat_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, object] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt or (
                    "You are a structured JSON generator. "
                    "Always respond with a single valid JSON object. "
                    "Do not include explanation, markdown, or code fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if not enable_thinking:
        body["thinking"] = {"type": "disabled"}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"zai 网络错误：{exc}") from exc

    if resp.status_code != 200:
        raise ProviderError(f"zai HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise ProviderError(f"zai 响应不是 JSON：{resp.text[:500]}") from exc

    try:
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"zai 响应格式异常：{json.dumps(data)[:500]}") from exc

    if not content.strip():
        # GLM 思考模式下偶发只返回 reasoning_content（或思考耗尽 max_tokens 被截断），
        # final content 为空。静默返回 "" 会让下游解析失败被误报成"AI 觉得没问题"，
        # 这里显式抛错并给出可操作的提示。
        finish = str(choice.get("finish_reason") or "")
        has_reasoning = bool(str(message.get("reasoning_content") or "").strip())
        raise ProviderError(
            "zai 返回空 content"
            + (f"（finish_reason={finish}）" if finish else "")
            + ("；模型只输出了思考过程" if has_reasoning else "")
            + "。建议：对该任务关闭思考（enable_thinking=false）或调大 max_tokens 后重试。"
        )

    return content, tokens_in, tokens_out
