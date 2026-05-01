"""Ollama Provider — 支持本地模型。

  - REST API: POST http://localhost:11434/api/chat
  - 模型名如: qwen2.5:32b / deepseek-coder:6.7b / llama3:8b
  - JSON 模式通过 prompt 指令引导（Ollama 部分模型不支持原生 JSON mode）
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)


def call_ollama(
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int = 4096,
    timeout: int = 120,
) -> Tuple[str, int, int]:
    """调用 Ollama。

    Returns:
        (response_text, tokens_in, tokens_out)
    """
    if not base_url:
        raise ValueError("Ollama base_url 不能为空")
    if not model:
        raise ValueError("Ollama model 不能为空")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.3,
        },
    }

    start = time.time()
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"无法连接 Ollama ({base_url})。请确认 Ollama 已启动。"
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Ollama 调用超时 ({timeout}s)")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Ollama HTTP 错误: {e.response.status_code} — {e.response.text[:300]}")

    data = resp.json()

    # Ollama 返回格式: {"message": {"content": "..."}, "eval_count": ..., "prompt_eval_count": ...}
    content = data.get("message", {}).get("content", "")

    tokens_in = data.get("prompt_eval_count", 0)
    tokens_out = data.get("eval_count", 0)

    # 尝试提取 JSON（Ollama 可能输出 markdown code fence）
    cleaned = content.strip()
    if "```" in cleaned:
        start_idx = cleaned.find("```")
        end_idx = cleaned.rfind("```")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            inner = cleaned[start_idx: end_idx + 3]
            # 去掉 ``` 标记
            lines = inner.split("\n")
            json_lines = [l for l in lines if not l.startswith("```")]
            cleaned = "\n".join(json_lines).strip()
        else:
            cleaned = cleaned.replace("```", "").strip()

    elapsed = round(time.time() - start, 2)
    logger.info(
        "ollama call %s tokens_in=%d tokens_out=%d elapsed=%ss",
        model, tokens_in, tokens_out, elapsed,
    )

    return cleaned, tokens_in, tokens_out
