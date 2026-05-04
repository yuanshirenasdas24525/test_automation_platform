"""PM 重设计 Phase 1：users / roles / user_roles / tasks / version_test_summaries
   + requirements 5 字段 + project_versions 状态扩展 + test_cases.version_id

  - 5 张新表 + 3 张表的字段增量
  - Migration 末尾 INSERT 6 个固定 role（admin/dev/test/pm/ui/ops）

Revision ID: pm_000003
Revises: pm_000002
Create Date: 2026-05-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pm_000003"
down_revision: Union[str, None] = "pm_000002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 6 个固定角色 seed
SEED_ROLES = [
    {"code": "admin", "name": "管理员",  "description": "全平台读写 + 成员管理"},
    {"code": "dev",   "name": "开发",    "description": "认领 dev 任务，修复 bug"},
    {"code": "test",  "name": "测试",    "description": "执行测试，建 bug，出报告"},
    {"code": "pm",    "name": "产品",    "description": "需求管理，PM 验收 gate"},
    {"code": "ui",    "name": "UI",     "description": "走查任务，设计稿资产"},
    {"code": "ops",   "name": "运维",    "description": "环境探活，发版部署"},
]


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ------------------------------------------------------------------
    # 1. users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ------------------------------------------------------------------
    # 2. roles
    # ------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_roles_code", "roles", ["code"], unique=True)

    # ------------------------------------------------------------------
    # 3. user_roles (m2m)
    # ------------------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    )

    # ------------------------------------------------------------------
    # 4. tasks
    # ------------------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requirement_id", sa.Integer(), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("severity", sa.String(length=4), nullable=True),
        sa.Column("assignee_dev_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("assignee_test_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("related_case_id", sa.Integer(), sa.ForeignKey("test_cases.id"), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("estimated_hours", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("actual_hours", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_tasks_requirement_id", "tasks", ["requirement_id"])
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"])
    op.create_index("ix_tasks_assignee_dev_id", "tasks", ["assignee_dev_id"])
    op.create_index("ix_tasks_assignee_test_id", "tasks", ["assignee_test_id"])
    op.create_index("ix_tasks_type", "tasks", ["type"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_type_status", "tasks", ["type", "status"])

    # ------------------------------------------------------------------
    # 5. version_test_summaries
    # ------------------------------------------------------------------
    op.create_table(
        "version_test_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("total_requirements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tasks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_test_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p0_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p1_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p2_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p3_bugs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_pass_rate", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("avg_fix_time_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("test_coverage", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_vts_version_id", "version_test_summaries", ["version_id"], unique=True)

    # ------------------------------------------------------------------
    # 6. requirements 加 5 字段（SQLite 用 batch_alter_table）
    # ------------------------------------------------------------------
    _req_cols = {c["name"] for c in op.get_bind().execute(sa.text("PRAGMA table_info(requirements)")).mappings().all()} if dialect == "sqlite" else set()
    with op.batch_alter_table("requirements") as batch:
        if "version_id" not in _req_cols:
            batch.add_column(sa.Column("version_id", sa.Integer(), nullable=True))
        if "system_status" not in _req_cols:
            batch.add_column(sa.Column("system_status", sa.String(length=20), nullable=True))
        if "business_status" not in _req_cols:
            batch.add_column(sa.Column("business_status", sa.String(length=20), nullable=True))
        if "assignee_pm_id" not in _req_cols:
            batch.add_column(sa.Column("assignee_pm_id", sa.Integer(), nullable=True))
        if "accepted_at" not in _req_cols:
            batch.add_column(sa.Column("accepted_at", sa.DateTime(), nullable=True))
    # FK 仅 PG 下创建
    if dialect != "sqlite":
        op.create_foreign_key("fk_req_version_id", "requirements", "project_versions", ["version_id"], ["id"])
        op.create_foreign_key("fk_req_assignee_pm_id", "requirements", "users", ["assignee_pm_id"], ["id"])
    op.create_index("ix_req_version_id", "requirements", ["version_id"])
    op.create_index("ix_req_system_status", "requirements", ["system_status"])
    op.create_index("ix_req_business_status", "requirements", ["business_status"])
    op.create_index("ix_req_assignee_pm_id", "requirements", ["assignee_pm_id"])

    # ------------------------------------------------------------------
    # 7. test_cases 加 version_id（SQLite 用 batch_alter_table）
    # ------------------------------------------------------------------
    _tc_cols = {c["name"] for c in op.get_bind().execute(sa.text("PRAGMA table_info(test_cases)")).mappings().all()} if dialect == "sqlite" else set()
    if "version_id" not in _tc_cols:
        with op.batch_alter_table("test_cases") as batch:
            batch.add_column(sa.Column("version_id", sa.Integer(), nullable=True))
    if dialect != "sqlite":
        op.create_foreign_key("fk_tc_version_id", "test_cases", "project_versions", ["version_id"], ["id"])
    op.create_index("ix_tc_version_id", "test_cases", ["version_id"])

    # ------------------------------------------------------------------
    # 8. seed 6 个固定 role
    # ------------------------------------------------------------------
    roles_table = sa.table(
        "roles",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(roles_table, SEED_ROLES)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 反序：先删字段，再删表
    op.drop_index("ix_tc_version_id", table_name="test_cases")
    with op.batch_alter_table("test_cases") as batch:
        batch.drop_column("version_id")

    op.drop_index("ix_req_assignee_pm_id", table_name="requirements")
    op.drop_index("ix_req_business_status", table_name="requirements")
    op.drop_index("ix_req_system_status", table_name="requirements")
    op.drop_index("ix_req_version_id", table_name="requirements")
    with op.batch_alter_table("requirements") as batch:
        batch.drop_column("accepted_at")
        batch.drop_column("assignee_pm_id")
        batch.drop_column("business_status")
        batch.drop_column("system_status")
        batch.drop_column("version_id")

    op.drop_index("ix_vts_version_id", table_name="version_test_summaries")
    op.drop_table("version_test_summaries")

    op.drop_index("ix_tasks_type_status", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_type", table_name="tasks")
    op.drop_index("ix_tasks_assignee_test_id", table_name="tasks")
    op.drop_index("ix_tasks_assignee_dev_id", table_name="tasks")
    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
    op.drop_index("ix_tasks_requirement_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_table("user_roles")

    op.drop_index("ix_roles_code", table_name="roles")
    op.drop_table("roles")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
