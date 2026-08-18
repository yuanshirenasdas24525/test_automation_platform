from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from server.services.test_accounts.secrets import (
    TEST_ACCOUNT_CONFIG_GROUP,
    decode_test_account_secret,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_VALID_STATES = {"normal", "admin", "disabled", "locked", "boundary"}


def _coerce_accounts(raw: Any) -> list[dict[str, Any]]:
    # config_store.config_value 是 String 列：JSON 配置以 json.dumps 后的字符串存，
    # 读回来是 str，这里先反序列化再按池处理。
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        state = str(item.get("state") or "normal").strip().lower()
        if state not in _VALID_STATES:
            state = "normal"
        out.append({
            "label": str(item.get("label") or username),
            "username": username,
            "password": decode_test_account_secret(item.get("password")),
            "state": state,
            "enabled": item.get("enabled", True) is not False,
        })
    return out


def load_account_sources(session: "Session", project_id: int) -> dict[str, Any]:
    """读项目账号来源：静态池 + 可选 dynamic_script。无 HTTP 字段、无平台默认。"""
    from database.models.config_store import ConfigStore

    rows = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.project_id == project_id,
            ConfigStore.category == "web",
            ConfigStore.config_group == TEST_ACCOUNT_CONFIG_GROUP,
        )
        .all()
    )
    config = {str(r.config_key): r.config_value for r in rows}
    return {
        "accounts": _coerce_accounts(config.get("accounts")),
        "dynamic_script": str(config.get("dynamic_script") or "").strip(),
    }
