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
import logging
import math
import re
from collections import OrderedDict
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
        max_frame_evidence: int = 6,
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
        self.calibrator = calibrator

    def cache_key(self, meta: TechnicalMetadata) -> str:
        vision_id = self.vision.model_id if self.vision else "none"
        return f"{meta.fingerprint}|{SIGNAL_VERSION}|{FUSION_VERSION}|frame-evidence.v1|{vision_id}"

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
                describe_frame = getattr(self.vision, "describe_frame", None)
                if describe_frame is not None and self.max_frame_evidence > 0:
                    count = min(self.max_frame_evidence, max(1, math.ceil(meta.duration_s / 4)))
                    frame_times = [round(meta.duration_s * (index + 0.5) / count, 3)
                                   for index in range(count)]
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
            except (ProviderError, MediaToolError, asyncio.TimeoutError) as e:
                logger.warning("vision analysis failed for %s: %s", meta.clip_id, e)
                degraded = (f"vision failed: {type(e).__name__}: {e}",)
                m.VISION_DEGRADED.inc(reason=type(e).__name__)

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
