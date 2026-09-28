"""
MONTA — Context Composer (Layer 4)
====================================
Assembles the ``ContextPack`` — the single, immutable input to Layers 5–7.

Sources are fetched concurrently, each under its own timeout. A slow or
failing source degrades the pack (recorded in ``provenance``) instead of
failing the request: MONTA would rather edit without history than not edit.
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Mapping, Sequence
from typing import TypeVar

from memory.narrative_memory import NarrativeLearner, NarrativeMemoryStore, PreferenceStore
from services.context_composer.platform_rules import PLATFORM_SPECS
from services.context_composer.preferences import apply_preferences, infer_preferences
from services.style_engine.presets import STYLE_PRESETS
from shared.contracts.base import revalidate
from shared.contracts.clip import ClipIntelligence, TechnicalMetadata
from shared.contracts.context import ContextPack, ContextSourceStatus, StylePresetRef
from shared.contracts.intent import IntentAnalysis
from shared.contracts.vocab import PACE_ORDER, Emotion, Genre, Pace
from shared.observability import catalog as m

logger = logging.getLogger("monta.context_composer")

T = TypeVar("T")

# Which intents each Layer 8 preset naturally serves.
PRESET_AFFINITY: dict[str, dict[str, set[str]]] = {
    "cinematic": {"genre": {Genre.CINEMATIC, Genre.DOCUMENTARY, Genre.LUXURY, Genre.TRAVEL, Genre.WEDDING},
                  "emotion": {Emotion.DRAMATIC, Emotion.EMOTIONAL, Emotion.LUXURY, Emotion.INSPIRING}},
    "trailer": {"genre": {Genre.CINEMATIC, Genre.SPORTS, Genre.PRODUCT},
                "emotion": {Emotion.DRAMATIC, Emotion.INTENSE, Emotion.MOTIVATIONAL}},
    "vlog": {"genre": {Genre.LIFESTYLE, Genre.TRAVEL, Genre.EDUCATION, Genre.PODCAST},
             "emotion": {Emotion.CALM, Emotion.JOYFUL, Emotion.UPLIFTING, Emotion.NOSTALGIC}},
    "hype": {"genre": {Genre.FITNESS, Genre.SPORTS, Genre.EVENT},
             "emotion": {Emotion.ENERGETIC, Emotion.AGGRESSIVE, Emotion.INTENSE, Emotion.MOTIVATIONAL}},
    "montage": {"genre": {Genre.SPORTS, Genre.FITNESS, Genre.EVENT, Genre.TRAVEL, Genre.FASHION},
                "emotion": {Emotion.ENERGETIC, Emotion.INSPIRING, Emotion.JOYFUL}},
}


def _pace_similarity(a: str, b: Pace) -> float:
    try:
        return max(0.0, 1 - abs(PACE_ORDER[Pace(a)] - PACE_ORDER[b]) / 2)
    except ValueError:
        return 0.0


def anonymize_foreign(records, user_id: str) -> tuple:
    """Other creators' successes are useful as patterns, never as identities: strip their ids."""
    out = []
    for r in records:
        if r.user_id == user_id:
            out.append(r)
            continue
        ref = hashlib.sha256(f"monta:{r.project_id}".encode()).hexdigest()[:16]
        out.append(revalidate(r, user_id="anonymous", project_id=ref, record_id=ref))
    return tuple(out)


def rank_style_presets(intent: IntentAnalysis, presets: Mapping[str, dict], limit: int = 3) -> tuple[StylePresetRef, ...]:
    """Score Layer 8 presets against intent: pacing (0.4), colour (0.3), genre/emotion affinity (0.3)."""
    ranked = []
    for name, params in presets.items():
        pace_s = _pace_similarity(params.get("cut_speed", ""), intent.pace.value)
        color_s = 1.0 if intent.color_grade and params.get("color_grade") == intent.color_grade.value.value else 0.0
        aff = PRESET_AFFINITY.get(name, {})
        aff_s = 0.5 * (intent.genre.value in aff.get("genre", set())) + 0.5 * (intent.emotion.value in aff.get("emotion", set()))
        relevance = round(0.4 * pace_s + 0.3 * color_s + 0.3 * aff_s, 3)
        if relevance <= 0:
            continue
        reasoning = (
            f"cut_speed '{params.get('cut_speed')}' vs pace '{intent.pace.value}' ({pace_s:.2f}); "
            f"color {'matches' if color_s else 'does not match'}; genre/emotion affinity {aff_s:.2f}."
        )
        ranked.append(StylePresetRef(name=name, parameters=dict(params), relevance=relevance, reasoning=reasoning))
    ranked.sort(key=lambda p: p.relevance, reverse=True)
    return tuple(ranked[:limit])


class ContextComposer:
    def __init__(
        self,
        *,
        memory: NarrativeMemoryStore,
        preferences: PreferenceStore,
        learner: NarrativeLearner,
        pattern_ids: Sequence[str],
        style_presets: Mapping[str, dict] = STYLE_PRESETS,
        source_timeout_s: float = 1.5,
        history_limit: int = 50,
        successful_limit: int = 10,
        experiment=None,
    ):
        """``experiment`` (evaluation.memory_experiment.MemoryExperiment) holds users out of narrative memory."""
        self.memory = memory
        self.preferences = preferences
        self.learner = learner
        self.pattern_ids = list(pattern_ids)
        self.style_presets = style_presets
        self.timeout = source_timeout_s
        self.history_limit = history_limit
        self.successful_limit = successful_limit
        self.experiment = experiment

    async def _timed(self, source: str, coro: Awaitable[T], empty: T) -> tuple[T, ContextSourceStatus]:
        started = time.perf_counter()
        try:
            value = await asyncio.wait_for(coro, self.timeout)
            status, detail = "ok", ""
        except asyncio.TimeoutError:
            value, status, detail = empty, "unavailable", f"timed out after {self.timeout}s"
        except Exception as e:  # a context source must never fail the edit
            logger.exception("context source %s failed", source)
            value, status, detail = empty, "unavailable", f"{type(e).__name__}: {e}"
        latency = (time.perf_counter() - started) * 1000
        m.CONTEXT_SOURCE.inc(source=source, status=status)
        return value, ContextSourceStatus(source=source, status=status, latency_ms=round(latency, 2), detail=detail)

    async def compose(
        self, *, project_id: str, user_id: str, intent: IntentAnalysis, clips: Sequence[TechnicalMetadata]
    ) -> ContextPack:
        if not clips:
            raise ValueError("ContextPack requires at least one clip")
        arm = self.experiment.assign(user_id) if self.experiment is not None else "memory"
        use_memory = arm == "memory"
        (explicit, s_explicit), (history, s_history) = await asyncio.gather(
            self._timed("explicit_preferences", self.preferences.get(user_id), None),
            self._timed("edit_history", self.memory.for_user(user_id, self.history_limit), []) if use_memory
            else self._skipped("edit_history"),
        )
        prefs = infer_preferences(user_id, explicit, history)
        intent = apply_preferences(intent, prefs)

        (priors, s_priors), (successful, s_success) = await asyncio.gather(
            self._timed("pattern_priors", self.learner.priors(intent.genre.value, self.pattern_ids), []) if use_memory
            else self._skipped("pattern_priors"),
            self._timed("successful_projects", self.memory.successful(intent.genre.value, limit=self.successful_limit), [])
            if use_memory else self._skipped("successful_projects"),
        )
        return ContextPack(
            project_id=project_id,
            user_id=user_id,
            user_prompt=intent.raw_prompt,
            intent=intent,
            user_preferences=prefs,
            historical_edits=tuple(history),
            previous_successful_projects=anonymize_foreign(successful, user_id),
            style_presets=rank_style_presets(intent, self.style_presets),
            story_patterns=tuple(priors),
            platform=PLATFORM_SPECS[intent.target_platform.value],
            video_metadata=tuple(clips),
            provenance=(s_explicit, s_history, s_priors, s_success),
            memory_arm=arm,
        )

    @staticmethod
    async def _skipped(source: str):
        return [], ContextSourceStatus(source=source, status="ok", latency_ms=0.0,
                                       detail="skipped: user is in the narrative-memory control arm")

    async def enrich_with_footage(
        self, pack: ContextPack, intent: IntentAnalysis, clip_intelligence: Mapping[str, ClipIntelligence]
    ) -> ContextPack:
        """Attach Layer 6 results; refresh genre-dependent context if footage revised the genre."""
        updates: dict = {"intent": intent, "clip_intelligence": dict(clip_intelligence)}
        if intent.genre.value != pack.intent.genre.value:
            updates["style_presets"] = rank_style_presets(intent, self.style_presets)
            if pack.memory_arm == "memory":
                (priors, s_priors), (successful, s_success) = await asyncio.gather(
                    self._timed("pattern_priors", self.learner.priors(intent.genre.value, self.pattern_ids), []),
                    self._timed("successful_projects", self.memory.successful(intent.genre.value, limit=self.successful_limit), []),
                )
                kept = tuple(s for s in pack.provenance if s.source not in ("pattern_priors", "successful_projects"))
                updates |= {
                    "story_patterns": tuple(priors),
                    "previous_successful_projects": anonymize_foreign(successful, pack.user_id),
                    "provenance": kept + (s_priors, s_success),
                }
        if intent.target_platform.value != pack.platform.platform:
            updates["platform"] = PLATFORM_SPECS[intent.target_platform.value]
        return revalidate(pack, **updates)

