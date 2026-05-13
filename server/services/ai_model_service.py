"""AI 模型配置 service。

存储约定（沿用 ai_gateway 现有读取约定）：
  config_store(
    category='ai',
    config_group=<model alias / 用户起的名字>,
    config_key='model'        config_value=<模型标识>
    config_key='api_key'      config_value=<密钥>
    config_key='base_url'     config_value=<可选>
    config_key='provider'     config_value=openai/anthropic/ollama/...
    config_key='supports_vision' config_value='true' | 'false'
    config_key='is_default'      config_value='true' | 'false'
    config_key='enabled'         config_value='true' | 'false'
    config_key='extra_<k>'       config_value=<任意字符串/JSON 串>
  )
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from database.models.config_store import ConfigStore
from database.schemas.ai_config import AiModelConfig


CATEGORY = "ai"

# 内置标量字段；其余 config_key 落到 extra（去前缀 extra_）
_SCALAR_KEYS = {
    "provider",
    "model",
    "base_url",
    "api_key",
    "supports_vision",
    "is_default",
    "enabled",
}
_BOOL_KEYS = {"supports_vision", "is_default", "enabled"}
_EXTRA_PREFIX = "extra_"


def _row_to_pair(row: ConfigStore) -> tuple[str, str | bool]:
    """(key, value) 元组，把 bool 反序列化。"""
    key = row.config_key
    raw = row.config_value or ""
    if key in _BOOL_KEYS:
        return key, str(raw).strip().lower() in ("true", "1", "yes")
    return key, raw


def _rows_to_config(name: str, rows: list[ConfigStore]) -> AiModelConfig:
    data: dict[str, Any] = {"name": name, "provider": "", "model": ""}
    extra: dict[str, Any] = {}
    for row in rows:
        k, v = _row_to_pair(row)
        if k in _SCALAR_KEYS:
            data[k] = v
        elif k.startswith(_EXTRA_PREFIX):
            raw_extra_key = k[len(_EXTRA_PREFIX):]
            # extra_ 透传值优先尝试 json.loads，失败则原样
            raw_val = row.config_value or ""
            try:
                extra[raw_extra_key] = json.loads(raw_val)
            except Exception:
                extra[raw_extra_key] = raw_val
    data["extra"] = extra
    # 兜底默认值，避免 Pydantic 校验失败
    data.setdefault("supports_vision", False)
    data.setdefault("is_default", False)
    data.setdefault("enabled", True)
    return AiModelConfig(**data)


def list_ai_models(session: Session) -> list[AiModelConfig]:
    rows = (
        session.query(ConfigStore)
        .filter(ConfigStore.category == CATEGORY)
        .all()
    )
    # 过滤掉旧的扁平 `ai` / `provider` 单 provider 配置（gateway 自己处理）
    by_group: dict[str, list[ConfigStore]] = {}
    for r in rows:
        if not r.config_group or r.config_group in ("ai", "provider"):
            continue
        by_group.setdefault(r.config_group, []).append(r)

    out: list[AiModelConfig] = []
    for name, group_rows in by_group.items():
        # 只列出有 model 的（其他可能是历史脏数据）
        if not any(r.config_key == "model" and r.config_value for r in group_rows):
            continue
        out.append(_rows_to_config(name, group_rows))
    return out


def get_ai_model(session: Session, name: str) -> AiModelConfig | None:
    rows = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.category == CATEGORY,
            ConfigStore.config_group == name,
        )
        .all()
    )
    if not rows:
        return None
    if not any(r.config_key == "model" and r.config_value for r in rows):
        return None
    return _rows_to_config(name, rows)


def get_default_ai_model(session: Session) -> AiModelConfig | None:
    """返回 is_default=True 且 enabled 的第一个；没有则返回任一 enabled。"""
    configs = [c for c in list_ai_models(session) if c.enabled]
    for c in configs:
        if c.is_default:
            return c
    return configs[0] if configs else None


def upsert_ai_model(
    session: Session,
    name: str,
    cfg: AiModelConfig,
) -> AiModelConfig:
    """覆盖式 upsert：先清掉 (category=ai, config_group=name) 下的所有行，再写新值。"""
    # 0. 若 cfg.is_default=True，先把其他 group 的 is_default 抹掉，单选行为
    if cfg.is_default:
        defaults = (
            session.query(ConfigStore)
            .filter(
                ConfigStore.category == CATEGORY,
                ConfigStore.config_key == "is_default",
                ConfigStore.config_group != name,
            )
            .all()
        )
        for row in defaults:
            row.config_value = "false"

    # 1. 删旧
    session.query(ConfigStore).filter(
        ConfigStore.category == CATEGORY,
        ConfigStore.config_group == name,
    ).delete(synchronize_session=False)

    # 2. 写新
    def _put(key: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            value = "true" if value else "false"
        session.add(
            ConfigStore(
                category=CATEGORY,
                config_group=name,
                config_key=key,
                config_value=str(value),
            )
        )

    _put("provider", cfg.provider)
    _put("model", cfg.model)
    _put("base_url", cfg.base_url)
    _put("api_key", cfg.api_key)
    _put("supports_vision", cfg.supports_vision)
    _put("is_default", cfg.is_default)
    _put("enabled", cfg.enabled)

    for k, v in (cfg.extra or {}).items():
        # extra 值统一 JSON 序列化，便于回读
        try:
            serialized = json.dumps(v, ensure_ascii=False)
        except Exception:
            serialized = str(v)
        session.add(
            ConfigStore(
                category=CATEGORY,
                config_group=name,
                config_key=f"{_EXTRA_PREFIX}{k}",
                config_value=serialized,
            )
        )

    session.flush()
    return cfg


def delete_ai_model(session: Session, name: str) -> int:
    n = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.category == CATEGORY,
            ConfigStore.config_group == name,
        )
        .delete(synchronize_session=False)
    )
    session.flush()
    return n
