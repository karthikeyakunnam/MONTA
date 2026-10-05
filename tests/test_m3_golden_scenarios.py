"""Real, offline M3 golden acceptance scenarios.

Run explicitly on a provisioned local-model host:
    MONTA_TEST_OLLAMA_TEXT_MODEL=qwen2.5:7b python3 -m pytest -m live -s

The test intentionally fails when Ollama/model inference is absent.  It must
never turn into a lexical-only green result.
"""

import json
from pathlib import Path
import subprocess

import pytest

from orchestration.pipeline import build_pipeline
from shared.contracts.clip import ClipSource
from shared.contracts.vocab import Pace
from tests.test_m3_end_to_end_real import _live_settings, _verify_live_ollama
from shared.providers.registry import build_providers


pytestmark = pytest.mark.live


def _generate_clip(path: Path, duration_s: float, color: str, frequency: int) -> Path:
    subprocess.check_call([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=640x360:d={duration_s}:r=30",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration_s}:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", str(path),
    ])
    return path


@pytest.fixture(scope="module")
def scenario_dataset(tmp_path_factory):
    media_dir = tmp_path_factory.mktemp("golden_media")
    return [
        ClipSource(clip_id="clip_a", path=str(_generate_clip(media_dir / "clip_a.mp4", 12.0, "teal", 330))),
        ClipSource(clip_id="clip_b", path=str(_generate_clip(media_dir / "clip_b.mp4", 12.0, "magenta", 550))),
        ClipSource(clip_id="clip_c", path=str(_generate_clip(media_dir / "clip_c.mp4", 12.0, "yellow", 880))),
    ]


GOLDEN_SCENARIOS = (
    ("cinematic_travel", "Make a cinematic travel video. Start slow and calm, then build energy for a big ending."),
    ("vertical_30_second_reel", "Make a 30-second vertical Instagram reel with energetic cuts and a strong ending."),
    ("simple_clean_edit", "Make a simple, clean edit with natural pacing and no flashy effects."),
    ("negation", "Make a fast edit; do not use slow or boring footage."),
    ("typo_imperfect_prompt", "starrt sloww and calm dont make it borring and go crazzy at the endd!!"),
    ("audio_enabled_edit", "Make an upbeat audio-enabled travel reel; keep the clip audio in sync with the cuts."),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("scenario", "prompt"), GOLDEN_SCENARIOS, ids=[item[0] for item in GOLDEN_SCENARIOS])
async def test_real_m3_golden_scenario(scenario: str, prompt: str, scenario_dataset, tmp_path: Path):
    settings = _live_settings()
    await _verify_live_ollama(settings)
    providers = build_providers(settings)
    pipeline = build_pipeline(providers=providers)
    output = tmp_path / f"{scenario}.mp4"

    try:
        result = await pipeline.run(
            project_id=f"golden_{scenario}", user_id="golden_creator", prompt=prompt, clips=scenario_dataset,
        )
        assert result.story is not None
        assert any(extractor.startswith("llm:") for extractor in result.intent.extractors)
        assert not any(reason.startswith("LLM extraction failed:") for reason in result.intent.degraded)

        timeline = pipeline.generate_timeline_ir(result.story, result.context_pack)
        render = await pipeline.render_story(result.story, result.context_pack, str(output), timeline=timeline)

        assert render.success and render.validation_passed
        assert output.exists() and render.file_size_bytes > 5000
        assert render.video_codec in ("h264", "avc1")
        assert render.audio_codec == "aac"
        assert [s.clip_id for s in timeline.video_segments] == [s.clip_id for s in result.story.timeline]
        assert abs(render.duration_s - timeline.duration_s) < 1.0
        if scenario == "vertical_30_second_reel":
            assert (render.width, render.height) == (1080, 1920)
            assert result.story.requested_duration_s == pytest.approx(30.0)
        if scenario == "negation":
            assert result.intent.pace.value in (Pace.FAST, Pace.AGGRESSIVE)

        record = {
            "scenario": scenario,
            "prompt": prompt,
            "input_clips": [clip.clip_id for clip in scenario_dataset],
            "selected_clips": list(result.story.selected_clip_ids),
            "story_pattern": result.story.story_pattern,
            "story": result.story.model_dump(mode="json"),
            "timeline_ir": timeline.model_dump(mode="json"),
            "output": {
                "path": str(output), "duration_s": render.duration_s,
                "resolution": f"{render.width}x{render.height}", "fps": render.fps,
                "encoder": render.encoder_used, "validation_passed": render.validation_passed,
            },
        }
        manifest = tmp_path / f"{scenario}_artifacts.json"
        manifest.write_text(json.dumps(record, indent=2))
        assert manifest.exists()
        print(json.dumps(record, indent=2))
    finally:
        await providers.aclose()
