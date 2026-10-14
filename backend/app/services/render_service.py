"""
MONTA — Render Service
========================
Manages render job lifecycle. Connects to Layer 13 (Render Farm)
with real multi-resolution and platform-specific export support.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional, Sequence

from services.render_farm.format_exporter import (
    ExportResult,
    FormatExporter,
    PLATFORM_SPECS,
)
from services.render_farm.multi_res import (
    MultiResRenderer,
    RESOLUTION_PRESETS,
    ResolutionResult,
)
from services.render_farm.renderer import MediaRenderer
from shared.contracts.timeline import TimelineIR
from shared.exceptions import RenderError

logger = logging.getLogger("monta.render_service")


class RenderService:
    """Service for managing video renders with multi-resolution and
    platform-specific export support.

    Orchestrates the render pipeline:
      Timeline IR → MediaRenderer → master MP4
      master MP4 → MultiResRenderer → resolution variants
      master MP4 → FormatExporter → platform-specific variants
    """

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        timeout_s: float = 600.0,
        output_root: str = "storage/renders",
    ):
        self.renderer = MediaRenderer(
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_s=timeout_s,
        )
        self.multi_res = MultiResRenderer(
            ffmpeg_bin=ffmpeg_bin,
            timeout_s=timeout_s,
        )
        self.exporter = FormatExporter(
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
            timeout_s=timeout_s,
        )
        self.output_root = Path(output_root)

    async def start_render(
        self,
        project_id: str,
        timeline: TimelineIR,
        resolutions: Optional[list[str]] = None,
        platforms: Optional[list[str]] = None,
        on_progress=None,
    ) -> dict:
        """Execute a complete render pipeline.

        1. Render the master output from Timeline IR
        2. Optionally generate resolution variants
        3. Optionally export for platforms

        Args:
            project_id: Unique project identifier.
            timeline: Validated Timeline IR.
            resolutions: Optional list of resolution labels (e.g. ["720p", "4k"]).
            platforms: Optional list of platform labels (e.g. ["instagram_reels"]).
            on_progress: Progress callback(percent, elapsed, stage).

        Returns:
            Dictionary with render results, resolution variants, and platform exports.
        """
        render_id = f"render_{uuid.uuid4().hex[:12]}"
        project_dir = self.output_root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        master_path = project_dir / f"{render_id}_master.mp4"

        # Step 1: Render master
        logger.info("Starting master render for project %s", project_id)
        master_result = await self.renderer.render(
            timeline, output_path=str(master_path), on_progress=on_progress,
        )

        if not master_result.success:
            raise RenderError(f"Master render failed: {master_result.error_message}")

        result = {
            "render_id": render_id,
            "status": "complete",
            "master": {
                "output_path": master_result.output_path,
                "duration_s": master_result.duration_s,
                "width": master_result.width,
                "height": master_result.height,
                "file_size_bytes": master_result.file_size_bytes,
                "render_time_s": master_result.render_time_s,
                "encoder_used": master_result.encoder_used,
                "warnings": list(master_result.warnings),
            },
            "resolutions": {},
            "exports": {},
        }

        # Step 2: Multi-resolution variants
        if resolutions:
            logger.info("Generating %d resolution variants", len(resolutions))
            res_dir = project_dir / "resolutions"
            res_results = await self.multi_res.render_all(
                str(master_path), resolutions, output_dir=str(res_dir),
            )
            for label, res in res_results.items():
                result["resolutions"][label] = {
                    "output_path": res.output_path,
                    "width": res.width,
                    "height": res.height,
                    "file_size_bytes": res.file_size_bytes,
                    "success": res.success,
                    "error_message": res.error_message,
                }

        # Step 3: Platform exports
        if platforms:
            logger.info("Exporting for %d platforms", len(platforms))
            export_dir = project_dir / "exports"
            export_results = await self.exporter.export_all(
                str(master_path), platforms, output_dir=str(export_dir),
            )
            for platform, exp in export_results.items():
                result["exports"][platform] = {
                    "output_path": exp.output_path,
                    "width": exp.width,
                    "height": exp.height,
                    "duration_s": exp.duration_s,
                    "file_size_bytes": exp.file_size_bytes,
                    "success": exp.success,
                    "was_trimmed": exp.was_trimmed,
                    "error_message": exp.error_message,
                }

        return result

    async def get_progress(self, render_id: str) -> dict:
        """Get current render progress (for non-Celery usage)."""
        return {"render_id": render_id, "status": "unknown"}

    async def cancel_render(self, render_id: str) -> None:
        """Cancel an in-progress render."""
        logger.warning("Cancel requested for render %s (not implemented for sync renders)", render_id)

    async def export_for_platform(self, source_path: str, platform: str) -> ExportResult:
        """Re-encode rendered video for a specific platform."""
        return await self.exporter.export(source_path, platform)

    @staticmethod
    def available_platforms() -> list[str]:
        """Return available platform export labels."""
        return list(PLATFORM_SPECS.keys())

    @staticmethod
    def available_resolutions() -> list[str]:
        """Return available resolution labels."""
        return list(RESOLUTION_PRESETS.keys())
