"""
MONTA — Render Worker Tasks
=============================
Background task owning video rendering (Layer 13) via MediaRenderer and FFmpeg.
"""

import asyncio
import logging
from pathlib import Path

from services.render_farm.renderer import MediaRenderer
from shared.contracts.timeline import TimelineIR
from workers.celery_app import app

logger = logging.getLogger("monta.worker.render")


@app.task(bind=True, name="tasks.render_video")
def render_video(
    self,
    timeline_dict: dict,
    output_path: str,
):
    """Executes deterministic video render from serialized Timeline IR."""
    timeline = TimelineIR.model_validate(timeline_dict)

    def on_progress(percent: float, elapsed_s: float, stage: str):
        self.update_state(
            state="RENDERING",
            meta={
                "percent": percent,
                "elapsed_s": elapsed_s,
                "stage": stage,
            },
        )

    renderer = MediaRenderer()
    result = asyncio.run(
        renderer.render(
            timeline=timeline,
            output_path=output_path,
            on_progress=on_progress,
        )
    )

    if not result.success:
        raise RuntimeError(f"Render failed: {result.error_message}")

    return {
        "status": "complete",
        "output_path": result.output_path,
        "duration_s": result.duration_s,
        "width": result.width,
        "height": result.height,
        "file_size_bytes": result.file_size_bytes,
        "render_time_s": result.render_time_s,
        "encoder_used": result.encoder_used,
        "warnings": list(result.warnings),
    }
