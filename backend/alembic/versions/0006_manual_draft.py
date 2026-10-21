"""Persist manual edits before a render is submitted.

Revision ID: 0006_manual_draft
Revises: 0005_prompt_revisions
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_manual_draft"
down_revision = "0005_prompt_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("manual_draft", sa.JSON(), nullable=True))
    op.add_column("projects", sa.Column("manual_draft_revision", sa.Integer(), nullable=False,
                                        server_default="0"))


def downgrade() -> None:
    op.drop_column("projects", "manual_draft_revision")
    op.drop_column("projects", "manual_draft")
