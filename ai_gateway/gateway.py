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
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TASK_DEFAULT_OPTIONS: dict[str, dict[str, Any]] = {
    # 大纲/查漏补缺是“结构化规划”，优先稳定 JSON，关闭 GLM/DeepSeek 类思考避免空 final content。
    "api_outline": {"timeout": 180, "max_tokens": 20000, "json_mode": True, "enable_thinking": False, "temperature": 0.2},
    "api_outline_gap": {"timeout": 180, "max_tokens": 12000, "json_mode": True, "enable_thinking": False, "temperature": 0.2},
    # 功能用例大纲/批量与接口档同构（否则落到空默认：json_mode=False + 8192 tokens + 0.4 温度）。
    "functional_outline": {"timeout": 180, "max_tokens": 20000, "json_mode": True, "enable_thinking": False, "temperature": 0.2},
    "functional_batch": {"timeout": 240, "max_tokens": 24000, "json_mode": False, "enable_thinking": True, "temperature": 0.3},
    # 详细用例需要推理接口依赖、变量贯通和参数值，保留思考。
    "api_batch": {"timeout": 240, "max_tokens": 24000, "json_mode": False, "enable_thinking": True, "temperature": 0.3},
    # 报告修复需要读真实响应和上下文，允许更长输出和更强推理。
    "api_report_fix": {"timeout": 300, "max_tokens": 20000, "json_mode": False, "enable_thinking": True, "temperature": 0.2},
    "api_run_diagnose": {"timeout": 180, "max_tokens": 12000, "json_mode": True, "enable_thinking": True, "temperature": 0.2},
    # 即时自愈每个失败请求都会调用，输出只需一个紧凑决策对象。
    "api_inline_heal": {"timeout": 120, "max_tokens": 5000, "json_mode": True, "enable_thinking": True, "temperature": 0.1},
}


def _extra_value(extra: dict[str, Any], task: str, key: str, default: Any) -> Any:
    """模型 extra 支持全局覆盖和按任务覆盖：max_tokens / api_batch_max_tokens。"""
    task_key = f"{task}_{key}"
    if task_key in extra:
        return extra[task_key]
    return extra.get(key, default)


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "开启", "是"}


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def model_task_options(cfg: "AiModelConfig", task: str) -> dict[str, Any]:  # noqa: F821
    """按模型和任务选择调用策略。

    extra 覆盖示例：
      - max_tokens=32000 或 api_batch_max_tokens=32000
      - enable_thinking=true 或 api_outline_enable_thinking=false
      - reasoning_effort=high 或 api_report_fix_reasoning_effort=high
      - temperature=0.2 / api_batch_temperature=0.3
    """
    defaults = dict(_TASK_DEFAULT_OPTIONS.get(task, {}))
    extra = cfg.extra or {}
    model = (cfg.model or "").lower()
    provider = (cfg.provider or "").lower()

    # OpenAI 推理模型不给 temperature 更稳；强度由 reasoning_effort 控制。
    default_reasoning = defaults.get("reasoning_effort")
    if default_reasoning is None and provider in {"openai", "azure"} and re.match(r"^(o\d|gpt-5)", model):
        default_reasoning = "medium" if task in {"api_outline", "api_outline_gap"} else "high"

    return {
        "timeout": _to_int(_extra_value(extra, task, "timeout", defaults.get("timeout", 120)), defaults.get("timeout", 120)),
        "max_tokens": _to_int(_extra_value(extra, task, "max_tokens", defaults.get("max_tokens", 8192)), defaults.get("max_tokens", 8192)),
        "json_mode": _to_bool(_extra_value(extra, task, "json_mode", defaults.get("json_mode", False)), defaults.get("json_mode", False)),
        "enable_thinking": _to_bool(
            _extra_value(extra, task, "enable_thinking", defaults.get("enable_thinking", True)),
            defaults.get("enable_thinking", True),
        ),
        "temperature": _to_float(_extra_value(extra, task, "temperature", defaults.get("temperature", 0.4)), defaults.get("temperature", 0.4)),
        "reasoning_effort": _extra_value(extra, task, "reasoning_effort", default_reasoning),
    }

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


def _load_ai_config(project_id: Optional[int]) -> dict:
    """加载项目级 AI 配置。"""
    if project_id is None:
        raise NoProviderConfiguredError("project_id 必填：全局 AI 模型配置已移除，请使用项目配置 → AI。")
    from utils.reload_config import config_center
    from database.db import DB

    db = DB()
    try:
        config_center.reload(db.sql, category=None)
    finally:
        db.close()

    providers = _get_provider_configs(project_id)
    if providers:
        for _name, cfg in providers.items():
            if cfg.get("enabled") and cfg.get("model"):
                return {
                    "provider": cfg.get("provider", ""),
                    "api_key": cfg.get("api_key", ""),
                    "model": cfg.get("model", ""),
                    "base_url": cfg.get("base_url") or "",
                    "max_tokens": cfg.get("max_tokens", 4096),
                }

    raise NoProviderConfiguredError(
        "项目 AI 模型配置为空。请在项目配置 → AI 添加至少一个启用模型。"
    )


def _get_provider_configs(project_id: Optional[int]) -> dict:
    """从项目配置读多 provider 配置。

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
    from database.db import DB

    if project_id is None:
        return {}

    db = DB()
    try:
        rows = db.sql.query(
            "SELECT config_group, config_key, config_value FROM config_store "
            "WHERE category = 'ai' AND project_id = :pid ORDER BY config_group, config_key",
            {"pid": project_id},
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
            "provider": (cfg.get("provider") or "").strip().lower(),
            "enabled": str(cfg.get("enabled", "true")).lower() != "false",
            "api_key": (cfg.get("api_key") or "").strip(),
            "model": (cfg.get("model") or "").strip(),
            "base_url": (cfg.get("base_url") or "").strip() or None,
            "max_tokens": int(cfg.get("max_tokens") or 0) or 4096,
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
    json_mode: bool = True,
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

    cfg = _load_ai_config(project_id)
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
        provider_name, api_key, model, prompt, base_url, max_tokens, timeout, json_mode
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
    providers_cfg = _get_provider_configs(project_id)
    enabled = {
        name: cfg for name, cfg in providers_cfg.items()
        if cfg.get("enabled") and cfg.get("model")
        and (cfg.get("api_key") or cfg.get("provider") == "ollama")
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
            provider = (cfg.get("provider") or "").strip().lower()
            raw, ti, to = _call_provider(
                provider, cfg.get("api_key", ""), cfg["model"], prompt,
                cfg.get("base_url"), cfg.get("max_tokens", 16384), timeout
            )
            output = _parse_json_output(raw)
            cost = _estimate_cost(provider, cfg["model"], ti, to)
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
    json_mode: bool = True,
) -> tuple:
    if provider_name in ("openai", "deepseek", "azure"):
        from .providers.openai_provider import call_openai
        return call_openai(api_key, model, prompt, base_url, max_tokens, timeout, json_mode=json_mode)
    elif provider_name == "zai":
        from .providers.zai_provider import call_zai
        return call_zai(api_key, model, prompt, base_url, max_tokens, timeout, json_mode=json_mode)
    elif provider_name == "anthropic":
        from .providers.anthropic_provider import call_anthropic
        return call_anthropic(api_key, model, prompt, base_url, max_tokens, timeout)
    elif provider_name == "ollama":
        from .providers.ollama_provider import call_ollama
        return call_ollama(base_url or "http://localhost:11434", model, prompt, max_tokens, timeout)
    else:
        raise ProviderError(f"不支持的 provider: {provider_name!r}（合法：openai / deepseek / zai / anthropic / ollama）")


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


# ---------------------------------------------------------------------------
# Markdown / Vision / OCR —— 需求分析（M6）用
#
# chat_json 强制 JSON 输出，需求分析想要的是 markdown 全文（带 ## 标题、列表、表格），
# 所以这里另起一组函数：chat_markdown / chat_markdown_with_images。
# 它们直接以 AiModelConfig 为入参（不再从 config_store 现读），避免和 chat_json 的
# 旧 provider 路由耦合。
# ---------------------------------------------------------------------------
import base64       # noqa: E402  —— 放在文件中段，保留上面的 stdlib imports
import os           # noqa: E402
import mimetypes    # noqa: E402

import requests     # noqa: E402


class ProviderDoesNotSupportVisionError(ProviderError):
    """所选 provider/model 不支持图像输入。"""


def _system_for_markdown() -> str:
    return (
        "你是资深产品经理与软件架构师。请用结构化 Markdown 输出你的分析，"
        "使用 ## 标题、列表、表格组织内容；不要包裹在代码块里。"
        "不要返回 JSON，输出的就是最终给人读的 Markdown 文档。"
    )


def _read_image_as_data_url(path: str) -> tuple[str, str]:
    """返回 (data_url, mime)。"""
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    with open(path, "rb") as f:
        b = f.read()
    return f"data:{mime};base64,{base64.b64encode(b).decode('ascii')}", mime


def _read_image_as_base64(path: str) -> tuple[str, str]:
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii"), mime


def chat_markdown(
    prompt: str,
    cfg: "AiModelConfig",   # noqa: F821 — 推迟到运行时 import
    timeout: int = 120,
    system_prompt: str | None = None,
    enable_thinking: bool = True,
    json_mode: bool = False,
    max_tokens: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
) -> tuple[str, int, int]:
    """文本 → Markdown。不强制 JSON。返回 (markdown_text, tokens_in, tokens_out)。

    enable_thinking: 仅对 zai(GLM) 生效。默认 True 保留模型思考；连通测试等场景传
      False 关闭思考以提速。其它 provider 忽略此参数。
    json_mode: 结构化短输出场景可开启，默认 False 以保持 Markdown 分析链路行为不变。"""
    provider = (cfg.provider or "").strip().lower()
    api_key = (cfg.api_key or "").strip()
    base_url = (cfg.base_url or "").strip() or None
    model = (cfg.model or "").strip()
    max_tokens = int(max_tokens or (cfg.extra or {}).get("max_tokens") or 8192)
    temperature = 0.4 if temperature is None else float(temperature)

    if provider in ("openai", "deepseek", "azure"):
        return _openai_markdown(
            api_key, model, prompt, base_url, max_tokens, timeout, system_prompt,
            json_mode=json_mode, enable_thinking=enable_thinking,
            temperature=temperature, reasoning_effort=reasoning_effort,
        )
    if provider == "zai":
        from .providers.zai_provider import call_zai
        return call_zai(
            api_key, model, prompt, base_url, max_tokens, timeout,
            json_mode=json_mode, system_prompt=system_prompt or _system_for_markdown(),
            enable_thinking=enable_thinking, temperature=temperature,
        )
    if provider == "anthropic":
        return _anthropic_markdown(api_key, model, prompt, base_url, max_tokens, timeout, system_prompt, temperature)
    if provider == "ollama":
        return _ollama_markdown(base_url or "http://localhost:11434", model, prompt, max_tokens, timeout, system_prompt, temperature)
    if provider == "custom":
        # 兼容 OpenAI 协议的自建网关
        return _openai_markdown(
            api_key, model, prompt, base_url, max_tokens, timeout, system_prompt,
            json_mode=json_mode, enable_thinking=enable_thinking,
            temperature=temperature, reasoning_effort=reasoning_effort,
        )
    raise ProviderError(f"不支持的 provider: {provider!r}")


def chat_markdown_with_images(
    prompt: str,
    image_paths: list[str],
    cfg: "AiModelConfig",   # noqa: F821
    timeout: int = 180,
) -> tuple[str, int, int]:
    """带图调用。provider/model 不支持 vision 时抛 ProviderDoesNotSupportVisionError。"""
    if not cfg.supports_vision:
        raise ProviderDoesNotSupportVisionError(
            f"模型 {cfg.name} (provider={cfg.provider}, model={cfg.model}) "
            f"未声明 supports_vision=True"
        )

    provider = (cfg.provider or "").strip().lower()
    api_key = (cfg.api_key or "").strip()
    base_url = (cfg.base_url or "").strip() or None
    model = (cfg.model or "").strip()
    max_tokens = int((cfg.extra or {}).get("max_tokens") or 8192)

    if provider in ("openai", "deepseek", "azure", "custom"):
        return _openai_vision(api_key, model, prompt, image_paths, base_url, max_tokens, timeout)
    if provider == "zai":
        return _zai_vision(api_key, model, prompt, image_paths, base_url, max_tokens, timeout)
    if provider == "anthropic":
        return _anthropic_vision(api_key, model, prompt, image_paths, base_url, max_tokens, timeout)
    if provider == "ollama":
        return _ollama_vision(base_url or "http://localhost:11434", model, prompt, image_paths, max_tokens, timeout)
    raise ProviderDoesNotSupportVisionError(
        f"provider {provider!r} 暂未实现 vision 调用分支"
    )


# ------- OpenAI 兼容 -------
def _openai_markdown(
    api_key: str, model: str, prompt: str,
    base_url: Optional[str], max_tokens: int, timeout: int,
    system_prompt: str | None = None,
    json_mode: bool = False,
    enable_thinking: bool = True,
    temperature: float = 0.4,
    reasoning_effort: str | None = None,
) -> tuple[str, int, int]:
    if not api_key:
        raise ProviderError("openai-compatible: api_key 未配置")
    url = (base_url or "https://api.openai.com").rstrip("/") + "/v1/chat/completions"
    # OpenAI 推理模型（o1/o3/o4/gpt-5 系）：拒收 temperature，且要求用
    # max_completion_tokens 替代 max_tokens，否则直接 HTTP 400。
    is_reasoning_model = bool(re.match(r"^(o\d|gpt-5)", (model or "").lower()))
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt or _system_for_markdown()},
            {"role": "user", "content": prompt},
        ],
    }
    if is_reasoning_model:
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
        body["temperature"] = temperature
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if base_url and "api.deepseek.com" in base_url and not enable_thinking:
        # DeepSeek V4 默认开启 thinking。结构化 JSON 生成不需要思考过程，
        # 关闭后可避免只返回 reasoning_content / 空 final content 的偶发现象。
        body["thinking"] = {"type": "disabled"}
    if reasoning_effort:
        # OpenAI 推理模型支持 reasoning_effort；其它 OpenAI-compatible 网关可通过 extra 显式开启。
        body["reasoning_effort"] = str(reasoning_effort)
    return _openai_call(url, api_key, body, timeout)


def _openai_vision(
    api_key: str, model: str, prompt: str, image_paths: list[str],
    base_url: Optional[str], max_tokens: int, timeout: int,
) -> tuple[str, int, int]:
    if not api_key:
        raise ProviderError("openai-compatible: api_key 未配置")
    url = (base_url or "https://api.openai.com").rstrip("/") + "/v1/chat/completions"

    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in image_paths:
        data_url, _ = _read_image_as_data_url(p)
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url},
        })

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_for_markdown()},
            {"role": "user", "content": content},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    return _openai_call(url, api_key, body, timeout)


def _zai_vision(
    api_key: str, model: str, prompt: str, image_paths: list[str],
    base_url: Optional[str], max_tokens: int, timeout: int,
) -> tuple[str, int, int]:
    if not api_key:
        raise ProviderError("zai api_key 未配置")
    from .providers.zai_provider import build_zai_chat_url

    url = build_zai_chat_url(base_url)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in image_paths:
        data_url, _ = _read_image_as_data_url(p)
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url},
        })
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_for_markdown()},
            {"role": "user", "content": content},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    return _openai_call(url, api_key, body, timeout)


def _openai_call(url: str, api_key: str, body: dict, timeout: int) -> tuple[str, int, int]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"openai 网络错误：{exc}") from exc
    if resp.status_code != 200:
        raise ProviderError(f"openai HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        if not content:
            logger.warning(
                "openai-compatible returned empty content model=%s finish_reason=%r "
                "message_keys=%s reasoning_prefix=%r",
                body.get("model"),
                choice.get("finish_reason"),
                sorted(message.keys()),
                str(message.get("reasoning_content") or "")[:300],
            )
        usage = data.get("usage") or {}
        return (
            content,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderError(f"openai 响应格式异常：{resp.text[:500]}") from exc


# ------- Anthropic -------
def _anthropic_markdown(
    api_key: str, model: str, prompt: str,
    base_url: Optional[str], max_tokens: int, timeout: int,
    system_prompt: str | None = None,
    temperature: float = 0.4,
) -> tuple[str, int, int]:
    if not api_key:
        raise ProviderError("anthropic api_key 未配置")
    url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt or _system_for_markdown(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    return _anthropic_call(url, api_key, body, timeout)


def _anthropic_vision(
    api_key: str, model: str, prompt: str, image_paths: list[str],
    base_url: Optional[str], max_tokens: int, timeout: int,
) -> tuple[str, int, int]:
    if not api_key:
        raise ProviderError("anthropic api_key 未配置")
    url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"

    blocks: list[dict] = []
    for p in image_paths:
        b64, mime = _read_image_as_base64(p)
        blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": b64},
        })
    blocks.append({"type": "text", "text": prompt})

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": _system_for_markdown(),
        "messages": [{"role": "user", "content": blocks}],
        "temperature": 0.4,
    }
    return _anthropic_call(url, api_key, body, timeout)


def _anthropic_call(url: str, api_key: str, body: dict, timeout: int) -> tuple[str, int, int]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"anthropic 网络错误：{exc}") from exc
    if resp.status_code != 200:
        raise ProviderError(f"anthropic HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        data = resp.json()
        text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        content = "".join(text_blocks)
        usage = data.get("usage") or {}
        return (
            content,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(f"anthropic 响应格式异常：{resp.text[:500]}") from exc


# ------- Ollama -------
def _ollama_markdown(
    base_url: str, model: str, prompt: str, max_tokens: int, timeout: int,
    system_prompt: str | None = None,
    temperature: float = 0.4,
) -> tuple[str, int, int]:
    return _ollama_call(base_url, {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt or _system_for_markdown()},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": temperature},
    }, timeout)


def _ollama_vision(
    base_url: str, model: str, prompt: str, image_paths: list[str],
    max_tokens: int, timeout: int,
) -> tuple[str, int, int]:
    images_b64 = []
    for p in image_paths:
        b64, _ = _read_image_as_base64(p)
        images_b64.append(b64)
    return _ollama_call(base_url, {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_for_markdown()},
            {"role": "user", "content": prompt, "images": images_b64},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.4},
    }, timeout)


def _ollama_call(base_url: str, payload: dict, timeout: int) -> tuple[str, int, int]:
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError as exc:
        raise ProviderError(f"无法连接 Ollama ({base_url})：{exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise ProviderError(f"Ollama 调用超时 ({timeout}s)") from exc
    if resp.status_code != 200:
        raise ProviderError(f"Ollama HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    content = (data.get("message") or {}).get("content", "") or ""
    return (
        content,
        int(data.get("prompt_eval_count") or 0),
        int(data.get("eval_count") or 0),
    )


# ---------------------------------------------------------------------------
# OCR —— vision-不可用时的回退路径
# ---------------------------------------------------------------------------
def ocr_extract(image_path: str, lang: str = "chi_sim+eng") -> str:
    """pytesseract 薄封装。失败返回空串（不抛异常 —— 调用方按"图片未识别"处理）。"""
    try:
        import pytesseract       # type: ignore
        from PIL import Image    # type: ignore
    except ImportError:
        logger.warning("ocr_extract: pytesseract / Pillow 未安装，跳过 OCR")
        return ""

    if not os.path.exists(image_path):
        logger.warning("ocr_extract: 图像不存在 %s", image_path)
        return ""

    try:
        with Image.open(image_path) as img:
            text = pytesseract.image_to_string(img, lang=lang)
        return (text or "").strip()
    except Exception as exc:       # noqa: BLE001
        logger.warning("ocr_extract failed for %s: %s", image_path, exc)
        return ""


# ---------------------------------------------------------------------------
# Provider ping —— /api/ai-models/{name}/test 的后端调用
# ---------------------------------------------------------------------------
def provider_ping(cfg: "AiModelConfig") -> str:    # noqa: F821
    """短测试调用，返回模型回复的前几十字。失败抛 ProviderError。"""
    text, _, _ = chat_markdown(
        prompt="请回复『ok』两个字，不需要别的内容。",
        cfg=cfg,
        timeout=30,
        enable_thinking=False,   # 连通测试不需要思考，GLM 关闭后秒回
    )
    return (text or "").strip()
