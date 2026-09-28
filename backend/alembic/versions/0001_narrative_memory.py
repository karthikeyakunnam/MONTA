"""Narrative memory and user style preferences (Layers 4/7/14).

Revision ID: 0001_narrative_memory
Revises:
Create Date: 2026-09-21

Additive only: creates two new tables, touches nothing existing. Safe to run
online. ``success_index`` is denormalized so pattern aggregation is a single
indexed GROUP BY rather than a per-row computation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_narrative_memory"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "narrative_memory",
        sa.Column("record_id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("project_type", sa.String(32), nullable=False),
        sa.Column("story_pattern", sa.String(64), nullable=False),
        sa.Column("pace", sa.String(16), nullable=True),
        sa.Column("emotion", sa.String(32), nullable=True),
        sa.Column("engagement_score", sa.Float, nullable=False),
        sa.Column("completion_rate", sa.Float, nullable=False),
        sa.Column("user_rating", sa.Float, nullable=False),
        sa.Column("success_index", sa.Float, nullable=False),
        sa.Column("successful_elements", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("engagement_score BETWEEN 0 AND 10", name="ck_nm_engagement_range"),
        sa.CheckConstraint("completion_rate BETWEEN 0 AND 10", name="ck_nm_completion_range"),
        sa.CheckConstraint("user_rating BETWEEN 0 AND 10", name="ck_nm_rating_range"),
        sa.CheckConstraint("success_index BETWEEN 0 AND 1", name="ck_nm_success_range"),
    )
    op.create_index("ix_narrative_memory_type_pattern", "narrative_memory", ["project_type", "story_pattern"])
    op.create_index("ix_narrative_memory_user_created", "narrative_memory", ["user_id", "created_at"])
    op.create_index("ix_narrative_memory_type_success", "narrative_memory", ["project_type", "success_index"])

    op.create_table(
        "user_style_preferences",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("preferences", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_style_preferences")
    op.drop_index("ix_narrative_memory_type_success", table_name="narrative_memory")
    op.drop_index("ix_narrative_memory_user_created", table_name="narrative_memory")
    op.drop_index("ix_narrative_memory_type_pattern", table_name="narrative_memory")
    op.drop_table("narrative_memory")
