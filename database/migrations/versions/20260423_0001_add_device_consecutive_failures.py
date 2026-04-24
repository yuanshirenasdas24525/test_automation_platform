"""add_device_consecutive_failures

给 devices 表补一个 `consecutive_failures` 字段，用于后端主动探测型心跳：
  - 每 30s 由 Celery beat 触发 probe_devices 任务；
  - 任务逐台做 Appium `/status` HTTP 探测：
      成功 → failures 清零 + 更新 last_heartbeat + 若原 offline 则恢复 idle
      失败 → failures + 1，到达阈值（默认 2）就把 status 置 offline
  - 人工不建议直接改这个字段。

兼容性：
  - SQLite 走 batch_alter_table（实现上是"新建-拷-rename"）。
  - PostgreSQL / MySQL 直接 ALTER TABLE ADD COLUMN，都无痛。

Revision ID: dev_hb_000001
Revises: v2_000001
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "dev_hb_000001"
down_revision: Union[str, None] = "v2_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 新字段带默认 0，存量行直接回填成 0（没有 NULL 值）
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("consecutive_failures")
