"""把旧 web/test_accounts（HTTP/script provider）配置迁移为静态池 + dynamic_script。"""
from __future__ import annotations

from typing import Any

_DROP_KEYS = {
    "api_base_url", "login_method", "login_path", "login_body", "token_jsonpath",
    "auth_header", "auth_scheme", "create_method", "create_path", "create_body",
    "user_id_jsonpath", "cleanup_method", "cleanup_path", "timeout_seconds",
    "provider", "shared_username", "shared_password", "prepare_script",
    "cleanup_script", "script_config", "auto_cleanup",
}


def build_pool_from_legacy(legacy: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """从旧配置键值构造 (accounts, dynamic_script)。password 保持已加密原值。"""
    pool: list[dict[str, Any]] = []
    username = str(legacy.get("shared_username") or "").strip()
    password = legacy.get("shared_password")
    if username and password:
        pool.append({
            "label": "共享账号", "username": username, "password": password,
            "state": "admin", "enabled": True,
        })
    dynamic = ""
    if str(legacy.get("provider") or "").strip().lower() == "script":
        dynamic = str(legacy.get("prepare_script") or "").strip()
    return pool, dynamic


def upgrade(session) -> None:
    from database.models import ConfigStore

    rows = (
        session.query(ConfigStore)
        .filter(ConfigStore.category == "web", ConfigStore.config_group == "test_accounts")
        .all()
    )
    by_project: dict[int, dict[str, Any]] = {}
    for r in rows:
        by_project.setdefault(r.project_id, {})[str(r.config_key)] = r.config_value
    for project_id, legacy in by_project.items():
        pool, dynamic = build_pool_from_legacy(legacy)
        for r in list(rows):
            if r.project_id == project_id and str(r.config_key) in _DROP_KEYS:
                session.delete(r)
        _upsert(session, project_id, "accounts", pool)
        _upsert(session, project_id, "dynamic_script", dynamic)
    session.commit()


def _upsert(session, project_id: int, key: str, value: Any) -> None:
    from database.models import ConfigStore

    row = (
        session.query(ConfigStore)
        .filter(
            ConfigStore.project_id == project_id,
            ConfigStore.category == "web",
            ConfigStore.config_group == "test_accounts",
            ConfigStore.config_key == key,
        )
        .first()
    )
    if row is None:
        session.add(ConfigStore(
            project_id=project_id, category="web", config_group="test_accounts",
            config_key=key, config_value=value,
        ))
    else:
        row.config_value = value
