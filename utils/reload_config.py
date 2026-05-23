"""配置热更新。支持 per-project 配置加载和全局模板回退。

使用：
    config_center.reload(db, project_id=1)
    val = config_center.get("host", "url", project_id=1, default="http://localhost")

project_id=None 表示全局模板。
get() 优先查 project_id 对应配置，查不到的 key 回退到全局模板。
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
            self._global_store: dict[str, dict[str, str]] = {}

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

        rows = db.query(
            "SELECT config_group, config_key, config_value, category, project_id "
            "FROM config_store"
        )

        # 按 project_id 分组
        new_global: dict[str, dict[str, str]] = {}
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
                store = new_global
            else:
                if pid not in new_stores:
                    new_stores[pid] = {}
                store = new_stores[pid]

            if group not in store:
                store[group] = {}
            store[group][key] = val

        self._global_store = new_global
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

        project_id=int  → 优先读项目配置，缺失的 key 回退到全局模板。
        project_id=None → 读全局模板；若空则遍历所有项目 store 做兼容回退。
        """
        self._ensure_loaded()

        # 1. 指定了 project_id → 只查该项目 + 全局回退
        if project_id is not None:
            project_store = self._stores.get(project_id, {})
            val = self._resolve(project_store, group, key)
            if val is not None:
                return val
            # 回退到全局模板
            val = self._resolve(self._global_store, group, key)
            if val is not None:
                return val
            return default if key is not None else (default if default is not None else {})

        # 2. 未指定 project_id → 全局模板优先，再遍历所有项目做兼容
        val = self._resolve(self._global_store, group, key)
        if val is not None:
            return val

        for _pid, store in self._stores.items():
            val = self._resolve(store, group, key)
            if val is not None:
                return val

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
