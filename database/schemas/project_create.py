"""ProjectCreate：创建 / 编辑项目的 Pydantic 请求体。

v2 (2026-04-27)：从单字符串 `type` 改为列表 `enabled_stacks`。
旧字段 `type` 已彻底移除（数据库列也 drop 掉了，参见迁移
proj_stk_000001）。前端 / 调用方需同步改成传 enabled_stacks。
"""
from __future__ import annotations

from typing import List, Optional

import pydantic

from database.models.project import ALL_PROJECT_STACKS


class ProjectCreate(pydantic.BaseModel):
    """
    创建 / 编辑项目的请求体。

    Pydantic v2 不再把 `Optional[X]` 当成"带 None 默认值"——必须显式 `= None`
    才是选填。description / icon 都是选填。

    enabled_stacks 校验：
      - 至少一个元素
      - 每个元素必须 ∈ {api, web, app, functional}
      - 顺序按用户勾选顺序保存（前端展示 chip 时用固定顺序，不依赖入库顺序）
    """
    name: str
    enabled_stacks: List[str]
    description: Optional[str] = None
    icon: Optional[str] = None

    @pydantic.field_validator("enabled_stacks")
    @classmethod
    def _validate_enabled_stacks(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("至少启用一个栈（api / web / app / functional）")
        # 去重 + 校验枚举值合法
        seen: set[str] = set()
        cleaned: list[str] = []
        for raw in v:
            stack = (raw or "").strip().lower()
            if stack not in ALL_PROJECT_STACKS:
                raise ValueError(
                    f"未知栈类型：{raw!r}（合法值：{sorted(ALL_PROJECT_STACKS)}）"
                )
            if stack not in seen:
                seen.add(stack)
                cleaned.append(stack)
        return cleaned
