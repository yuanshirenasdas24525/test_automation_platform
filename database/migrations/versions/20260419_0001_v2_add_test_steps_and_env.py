"""v2_add_test_steps_and_env

给平台增加 v2 所需的核心表和字段：
  1. 新建 test_steps, test_environments, test_variables, devices
  2. 扩展 test_cases 增加 case_type/tags/env_id/pre_hook/post_hook/variables/timeout/retry/priority
  3. 放宽 test_cases.method/data_type/assertion 的 NOT NULL 约束（v1 API 专用字段，v2 以 steps 为主）
  4. 扩展 test_step_reports 增加 case_execution_id/step_id/step_type/attachments

⚠️ 这是手写的迁移（不是 autogenerate），已考虑 PostgreSQL + SQLite 双兼容：
   - JSON 类型用 sa.JSON()，在 PG 会通过 with_variant 升级为 JSONB
   - 所有 ALTER 都走 batch_alter_table，SQLite 下自动使用"新建-复制-重命名"策略

Revision ID: v2_000001
Revises:
Create Date: 2026-04-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "v2_000001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 跨数据库的 JSON 类型（跟 src/database/base.py 的 JSONType 保持一致）
def _json_type():
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ===================================================================
    # 1. 新建 test_environments
    # ===================================================================
    op.create_table(
        "test_environments",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("project_id", sa.Integer(),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("category", sa.String(20)),
        sa.Column("description", sa.String(255)),
        sa.Column("host", sa.String(255)),
        sa.Column("device_pool", sa.String(64)),
        sa.Column("browser_config", _json_type()),
        sa.Column("variables", _json_type()),
        sa.Column("secrets", _json_type()),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(),
                  server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===================================================================
    # 2. 新建 test_steps（核心！）
    # ===================================================================
    op.create_table(
        "test_steps",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("case_id", sa.Integer(),
                  sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False, index=True),
        sa.Column("skip", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config", _json_type(), nullable=False),
        sa.Column("extract", _json_type()),
        sa.Column("assertion", _json_type()),
        sa.Column("wait_before", sa.Float(), server_default="0"),
        sa.Column("timeout", sa.Integer(), server_default="30"),
        sa.Column("retry", sa.Integer(), server_default="0"),
        sa.Column("on_failure", sa.String(20), server_default="stop"),
    )
    op.create_index("ix_test_steps_case_id", "test_steps", ["case_id"])
    op.create_index("ix_test_steps_step_type", "test_steps", ["step_type"])

    # PG 额外的 GIN 索引（只在 PG 下创建）
    if dialect == "postgresql":
        op.execute("CREATE INDEX idx_test_steps_config_gin ON test_steps USING GIN (config);")

    # ===================================================================
    # 3. 新建 test_variables
    # ===================================================================
    op.create_table(
        "test_variables",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("scope", sa.String(20), nullable=False, index=True),
        sa.Column("scope_id", sa.Integer(), index=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text()),
        sa.Column("secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("description", sa.String(255)),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(),
                  server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("scope", "scope_id", "key", name="uq_variable_scope_key"),
    )

    # ===================================================================
    # 4. 新建 devices
    # ===================================================================
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("udid", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("platform_version", sa.String(32)),
        sa.Column("device_name", sa.String(128)),
        sa.Column("brand", sa.String(64)),
        sa.Column("model", sa.String(128)),
        sa.Column("agent_host", sa.String(128), index=True),
        sa.Column("agent_port", sa.Integer()),
        sa.Column("appium_port", sa.Integer()),
        sa.Column("pool", sa.String(64), server_default="default", index=True),
        sa.Column("status", sa.String(20), server_default="offline", index=True),
        sa.Column("owner_execution_id", sa.Integer(), index=True),
        sa.Column("capabilities", _json_type()),
        sa.Column("tags", _json_type()),
        sa.Column("last_heartbeat", sa.DateTime()),
        sa.Column("create_time", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime(),
                  server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===================================================================
    # 5. 扩展 test_cases（新字段 + 放宽约束）
    # ===================================================================
    with op.batch_alter_table("test_cases") as batch:
        # 新增字段
        batch.add_column(sa.Column("case_type", sa.String(20),
                                   server_default="api", nullable=True))
        batch.add_column(sa.Column("tags", _json_type()))
        batch.add_column(sa.Column("priority", sa.Integer(), server_default="2"))
        batch.add_column(sa.Column("env_id", sa.Integer()))
        batch.add_column(sa.Column("pre_hook", _json_type()))
        batch.add_column(sa.Column("post_hook", _json_type()))
        batch.add_column(sa.Column("variables", _json_type()))
        batch.add_column(sa.Column("timeout", sa.Integer(), server_default="60"))
        batch.add_column(sa.Column("retry", sa.Integer(), server_default="0"))

        # 放宽 v1 遗留字段的 NOT NULL
        batch.alter_column("method", nullable=True)
        batch.alter_column("data_type", nullable=True)
        batch.alter_column("assertion", nullable=True)
        batch.alter_column("skip", nullable=True)   # 避免老数据 NULL 写入失败

    # FK: test_cases.env_id -> test_environments.id
    # （batch_alter_table 里加 FK 有兼容性问题，这里放到 batch 外做；PG 直接 ALTER 就行）
    if dialect != "sqlite":
        op.create_foreign_key(
            "fk_test_cases_env_id",
            source_table="test_cases",
            referent_table="test_environments",
            local_cols=["env_id"],
            remote_cols=["id"],
            ondelete="SET NULL",
        )

    # index 给 case_type 加查询加速
    op.create_index("ix_test_cases_case_type", "test_cases", ["case_type"])

    # ===================================================================
    # 6. 扩展 test_step_reports
    # ===================================================================
    with op.batch_alter_table("test_step_reports") as batch:
        batch.add_column(sa.Column("case_execution_id", sa.Integer()))
        batch.add_column(sa.Column("step_id", sa.Integer()))
        batch.add_column(sa.Column("step_type", sa.String(50)))
        batch.add_column(sa.Column("attachments", _json_type()))

    op.create_index("ix_test_step_reports_case_execution_id",
                    "test_step_reports", ["case_execution_id"])
    op.create_index("ix_test_step_reports_step_id",
                    "test_step_reports", ["step_id"])


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ---- 反向：test_step_reports ----
    op.drop_index("ix_test_step_reports_step_id", "test_step_reports")
    op.drop_index("ix_test_step_reports_case_execution_id", "test_step_reports")
    with op.batch_alter_table("test_step_reports") as batch:
        batch.drop_column("attachments")
        batch.drop_column("step_type")
        batch.drop_column("step_id")
        batch.drop_column("case_execution_id")

    # ---- 反向：test_cases ----
    op.drop_index("ix_test_cases_case_type", "test_cases")
    if dialect != "sqlite":
        op.drop_constraint("fk_test_cases_env_id", "test_cases", type_="foreignkey")
    with op.batch_alter_table("test_cases") as batch:
        # 恢复 NOT NULL（注意：如果已有 NULL 数据会失败，需要先手动清理）
        batch.alter_column("method", nullable=False)
        batch.alter_column("data_type", nullable=False)
        batch.alter_column("assertion", nullable=False)
        batch.alter_column("skip", nullable=False)
        batch.drop_column("retry")
        batch.drop_column("timeout")
        batch.drop_column("variables")
        batch.drop_column("post_hook")
        batch.drop_column("pre_hook")
        batch.drop_column("env_id")
        batch.drop_column("priority")
        batch.drop_column("tags")
        batch.drop_column("case_type")

    # ---- 反向：新表 ----
    op.drop_table("devices")
    op.drop_table("test_variables")

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_test_steps_config_gin;")
    op.drop_index("ix_test_steps_step_type", "test_steps")
    op.drop_index("ix_test_steps_case_id", "test_steps")
    op.drop_table("test_steps")

    op.drop_table("test_environments")
