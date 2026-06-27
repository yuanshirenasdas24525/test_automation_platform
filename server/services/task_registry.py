"""
任务类型注册表 —— 统一管理平台所有异步任务的"进行中"状态查询。

设计目标：
  - 新增任务类型只需在注册表里加一个条目，无需改动聚合 API 和前端代码。
  - 每个条目自带 query_fn，指定如何从对应表查询进行中的任务。
  - get_all_in_progress 遍历所有条目，合并为统一格式返回。

使用方式：
  from server.services.task_registry import task_registry, TaskTypeInfo

  task_registry.register(TaskTypeInfo(
      key="my_task",
      label="我的任务",
      category="system",
      icon="Database",
      query_fn=_query_my_table,
      detail_url_tpl="/my-page/{id}",
  ))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Union

from sqlalchemy.orm import Session as SASession


# ---------------------------------------------------------------------------
# 查询函数签名：接收 db_session、可选的 project_id、limit，返回 dict 列表
# ---------------------------------------------------------------------------
class TaskQueryFn(Protocol):
    def __call__(
        self,
        db_session: SASession,
        project_id: Optional[int],
        limit: int,
    ) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# 注册条目
# ---------------------------------------------------------------------------
@dataclass
class TaskTypeInfo:
    """一种异步任务类型的元信息。

    Attributes:
        key: 唯一标识（如 "ai_requirement_parse"）
        label: 中文名（如 "AI 需求分析"）
        category: 大类 — "ai" | "execution" | "system"
        icon: 前端 lucide 图标名（如 "Brain", "Play", "Smartphone"）
        query_fn: 查询该类型进行中任务的函数
        detail_url_tpl: 跳转链接模板，支持 {id} / {project_id} 占位
    """
    key: str
    label: str
    category: str
    icon: str
    query_fn: TaskQueryFn
    detail_url_tpl: str = ""


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
class TaskRegistry:
    """全局任务类型注册表（单例）。"""

    _entries: dict[str, TaskTypeInfo]

    def __init__(self) -> None:
        self._entries = {}

    def register(self, info: TaskTypeInfo) -> None:
        """注册一种任务类型。重复 key 会覆盖（最后注册生效）。"""
        self._entries[info.key] = info

    def get_all_in_progress(
        self,
        db_session: SASession,
        project_id: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """遍历所有注册条目，查询进行中任务，合并为统一格式。

        返回的每条 dict 包含：
          - type_key, type_label, category, icon
          - id, name, status
          - project_id, project_name
          - started_at (ISO string or None)
          - detail_url (已拼好的跳转链接)
        """
        all_tasks: list[dict[str, Any]] = []

        for info in self._entries.values():
            try:
                raw_rows = info.query_fn(db_session, project_id, limit)
            except Exception:
                # 某类任务查询失败不影响其他类型
                continue

            for row in raw_rows:
                unified = self._unify(row, info)
                if unified:
                    all_tasks.append(unified)

        # 按 started_at 倒序（最新的在前）
        all_tasks.sort(
            key=lambda t: t.get("started_at") or "",
            reverse=True,
        )
        return all_tasks[:limit]

    @staticmethod
    def _unify(row: dict[str, Any], info: TaskTypeInfo) -> dict[str, Any] | None:
        """把单个查询结果的行 dict 转成前端统一格式。"""
        task_id = row.get("id")
        if task_id is None:
            return None

        project_id = row.get("project_id")
        detail_url = row.get("detail_url") or info.detail_url_tpl
        if detail_url:
            detail_url = detail_url.replace("{id}", str(task_id))
            detail_url = detail_url.replace(
                "{project_id}", str(project_id) if project_id else ""
            )

        return {
            "type_key": info.key,
            "type_label": info.label,
            "category": info.category,
            "icon": info.icon,
            "id": task_id,
            "name": row.get("name") or info.label,
            "status": row.get("status") or "unknown",
            "project_id": project_id,
            "project_name": row.get("project_name"),
            "started_at": _iso(row.get("started_at")),
            "detail_url": detail_url,
        }


def _iso(value: Any) -> str | None:
    """datetime → ISO 字符串；None → None。"""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# 全局单例
task_registry = TaskRegistry()
