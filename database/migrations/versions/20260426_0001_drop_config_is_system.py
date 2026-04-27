"""drop_config_is_system

把 `config_store.is_system` 列删掉。

背景：
  之前为了"禁止删除平台 seed 进来的系统配置项"加了这个布尔列。
  后续把"系统配置 seed"模式整体改成"前端推荐配置项面板（用户自助一键填入）"，
  所有配置项都允许用户自由 CRUD，is_system 已无用途，且会让人误以为还存在
  系统级保护项。这里把列彻底删掉，以免给后续读代码的人造成困惑。

  老库里如果存在 is_system=True 的行，行本身不会被删除（只是失去标记）。
  对应应用代码同步去掉了 is_system 字段读取与写入。

Revision ID: cfg_sys_drop_01
Revises: cfg_sys_000001
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "cfg_sys_drop_01"
down_revision: Union[str, None] = "cfg_sys_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 上 drop_column 必须走 batch_alter_table（它会重建表）
    with op.batch_alter_table("config_store") as batch_op:
        batch_op.drop_column("is_system")


def downgrade() -> None:
    # 回滚把列加回来；缺省值 False，老数据全部恢复成"非系统项"。
    with op.batch_alter_table("config_store") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )
