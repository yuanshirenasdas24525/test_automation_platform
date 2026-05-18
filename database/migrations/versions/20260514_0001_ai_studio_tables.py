"""AI Studio M1 —— 对话写需求 + AI 下发编码：4 张新表 + 2 张老表加字段

新表：
  1. ai_dialogue_sessions    —— 对话会话（PM 跟模型澄清需求的过程）
  2. ai_requirement_drafts   —— 对话产出的需求草稿（PM review 后入 requirements）
  3. coding_tasks            —— AI 编码任务（一条需求 → 一份 diff → 临时分支）
  4. code_chunks             —— RAG 索引（项目代码片段 + embedding）

老表改动（用 batch_alter_table 兼容 SQLite ALTER 限制；PG 也能正常跑）：
  5. projects 加 6 列：git_url / git_default_branch / git_auth_type /
       git_auth_secret_encrypted / rag_indexed_at / rag_index_status
  6. requirements 加 2 列：spec_json / source_dialogue_session_id

embedding 列暂用 sa.JSON() —— 等第 3 批 RAG 链路实施时切到 PG + pgvector 时
单独再做一次 ALTER COLUMN TYPE vector(1536) USING ... 的类型迁移。第 1 批
建表 + 后续 dialogue / coding_task 链路都不需要写 embedding。

Revision ID: ai_studio_m1_001
Revises: ai_m7_001
Create Date: 2026-05-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ai_studio_m1_001"
down_revision: Union[str, None] = "ai_m7_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── 1) ai_dialogue_sessions ─────────────────────────────────────────
    op.create_table(
        "ai_dialogue_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
        sa.Column("turns", sa.JSON(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=True),
        sa.Column("model_name", sa.String(length=80), nullable=True),
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
        "ix_ai_dialogue_sessions_project_id",
        "ai_dialogue_sessions",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_dialogue_sessions_user_id",
        "ai_dialogue_sessions",
        ["user_id"],
    )
    op.create_index(
        "ix_ai_dialogue_sessions_status",
        "ai_dialogue_sessions",
        ["status"],
    )

    # ─── 2) ai_requirement_drafts ────────────────────────────────────────
    op.create_table(
        "ai_requirement_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("ai_dialogue_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending_review",
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ai_run_id",
            sa.Integer(),
            sa.ForeignKey("ai_runs.id"),
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
        "ix_ai_requirement_drafts_session_id",
        "ai_requirement_drafts",
        ["session_id"],
    )
    op.create_index(
        "ix_ai_requirement_drafts_status",
        "ai_requirement_drafts",
        ["status"],
    )
    op.create_index(
        "ix_ai_requirement_drafts_requirement_id",
        "ai_requirement_drafts",
        ["requirement_id"],
    )

    # ─── 3) coding_tasks ─────────────────────────────────────────────────
    op.create_table(
        "coding_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_requirement_id",
            sa.Integer(),
            sa.ForeignKey("requirements.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "sequence",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=80), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("temp_branch", sa.String(length=200), nullable=True),
        sa.Column("diff_blob", sa.Text(), nullable=True),
        sa.Column("accepted_hunks", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "ai_run_id",
            sa.Integer(),
            sa.ForeignKey("ai_runs.id"),
            nullable=True,
        ),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
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
        "ix_coding_tasks_requirement_id",
        "coding_tasks",
        ["requirement_id"],
    )
    op.create_index(
        "ix_coding_tasks_parent_requirement_id",
        "coding_tasks",
        ["parent_requirement_id"],
    )
    op.create_index(
        "ix_coding_tasks_project_id",
        "coding_tasks",
        ["project_id"],
    )
    op.create_index(
        "ix_coding_tasks_status",
        "coding_tasks",
        ["status"],
    )

    # ─── 4) code_chunks ──────────────────────────────────────────────────
    # embedding 列暂用 JSON 占位，等第 3 批 RAG 切 pgvector 时再 ALTER TYPE
    op.create_table(
        "code_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("git_sha", sa.String(length=40), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "project_id",
            "git_sha",
            "file_path",
            "chunk_idx",
            name="uq_code_chunks_project_sha_path_idx",
        ),
    )
    op.create_index(
        "ix_code_chunks_project_id",
        "code_chunks",
        ["project_id"],
    )
    op.create_index(
        "ix_code_chunks_git_sha",
        "code_chunks",
        ["git_sha"],
    )

    # ─── 5) projects 加 6 列 ─────────────────────────────────────────────
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("git_url", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column(
                "git_default_branch",
                sa.String(length=80),
                server_default="main",
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("git_auth_type", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("git_auth_secret_encrypted", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("rag_indexed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "rag_index_status",
                sa.String(length=20),
                server_default="pending",
                nullable=True,
            )
        )

    # ─── 6) requirements 加 2 列 ────────────────────────────────────────
    # 注意：source_dialogue_session_id 的 FK 依赖 ai_dialogue_sessions 表，
    # 必须放在 #1 之后；放迁移末尾即可
    with op.batch_alter_table("requirements") as batch_op:
        batch_op.add_column(sa.Column("spec_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("source_dialogue_session_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_requirements_source_dialogue_session",
            "ai_dialogue_sessions",
            ["source_dialogue_session_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_requirements_source_dialogue_session_id",
        "requirements",
        ["source_dialogue_session_id"],
    )


def downgrade() -> None:
    # 反序：先撤老表改动，再 drop 新表
    op.drop_index(
        "ix_requirements_source_dialogue_session_id",
        table_name="requirements",
    )
    with op.batch_alter_table("requirements") as batch_op:
        batch_op.drop_constraint(
            "fk_requirements_source_dialogue_session", type_="foreignkey"
        )
        batch_op.drop_column("source_dialogue_session_id")
        batch_op.drop_column("spec_json")

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("rag_index_status")
        batch_op.drop_column("rag_indexed_at")
        batch_op.drop_column("git_auth_secret_encrypted")
        batch_op.drop_column("git_auth_type")
        batch_op.drop_column("git_default_branch")
        batch_op.drop_column("git_url")

    op.drop_index("ix_code_chunks_git_sha", table_name="code_chunks")
    op.drop_index("ix_code_chunks_project_id", table_name="code_chunks")
    op.drop_table("code_chunks")

    op.drop_index("ix_coding_tasks_status", table_name="coding_tasks")
    op.drop_index("ix_coding_tasks_project_id", table_name="coding_tasks")
    op.drop_index("ix_coding_tasks_parent_requirement_id", table_name="coding_tasks")
    op.drop_index("ix_coding_tasks_requirement_id", table_name="coding_tasks")
    op.drop_table("coding_tasks")

    op.drop_index(
        "ix_ai_requirement_drafts_requirement_id",
        table_name="ai_requirement_drafts",
    )
    op.drop_index("ix_ai_requirement_drafts_status", table_name="ai_requirement_drafts")
    op.drop_index(
        "ix_ai_requirement_drafts_session_id",
        table_name="ai_requirement_drafts",
    )
    op.drop_table("ai_requirement_drafts")

    op.drop_index("ix_ai_dialogue_sessions_status", table_name="ai_dialogue_sessions")
    op.drop_index("ix_ai_dialogue_sessions_user_id", table_name="ai_dialogue_sessions")
    op.drop_index("ix_ai_dialogue_sessions_project_id", table_name="ai_dialogue_sessions")
    op.drop_table("ai_dialogue_sessions")
