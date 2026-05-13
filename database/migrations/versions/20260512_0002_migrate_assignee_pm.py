"""数据迁移：Requirement.assignee_pm_id → RequirementAssignee(role='pm')

把所有非空的 Requirement.assignee_pm_id 复制成 RequirementAssignee 行（role='pm'），
旧字段保留兼容存量代码，前端 / 新代码统一从 RequirementAssignee 读。

幂等：以 (requirement_id, user_id, role='pm') 三元唯一约束保证重复执行无副作用。

Revision ID: pm_000007
Revises: pm_000006
Create Date: 2026-05-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000007"
down_revision: Union[str, None] = "pm_000006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ON CONFLICT 对 Postgres 走原生；其他方言（sqlite）退化成 INSERT OR IGNORE。
    if dialect == "postgresql":
        bind.execute(sa.text("""
            INSERT INTO requirement_assignees (requirement_id, user_id, role, created_at)
            SELECT id, assignee_pm_id, 'pm', NOW()
              FROM requirements
             WHERE assignee_pm_id IS NOT NULL
            ON CONFLICT (requirement_id, user_id, role) DO NOTHING
        """))
    else:
        bind.execute(sa.text("""
            INSERT OR IGNORE INTO requirement_assignees (requirement_id, user_id, role, created_at)
            SELECT id, assignee_pm_id, 'pm', CURRENT_TIMESTAMP
              FROM requirements
             WHERE assignee_pm_id IS NOT NULL
        """))


def downgrade() -> None:
    # 不删 RequirementAssignee 行 —— 因为上游可能也手工增删过 pm 角色记录，
    # 盲删会误伤。需要回滚时手动清理。
    pass
