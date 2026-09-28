"""
MONTA — Celery Application
=============================
Celery app configuration for background task processing.
"""

import os
# pyrefly: ignore [missing-import]
from celery import Celery

# Load broker URL from environment
BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

app = Celery(
    "monta",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=[
        "workers.tasks.pipeline_tasks",
        "workers.tasks.render_tasks",
        "workers.tasks.export_tasks",
    ],
)

app.conf.beat_schedule = {
    # Interrupted uploads leave temp files in storage/.incoming; clear them hourly.
    "sweep-incoming-uploads": {"task": "tasks.sweep_incoming", "schedule": 3600.0, "kwargs": {"older_than_s": 3600}},
}

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
)
