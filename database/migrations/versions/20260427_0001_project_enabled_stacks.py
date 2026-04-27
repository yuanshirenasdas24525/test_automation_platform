"""project_enabled_stacks

把 `projects.type`（单字符串：API / Web / Mobile）替换成 `projects.enabled_stacks`
（JSON 数组：["api","web","app","functional"] 任意子集）。

背景：
  老模型一个项目只能属于一种栈，导致同一业务（比如电商）要建"电商-API
  / 电商-Web / 电商-App"三个项目，模块、配置、变量都得复制三份；
  AI 上下文也只能看到单栈的半截信息。重构后：项目按业务域划分，进入
  项目后用栈 Tab（API / Web / App / 功能用例）切换执行视角。

迁移策略（一步到位）：
  1. 先加 enabled_stacks 列（nullable，方便 batch_alter_table 重建表）；
  2. 用 Python 侧逐行回填：type=Mobile→["app"]、type=Web→["web"]、
     其它/None→["api"]；
  3. 把 enabled_stacks 改成 NOT NULL + server_default '["api"]'，再 drop 掉 type 列。

降级回滚：
  把 type 加回来，按 enabled_stacks 第一个元素回写（Mobile/Web/API 三种），
  drop 掉 enabled_stacks。

Revision ID: proj_stk_000001
Revises: cfg_sys_drop_01
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "proj_stk_000001"
down_revision: Union[str, None] = "cfg_sys_drop_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 老 type 字符串 → 新 stack 名称的映射。键统一小写后查。
_TYPE_TO_STACK = {
    "mobile": "app",
    "app": "app",
    "web": "web",
    "api": "api",
}


def upgrade() -> None:
    bind = op.get_bind()

    # Step 1：先加上 enabled_stacks（nullable=True，便于回填）
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("enabled_stacks", sa.JSON(), nullable=True))

    # Step 2：Python 侧逐行回填。用 sa.table()/sa.column() 而不是 ORM model，
    # 避免迁移依赖业务模型当下的字段定义（model 已经改成新版了，再 import
    # 反而会拿不到 type 列）。
    projects_t = sa.table(
        "projects",
        sa.column("id", sa.Integer),
        sa.column("type", sa.String),
        sa.column("enabled_stacks", sa.JSON),
    )
    rows = bind.execute(sa.select(projects_t.c.id, projects_t.c.type)).fetchall()
    for row in rows:
        raw = (row.type or "api").strip().lower()
        stack = _TYPE_TO_STACK.get(raw, "api")
        bind.execute(
            projects_t.update()
            .where(projects_t.c.id == row.id)
            .values(enabled_stacks=[stack])
        )

    # Step 3：补 NOT NULL + server_default，并 drop 掉 type。
    # SQLite 的 ALTER 限制需要走 batch_alter_table 重建表。
    with op.batch_alter_table("projects") as batch_op:
        batch_op.alter_column(
            "enabled_stacks",
            existing_type=sa.JSON(),
            nullable=False,
            server_default='["api"]',
        )
        batch_op.drop_column("type")


def downgrade() -> None:
    bind = op.get_bind()

    # Step 1：先把 type 加回来（nullable=True，便于回填）
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("type", sa.String(), nullable=True))

    # Step 2：从 enabled_stacks 取第一个元素回写 type。
    # 没法完美还原（mobile vs app / 多栈情况会丢信息），这里取最常见的优先级：
    #   有 app → "Mobile"；否则有 web → "Web"；否则 → "API"。
    projects_t = sa.table(
        "projects",
        sa.column("id", sa.Integer),
        sa.column("type", sa.String),
        sa.column("enabled_stacks", sa.JSON),
    )
    rows = bind.execute(sa.select(projects_t.c.id, projects_t.c.enabled_stacks)).fetchall()
    for row in rows:
        stacks = row.enabled_stacks or ["api"]
        if "app" in stacks:
            type_str = "Mobile"
        elif "web" in stacks:
            type_str = "Web"
        else:
            type_str = "API"
        bind.execute(
            projects_t.update()
            .where(projects_t.c.id == row.id)
            .values(type=type_str)
        )

    # Step 3：把 type 改成 NOT NULL，drop 掉 enabled_stacks。
    with op.batch_alter_table("projects") as batch_op:
        batch_op.alter_column("type", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("enabled_stacks")
