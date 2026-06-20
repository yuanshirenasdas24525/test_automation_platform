"""add_task_bug_fix_fields

Revision ID: 3f9329fdefae
Revises: bug_version_id_001
Create Date: 2026-05-22 14:05:51.234111+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3f9329fdefae'
down_revision: Union[str, None] = 'bug_version_id_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('fix_description', sa.Text(), nullable=True))
    op.add_column('tasks', sa.Column('fix_commit_sha', sa.String(length=64), nullable=True))
    op.add_column('tasks', sa.Column('fix_commit_branch', sa.String(length=200), nullable=True))
    op.add_column('tasks', sa.Column('fix_suggestion', sa.Text(), nullable=True))
    op.add_column('tasks', sa.Column('fix_agent_used', sa.String(length=50), nullable=True))
    op.add_column('tasks', sa.Column('fix_ai_run_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'tasks', 'ai_runs', ['fix_ai_run_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(None, 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'fix_ai_run_id')
    op.drop_column('tasks', 'fix_agent_used')
    op.drop_column('tasks', 'fix_suggestion')
    op.drop_column('tasks', 'fix_commit_branch')
    op.drop_column('tasks', 'fix_commit_sha')
    op.drop_column('tasks', 'fix_description')
