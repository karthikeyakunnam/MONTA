"""Calibration decision and outcome logs.

Revision ID: 0002_calibration
Revises: 0001_narrative_memory
Create Date: 2026-09-22

Additive: two append-only tables. Outcomes are joined to decisions on
decision_id; the latest outcome per decision wins.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_calibration"
down_revision = "0001_narrative_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_log",
        sa.Column("decision_id", sa.String(32), primary_key=True),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column("predicted_value", sa.String(256), nullable=False),
        sa.Column("raw_confidence", sa.Float, nullable=False),
        sa.Column("calibrated_confidence", sa.Float, nullable=False),
        sa.Column("versions", sa.String(512), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_log_component_field", "decision_log", ["component", "field", "created_at"])
    op.create_table(
        "outcome_log",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("decision_id", sa.String(32), nullable=False),
        sa.Column("correct", sa.Boolean, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("observed_value", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outcome_log_decision", "outcome_log", ["decision_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_outcome_log_decision", table_name="outcome_log")
    op.drop_table("outcome_log")
    op.drop_index("ix_decision_log_component_field", table_name="decision_log")
    op.drop_table("decision_log")
