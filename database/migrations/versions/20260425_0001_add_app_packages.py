"""add_app_packages

新增 `app_packages` 表，承接「App 包管理」功能：用户上传的 .apk / .ipa 文件
元信息存这里，物理文件落到 data/app_packages/。后续 step 编辑器从这张表
拉下拉选项，让用户在 app_install / app_launch 等 step 上「按包名挑」而不是
手粘路径。

字段对应 database/models/app_package.py，迁移这边只关心建表语句。

Revision ID: app_pkg_000001
Revises: dev_hb_000001
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "app_pkg_000001"
down_revision: Union[str, None] = "dev_hb_000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_packages",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("app_package", sa.String(length=255), nullable=True),
        sa.Column("bundle_id", sa.String(length=255), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "upload_time",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index("ix_app_packages_platform", "app_packages", ["platform"])


def downgrade() -> None:
    op.drop_index("ix_app_packages_platform", table_name="app_packages")
    op.drop_table("app_packages")
