"""Persist structured editing choices with each project.

Revision ID: 0004_project_edit_options
Revises: 0003_core_tables
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_project_edit_options"
down_revision = "0003_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("edit_options", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("projects", "edit_options")
