"""seed isolated script functions

Revision ID: seed_script_functions_001
Revises: script_runtime_isolation_001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from database.script_seeds import GLOBAL_SCRIPT_SEEDS


revision = "seed_script_functions_001"
down_revision = "script_runtime_isolation_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    table = sa.table(
        "script_store",
        sa.column("name", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("code", sa.Text()),
        sa.column("enabled", sa.Boolean()),
        sa.column("project_id", sa.Integer()),
        sa.column("description", sa.String()),
        sa.column("requirements", sa.JSON()),
    )
    for item in GLOBAL_SCRIPT_SEEDS:
        exists = connection.execute(
            sa.select(sa.literal(1)).select_from(table).where(
                table.c.name == item["name"],
                table.c.kind == "function",
                table.c.project_id.is_(None),
            ).limit(1)
        ).first()
        if exists is None:
            connection.execute(table.insert().values(
                name=item["name"],
                kind="function",
                code=item["code"],
                enabled=True,
                project_id=None,
                description=item["description"],
                requirements=[],
            ))


def downgrade() -> None:
    names = [item["name"] for item in GLOBAL_SCRIPT_SEEDS]
    table = sa.table(
        "script_store",
        sa.column("name", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("project_id", sa.Integer()),
    )
    op.get_bind().execute(
        table.delete().where(
            table.c.project_id.is_(None),
            table.c.kind == "function",
            table.c.name.in_(names),
        )
    )
