"""add_project_id_to_config_store

Revision ID: c8753707405a
Revises: 3f9329fdefae
Create Date: 2026-05-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8753707405a'
down_revision: Union[str, None] = '3f9329fdefae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('config_store', sa.Column('project_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'config_store', 'projects', ['project_id'], ['id'])
    op.create_index('ix_config_store_project_id', 'config_store', ['project_id'])

    # 数据迁移：现有全局配置 → "测试平台" 项目
    op.execute("""
        UPDATE config_store
        SET project_id = (SELECT id FROM projects WHERE name = '测试平台' LIMIT 1)
    """)


def downgrade() -> None:
    op.drop_index('ix_config_store_project_id', table_name='config_store')
    op.drop_constraint(None, 'config_store', type_='foreignkey')
    op.drop_column('config_store', 'project_id')
