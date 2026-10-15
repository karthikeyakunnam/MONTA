"""Persist prompt revisions and associate renders with their source job.

Revision ID: 0005_prompt_revisions
Revises: 0004_project_edit_options
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_prompt_revisions"
down_revision = "0004_project_edit_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("prompt_version", sa.Integer(), nullable=False, server_default="1"))

    op.add_column("jobs", sa.Column("prompt_version", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("prompt_snapshot", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("target_platform_snapshot", sa.String(32), nullable=True))
    op.add_column("jobs", sa.Column("edit_options_snapshot", sa.JSON(), nullable=True))
    op.add_column("jobs", sa.Column("idempotency_key", sa.String(96), nullable=True))
    op.add_column("jobs", sa.Column("retry_of_job_id", sa.String(64), nullable=True))
    op.create_index("uq_jobs_project_idempotency", "jobs", ["project_id", "idempotency_key"], unique=True)

    # Past prompts/options were not versioned. Preserve the best available snapshot
    # from the existing project row; all new jobs are captured immutably at submission.
    op.execute(sa.text("""
        UPDATE jobs
        SET prompt_version = 1,
            prompt_snapshot = (SELECT raw_prompt FROM projects WHERE projects.id = jobs.project_id),
            target_platform_snapshot = (SELECT target_platform FROM projects WHERE projects.id = jobs.project_id),
            edit_options_snapshot = (SELECT edit_options FROM projects WHERE projects.id = jobs.project_id)
    """))

    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_state", type_="check")
        batch.create_check_constraint(
            "ck_jobs_state",
            "state IN ('pending', 'queued', 'running', 'succeeded', 'failed', 'submit_failed', 'cancelled')",
        )

    with op.batch_alter_table("renders") as batch:
        batch.add_column(
            sa.Column("job_id", sa.String(64),
                      sa.ForeignKey("jobs.id", ondelete="SET NULL", name="fk_renders_job_id_jobs"), nullable=True)
        )
    op.create_index("ix_renders_job", "renders", ["job_id"])

    # Link existing worker-produced files by their unique job suffix where possible.
    bind = op.get_bind()
    jobs = bind.execute(sa.text("SELECT id, project_id FROM jobs")).all()
    renders = bind.execute(sa.text("SELECT id, project_id, output_path FROM renders WHERE job_id IS NULL")).all()
    for job_id, project_id in jobs:
        # Worker filenames end in `_job_<hex>.mp4`, while the row's id is
        # `job_<hex>`. Compare basenames in Python: SQL LIKE treats underscores
        # as wildcards and the old `_job_{job_id}` spelling doubled `job_`.
        suffix = f"_{job_id}.mp4"
        matching = [render_id for render_id, render_project_id, path in renders
                    if render_project_id == project_id and path and path.rsplit("/", 1)[-1].endswith(suffix)]
        for render_id in matching:
            bind.execute(sa.text("UPDATE renders SET job_id = :job_id WHERE id = :render_id"),
                         {"job_id": job_id, "render_id": render_id})


def downgrade() -> None:
    op.drop_index("ix_renders_job", table_name="renders")
    op.drop_column("renders", "job_id")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_state", type_="check")
        batch.create_check_constraint(
            "ck_jobs_state",
            "state IN ('pending', 'queued', 'running', 'succeeded', 'failed', 'submit_failed')",
        )
    op.drop_index("uq_jobs_project_idempotency", table_name="jobs")
    op.drop_column("jobs", "retry_of_job_id")
    op.drop_column("jobs", "idempotency_key")
    op.drop_column("jobs", "edit_options_snapshot")
    op.drop_column("jobs", "target_platform_snapshot")
    op.drop_column("jobs", "prompt_snapshot")
    op.drop_column("jobs", "prompt_version")
    op.drop_column("projects", "prompt_version")
