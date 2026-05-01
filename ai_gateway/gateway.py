"""LLM 网关主入口 — 支持分析模式、多模型路由。

职责：
  1. 从配置中心读 provider 配置
  2. 按分析模式选择模型和策略
  3. 加载并渲染 prompt 模板（支持项目上下文注入）
  4. 调对应 Provider，强制 JSON 输出
  5. 多模型模式下并行调用 + 结果聚合
  6. 解析 + 计算 token / 成本

分析模式（analysis_mode）：
  - quick       → 快速扫描：轻量模型 + 简化 prompt + Top-5 上下文
  - standard    → 标准分析（默认）：平衡模型 + 完整 prompt + Top-20 上下文
  - deep        → 深度分析：旗舰模型 + CoT prompt + Top-50 上下文
  - multi_model → 多模型集成：2-3 个模型并行 + 聚合

不做：
  - 持久化（由 tasks/ai_tasks.py 负责）
  - HTTP 路由（由 server/api/ai.py 负责）
"""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """LLM 调用本身失败（网络 / 认证 / 限流 / 输出解析）。"""


class NoProviderConfiguredError(ProviderError):
    """配置中心里没配 AI provider。"""


# ---------------------------------------------------------------------------
# 分析模式 → 模型策略映射
# ---------------------------------------------------------------------------
MODE_MODEL_MAP = {
    "quick": {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-haiku-20241022",
    },
    "standard": {
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20241022",
    },
    "deep": {
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20241022",
    },
}
MODE_TIMEOUT_MAP = {"quick": 30, "standard": 120, "deep": 300, "multi_model": 300}
MODE_MAX_TOKENS_MAP = {"quick": 4096, "standard": 16384, "deep": 32768, "multi_model": 16384}


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def _load_ai_config() -> dict:
    """加载 AI 配置。优先新格式（多 provider），回退旧格式。"""
    from utils.reload_config import config_center
    from database.db import DB

    db = DB()
    try:
        config_center.reload(db.sql, category=None)
    finally:
        db.close()

    # 优先：新格式多 provider，取第一个启用的
    providers = _get_provider_configs()
    if providers:
        for name, cfg in providers.items():
            if cfg.get("enabled") and cfg.get("model"):
                return {
                    "provider": name,
                    "api_key": cfg.get("api_key", ""),
                    "model": cfg.get("model", ""),
                    "base_url": cfg.get("base_url") or "",
                    "max_tokens": cfg.get("max_tokens", 4096),
                }

    # 回退：旧扁平格式 config_group=ai
    cfg = config_center.get("ai") or {}
    if cfg.get("provider") and cfg.get("model"):
        return {
            "provider": cfg.get("provider", ""),
            "api_key": cfg.get("api_key", ""),
            "model": cfg.get("model", ""),
            "base_url": cfg.get("base_url") or "",
            "max_tokens": int(cfg.get("max_tokens") or 4096),
        }

    raise NoProviderConfiguredError(
        "配置中心 'ai' 分组为空。请在前端配置中心 → AI Tab 添加至少一个模型。\n"
        "格式：config_group=模型名, config_key=model, config_value=模型标识, category=ai"
    )


def _get_provider_configs() -> dict:
    """从配置中心读多 provider 配置。

    新的直观配置格式：
      config_group=deepseek, config_key=model, config_value=deepseek-chat, category=ai
      config_group=deepseek, config_key=api_key, config_value=sk-..., category=ai
      config_group=deepseek, config_key=base_url, config_value=https://api.deepseek.com, category=ai
      config_group=ollama, config_key=model, config_value=qwen2.5:32b, category=ai
      config_group=ollama, config_key=base_url, config_value=http://localhost:11434, category=ai

    每个 config_group 自动成为一个 provider，config_key/config_value 为其属性。
    第三方模型额外加一条 config_key=enabled, config_value=true 来启用；没有 enabled 的
    config_group 只会被有 provider/model/api_key 的检测到并自动推断为 enabled。
    """
    from utils.reload_config import config_center
    from database.db import DB

    db = DB()
    try:
        rows = db.sql.query(
            "SELECT config_group, config_key, config_value FROM config_store "
            "WHERE category = 'ai' ORDER BY config_group, config_key"
        )
    finally:
        db.close()

    if not rows:
        return {}

    # 按 config_group 分组
    providers: dict = {}
    for row in rows:
        group = row["config_group"]
        key = row["config_key"]
        val = row["config_value"]

        if group in ("ai", "provider"):
            continue  # 跳过旧的扁平单 provider 配置

        if group not in providers:
            providers[group] = {}
        providers[group][key] = val

    # 补默认值 & 推断 enabled
    result = {}
    for name, cfg in providers.items():
        if not cfg.get("model"):
            continue  # 没有 model 的不算 provider

        result[name] = {
            "enabled": str(cfg.get("enabled", "true")).lower() != "false",
            "api_key": (cfg.get("api_key") or "").strip(),
            "model": (cfg.get("model") or "").strip(),
            "base_url": (cfg.get("base_url") or "").strip() or None,
            "max_tokens": int(cfg.get("max_tokens") or 0) or 4096,
        }

    # 如果没有新格式，回退旧的扁平单 provider
    if not result:
        cfg = config_center.get("ai") or {}
        provider = (cfg.get("provider") or "").strip().lower()
        if provider:
            result[provider] = {
                "enabled": True,
                "api_key": (cfg.get("api_key") or "").strip(),
                "base_url": (cfg.get("base_url") or "").strip() or None,
                "model": (cfg.get("model") or "").strip(),
                "max_tokens": int(cfg.get("max_tokens") or 4096),
            }

    return result


# ---------------------------------------------------------------------------
# Prompt 加载
# ---------------------------------------------------------------------------
PROMPTS_DIR = Path(__file__).parent / "prompts"
_PROMPT_VERSION = "v2"   # 改 prompt 时 +1


def _load_prompt(feature: str) -> str:
    """从 ai_gateway/prompts/<feature>.md 读 prompt 模板。"""
    f = PROMPTS_DIR / f"{feature}.md"
    if not f.exists():
        raise ProviderError(f"prompt 模板不存在：{f}")
    return f.read_text(encoding="utf-8")


def _render_prompt(template: str, user_input: dict) -> str:
    """{{KEY}} 占位符替换。"""
    out = template
    for k, v in (user_input or {}).items():
        placeholder = "{{" + str(k).upper() + "}}"
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, indent=2)
        out = out.replace(placeholder, str(v) if v is not None else "")
    return out


# ---------------------------------------------------------------------------
# 主入口（增强版）
# ---------------------------------------------------------------------------
def chat_json(
    feature: str,
    user_input: dict,
    project_id: Optional[int] = None,
    timeout: int = 120,
    analysis_mode: str = "standard",
    context_text: str = "",
) -> dict:
    """调一次 LLM，强制 JSON 输出。

    Args:
        feature: prompt 模板名（如 requirement_parse）
        user_input: 渲染 prompt 的变量字典
        project_id: 项目 ID（仅记录，不影响调用）
        timeout: 超时秒数
        analysis_mode: 分析模式（quick / standard / deep / multi_model）
        context_text: 注入的项目上下文字符串（来自 context_service）

    Returns:
        {output, provider, model, tokens_in, tokens_out, cost_usd,
         prompt_hash, prompt_version, analysis_mode}
    """
    if analysis_mode == "multi_model":
        return _chat_json_multi(feature, user_input, project_id, timeout, context_text)

    cfg = _load_ai_config()
    provider_name = (cfg.get("provider") or "").strip().lower()
    model = (cfg.get("model") or "").strip() or _default_model(provider_name, analysis_mode)
    api_key = (cfg.get("api_key") or "").strip()
    base_url = (cfg.get("base_url") or "").strip() or None
    max_tokens = int(cfg.get("max_tokens") or MODE_MAX_TOKENS_MAP.get(analysis_mode, 16384))
    timeout = timeout or MODE_TIMEOUT_MAP.get(analysis_mode, 120)

    template = _load_prompt(feature)
    prompt = _render_prompt(template, user_input)

    # 注入项目上下文（如果有）
    if context_text:
        prompt = _inject_context(prompt, context_text)

    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    # 路由
    raw, tokens_in, tokens_out = _call_provider(
        provider_name, api_key, model, prompt, base_url, max_tokens, timeout
    )

    output = _parse_json_output(raw)
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
        "analysis_mode": analysis_mode,
    }


def _chat_json_multi(
    feature: str,
    user_input: dict,
    project_id: Optional[int],
    timeout: int,
    context_text: str,
) -> dict:
    """多模型集成模式：并行调用 2-3 个模型，聚合结果。"""
    providers_cfg = _get_provider_configs()
    enabled = {
        name: cfg for name, cfg in providers_cfg.items()
        if cfg.get("enabled") and cfg.get("model")
        and (cfg.get("api_key") or name == "ollama")
    }

    if len(enabled) < 2:
        # 不够 2 个启用模型 → 降级为深度单模型
        logger.warning("multi_model 模式下可用 provider < 2，降级为 deep 单模型")
        return chat_json(
            feature, user_input, project_id, timeout, analysis_mode="deep", context_text=context_text
        )

    # 取最多 3 个
    selected = dict(list(enabled.items())[:3])

    template = _load_prompt(feature)
    prompt = _render_prompt(template, user_input)
    if context_text:
        prompt = _inject_context(prompt, context_text)

    timeout = timeout or MODE_TIMEOUT_MAP["multi_model"]

    results: List[Dict[str, Any]] = []
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0

    def _call_one(name: str, cfg: dict) -> Optional[dict]:
        try:
            raw, ti, to = _call_provider(
                name, cfg.get("api_key", ""), cfg["model"], prompt,
                cfg.get("base_url"), cfg.get("max_tokens", 16384), timeout
            )
            output = _parse_json_output(raw)
            cost = _estimate_cost(name, cfg["model"], ti, to)
            return {"name": name, "model": cfg["model"], "output": output, "ti": ti, "to": to, "cost": cost}
        except Exception as e:
            logger.warning("multi_model: provider %s 调用失败: %s", name, e)
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_call_one, name, cfg): name for name, cfg in selected.items()}
        for f in as_completed(futures):
            r = f.result()
            if r:
                results.append(r)

    if not results:
        raise ProviderError("multi_model: 所有 provider 均调用失败")

    # 聚合
    aggregated = _aggregate_outputs([r["output"] for r in results], feature)
    total_tokens_in = sum(r["ti"] or 0 for r in results)
    total_tokens_out = sum(r["to"] or 0 for r in results)
    total_cost = round(sum(r["cost"] or 0 for r in results), 6)

    providers_used = [r["name"] for r in results]
    models_used = [r["model"] for r in results]

    return {
        "output": aggregated,
        "provider": "+".join(providers_used),
        "model": "+".join(models_used),
        "tokens_in": total_tokens_in,
        "tokens_out": total_tokens_out,
        "cost_usd": total_cost,
        "prompt_hash": prompt_hash,
        "prompt_version": _PROMPT_VERSION,
        "analysis_mode": "multi_model",
    }


# ---------------------------------------------------------------------------
# Provider 调用
# ---------------------------------------------------------------------------
def _call_provider(
    provider_name: str, api_key: str, model: str, prompt: str,
    base_url: Optional[str], max_tokens: int, timeout: int,
) -> tuple:
    if provider_name in ("openai", "deepseek", "azure"):
        from .providers.openai_provider import call_openai
        return call_openai(api_key, model, prompt, base_url, max_tokens, timeout)
    elif provider_name == "anthropic":
        from .providers.anthropic_provider import call_anthropic
        return call_anthropic(api_key, model, prompt, base_url, max_tokens, timeout)
    elif provider_name == "ollama":
        from .providers.ollama_provider import call_ollama
        return call_ollama(base_url or "http://localhost:11434", model, prompt, max_tokens, timeout)
    else:
        raise ProviderError(f"不支持的 provider: {provider_name!r}（合法：openai / deepseek / anthropic / ollama）")


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------
def _parse_json_output(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        cleaned = _strip_code_fence(raw)
        try:
            return json.loads(cleaned)
        except Exception as exc:
            raise ProviderError(
                f"LLM 输出不是合法 JSON：{exc}\n输出前 500 字：\n{raw[:500]}"
            )


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl > 0:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


# ---------------------------------------------------------------------------
# 上下文注入
# ---------------------------------------------------------------------------
def _inject_context(prompt: str, context_text: str) -> str:
    """将项目上下文注入 prompt 中。"""
    separator = "\n\n---\n## 项目上下文（历史模块与知识库）\n\n"
    return prompt + separator + context_text + "\n---\n"


# ---------------------------------------------------------------------------
# 多模型聚合
# ---------------------------------------------------------------------------
def _aggregate_outputs(outputs: List[dict], feature: str) -> dict:
    """聚合多个模型的输出。策略：投票 + 最佳选择混合。"""
    if len(outputs) == 1:
        return outputs[0]

    # 对于 requirement_parse：提取 requirements 数组，按 title 去重合并
    if feature == "requirement_parse":
        return _aggregate_requirements(outputs)

    # 对其他 feature：简单取最完整的那个结果
    return max(outputs, key=lambda o: len(json.dumps(o, ensure_ascii=False)))


def _aggregate_requirements(outputs: List[dict]) -> dict:
    """需求解析的聚合：多模型提取的需求，按 title 去重合并。"""
    all_reqs: Dict[str, dict] = {}
    summaries: List[str] = []

    for out in outputs:
        summaries.append(out.get("summary", ""))
        for req in out.get("requirements") or []:
            title = (req.get("title") or "").strip()
            if not title:
                continue
            normalized = title.lower().rstrip("。,.;；")[:50]
            if normalized not in all_reqs:
                all_reqs[normalized] = {
                    "title": title,
                    "description": req.get("description", ""),
                    "acceptance_criteria": req.get("acceptance_criteria") or [],
                    "priority": req.get("priority") or 2,
                    "tags": req.get("tags") or [],
                    "depends_on": req.get("depends_on") or [],
                    "_model_count": 1,       # 内部字段：多少模型一致
                    "_merge_source": [req.get("description", "")],
                }
            else:
                existing = all_reqs[normalized]
                existing["_model_count"] += 1
                existing["_merge_source"].append(req.get("description", ""))
                # 合并 acceptance_criteria（去重）
                for ac in (req.get("acceptance_criteria") or []):
                    if ac not in existing["acceptance_criteria"]:
                        existing["acceptance_criteria"].append(ac)
                # 合并 tags
                for tag in (req.get("tags") or []):
                    if tag not in existing["tags"]:
                        existing["tags"].append(tag)

    # 清理内部字段
    requirements = []
    high_confidence = 0
    for item in all_reqs.values():
        mc = item.pop("_model_count", 1)
        item.pop("_merge_source", [])
        item["_confidence"] = "high" if mc >= 2 else "medium"
        if mc >= 2:
            high_confidence += 1
        requirements.append(item)

    # 选出最完整的 summary
    best_summary = max(summaries, key=len) if summaries else ""

    return {
        "requirements": requirements,
        "summary": best_summary,
        "_aggregation": {
            "total_outputs": len(outputs),
            "total_requirements": len(requirements),
            "high_confidence": high_confidence,
            "method": "voting",
        },
    }


# ---------------------------------------------------------------------------
# 成本估算
# ---------------------------------------------------------------------------
_PRICING = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "claude-3-5-sonnet-20241022": (0.003, 0.015),
    "claude-3-5-haiku-20241022": (0.001, 0.005),
    "claude-3-opus-20240229": (0.015, 0.075),
}


def _estimate_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    if not tokens_in and not tokens_out:
        return 0.0
    in_p, out_p = _PRICING.get(model, (0.0, 0.0))
    return round((tokens_in or 0) / 1000 * in_p + (tokens_out or 0) / 1000 * out_p, 6)


def _default_model(provider: str, analysis_mode: str) -> str:
    m = MODE_MODEL_MAP.get(analysis_mode, {}).get(provider, "")
    if m:
        return m
    if provider == "openai":
        return "gpt-4o-mini"
    if provider == "anthropic":
        return "claude-3-5-sonnet-20241022"
    return ""
