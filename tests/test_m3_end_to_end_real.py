"""
MONTA — Milestone 3 Primary Acceptance Test
============================================
Executes the complete, end-to-end local vertical slice:
Real MP4 footage + Natural-language prompt
  → Prompt Intelligence (Local Ollama LLM)
  → Immutable ContextPack
  → Video Intelligence (Deterministic Signals + Keyframes)
  → Story Architect
  → Timeline IR Translation
  → Edit Plan Validation
  → FFmpeg Compilation
  → Hardware-Aware Media Rendering
  → Output ffprobe Validation
  → Frame Decoding Playability Audit

NO MOCKS in the primary pipeline path. 100% offline, local execution.
"""

import json
import os
from pathlib import Path
import subprocess
import pytest

from orchestration.pipeline import build_pipeline
from shared.contracts.clip import ClipSource
from shared.contracts.timeline import TimelineIR
from shared.local_runtime.adapters.ollama import OllamaRuntime
from shared.providers.base import GenerationConfig, Message
from shared.providers.registry import ProviderBundle, ProviderSettings, build_providers


# This is an explicit acceptance test, not a mocked or degraded test path. It
# is excluded from the default suite and must be invoked with `pytest -m live`
# on a host where Ollama and the named local model are already available.
pytestmark = pytest.mark.live


def _generate_test_clip(path: Path, duration_s: float, color: str, freq: int) -> Path:
    """Creates a real valid H.264/AAC MP4 clip with motion and audio."""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=640x360:d={duration_s}:r=30",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration_s}:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(path),
    ]
    subprocess.check_call(cmd)
    return path


@pytest.fixture(scope="module")
def real_video_dataset(tmp_path_factory):
    media_dir = tmp_path_factory.mktemp("m3_media")
    clip1 = _generate_test_clip(media_dir / "clip_intro_calm.mp4", duration_s=6.0, color="blue", freq=330)
    clip2 = _generate_test_clip(media_dir / "clip_build_action.mp4", duration_s=6.0, color="orange", freq=550)
    clip3 = _generate_test_clip(media_dir / "clip_climax_epic.mp4", duration_s=6.0, color="red", freq=880)
    return [
        ClipSource(clip_id="c_intro", path=str(clip1)),
        ClipSource(clip_id="c_build", path=str(clip2)),
        ClipSource(clip_id="c_climax", path=str(clip3)),
    ]


def _live_settings() -> ProviderSettings:
    """Use explicit test model names, never a simulated or automatic fallback."""
    text_model = os.environ.get("MONTA_TEST_OLLAMA_TEXT_MODEL", "qwen2.5:7b")
    vision_model = os.environ.get("MONTA_TEST_OLLAMA_VISION_MODEL", "")
    return ProviderSettings(
        monta_offline_mode=True,
        monta_text_providers="ollama",
        monta_vision_providers="ollama_vl" if vision_model else "",
        ollama_base_url=os.environ.get("MONTA_TEST_OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_text_model=text_model,
        ollama_vision_model=vision_model or "qwen2-vl:7b",
    )


async def _verify_live_ollama(settings: ProviderSettings) -> None:
    """Fail closed unless real local JSON inference succeeds before the pipeline starts."""
    runtime = OllamaRuntime(base_url=settings.ollama_base_url)
    try:
        health = await runtime.check_health()
        assert health.is_healthy, f"Ollama is unavailable at {settings.ollama_base_url}: {health.error_message}"
        assert settings.ollama_text_model in health.installed_models, (
            f"Required text model '{settings.ollama_text_model}' is not installed; "
            f"available: {', '.join(health.installed_models) or '(none)'}"
        )
        if settings.monta_vision_providers:
            assert settings.ollama_vision_model in health.installed_models, (
                f"Required vision model '{settings.ollama_vision_model}' is not installed; "
                f"available: {', '.join(health.installed_models)}"
            )
        response = await runtime.generate_chat(
            [Message.user('Return exactly JSON: {"status":"ok"}')],
            settings.ollama_text_model,
            GenerationConfig(temperature=0.0, json_output=True, timeout_s=30.0),
        )
        assert json.loads(response.text).get("status") == "ok"
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_m3_end_to_end_local_vertical_slice(real_video_dataset, tmp_path: Path):
    """
    The Primary M3 Milestone Verification:
    Full pipeline execution from natural language prompt to rendered MP4 video.
    """
    output_mp4 = tmp_path / "m3_final_monta_edit.mp4"

    prompt = (
        "Make a cinematic travel reel. Start calm with the blue scenery, "
        "build up energy in the middle, and finish with the most epic red climax shot."
    )

    # 1. Require a reachable Ollama daemon, an installed model, and real local
    # structured inference. No lexical fallback can satisfy this acceptance test.
    settings = _live_settings()
    await _verify_live_ollama(settings)
    providers = build_providers(settings)
    assert providers.is_offline

    # 2. Build production MontaPipeline instance
    pipeline = build_pipeline(providers=providers)

    # 3. Execute End-to-End Pipeline
    try:
        result = await pipeline.run(
            project_id="m3_acceptance_project",
            user_id="creator_001",
            prompt=prompt,
            clips=real_video_dataset,
        )

        # --- Verification of Brain & Planning ---
        assert result.intent is not None
        assert any(extractor.startswith("llm:") for extractor in result.intent.extractors)
        assert not any(reason.startswith("LLM extraction failed:") for reason in result.intent.degraded)
        assert result.context_pack is not None
        assert len(result.context_pack.clip_intelligence) == 3
        if settings.monta_vision_providers:
            assert all(c.provenance.vision_model for c in result.context_pack.clip_intelligence.values())
            assert all(not any(reason.startswith("vision failed:") for reason in c.provenance.degraded)
                       for c in result.context_pack.clip_intelligence.values())
        else:
            assert all("no vision provider configured" in c.provenance.degraded
                       for c in result.context_pack.clip_intelligence.values())

        # Verify Story Plan was constructed from the analysis, not a fixture.
        assert result.story is not None
        assert len(result.story.timeline) >= 2
        assert result.story.total_duration_s > 0

        print(f"\n[M3 Brain] Story Pattern Selected: {result.story.story_pattern}")
        print(f"[M3 Brain] Story Cuts Planned: {len(result.story.timeline)}")
        print(f"[M3 Brain] Total Story Planned Duration: {result.story.total_duration_s:.1f}s")

        # 4. Translate the real StoryPlan; this includes M2 validation and must
        # preserve exact selection/order/source ranges from the architect.
        timeline_ir = pipeline.generate_timeline_ir(result.story, result.context_pack)
        assert isinstance(timeline_ir, TimelineIR)
        assert len(timeline_ir.video_segments) == len(result.story.timeline)
        assert timeline_ir.duration_s == pytest.approx(result.story.total_duration_s, abs=0.001)
        assert [segment.clip_id for segment in timeline_ir.video_segments] == [cut.clip_id for cut in result.story.timeline]

        # 5. Render to Playable MP4 with the existing M2 renderer.
        progress_stages = []

        def on_render_progress(pct: float, elapsed: float, stage: str):
            progress_stages.append((pct, stage))

        render_result = await pipeline.render_story(
            story=result.story,
            pack=result.context_pack,
            output_path=str(output_mp4),
            on_progress=on_render_progress,
            timeline=timeline_ir,
        )

        # --- Verification of Media Execution & Output ---
        assert render_result.success
        assert render_result.validation_passed
        assert render_result.execution_plan["input_files"]
        assert output_mp4.exists()
        assert render_result.file_size_bytes > 5000
        assert render_result.width == 1080  # Reel intent → portrait platform target
        assert render_result.height == 1920
        assert render_result.video_codec in ("h264", "avc1")
        assert render_result.audio_codec == "aac"
        assert abs(render_result.duration_s - result.story.total_duration_s) < 1.0

        print(f"[M3 Media] Rendered File Size: {render_result.file_size_mb} MB")
        print(f"[M3 Media] Render Time: {render_result.render_time_s}s (Encoder: {render_result.encoder_used})")
        print(f"[M3 Media] Output Resolution: {render_result.width}x{render_result.height} @ {render_result.fps} fps")

        # 6. Independent full decode and independent seek/frame extraction.
        subprocess.check_call(["ffmpeg", "-v", "error", "-i", str(output_mp4), "-f", "null", "-"])

        thumb_path = tmp_path / "frame_sample.jpg"
        subprocess.check_call([
            "ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(output_mp4),
            "-frames:v", "1", str(thumb_path),
        ])
        assert thumb_path.exists()
        assert thumb_path.stat().st_size > 1000

        print("[M3 Acceptance] Full End-to-End Vertical Slice Verified Successfully!")
    finally:
        await providers.aclose()
