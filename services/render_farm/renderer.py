"""
MONTA — Media Renderer (Layer 13)
==================================
Hardware-aware rendering engine. Orchestrates validation, compilation,
process execution with hardware acceleration (Apple VideoToolbox / NVIDIA NVENC),
graceful CPU fallback, and post-render validation.
"""

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from services.edit_executor.compiler import FFmpegCompiler
from services.edit_executor.executor import (
    FFmpegExecutionError,
    FFmpegExecutor,
)
from services.edit_executor.validator import EditPlanValidator
from services.render_farm.output_validator import OutputValidator, RenderResult
from shared.contracts.timeline import TimelineIR
from shared.hardware.detector import HardwareDetector
from shared.hardware.profile import HardwareProfile

logger = logging.getLogger("monta.renderer")


class MediaRenderer:
    """Hardware-aware video rendering engine."""

    def __init__(
        self,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        hardware_profile: Optional[HardwareProfile] = None,
        timeout_s: float = 600.0,
    ):
        self.hardware = hardware_profile or HardwareDetector.detect()
        self.compiler = FFmpegCompiler(ffmpeg_bin=ffmpeg_bin, hardware_profile=self.hardware)
        self.executor = FFmpegExecutor(timeout_s=timeout_s)
        self.validator = EditPlanValidator()
        self.output_validator = OutputValidator(ffprobe_bin=ffprobe_bin)

    async def render(
        self,
        timeline: TimelineIR,
        output_path: str | Path,
        on_progress: Optional[Callable[[float, float, str], None]] = None,
    ) -> RenderResult:
        """
        Executes full rendering pipeline:
        1. Validate Timeline IR & source media
        2. Compile FFmpeg command with hardware acceleration
        3. Execute render (with CPU fallback if hardware encoder fails)
        4. Validate output with ffprobe
        """
        # Step 1: Pre-execution validation
        if on_progress:
            on_progress(0.0, 0.0, "validating")
        validation = self.validator.validate(timeline)
        validation.raise_if_invalid()

        # Step 2: Compile command
        if on_progress:
            on_progress(5.0, 0.0, "compiling")
        cmd = self.compiler.compile(timeline, output_path=output_path, force_cpu=False)

        def exec_progress(pct: float, elapsed: float):
            if on_progress:
                # Map 0-100% of execution to 10-90% of overall render pipeline
                overall_pct = 10.0 + (pct * 0.8)
                on_progress(round(overall_pct, 1), elapsed, "rendering")

        # Step 3: Execute render with automatic fallback
        render_time = 0.0
        used_encoder = cmd.encoder_used
        executed_cmd = cmd

        try:
            render_time = await self.executor.execute(cmd, on_progress=exec_progress)
        except FFmpegExecutionError as e:
            # If hardware encoder failed, retry deterministically with CPU libx264
            if cmd.encoder_used != "libx264":
                logger.warning(
                    "Hardware encoder %s failed. Retrying with CPU libx264 fallback: %s",
                    cmd.encoder_used,
                    e,
                )
                if on_progress:
                    on_progress(10.0, 0.0, "cpu_fallback")
                fallback_cmd = self.compiler.compile(timeline, output_path=output_path, force_cpu=True)
                used_encoder = fallback_cmd.encoder_used
                executed_cmd = fallback_cmd
                render_time = await self.executor.execute(fallback_cmd, on_progress=exec_progress)
            else:
                raise

        # Step 4: Validate output with ffprobe
        if on_progress:
            on_progress(95.0, render_time, "validating_output")
        result = await self.output_validator.validate(
            output_path=output_path,
            timeline=timeline,
            render_time_s=render_time,
            encoder_used=used_encoder,
        )
        result = replace(result, execution_plan={
            "argv": list(executed_cmd.argv),
            "input_files": list(executed_cmd.input_files),
            "output_path": executed_cmd.output_path,
            "expected_duration_s": executed_cmd.expected_duration_s,
            "filter_graph": executed_cmd.filter_graph,
            "encoder_used": used_encoder,
            "used_cpu_fallback": used_encoder != cmd.encoder_used,
        })

        if on_progress:
            on_progress(100.0, render_time, "complete" if result.success else "failed")

        return result
