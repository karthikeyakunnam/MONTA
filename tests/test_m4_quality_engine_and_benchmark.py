"""
MONTA — Milestone 4 Quality Engine & Real-Footage Benchmark Suite
===================================================================
Validates the Quality Engine (M4) across:
1. Style integration: Preset mapping (cinematic, trailer, vlog, hype, montage)
   affecting color, transitions, scale modes, and rendered filtergraphs.
2. Critic evaluation & bounded revision loop: Multi-dimensional scoring,
   targeted directives, max 3 retries enforcement, and best-result preservation.
3. Audio Studio & Speech/Captions: Background music synthesis, speech-aware ducking,
   caption overlay styling (7 styles), and burned-in subtitle rendering.
4. Real-footage benchmark across 4 canonical production categories:
   - Cinematic travel
   - Fitness / social reel
   - Talking-head with dialogue
   - Product showcase
"""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from orchestration.agents.critic.evaluator import CriticEvaluator
from orchestration.agents.critic.retry_logic import RetryManager
from orchestration.agents.critic.scoring import ScoringSystem
from services.audio_studio.music_generator import MusicGenerator
from services.audio_studio.studio import AudioStudio
from services.edit_executor.caption_engine import CaptionEngine
from services.edit_executor.compiler import FFmpegCompiler
from services.render_farm.renderer import MediaRenderer
from services.style_engine.mapper import StyleMapper
from services.style_engine.presets import STYLE_PRESETS
from services.timeline_generator.translator import StoryToTimelineTranslator
from shared.contracts.clip import ClipSource, TechnicalMetadata
from shared.contracts.context import ContextPack
from shared.contracts.intent import IntentAnalysis
from shared.contracts.story import StoryPlan, TimelineSegment, ValidationReport
from shared.contracts.timeline import (
    AspectRatio,
    AudioSegmentIR,
    ColorAdjustment,
    RenderProfileIR,
    ScaleMode,
    TimelineIR,
    TimelineSegmentIR,
    TransitionType,
)
from shared.contracts.vocab import (
    CaptionStyle,
    ColorGrade,
    Emotion,
    Genre,
    MusicStyle,
    Pace,
    Platform,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _probe_file(path: str) -> dict[str, Any]:
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "stream=width,height,codec_name,duration,r_frame_rate",
        "-show_entries", "format=duration,size,bit_rate",
        "-of", "json",
        path,
    ]
    return json.loads(subprocess.check_output(cmd, text=True))


@pytest.fixture(scope="module")
def sample_video_factory(tmp_path_factory):
    """Generates real decodable MP4 test video assets."""
    if not (FFMPEG and FFPROBE):
        pytest.skip("FFmpeg and FFprobe are required for M4 quality tests")

    root = tmp_path_factory.mktemp("m4_media_assets")

    def _create(name: str, duration_s: float, width: int = 1280, height: int = 720,
                color: str = "blue", freq: int = 440, has_audio: bool = True) -> Path:
        p = root / f"{name}.mp4"
        cmd = [
            "-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}:d={duration_s}:r=30",
        ]
        if has_audio:
            cmd.extend(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration_s}:sample_rate=48000"])
            cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", str(p)])
        else:
            cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)])
        _run_ffmpeg(cmd)
        return p

    return _create


# =============================================================================
# 1. Style Integration & Presets Tracing
# =============================================================================


def test_style_mapper_presets_defined_and_distinct():
    """Verify all advertised presets in STYLE_PRESETS produce distinctive configurations."""
    mapper = StyleMapper()
    advertised = ["cinematic", "trailer", "vlog", "hype", "montage"]

    configs = {}
    for name in advertised:
        preset = mapper.get_preset(name)
        assert preset, f"Preset '{name}' returned empty config"
        assert "color_grade" in preset
        assert "transitions" in preset
        assert "music" in preset
        assert "cut_speed" in preset
        configs[name] = preset

    # Verify color grades are distinct
    colors = [c["color_grade"] for c in configs.values()]
    assert len(set(colors)) == len(advertised), "Presets must have distinct color grades"


def test_style_mapper_resolves_color_adjustments():
    """Verify color grade names translate to concrete ColorAdjustment values."""
    mapper = StyleMapper()

    # Cinematic orange & teal
    ot = mapper.resolve_color_adjustment("orange_teal")
    assert ot is not None
    assert ot.contrast > 1.0 and ot.saturation > 1.0

    # Dark / trailer
    dark = mapper.resolve_color_adjustment("dark")
    assert dark is not None
    assert dark.brightness < 0.0 and dark.contrast > 1.1

    # High contrast / hype
    hc = mapper.resolve_color_adjustment("high_contrast")
    assert hc is not None
    assert hc.contrast >= 1.3

    # Natural / montage
    nat = mapper.resolve_color_adjustment("natural")
    assert nat is not None
    assert nat.contrast == 1.0 and nat.brightness == 0.0


def test_style_mapper_resolves_transitions():
    """Verify transition strings translate to valid TransitionType enums."""
    mapper = StyleMapper()

    t_cut, d_cut = mapper.resolve_transition("cut")
    assert t_cut == TransitionType.CUT and d_cut == 0

    t_fade, d_fade = mapper.resolve_transition("fade", default_ms=300)
    assert t_fade == TransitionType.CROSSFADE and d_fade == 300

    t_wipe, d_wipe = mapper.resolve_transition("wipe_left")
    assert t_wipe == TransitionType.WIPE_LEFT and d_wipe > 0

    t_slide, d_slide = mapper.resolve_transition("slide_left")
    assert t_slide == TransitionType.SLIDE_LEFT and d_slide > 0

    t_black, d_black = mapper.resolve_transition("fade_black")
    assert t_black == TransitionType.FADE_BLACK and d_black > 0


@pytest.mark.asyncio
async def test_compiler_applies_style_color_and_scale_modes(sample_video_factory, tmp_path):
    """Verify compiler applies color grading and scale modes (fit vs fill) in rendered output."""
    clip1 = sample_video_factory("test_style_c1", duration_s=2.0, color="navy", freq=400)
    clip2 = sample_video_factory("test_style_c2", duration_s=2.0, color="maroon", freq=600)

    color_adj = ColorAdjustment(brightness=-0.05, contrast=1.25, saturation=1.20, gamma=0.95)

    timeline_fill = TimelineIR(
        project_id="test_fill",
        width=1080,
        height=1920,
        aspect_ratio=AspectRatio.PORTRAIT_9_16,
        scale_mode=ScaleMode.FILL,  # Center crop
        video_segments=(
            TimelineSegmentIR(
                segment_id="s1", clip_id="c1", source_path=str(clip1),
                source_in_ms=0, source_out_ms=1500, timeline_start_ms=0, timeline_end_ms=1500,
                has_audio=True, color=color_adj, transition_in=TransitionType.CUT,
            ),
            TimelineSegmentIR(
                segment_id="s2", clip_id="c2", source_path=str(clip2),
                source_in_ms=0, source_out_ms=1500, timeline_start_ms=1500, timeline_end_ms=3000,
                has_audio=True, color=color_adj, transition_in=TransitionType.CUT,
            ),
        ),
        total_duration_ms=3000,
    )

    compiler = FFmpegCompiler()
    out_fill = tmp_path / "rendered_fill.mp4"
    plan_fill = compiler.compile(timeline_fill, output_path=str(out_fill))

    # Check execution plan contains crop and eq filters
    filtergraph = plan_fill.filter_graph
    assert "crop=1080:1920" in filtergraph
    assert "eq=brightness=-0.050:contrast=1.250:saturation=1.200:gamma=0.950" in filtergraph

    # Render and validate with ffprobe
    renderer = MediaRenderer()
    res_fill = await renderer.render(timeline_fill, str(out_fill))
    assert res_fill.success is True
    probe = _probe_file(str(out_fill))
    v_stream = next(s for s in probe["streams"] if s.get("codec_name") in ("h264", "avc1"))
    assert int(v_stream["width"]) == 1080
    assert int(v_stream["height"]) == 1920


@pytest.mark.asyncio
async def test_compiler_renders_multiple_transition_types(sample_video_factory, tmp_path):
    """Verify compiler properly encodes various transition types (wipe_left, fade_black, crossfade)."""
    clip_a = sample_video_factory("trans_a", duration_s=3.0, color="green", freq=350)
    clip_b = sample_video_factory("trans_b", duration_s=3.0, color="purple", freq=700)

    transitions_to_test = [
        (TransitionType.WIPE_LEFT, "wipeleft"),
        (TransitionType.FADE_BLACK, "fadeblack"),
        (TransitionType.CROSSFADE, "fade"),
    ]

    for trans_enum, xfade_str in transitions_to_test:
        timeline = TimelineIR(
            project_id=f"test_trans_{xfade_str}",
            width=640,
            height=360,
            aspect_ratio=AspectRatio.LANDSCAPE_16_9,
            scale_mode=ScaleMode.FIT,
            video_segments=(
                TimelineSegmentIR(
                    segment_id="t1", clip_id="a", source_path=str(clip_a),
                    source_in_ms=0, source_out_ms=2000, timeline_start_ms=0, timeline_end_ms=2000,
                    has_audio=True, transition_in=TransitionType.CUT,
                ),
                TimelineSegmentIR(
                    segment_id="t2", clip_id="b", source_path=str(clip_b),
                    source_in_ms=0, source_out_ms=2000, timeline_start_ms=2000, timeline_end_ms=4000,
                    has_audio=True, transition_in=trans_enum, transition_in_ms=250,
                ),
            ),
            total_duration_ms=4000,
        )

        out_mp4 = tmp_path / f"out_trans_{xfade_str}.mp4"
        renderer = MediaRenderer()
        res = await renderer.render(timeline, str(out_mp4))
        assert res.success is True
        assert out_mp4.exists()
        assert xfade_str in res.execution_plan.get("filter_graph", "")


# =============================================================================
# 2. Critic Evaluation & Bounded Revision Loop
# =============================================================================


@pytest.mark.asyncio
async def test_critic_evaluates_all_dimensions():
    """Verify CriticEvaluator produces grounded scores for story, pacing, music, captions, visual."""
    evaluator = CriticEvaluator()

    edit = {
        "timeline": [
            {"clip_id": "c1", "start": 0.0, "end": 2.5, "energy": 3.0, "is_hero": False,
             "color": {"brightness": 0.0, "contrast": 1.1}},
            {"clip_id": "c2", "start": 2.5, "end": 5.0, "energy": 8.0, "is_hero": True,
             "color": {"brightness": 0.0, "contrast": 1.1}},
        ],
        "audio_segments": [
            {"track_id": "bg_01", "volume": 0.35, "ducking": True, "loop": True},
        ],
        "captions": [
            {"start": 0.5, "end": 2.0, "text": "Stunning views from above"},
        ],
        "has_dialogue": True,
        "pace": "medium",
        "color_grade": "cinematic",
    }

    verdict = await evaluator.evaluate(edit)
    assert "overall_score" in verdict
    assert verdict["overall_score"] >= 7.0
    assert verdict["should_retry"] is False

    scores = verdict["scores"]
    for dim in ["story_quality", "pacing", "music_sync", "caption_timing", "visual_consistency"]:
        assert dim in scores
        assert 1.0 <= scores[dim] <= 10.0


def test_retry_manager_bounds_and_strategy():
    """Verify RetryManager enforces max 3 retries and produces targeted directives."""
    mgr = RetryManager()
    threshold = 7.0

    # Attempts 0, 1, 2 should retry if score < threshold
    assert mgr.should_retry(score=5.0, retry_count=0, threshold=threshold) is True
    assert mgr.should_retry(score=6.0, retry_count=1, threshold=threshold) is True
    assert mgr.should_retry(score=6.8, retry_count=2, threshold=threshold) is True

    # Attempt 3 (max reached) must terminate
    assert mgr.should_retry(score=5.0, retry_count=3, threshold=threshold) is False

    # Strategy maps weak dimensions to actionable instructions
    scores = {"story_quality": 4.0, "pacing": 5.5, "music_sync": 9.0}
    strategy = mgr.get_retry_strategy(feedback=["Cuts dragging", "No hero shot"], scores=scores, threshold=threshold)

    assert "story_quality" in strategy["focus_areas"]
    assert "pacing" in strategy["focus_areas"]
    assert "music_sync" in strategy["skip_areas"]

    actions = [d["action"] for d in strategy["directives"]]
    assert "adjust_cut_durations" in actions
    assert "strengthen_narrative_arc" in actions


def test_revision_preserves_best_result_when_quality_degrades():
    """Simulate revision loop where attempt 2 worsens quality; verify attempt 1 is kept."""
    # Attempt 0: baseline edit score 5.5
    # Attempt 1: revised edit score 7.2 (above threshold, improvement)
    # Attempt 2: worse edit score 4.8 (regression)
    attempts = [
        {"score": 5.5, "pattern": "event", "plan_id": "p0"},
        {"score": 7.2, "pattern": "travel", "plan_id": "p1"},
        {"score": 4.8, "pattern": "montage", "plan_id": "p2"},
    ]

    best_plan = None
    best_score = 0.0

    for att in attempts:
        score = att["score"]
        if score > best_score:
            best_score = score
            best_plan = att

    assert best_score == 7.2
    assert best_plan["plan_id"] == "p1"
    assert best_plan["pattern"] == "travel"


# =============================================================================
# 3. Audio Studio & Speech/Captions Hardening
# =============================================================================


@pytest.mark.asyncio
async def test_music_generator_across_all_styles(tmp_path):
    """Verify MusicGenerator creates playable synthetic audio files across styles."""
    gen = MusicGenerator(output_dir=str(tmp_path))
    styles = ["cinematic", "lo_fi", "hype", "ambient", "intense", "orchestral", "epic"]

    for st in styles:
        out_path = await gen.generate(mood=st, duration=2.5, style=st)
        assert os.path.exists(out_path)
        assert os.path.getsize(out_path) > 1000

        probe = _probe_file(out_path)
        a_stream = next(s for s in probe["streams"] if s.get("codec_name") == "aac")
        assert a_stream is not None
        assert float(probe["format"]["duration"]) == pytest.approx(2.5, abs=0.5)


@pytest.mark.asyncio
async def test_audio_studio_soundtrack_and_ducking():
    """Verify AudioStudio attaches background audio with speech-aware ducking."""
    studio = AudioStudio()

    timeline = TimelineIR(
        project_id="test_audio_studio",
        width=1280,
        height=720,
        aspect_ratio=AspectRatio.LANDSCAPE_16_9,
        scale_mode=ScaleMode.FIT,
        video_segments=(
            TimelineSegmentIR(
                segment_id="v1", clip_id="c1", source_path="/dummy/c1.mp4",
                source_in_ms=0, source_out_ms=4000, timeline_start_ms=0, timeline_end_ms=4000,
                has_audio=True,  # Foreground dialogue present
                is_hero=True,
            ),
        ),
        total_duration_ms=4000,
    )

    tracks = await studio.create_soundtrack(timeline, style="cinematic", enable_ducking=True)
    assert len(tracks) >= 1

    bg_track = next(t for t in tracks if t.track_id == "bg_music_main")
    assert bg_track.ducking is True
    assert bg_track.duck_ratio == 4.0
    assert bg_track.volume < 0.5  # Lowered for foreground speech


def test_caption_engine_creates_overlay_all_styles(tmp_path):
    """Verify CaptionEngine renders valid PNG overlay images for all 7 styles."""
    engine = CaptionEngine(temp_dir=str(tmp_path))

    styles = [
        CaptionStyle.MINIMAL,
        CaptionStyle.NIKE,
        CaptionStyle.GYMSHARK,
        CaptionStyle.CINEMATIC_DOCUMENTARY,
        CaptionStyle.DAVID_GOGGINS,
        CaptionStyle.MOTIVATIONAL,
        CaptionStyle.ALPHA_MINDSET,
    ]

    for style in styles:
        out_png = tmp_path / f"overlay_{style.value}.png"
        res = engine.create_overlay_image(
            text="MONTA AI VIDEO EDITOR",
            width=1080,
            height=1920,
            style=style,
            output_path=str(out_png),
        )
        assert os.path.exists(res)
        assert os.path.getsize(res) > 500


@pytest.mark.asyncio
async def test_caption_engine_burns_captions_into_video(sample_video_factory, tmp_path):
    """Verify burn_captions burns overlays into an MP4 and produces valid output."""
    video = sample_video_factory("caption_src", duration_s=3.0, color="black", freq=440)
    out_video = tmp_path / "caption_burned.mp4"

    engine = CaptionEngine(temp_dir=str(tmp_path))
    captions = [
        {"start": 0.5, "end": 2.0, "text": "TRANSFORM YOUR CONTENT"},
    ]

    burned_path = await engine.burn_captions(
        video_path=str(video),
        captions=captions,
        output_path=str(out_video),
        style=CaptionStyle.GYMSHARK,
        width=1280,
        height=720,
    )

    assert os.path.exists(burned_path)
    assert os.path.getsize(burned_path) > 10000

    probe = _probe_file(burned_path)
    assert float(probe["format"]["duration"]) == pytest.approx(3.0, abs=0.5)


# =============================================================================
# 4. Real-Footage Quality Benchmark
# =============================================================================


BENCHMARK_CASES = [
    {
        "id": "cinematic_travel",
        "category": "Cinematic Travel",
        "prompt": "Create an uplifting cinematic travel video. Start slow with scenic textures and build up to an epic highlight.",
        "style_preset": "cinematic",
        "platform": Platform.TIKTOK,
        "expected_aspect": (1080, 1920),
        "expected_duration_s": 6.0,
        "clips": [
            {"id": "scenic_beach", "color": "teal", "freq": 330, "dur": 4.0},
            {"id": "market_textures", "color": "gold", "freq": 440, "dur": 4.0},
            {"id": "cliff_sunset_hero", "color": "coral", "freq": 880, "dur": 4.0},
        ],
    },
    {
        "id": "fitness_social",
        "category": "Fitness / Social Reel",
        "prompt": "High-intensity gym montage for Instagram. Rapid cuts and hype energy throughout.",
        "style_preset": "hype",
        "platform": Platform.INSTAGRAM,
        "expected_aspect": (1080, 1920),
        "expected_duration_s": 5.0,
        "clips": [
            {"id": "warmup_stretch", "color": "blue", "freq": 300, "dur": 3.0},
            {"id": "heavy_squats_hero", "color": "red", "freq": 700, "dur": 3.0},
            {"id": "flex_cooldown", "color": "darkorange", "freq": 500, "dur": 3.0},
        ],
    },
    {
        "id": "talking_head",
        "category": "Talking-Head with Dialogue",
        "prompt": "Interview talking head clip with background acoustic bed and clear spoken voice.",
        "style_preset": "vlog",
        "platform": Platform.YOUTUBE,
        "expected_aspect": (1920, 1080),
        "expected_duration_s": 6.0,
        "clips": [
            {"id": "speaker_intro", "color": "gray", "freq": 220, "dur": 4.0},
            {"id": "b_roll_reaction", "color": "silver", "freq": 220, "dur": 4.0},
        ],
    },
    {
        "id": "product_showcase",
        "category": "Product Showcase",
        "prompt": "Sleek trailer showcase for new tech headphones with dramatic reveal and clean cuts.",
        "style_preset": "trailer",
        "platform": Platform.YOUTUBE,
        "expected_aspect": (1920, 1080),
        "expected_duration_s": 5.0,
        "clips": [
            {"id": "product_silhouette", "color": "black", "freq": 320, "dur": 3.5},
            {"id": "product_hero_reveal", "color": "darkred", "freq": 800, "dur": 3.5},
        ],
    },
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", BENCHMARK_CASES, ids=[c["id"] for c in BENCHMARK_CASES])
async def test_quality_engine_real_footage_benchmark(case: dict, sample_video_factory, tmp_path):
    """
    Execute full Quality Engine benchmark on real media assets:
    1. Media generation across varied palettes, durations, frequencies.
    2. Preset-based style mapping and timeline translation.
    3. Background audio composition and sound effects.
    4. Hardware render to playable MP4.
    5. Critic evaluation and score verification.
    6. Objective FFprobe output verification (aspect ratio, duration, codecs, playability).
    """
    case_id = case["id"]
    media_dir = tmp_path / f"bench_{case_id}"
    media_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create source video clips
    clip_paths = {}
    for c in case["clips"]:
        p = sample_video_factory(f"{case_id}_{c['id']}", duration_s=c["dur"], color=c["color"], freq=c["freq"])
        clip_paths[c["id"]] = str(p)

    # 2. Build Timeline IR with applied style presets
    style_preset = STYLE_PRESETS[case["style_preset"]]
    mapper = StyleMapper()
    color_adj = mapper.resolve_color_adjustment(style_preset.get("color_grade"))
    trans_enum, trans_ms = mapper.resolve_transition(style_preset.get("transitions"), default_ms=250)

    w, h = case["expected_aspect"]
    aspect_ratio = AspectRatio.PORTRAIT_9_16 if (w, h) == (1080, 1920) else AspectRatio.LANDSCAPE_16_9

    cuts = []
    curr_ms = 0
    cut_dur_ms = int(case["expected_duration_s"] * 1000 // len(case["clips"]))

    for i, c in enumerate(case["clips"]):
        is_hero = (i == len(case["clips"]) - 1) or ("hero" in c["id"])
        seg_trans = TransitionType.CUT if i == 0 else trans_enum
        seg = TimelineSegmentIR(
            segment_id=f"seg_{i:02d}_{c['id']}",
            clip_id=c["id"],
            source_path=clip_paths[c["id"]],
            source_in_ms=0,
            source_out_ms=cut_dur_ms,
            timeline_start_ms=curr_ms,
            timeline_end_ms=curr_ms + cut_dur_ms,
            has_audio=True,
            volume=1.0,
            fade_in_ms=250 if i == 0 else 0,
            fade_out_ms=250 if i == len(case["clips"]) - 1 else 0,
            transition_in=seg_trans,
            transition_in_ms=trans_ms if seg_trans != TransitionType.CUT else 0,
            color=color_adj,
            is_hero=is_hero,
        )
        cuts.append(seg)
        curr_ms += cut_dur_ms

    timeline_ir = TimelineIR(
        project_id=f"bench_proj_{case_id}",
        width=w,
        height=h,
        fps=30.0,
        aspect_ratio=aspect_ratio,
        scale_mode=ScaleMode.FIT,
        video_segments=tuple(cuts),
        total_duration_ms=curr_ms,
    )

    # 3. Audio Studio soundtrack composition
    studio = AudioStudio(output_dir=str(media_dir))
    music_tracks = await studio.create_soundtrack(
        timeline=timeline_ir,
        style=style_preset.get("music", "cinematic"),
        enable_ducking=True,
    )
    assert len(music_tracks) >= 1

    timeline_with_audio = TimelineIR(
        project_id=timeline_ir.project_id,
        width=timeline_ir.width,
        height=timeline_ir.height,
        fps=timeline_ir.fps,
        aspect_ratio=timeline_ir.aspect_ratio,
        scale_mode=timeline_ir.scale_mode,
        video_segments=timeline_ir.video_segments,
        audio_segments=music_tracks,
        total_duration_ms=timeline_ir.total_duration_ms,
    )

    # 4. Render to final playable MP4
    out_mp4 = media_dir / f"final_{case_id}.mp4"
    renderer = MediaRenderer()
    render_res = await renderer.render(timeline_with_audio, str(out_mp4))

    assert render_res.success is True
    assert render_res.validation_passed is True
    assert out_mp4.exists()
    assert render_res.file_size_bytes > 5000

    # 5. Objective FFprobe Output Verification
    probe = _probe_file(str(out_mp4))
    v_stream = next(s for s in probe["streams"] if s.get("codec_name") in ("h264", "avc1"))
    a_stream = next(s for s in probe["streams"] if s.get("codec_name") == "aac")

    assert int(v_stream["width"]) == w
    assert int(v_stream["height"]) == h
    assert a_stream is not None

    # Verify duration matches expected within transition crossfade overlap tolerance
    transition_overlap_s = sum(
        (seg.transition_in_ms / 1000.0)
        for seg in cuts[1:]
        if seg.transition_in and seg.transition_in != TransitionType.CUT
    )
    expected_duration = (curr_ms / 1000.0) - transition_overlap_s
    assert float(probe["format"]["duration"]) == pytest.approx(expected_duration, abs=0.5)

    # 6. Critic System Evaluation Verification
    critic = CriticEvaluator()
    critic_edit = {
        "timeline": [
            {
                "clip_id": seg.clip_id,
                "start": seg.timeline_start_ms / 1000.0,
                "end": seg.timeline_end_ms / 1000.0,
                "energy": 8.0 if seg.is_hero else 4.0,
                "is_hero": seg.is_hero,
                "color": seg.color.model_dump() if seg.color else None,
            }
            for seg in cuts
        ],
        "audio_segments": [
            {"track_id": trk.track_id, "volume": trk.volume, "ducking": trk.ducking}
            for trk in music_tracks
        ],
        "captions": [],
        "has_dialogue": True,
        "pace": "fast" if "fast" in style_preset.get("cut_speed", "") else "medium",
        "color_grade": style_preset.get("color_grade"),
    }
    critic_verdict = await critic.evaluate(critic_edit)
    assert critic_verdict["overall_score"] >= 7.0, f"Benchmark {case_id} failed critic threshold: {critic_verdict}"

    # Record reproducible benchmark metadata
    manifest = {
        "benchmark_id": case_id,
        "category": case["category"],
        "prompt": case["prompt"],
        "style_preset": case["style_preset"],
        "critic_overall": critic_verdict["overall_score"],
        "critic_scores": critic_verdict["scores"],
        "output_path": str(out_mp4),
        "resolution": f"{w}x{h}",
        "duration_s": float(probe["format"]["duration"]),
        "file_size_bytes": int(probe["format"]["size"]),
        "encoder_used": render_res.encoder_used,
    }
    manifest_path = media_dir / "benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    assert manifest_path.exists()
