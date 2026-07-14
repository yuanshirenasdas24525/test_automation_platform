"""配置热更新。只加载项目级配置。

使用：
    config_center.reload(db)
    val = config_center.get("host", "url", project_id=1, default="http://localhost")

get() 指定 project_id 时只查该项目配置；未指定 project_id 时返回默认值。
"""
from __future__ import annotations

from typing import Any

from utils.logger import LOGGER


class ConfigCenter:
    _instance = None
    _loaded: bool = False

    def __init__(self):
        if not hasattr(self, "_stores"):
            self._stores: dict[int, dict[str, dict[str, str]]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def reload(self, db, project_id: int | None = None, category: str | None = None):
        """从 config_store 表同步配置到内存。"""
        self._do_reload(db, project_id=project_id, category=category)
        ConfigCenter._loaded = True

    def _do_reload(self, db, project_id=None, category=None):
        if category:
            LOGGER.info(f"[ConfigCenter] 精准重载 category={category}, project_id={project_id}")
        else:
            LOGGER.info(f"[ConfigCenter] 全量重载 project_id={project_id}")

        params: dict[str, int] = {}
        sql = (
            "SELECT config_group, config_key, config_value, category, project_id "
            "FROM config_store"
        )
        if project_id is not None:
            sql += " WHERE project_id = :pid"
            params["pid"] = project_id
        rows = db.query(sql, params)

        # 按 project_id 分组；project_id 为空的历史全局模板行不再加载。
        new_stores: dict[int, dict[str, dict[str, str]]] = {}

        for row in rows:
            pid = row["project_id"]  # int or None
            cat = row.get("category") or ""
            if category and cat != category:
                continue

            group = row["config_group"]
            key = row["config_key"]
            val = row["config_value"]

            if pid is None:
                continue
            if pid not in new_stores:
                new_stores[pid] = {}
            store = new_stores[pid]

            if group not in store:
                store[group] = {}
            store[group][key] = val

        self._stores = new_stores

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def _ensure_loaded(self):
        """首次访问时自动从 DB 加载配置。"""
        if ConfigCenter._loaded:
            return
        try:
            from database.db import DB
            db = DB()
            try:
                self._do_reload(db.sql)
                ConfigCenter._loaded = True
            finally:
                db.close()
        except Exception:
            LOGGER.warning("[ConfigCenter] 延迟加载失败，使用空配置", exc_info=True)

    def get(
        self,
        group: str,
        key: str | None = None,
        default: Any = None,
        project_id: int | None = None,
    ) -> Any:
        """读取配置。

        project_id=int  → 只读项目配置。
        project_id=None → 返回 default；不再回退任意项目或全局模板。
        """
        self._ensure_loaded()

        if project_id is not None:
            project_store = self._stores.get(project_id, {})
            val = self._resolve(project_store, group, key)
            if val is not None:
                return val
            return default if key is not None else (default if default is not None else {})

        if key is None:
            return default if default is not None else {}
        return default

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve(
        store: dict[str, dict[str, str]],
        group: str,
        key: str | None,
    ) -> Any | None:
        """从 store 中拿配置；找不到返回 None（与 default 区分）。"""
        group_config = store.get(group, {})
        if not group_config:
            return None
        if key is None:
            return dict(group_config)
        if key in group_config:
            return group_config[key]
        return None


config_center = ConfigCenter()
