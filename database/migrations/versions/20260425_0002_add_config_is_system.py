"""add_config_is_system

给 `config_store` 加 `is_system` 标记，区分"平台 seed 进来的系统配置项"
（不允许删，避免链路断掉）和"用户自建项"（可改可删）。

字段对应 database/models/config_store.py。

Revision ID: cfg_sys_000001
Revises: app_pkg_000001
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cfg_sys_000001"
down_revision: Union[str, None] = "app_pkg_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 不支持 ALTER COLUMN 设置默认值，用 batch_alter_table 做兼容
    with op.batch_alter_table("config_store") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("config_store") as batch_op:
        batch_op.drop_column("is_system")
