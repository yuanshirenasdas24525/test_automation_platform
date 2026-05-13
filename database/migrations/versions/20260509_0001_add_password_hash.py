"""add_password_hash_to_users

给 users 表增加 password_hash 列（bcrypt 哈希存储），前置登录功能。
nullable=True 兼容存量无密码用户。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000005"
down_revision: Union[str, None] = "pm_000004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")
