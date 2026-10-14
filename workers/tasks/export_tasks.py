"""
MONTA — Export Tasks
======================
Background tasks for platform-specific video export and multi-resolution re-encoding.
Uses the real FormatExporter and MultiResRenderer services.
"""

import asyncio
import logging

from services.render_farm.format_exporter import FormatExporter
from services.render_farm.multi_res import MultiResRenderer
from workers.celery_app import app

logger = logging.getLogger("monta.worker.export")


@app.task(bind=True, name="tasks.export_for_platform")
def export_for_platform(self, source_path: str, platform: str, output_dir: str | None = None):
    """Re-encode rendered video for a specific platform.

    Args:
        source_path: Path to the master rendered MP4.
        platform: Platform label (e.g. "instagram_reels", "tiktok").
        output_dir: Output directory. Defaults to same as source.
    """
    self.update_state(state="EXPORTING", meta={"platform": platform, "percent": 0})

    exporter = FormatExporter()
    result = asyncio.run(exporter.export(source_path, platform, output_dir))

    if not result.success:
        raise RuntimeError(f"Platform export failed for {platform}: {result.error_message}")

    return {
        "platform": result.platform,
        "output_path": result.output_path,
        "width": result.width,
        "height": result.height,
        "duration_s": result.duration_s,
        "file_size_bytes": result.file_size_bytes,
        "was_trimmed": result.was_trimmed,
    }


@app.task(bind=True, name="tasks.export_multi_resolution")
def export_multi_resolution(
    self,
    source_path: str,
    resolutions: list[str],
    output_dir: str | None = None,
):
    """Re-encode rendered video at multiple resolutions.

    Args:
        source_path: Path to the master rendered MP4.
        resolutions: List of resolution labels (e.g. ["720p", "1080p"]).
        output_dir: Output directory. Defaults to same as source.
    """
    def on_progress(label: str, percent: float):
        self.update_state(
            state="TRANSCODING",
            meta={"resolution": label, "percent": percent},
        )

    renderer = MultiResRenderer()
    results = asyncio.run(
        renderer.render_all(source_path, resolutions, output_dir, on_progress=on_progress)
    )

    output = {}
    failures = []
    for label, res in results.items():
        if res.success:
            output[label] = {
                "output_path": res.output_path,
                "width": res.width,
                "height": res.height,
                "file_size_bytes": res.file_size_bytes,
            }
        else:
            failures.append(f"{label}: {res.error_message}")

    if failures and not output:
        raise RuntimeError(f"All resolution exports failed: {'; '.join(failures)}")

    return {
        "results": output,
        "failures": failures,
    }
