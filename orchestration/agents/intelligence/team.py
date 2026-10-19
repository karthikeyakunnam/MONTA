"""
MONTA — Video Intelligence Team (Layer 6)
===========================================
Per-clip pipeline: cache → decode luma → signals (off the event loop) →
keyframes → vision (optional) → fusion → cache.

Failure policy:
* media decode failure → raises (the Director marks the clip task failed; the
  story is built from the remaining clips).
* vision failure → the clip is still returned, signal-only, with the failure
  recorded in ``provenance.degraded``.

Caching is content-addressed (file fingerprint + analyzer versions + vision
model), so re-uploads and re-edits of the same footage never pay twice.
"""

import asyncio
import hashlib
import logging
import math
import os
import re
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

from orchestration.agents.intelligence.fusion import FUSION_VERSION, fuse
from orchestration.agents.intelligence.media import MediaBackend, MediaToolError, keyframe_times
from orchestration.agents.intelligence.signals import SIGNAL_VERSION, compute_signals
from orchestration.agents.intelligence.vision import VisionAnalyzer
from shared.contracts.base import revalidate
from shared.contracts.clip import ClipIntelligence, FrameEvidence, TechnicalMetadata
from shared.observability import catalog as m
from shared.observability.tracing import span
from shared.providers.errors import ProviderError

logger = logging.getLogger("monta.intelligence")


class IntelligenceCache(Protocol):
    async def get(self, key: str) -> ClipIntelligence | None: ...
    async def put(self, key: str, value: ClipIntelligence) -> None: ...


class LRUIntelligenceCache:
    """Bounded in-process LRU. Use a shared store (Redis/Postgres) implementing the same protocol across workers."""

    def __init__(self, max_entries: int = 10_000):
        self.max_entries = max_entries
        self._data: OrderedDict[str, ClipIntelligence] = OrderedDict()

    async def get(self, key: str) -> ClipIntelligence | None:
        value = self._data.get(key)
        if value is not None:
            self._data.move_to_end(key)
        return value

    async def put(self, key: str, value: ClipIntelligence) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


class FileIntelligenceCache:
    """Local, content-addressed observations shared across worker restarts."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    async def get(self, key: str) -> ClipIntelligence | None:
        path = self._path(key)
        try:
            return ClipIntelligence.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as error:
            logger.warning("ignoring invalid intelligence cache entry %s: %s", path, error)
            return None

    async def put(self, key: str, value: ClipIntelligence) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".intel-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value.model_dump_json())
            os.replace(temp_name, self._path(key))
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


class VideoIntelligenceTeam:
    def __init__(
        self,
        media: MediaBackend,
        *,
        vision: VisionAnalyzer | None = None,
        cache: IntelligenceCache | None = None,
        keyframe_count: int = 4,
        max_signal_frames: int = 240,
        signal_width: int = 320,
        keyframe_width: int = 512,
        max_frame_evidence: int = 12,
        frame_evidence_interval_s: float = 3.0,
        calibrator=None,
    ):
        self.media = media
        self.vision = vision
        self.cache = cache
        self.keyframe_count = keyframe_count
        self.max_signal_frames = max_signal_frames
        self.signal_width = signal_width
        self.keyframe_width = keyframe_width
        self.max_frame_evidence = max_frame_evidence
        self.frame_evidence_interval_s = frame_evidence_interval_s
        self.calibrator = calibrator

    def cache_key(self, meta: TechnicalMetadata) -> str:
        vision_id = self.vision.model_id if self.vision else "none"
        return (f"{meta.fingerprint}|{SIGNAL_VERSION}|{FUSION_VERSION}|frame-evidence.v3:"
                f"{self.max_frame_evidence}:{self.frame_evidence_interval_s}|{vision_id}")

    async def enrich_short_scene_evidence(self, meta: TechnicalMetadata,
                                          clip: ClipIntelligence, beats) -> ClipIntelligence:
        """Sample short measured shots near relevant observations, with local vision.

        An existing sparse sample may land on only one frame of a 1–2s shot.
        Additional source-local observations can corroborate it; a hard cut
        alone never supplies a semantic label. Work is bounded and only used
        for explicit ordered scene beats when the ordinary plan is infeasible.
        """
        describe = getattr(self.vision, "describe_frame", None)
        if describe is None or not clip.scene_cut_times_s:
            return clip
        from orchestration.agents.story.beat_matching import (
            _candidates, _concepts, _context_candidates, _greeting_gesture, _usable_spans,
            _interpersonal_action, _requested,
        )

        requested = [_requested(beat.description) for beat in beats]
        covered = []
        for index, beat in enumerate(beats):
            anchors = _candidates(index, beat, {clip.clip_id: clip}, None)
            covered.extend((*anchors, *_context_candidates(index, beat,
                                                              {clip.clip_id: clip}, anchors)))
        bounds = _usable_spans(clip)
        spans = []
        observations = tuple(clip.frame_evidence)
        for index, (start, end) in enumerate(bounds):
            if end - start < 0.75:
                continue
            here = [item for item in observations if start <= item.time_s < end]
            if any(start <= item.source_in_s < end for item in covered):
                continue
            nearby = [item for item in observations
                      if start - 1.75 <= item.time_s < end + 1.75]
            beat_scores = []
            for need in requested:
                score = 0
                for item in nearby:
                    overlap = len(need & _concepts(item.description))
                    social = "greeting" in need and (
                        _greeting_gesture(item.description) or _interpersonal_action(item.description))
                    proximity = ("greeting" in need and social and item not in here and
                                 abs(item.time_s - start) <= 0.5)
                    follows_anchor = (item not in here and any(
                        anchor.is_anchor and anchor.clip_id == clip.clip_id and
                        0 <= start - anchor.source_out_s <= 0.5
                        for anchor in covered))
                    # A greeting gesture near a measured cut is a reason to
                    # inspect the next shot, never evidence that it continues.
                    score = max(score, (2 if item in here else 1) * (overlap + int(social)) +
                                (2 if proximity or follows_anchor else 0))
                beat_scores.append(score)
            if any(beat_scores):
                spans.append((tuple(beat_scores), index, start, end, here))
        # Reserve a few nearby measured shots for EACH beat. Otherwise generic
        # venue/banner observations can consume the entire local inference
        # budget and leave a brief stage action unsampled.
        selected = {}
        for beat_index in range(len(requested)):
            ranked = sorted((span for span in spans if span[0][beat_index]),
                            key=lambda span: (-span[0][beat_index], span[1]))
            for span in ranked[:6]:
                selected[span[1]] = span
        ordered = sorted(selected.values(), key=lambda span: (-max(span[0]), span[1]))[:16]
        times = []
        for _, _, start, end, existing in ordered:
            margin = min(0.12, (end - start) * 0.1)
            candidates = (start + max(margin, (end - start) * 0.3),
                          start + min(end - start - margin, (end - start) * 0.7))
            for at in candidates:
                if len(times) >= 24:
                    break
                if (all(abs(at - item.time_s) > 0.15 for item in observations) and
                        all(abs(at - prior) > 0.15 for prior in times)):
                    times.append(round(at, 3))
            if len(times) >= 24:
                break
        if not times:
            return clip
        augmented = list(observations)
        degraded = list(clip.provenance.degraded)
        try:
            frames = await self.media.keyframes(meta, times, width=self.keyframe_width)
        except (MediaToolError, asyncio.TimeoutError) as error:
            degraded.append(f"targeted scene frame extraction failed: {type(error).__name__}: {error}")
            return revalidate(clip, provenance=revalidate(clip.provenance, degraded=tuple(degraded)))
        for at, frame in zip(times, frames):
            try:
                description = await describe(meta, frame, at)
                augmented.append(FrameEvidence(time_s=at, description=description,
                                               model_id=self.vision.model_id))
            except (ProviderError, MediaToolError, asyncio.TimeoutError) as error:
                degraded.append(f"targeted scene evidence at {at:.2f}s failed: "
                                f"{type(error).__name__}: {error}")
        return revalidate(clip, frame_evidence=tuple(sorted(augmented, key=lambda item: item.time_s)),
                          provenance=revalidate(clip.provenance, degraded=tuple(degraded)))

    async def analyze(self, meta: TechnicalMetadata) -> ClipIntelligence:
        with span("clip.analyze", duration_s=round(meta.duration_s, 2)) as sp:
            try:
                result = await self._analyze(meta)
            except BaseException as e:
                m.CLIP_ANALYSIS.inc(outcome=type(e).__name__)
                raise
            outcome = "cache_hit" if result.provenance.cache_hit else ("unusable" if not result.usable else "ok")
            m.CLIP_ANALYSIS.inc(outcome=outcome)
            sp.set(outcome=outcome, vision=bool(result.provenance.vision_model))
            return result

    async def _analyze(self, meta: TechnicalMetadata) -> ClipIntelligence:
        key = self.cache_key(meta)
        if self.cache:
            cached = await self.cache.get(key)
            m.INTEL_CACHE.inc(result="hit" if cached is not None else "miss")
            if cached is not None:
                return revalidate(cached, **{
                    "clip_id": meta.clip_id,
                    "provenance": revalidate(cached.provenance, cache_hit=True),
                })

        frames, fps = await self.media.sample_luma(meta, max_frames=self.max_signal_frames, width=self.signal_width)
        signals = await asyncio.to_thread(compute_signals, frames, fps)

        observation, degraded, arbitration = None, (), None
        frame_evidence: list[FrameEvidence] = []
        if self.vision is None:
            degraded = ("no vision provider configured",)
            m.VISION_DEGRADED.inc(reason="disabled")
        else:
            times = keyframe_times(meta.duration_s, signals.peak_time_s, self.keyframe_count)
            try:
                images = await self.media.keyframes(meta, times, width=self.keyframe_width)
                observation, arbitration = await self.vision.observe_with_arbitration(meta, images, times)
            except (ProviderError, MediaToolError, asyncio.TimeoutError) as e:
                logger.warning("vision analysis failed for %s: %s", meta.clip_id, e)
                degraded = (f"vision failed: {type(e).__name__}: {e}",)
                m.VISION_DEGRADED.inc(reason=type(e).__name__)
            describe_frame = getattr(self.vision, "describe_frame", None)
            if describe_frame is not None and self.max_frame_evidence > 0:
                # Short social actions can occur between three-second samples.
                # Bound local inference while retaining roughly 1.5-second
                # coverage on short clips and source-local timestamps.
                count = min(self.max_frame_evidence,
                            max(1, math.ceil(meta.duration_s / self.frame_evidence_interval_s)))
                frame_times = [round(meta.duration_s * (index + 0.5) / count, 3)
                               for index in range(count)]
                try:
                    frame_images = await self.media.keyframes(meta, frame_times, width=self.keyframe_width)
                    if len(frame_images) != len(frame_times):
                        degraded = (*degraded, "frame evidence incomplete: source frame extraction returned fewer images")
                    for at, frame in zip(frame_times, frame_images):
                        for attempt in range(2):
                            try:
                                description = await describe_frame(meta, frame, at)
                                frame_evidence.append(FrameEvidence(
                                    time_s=at, description=description, model_id=self.vision.model_id))
                                break
                            except (ProviderError, MediaToolError, asyncio.TimeoutError) as error:
                                if attempt == 0:
                                    await asyncio.sleep(0.25)
                                else:
                                    degraded = (*degraded, f"frame evidence at {at:.2f}s failed: "
                                                f"{type(error).__name__}: {error}")
                except (MediaToolError, asyncio.TimeoutError) as error:
                    degraded = (*degraded, f"frame evidence extraction failed: {type(error).__name__}: {error}")

        result = fuse(meta, signals, observation, vision_model=self.vision.model_id if self.vision else None, degraded=degraded)
        if frame_evidence:
            descriptions = [set(re.findall(r"[a-z0-9]+", item.description.casefold()))
                            for item in frame_evidence]
            def corroborated(label: str) -> bool:
                terms = set(re.findall(r"[a-z0-9]+", label.casefold()))
                return bool(terms) and sum(terms <= words for words in descriptions) >= min(2, len(descriptions))

            scene_type = result.scene_type if corroborated(result.scene_type) else ""
            visual_tags = tuple(tag for tag in result.visual_tags if corroborated(tag))
            note = (" Clip-wide scene labels were withheld when fewer than two sampled frames supported them; "
                    "timestamped model descriptions are retained as uncertain evidence.")
            result = revalidate(result, frame_evidence=tuple(frame_evidence),
                                scene_type=scene_type, visual_tags=visual_tags,
                                reasoning=result.reasoning + note)
        if arbitration is not None:
            result = revalidate(result, provenance=revalidate(result.provenance, arbitration=arbitration))
        if self.calibrator is not None:
            result = self.calibrator.calibrate_clip(result)
        vision_failed = self.vision is not None and (observation is None or bool(degraded))
        if self.cache and not vision_failed:  # never cache a degraded result; retry vision next time
            await self.cache.put(key, result)
        return result
