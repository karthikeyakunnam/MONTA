"""Core application tables: users, projects, clips, jobs, timelines, renders.

Revision ID: 0003_core_tables
Revises: 0002_calibration
Create Date: 2026-09-27

The ORM models existed from the scaffold but no migration ever created their
tables. This revision creates all six.

Choices worth noting:
* ``status``/``state`` are ``VARCHAR`` with CHECK constraints rather than PostgreSQL
  ENUM types, so adding a state is a code change, not an ``ALTER TYPE`` on a live table.
* ``clips`` has ``UNIQUE(project_id, sha256)``: the database, not application code, is
  what finally guarantees a project cannot hold the same bytes twice under a race.
* Every child table cascades from ``projects``, so deleting a project cannot orphan rows.
* Indexes match the real access paths: clips by project, clips by hash (dedupe),
  jobs by project and by state (operator queue views).
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_core_tables"
down_revision = "0002_calibration"
branch_labels = None
depends_on = None

PROJECT_STATUSES = ("draft", "uploading", "queued", "analyzing", "story_building", "rendering", "complete", "failed")
CLIP_STATUSES = ("uploaded", "analyzing", "analyzed", "rejected", "failed")
JOB_STATES = ("pending", "queued", "running", "succeeded", "failed", "submit_failed")
RENDER_STATUSES = ("queued", "processing", "complete", "failed", "cancelled")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("preferred_style", sa.String(32), nullable=True),
        sa.Column("preferred_color_grade", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("status_detail", sa.Text, nullable=True),
        sa.Column("raw_prompt", sa.Text, nullable=True),
        sa.Column("parsed_intent", sa.JSON, nullable=True),
        sa.Column("context", sa.JSON, nullable=True),
        sa.Column("story_plan", sa.JSON, nullable=True),
        sa.Column("story_judgement", sa.JSON, nullable=True),
        sa.Column("target_platform", sa.String(32), nullable=False, server_default="instagram"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("status", PROJECT_STATUSES), name="ck_projects_status"),
    )
    op.create_index("ix_projects_user_created", "projects", ["user_id", "created_at"])

    op.create_table(
        "clips",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploaded"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("duplicate_of", sa.String(64), sa.ForeignKey("clips.id", ondelete="SET NULL"), nullable=True),
        sa.Column("container", sa.String(64), nullable=True),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("video_codec", sa.String(32), nullable=True),
        sa.Column("fps", sa.Float, nullable=True),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("resolution", sa.String(16), nullable=True),
        sa.Column("resolution_class", sa.String(8), nullable=True),
        sa.Column("duration", sa.Float, nullable=True),
        sa.Column("bitrate", sa.Integer, nullable=True),
        sa.Column("file_size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("has_audio", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("audio_streams", sa.JSON, nullable=True),
        sa.Column("rejection_issues", sa.JSON, nullable=True),
        sa.Column("scene_tags", sa.JSON, nullable=True),
        sa.Column("actions", sa.JSON, nullable=True),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("quality_details", sa.JSON, nullable=True),
        sa.Column("emotion_tags", sa.JSON, nullable=True),
        sa.Column("intelligence", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("status", CLIP_STATUSES), name="ck_clips_status"),
        sa.CheckConstraint("file_size_bytes >= 0", name="ck_clips_size_nonnegative"),
        sa.UniqueConstraint("project_id", "sha256", name="uq_clips_project_sha256"),
    )
    op.create_index("ix_clips_project_created", "clips", ["project_id", "created_at"])
    op.create_index("ix_clips_sha256", "clips", ["sha256"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="pipeline"),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("state", JOB_STATES), name="ck_jobs_state"),
    )
    op.create_index("ix_jobs_project_created", "jobs", ["project_id", "created_at"])
    op.create_index("ix_jobs_state", "jobs", ["state"])

    op.create_table(
        "timelines",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("story_acts", sa.JSON, nullable=True),
        sa.Column("style_config", sa.JSON, nullable=True),
        sa.Column("entries", sa.JSON, nullable=True),
        sa.Column("total_duration", sa.Float, nullable=True),
        sa.Column("critic_score", sa.Float, nullable=True),
        sa.Column("critic_feedback", sa.JSON, nullable=True),
        sa.Column("revision_count", sa.Float, nullable=True, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "renders",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("resolution", sa.String(16), nullable=False),
        sa.Column("format", sa.String(16), nullable=False, server_default="mp4"),
        sa.Column("output_path", sa.String(512), nullable=True),
        sa.Column("file_size_bytes", sa.Float, nullable=True),
        sa.Column("duration", sa.Float, nullable=True),
        sa.Column("progress_percent", sa.Float, nullable=True, server_default="0"),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.Column("platform", sa.String(32), nullable=True),
        sa.Column("platform_config", sa.JSON, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", RENDER_STATUSES), name="ck_renders_status"),
    )
    op.create_index("ix_renders_project_created", "renders", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_renders_project_created", table_name="renders")
    op.drop_table("renders")
    op.drop_table("timelines")
    op.drop_index("ix_jobs_state", table_name="jobs")
    op.drop_index("ix_jobs_project_created", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_clips_sha256", table_name="clips")
    op.drop_index("ix_clips_project_created", table_name="clips")
    op.drop_table("clips")
    op.drop_index("ix_projects_user_created", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
