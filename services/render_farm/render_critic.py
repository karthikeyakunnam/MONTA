"""Checks facts in the encoded MP4 before it becomes the current render.

Model judgements are deliberately absent here. Every result cites pixels,
audio samples, ffprobe metadata, or an explicit verification limit.
"""

import io
import json
import re

import numpy as np
from PIL import Image

from orchestration.agents.intelligence.media import (
    FFmpegMediaBackend, PROTOCOL_ARGS, check_input_path,
)
from shared.contracts.clip import TechnicalMetadata

BLACK_FILTER = "blackdetect=d=0.01:pix_th=0.15:pic_th=0.95"
BLACK_INTERVAL = re.compile(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)")
MAX_VOLUME = re.compile(r"max_volume:\s*([+-]?[\d.]+) dB")


def _image(part) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(part.data)).convert("RGB"))


async def inspect_render(path: str, timeline, *, ffmpeg_bin: str = "ffmpeg",
                         storage_root: str, actual_duration_s: float,
                         scene_beats=(), captions_burned: bool = False) -> dict:
    """Inspect one validated output using fixed FFmpeg arguments and bounded stills.

    A failed required title or unexplained black cut blocks publication. Warnings
    describe possible creative defects without pretending a heuristic proves them.
    """
    media = FFmpegMediaBackend(ffmpeg=ffmpeg_bin, allowed_roots=(storage_root,))
    output = check_input_path(path, media.allowed_roots)
    probe_data = json.loads(await media._run([
        media.ffprobe, "-v", "error", *PROTOCOL_ARGS, "-show_entries",
        "format=duration:stream=codec_type,width,height", "-of", "json", str(output),
    ]))
    video_stream = next((stream for stream in probe_data.get("streams", [])
                         if stream.get("codec_type") == "video"), {})
    encoded_duration = float(probe_data.get("format", {}).get("duration", 0))
    duration_tolerance = max(0.2, 2 / timeline.fps)
    checks: list[dict] = [{
        "name": "duration_and_frame", "status": "passed" if (
            video_stream.get("width") == timeline.width
            and video_stream.get("height") == timeline.height
            and abs(encoded_duration - timeline.render_duration_s) <= duration_tolerance
        ) else "failed",
        "detail": "Compared ffprobe metadata with the validated timeline, including transition overlap",
        "evidence": {"encoded_duration_s": round(encoded_duration, 3),
                     "expected_duration_s": round(timeline.render_duration_s, 3),
                     "tolerance_s": round(duration_tolerance, 3),
                     "encoded_width": video_stream.get("width"), "encoded_height": video_stream.get("height"),
                     "expected_width": timeline.width, "expected_height": timeline.height},
    }]
    meta = TechnicalMetadata(
        clip_id="render", path=str(output), duration_s=encoded_duration or actual_duration_s, fps=timeline.fps,
        width=video_stream.get("width") or timeline.width,
        height=video_stream.get("height") or timeline.height,
        codec="encoded", has_audio=any(s.get("codec_type") == "audio"
                                       for s in probe_data.get("streams", [])),
        file_size_bytes=output.stat().st_size, fingerprint="render-inspection",
    )

    _, black_log = await media._run_output([
        ffmpeg_bin, "-hide_banner", "-nostdin", "-v", "info", *PROTOCOL_ARGS,
        "-i", str(output), "-vf", BLACK_FILTER, "-an", "-f", "null", "-",
    ])
    intervals = [tuple(float(value) for value in found.groups())
                 for found in BLACK_INTERVAL.finditer(black_log.decode(errors="replace"))]
    cut_issues = []
    for start, end, duration in intervals:
        near = next((index for index, segment in enumerate(timeline.video_segments[1:], 1)
                     if abs(start - segment.timeline_start_ms / 1000) <= 0.12), None)
        if near is None:
            continue
        before = timeline.video_segments[near - 1]
        after = timeline.video_segments[near]
        deliberate = (before.fade_out_ms > 0 or after.fade_in_ms > 0
                      or after.transition_in.value in {"fade_black", "fade_white"})
        cut_issues.append({"at_s": round(start, 3), "duration_s": round(duration, 3),
                           "deliberate_fade": deliberate})
    unexplained = [item for item in cut_issues if not item["deliberate_fade"]]
    ending_blackout = [dict(at_s=round(start, 3), duration_s=round(duration, 3))
                       for start, end, duration in intervals
                       if end >= encoded_duration - 0.12 and duration >= 0.05
                       and timeline.video_segments[-1].fade_out_ms == 0]
    checks.append({
        "name": "black_frames", "status": "failed" if unexplained or ending_blackout else
        "warning" if intervals else "passed",
        "evidence": {"intervals": [dict(start_s=round(s, 3), end_s=round(e, 3),
                                      duration_s=round(d, 3)) for s, e, d in intervals],
                     "cut_issues": cut_issues, "ending_blackout": ending_blackout},
        "detail": ("Unrequested near-black ending" if ending_blackout else
                   "Unexplained black frames at a cut" if unexplained else
                   "Near-black frames detected; review intentional fades or source darkness" if intervals else
                   "No near-black intervals detected"),
    })

    for overlay in timeline.text_overlays:
        if overlay.role not in {"title", "subtitle", "credit", "caption"}:
            continue
        asset = check_input_path(overlay.asset_path, media.allowed_roots)
        rgba = np.asarray(Image.open(asset).convert("RGBA"))
        mask = rgba[:, :, 3] > 220
        if not np.any(mask):
            checks.append({"name": f"text:{overlay.overlay_id}", "status": "unavailable",
                           "detail": "Overlay has no opaque pixels to compare", "evidence": {"text": overlay.text}})
            continue
        at = (overlay.start_ms + overlay.end_ms) / 2000
        frame = _image((await media.keyframes(meta, [at], width=timeline.width))[0])
        if frame.shape != rgba.shape[:2] + (3,):
            checks.append({"name": f"text:{overlay.overlay_id}", "status": "unavailable",
                           "detail": "Decoded frame size differs from overlay asset", "evidence": {"text": overlay.text}})
            continue
        delta = np.abs(frame[mask].astype(np.int16) - rgba[:, :, :3][mask].astype(np.int16)).mean(axis=1)
        match = float(np.mean(delta < 40))
        checks.append({"name": f"text:{overlay.overlay_id}",
                       "status": "passed" if match >= 0.70 else "failed",
                       "detail": "Compared opaque text pixels in the encoded MP4 with the exact rasterized text asset",
                       "evidence": {"text": overlay.text, "sample_time_s": round(at, 3),
                                    "matching_pixel_fraction": round(match, 3),
                                    "opaque_pixels": int(mask.sum())}})

    # At cuts these measured differences may be intentional, so they are warnings.
    all_boundaries = [segment.timeline_start_ms / 1000 for segment in timeline.video_segments[1:]]
    boundaries = all_boundaries if len(all_boundaries) <= 24 else [
        all_boundaries[round(i * (len(all_boundaries) - 1) / 23)] for i in range(24)]
    sample_times = [time for boundary in boundaries
                    for time in (max(0.0, boundary - 0.12), min(actual_duration_s - 0.02, boundary + 0.12))]
    sampled = await media.keyframes(meta, sample_times, width=160) if sample_times else []
    jumps = []
    for index, boundary in enumerate(boundaries):
        left, right = _image(sampled[index * 2]), _image(sampled[index * 2 + 1])
        jump = abs(float(left.mean()) - float(right.mean()))
        if jump > 35:
            entry = {"at_s": round(boundary, 3), "mean_rgb_jump": round(jump, 1),
                     "classification": "source_comparison_unavailable"}
            segment_index = next((i for i, segment in enumerate(timeline.video_segments[1:], 1)
                                  if abs(segment.timeline_start_ms / 1000 - boundary) < 0.001), None)
            if segment_index is not None:
                previous = timeline.video_segments[segment_index - 1]
                following = timeline.video_segments[segment_index]
                if following.transition_in.value == "cut":
                    source_means = []
                    for segment, at in ((previous, max(0, previous.source_out_ms / 1000 - 0.12)),
                                        (following, following.source_in_ms / 1000 + 0.12)):
                        source = check_input_path(segment.source_path, media.allowed_roots)
                        source_meta = TechnicalMetadata(
                            clip_id=segment.clip_id, path=str(source),
                            duration_s=max(0.1, segment.source_out_ms / 1000), fps=timeline.fps,
                            width=timeline.width, height=timeline.height, codec="source",
                            has_audio=False, file_size_bytes=source.stat().st_size,
                            fingerprint="source-cut-comparison",
                        )
                        source_means.append(float(_image((await media.keyframes(
                            source_meta, [at], width=160))[0]).mean()))
                    source_jump = abs(source_means[0] - source_means[1])
                    entry["source_mean_rgb_jump"] = round(source_jump, 1)
                    entry["classification"] = (
                        "source_driven_scene_change" if abs(jump - source_jump) <= 15
                        else "output_change_exceeds_source" if jump > source_jump + 15
                        else "source_change_reduced_in_output")
            jumps.append(entry)
    checks.append({"name": "exposure_cuts", "status": "warning" if jumps else "passed",
                   "detail": "Adjacent cut-frame brightness compared with the original source frames; scene changes can be intentional",
                   "evidence": {"threshold": 35, "jumps": jumps,
                                "sampled_boundaries": len(boundaries),
                                "total_boundaries": len(all_boundaries)}})

    if meta.has_audio:
        _, audio_log = await media._run_output([
            ffmpeg_bin, "-hide_banner", "-nostdin", "-v", "info", *PROTOCOL_ARGS,
            "-i", str(output), "-vn", "-af", "volumedetect", "-f", "null", "-",
        ])
        peak = MAX_VOLUME.search(audio_log.decode(errors="replace"))
        decoded = await media._run([
            ffmpeg_bin, "-v", "error", "-nostdin", *PROTOCOL_ARGS,
            "-i", str(output), "-vn", "-ac", "1", "-ar", "16000",
            "-f", "f32le", "pipe:1",
        ])
        samples = np.frombuffer(decoded, dtype="<f4")
        high = np.abs(samples) >= 0.999
        high_indices = np.flatnonzero(high)
        if len(high_indices):
            starts = np.r_[0, np.flatnonzero(np.diff(high_indices) > 1) + 1]
            ends = np.r_[starts[1:], len(high_indices)]
            longest_run = int(np.max(ends - starts))
        else:
            longest_run = 0
        high_fraction = float(np.mean(high)) if len(samples) else 0.0
        confirmed_clipping = high_fraction >= 0.001 and longest_run >= 3
    else:
        peak = None
        samples = np.array([], dtype="<f4")
        high_fraction = 0.0
        longest_run = 0
        confirmed_clipping = False
    peak_db = float(peak.group(1)) if peak else None
    checks.append({"name": "audio_peak", "status": "warning" if peak_db is not None and peak_db >= -0.2
                   else "passed" if peak_db is not None else "unavailable",
                   "detail": ("No audio stream in encoded MP4" if not meta.has_audio else
                              "Peak near 0 dBFS indicates possible clipping; this is not a speech-alignment check"),
                   "evidence": {"max_volume_db": peak_db}})
    checks.append({"name": "audio_clipping",
                   "status": "failed" if confirmed_clipping else "warning" if high_fraction > 0 else
                   "passed" if len(samples) else "unavailable",
                   "detail": ("Decoded mono float samples at 16 kHz: at least 0.1% near full scale and "
                              "a run of at least 3 consecutive samples is treated as confirmed clipping. "
                              "AAC decoding may introduce isolated overshoots."),
                   "evidence": {"sample_count": len(samples),
                                "near_full_scale_fraction": round(high_fraction, 6),
                                "longest_near_full_scale_run": longest_run,
                                "decoded_peak": round(float(np.max(np.abs(samples))), 4) if len(samples) else None}})

    if scene_beats:
        checks.append({"name": "narrative_scene_relevance", "status": "unavailable",
                       "detail": "Local frame descriptions are unverified and cannot prove scene meaning in the MP4; review the full video",
                       "evidence": {"required_beats": [beat.description for beat in scene_beats]}})
    if captions_burned:
        checks.append({"name": "caption_text", "status": "unavailable",
                       "detail": "Caption OCR is unavailable; exact burned text was not verified from encoded pixels",
                       "evidence": {}})

    statuses = {item["status"] for item in checks}
    return {"status": "failed" if "failed" in statuses else "warning" if "warning" in statuses
            else "unavailable" if "unavailable" in statuses else "passed",
            "checks": checks, "output_path": str(output)}
