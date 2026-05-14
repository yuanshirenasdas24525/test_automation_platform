"""ai_case_drafts 新表 + test_cases 加 4 列（M7 AI 用例草稿）

M7 AI 测试用例一键生成：AI 产出落 ai_case_drafts(status=pending) 草稿区，PM
review/edit/选中后 batch_commit 才写 test_cases。test_cases 同步加：
  - source：留痕草稿来源（manual / ai_m7）
  - draft_id：可反查源草稿
  - requirement_id：直接关联需求，避免后续 case ↔ requirement 反查 module
  - business_steps：结构化业务步骤（M8 反推 UI 自动化 step 的入参）

Revision ID: ai_m7_001
Revises: 565bb2dfd337
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ai_m7_001"
down_revision: Union[str, None] = "565bb2dfd337"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── 1) ai_case_drafts 主表 ──────────────────────────────────────
    op.create_table(
        "ai_case_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "analysis_document_id",
            sa.Integer(),
            sa.ForeignKey("requirement_analysis_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ai_run_id",
            sa.Integer(),
            sa.ForeignKey("ai_runs.id"),
            nullable=True,
        ),
        # 同一次触发批次的 uuid（前端按 batch 分 tab）
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        # provider / model 留痕（直接走字符串，跟 ai_runs.model 对齐）
        sa.Column("model_label", sa.String(length=100), nullable=True),

        # 用例字段（functional 用例的草稿态）
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("preconditions", sa.Text(), nullable=True),
        sa.Column("steps_text", sa.Text(), nullable=True),
        sa.Column("expected", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="2", nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),

        # M8 衔接：step_template[i] = {step_type, hint, needs_ui_detail}
        sa.Column("step_template", sa.JSON(), nullable=True),

        # 是否标记"需 UI 细节"（前端高亮，PM 二次补图后可单独重跑）
        sa.Column(
            "needs_ui_detail",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        # PM 上传 UI 截图的 attachment.id 数组（vision/OCR 走的就是这批）
        sa.Column("ui_image_refs", sa.JSON(), nullable=True),

        # 状态：pending / accepted / rejected
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        # 入库后回写：指向 test_cases.id
        sa.Column(
            "committed_case_id",
            sa.Integer(),
            sa.ForeignKey("test_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_case_drafts_requirement_id",
        "ai_case_drafts",
        ["requirement_id"],
    )
    op.create_index(
        "ix_ai_case_drafts_batch_id",
        "ai_case_drafts",
        ["batch_id"],
    )
    op.create_index(
        "ix_ai_case_drafts_status",
        "ai_case_drafts",
        ["status"],
    )
    op.create_index(
        "ix_ai_case_drafts_req_status_created",
        "ai_case_drafts",
        ["requirement_id", "status", "created_at"],
    )
    op.create_index(
        "ix_ai_case_drafts_committed_case_id",
        "ai_case_drafts",
        ["committed_case_id"],
    )

    # ─── 2) test_cases 加 4 列 ────────────────────────────────────────
    # 注意：SQLite ALTER TABLE 限制，FK 用 batch_alter_table 包一层，PG 也兼容
    with op.batch_alter_table("test_cases") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=20),
                server_default="manual",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("draft_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("requirement_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("business_steps", sa.JSON(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_test_cases_draft_id",
            "ai_case_drafts",
            ["draft_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_test_cases_requirement_id",
            "requirements",
            ["requirement_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_test_cases_source",
        "test_cases",
        ["source"],
    )
    op.create_index(
        "ix_test_cases_requirement_id",
        "test_cases",
        ["requirement_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_cases_requirement_id", table_name="test_cases")
    op.drop_index("ix_test_cases_source", table_name="test_cases")
    with op.batch_alter_table("test_cases") as batch_op:
        batch_op.drop_constraint("fk_test_cases_requirement_id", type_="foreignkey")
        batch_op.drop_constraint("fk_test_cases_draft_id", type_="foreignkey")
        batch_op.drop_column("business_steps")
        batch_op.drop_column("requirement_id")
        batch_op.drop_column("draft_id")
        batch_op.drop_column("source")

    op.drop_index("ix_ai_case_drafts_committed_case_id", table_name="ai_case_drafts")
    op.drop_index("ix_ai_case_drafts_req_status_created", table_name="ai_case_drafts")
    op.drop_index("ix_ai_case_drafts_status", table_name="ai_case_drafts")
    op.drop_index("ix_ai_case_drafts_batch_id", table_name="ai_case_drafts")
    op.drop_index("ix_ai_case_drafts_requirement_id", table_name="ai_case_drafts")
    op.drop_table("ai_case_drafts")
