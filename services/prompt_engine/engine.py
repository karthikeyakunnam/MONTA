"""
MONTA — Prompt Intelligence Engine (Layer 3)
==============================================
Public entry point for intent extraction.

``analyze`` runs the deterministic lexical extractor (microseconds, no I/O)
and — when a text provider is configured — the LLM extractor, then merges
them field by field:

* agree      → confidences combine (1 - (1-a)(1-b)); evidence is unioned.
* one-sided  → the side with evidence wins; LLM-only values are discounted
               because they have no span-level grounding.
* disagree   → the more confident side wins, its confidence is reduced by the
               loser's, and an ``Ambiguity`` is recorded for the Critic.

LLM failure never fails the request: the lexical result is returned with the
failure listed under ``degraded``.

``reconcile_with_footage`` revises low-confidence genre/emotion using Layer 6
evidence once clips are analyzed ("make it cool" + gym footage → fitness).
"""

import asyncio
import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from enum import StrEnum

from services.prompt_engine import lexicon as L
from services.prompt_engine.lexical_extractor import ENUMS, LexicalIntentExtractor
from services.prompt_engine.llm_extractor import LLMField, LLMIntent, LLMIntentExtractor
from shared.contracts.base import revalidate
from shared.contracts.clip import ClipIntelligence
from shared.contracts.explain import Evidence, Explained, clamp
from shared.contracts.intent import Ambiguity, ArcBeat, Conflict, IntentAnalysis
from shared.contracts.vocab import Emotion, Genre
from shared.providers.base import ModelProvider
from shared.providers.errors import ProviderError
from shared.observability import catalog as m
from shared.observability.tracing import span
from shared.text import phrases_in, tokenize

logger = logging.getLogger("monta.prompt_engine")

LLM_ONLY_DISCOUNT = 0.85
FOOTAGE_OVERRIDE_BELOW = 0.5

FOOTAGE_GENRE_CUES: dict[Genre, tuple[str, ...]] = {
    Genre.FITNESS: ("gym", "deadlift", "squat", "bench press", "barbell", "dumbbell", "weights", "workout", "lifting",
                    "treadmill", "kettlebell", "pull-up", "pullup", "push-up", "pushup", "flex"),
    Genre.SPORTS: ("ball", "stadium", "court", "soccer", "football", "basketball", "jersey", "racket", "skateboard",
                   "surfboard", "race", "boxing ring", "goalpost", "athlete"),
    Genre.TRAVEL: ("beach", "mountain", "airport", "airplane", "landmark", "hotel", "skyline", "hiking", "ocean",
                   "waterfall", "passport", "luggage", "tourist", "sightseeing"),
    Genre.WEDDING: ("bride", "groom", "wedding", "altar", "bouquet", "veil", "ring exchange", "vows"),
    Genre.EVENT: ("stage", "crowd", "concert", "audience", "festival", "party", "dj", "podium", "balloons", "confetti"),
    Genre.PRODUCT: ("product", "packaging", "unboxing", "packshot", "device", "bottle", "logo"),
    Genre.PODCAST: ("microphone", "podcast", "headphones", "interview", "talking head"),
    Genre.EDUCATION: ("whiteboard", "screen recording", "slides", "diagram", "classroom", "tutorial"),
    Genre.FASHION: ("runway", "outfit", "posing", "clothing", "mirror", "fashion"),
    Genre.LUXURY: ("yacht", "supercar", "wristwatch", "champagne", "mansion", "jewelry"),
    Genre.BUSINESS: ("office", "meeting", "conference room", "handshake", "presentation"),
    Genre.LIFESTYLE: ("kitchen", "coffee", "bedroom", "cooking", "breakfast"),
}


def record_intent_metrics(intent: IntentAnalysis) -> None:
    for field in (L.GENRE, L.EMOTION, L.PACE, L.PLATFORM):
        f: Explained = getattr(intent, field)
        m.INTENT_CONFIDENCE.observe(f.confidence, field=field)
        if f.is_default:
            m.INTENT_FIELD_DEFAULT.inc(field=field)
    for kind, n in (("ambiguity", len(intent.ambiguities)), ("conflict", len(intent.conflicts)),
                    ("degraded", len(intent.degraded)), ("correction", len(intent.corrections))):
        if n:
            m.INTENT_ISSUES.inc(n, kind=kind)


def _values(f: Explained | None) -> str | None:
    if f is None:
        return None
    return f.value.value if isinstance(f.value, StrEnum) else str(f.value)


class IntentEngine:
    def __init__(self, llm: ModelProvider | Sequence[ModelProvider] | None = None, *, llm_timeout_s: float = 12.0,
                 calibrator=None, reliability=None):
        """
        ``llm``: one provider, or several (arbitrated per field by measured reliability).
        ``calibrator`` (shared.calibration.Calibrator) maps raw confidences to measured accuracy.
        """
        self.lexical = LexicalIntentExtractor()
        has_llm = llm is not None and (not isinstance(llm, Sequence) or len(llm) > 0)
        self.llm = LLMIntentExtractor(llm, timeout_s=llm_timeout_s, reliability=reliability) if has_llm else None
        self.calibrator = calibrator

    async def analyze(self, prompt: str) -> IntentAnalysis:
        """Interpret a raw creator prompt. Never raises for user input. Instrumented."""
        with span("intent.analyze", prompt_chars=len(prompt or "")) as sp:
            intent = await self._analyze(prompt)
            if self.calibrator is not None:
                intent = self.calibrator.calibrate_intent(intent)
            sp.set(extractors=",".join(intent.extractors), confidence=intent.overall_confidence)
        record_intent_metrics(intent)
        return intent

    async def _analyze(self, prompt: str) -> IntentAnalysis:
        lexical = self.lexical.extract(prompt or "")
        if self.llm is None:
            return revalidate(lexical, **{"degraded": lexical.degraded + ("no text LLM configured; lexical extraction only",)})
        try:
            llm_value, arbitration = await self.llm.extract(prompt or "")
        except (ProviderError, asyncio.TimeoutError) as e:
            logger.warning("LLM intent extraction failed: %s", e)
            return revalidate(lexical, **{"degraded": lexical.degraded + (f"LLM extraction failed: {type(e).__name__}: {e}",)})
        merged = self.merge(lexical, llm_value, self.llm.name)
        if arbitration is None:
            return merged
        low = [r for r in arbitration.records if r.field in (L.GENRE, L.EMOTION, L.PACE, L.PLATFORM)
               and r.kind == "explained" and r.rule != "unanimous" and r.margin < 0.3]
        extra = tuple(Ambiguity(field=r.field, candidates=tuple(dict.fromkeys(c.value for c in r.candidates)),
                                reasoning=f"models disagree on {r.field}: {r.rationale}",
                                clarifying_question=f"Which {r.field.replace('_', ' ')} did you mean?") for r in low)
        return revalidate(merged, arbitration=arbitration, ambiguities=merged.ambiguities + extra)

    # ------------------------------------------------------------------ merging


    def merge(self, lexical: IntentAnalysis, llm: LLMIntent, llm_name: str) -> IntentAnalysis:
        updates: dict = {}
        ambiguities = list(lexical.ambiguities)
        missing = {m.field: m for m in lexical.missing}

        for dim in (*ENUMS, L.REFERENCE, "target_duration_s"):
            lex_field: Explained | None = getattr(lexical, dim)
            llm_field: LLMField | None = getattr(llm, dim)
            merged, amb = self._merge_field(dim, lex_field, llm_field)
            updates[dim] = merged
            if amb:
                ambiguities.append(amb)
            if merged is not None and not merged.is_default:
                missing.pop(dim, None)

        arc = lexical.pacing_arc or tuple(
            ArcBeat(section=b.section, pace=b.pace, emotion=b.emotion, span=b.span,
                    reasoning=f"LLM identified a {b.section}-of-edit instruction: '{b.span}'.")
            for b in llm.pacing_arc if b.pace or b.emotion
        )
        known_amb = {(a.field, frozenset(a.candidates)) for a in ambiguities}
        for a in llm.ambiguities:
            if (a.field, frozenset(a.candidates)) not in known_amb:
                ambiguities.append(Ambiguity(field=a.field, candidates=tuple(a.candidates), reasoning=a.reasoning,
                                             clarifying_question=a.clarifying_question))
        conflicts = list(lexical.conflicts)
        known_conf = {frozenset(c.values) for c in conflicts}
        for c in llm.conflicts:
            if frozenset(c.values) not in known_conf:
                conflicts.append(Conflict(fields=tuple(c.fields), values=tuple(c.values), severity=c.severity,
                                          reasoning=c.reasoning, resolution=c.resolution))

        core = [updates[d] for d in (L.GENRE, L.EMOTION, L.PACE, L.PLATFORM)]
        overall = sum(f.confidence for f in core) / 4
        overall -= 0.1 * sum(1 for c in conflicts if c.severity == "hard") + 0.04 * len(ambiguities)

        return revalidate(lexical, **{
            **updates,
            "pacing_arc": arc,
            "ambiguities": tuple(ambiguities),
            "conflicts": tuple(conflicts),
            "missing": tuple(missing.values()),
            "overall_confidence": round(clamp(overall), 3),
            "extractors": lexical.extractors + (llm_name,),
        })

    @staticmethod
    def _merge_field(dim: str, lex: Explained | None, llm: LLMField | None) -> tuple[Explained | None, Ambiguity | None]:
        if llm is None:
            return lex, None
        llm_value = llm.value.value if isinstance(llm.value, StrEnum) else llm.value
        typ = ENUMS.get(dim, float if dim == "target_duration_s" else str)
        llm_ev = Evidence(source="llm", detail=llm.reasoning, weight=llm.confidence, span=llm.evidence_span)

        if lex is None or lex.is_default:
            conf = round(llm.confidence * LLM_ONLY_DISCOUNT, 3)
            return Explained[typ](
                value=llm.value, confidence=conf,
                reasoning=f"LLM inferred '{llm_value}': {llm.reasoning} (no lexical corroboration, confidence discounted).",
                evidence=(llm_ev,),
            ), None

        lex_value = _values(lex)
        if dim == "target_duration_s":
            agree = abs(float(lex.value) - float(llm.value)) < 1.0
        else:
            agree = lex_value == llm_value
        if agree:
            conf = round(clamp(1 - (1 - lex.confidence) * (1 - llm.confidence), 0, 0.99), 3)
            return revalidate(lex, **{
                "confidence": conf,
                "reasoning": f"{lex.reasoning} LLM agrees: {llm.reasoning}",
                "evidence": lex.evidence + (llm_ev,),
            }), None

        compatible = any({lex_value, llm_value} <= group for group in L.COMPATIBLE_VALUES.get(dim, []))
        if llm.confidence > lex.confidence:
            winner_value, winner_conf, loser_value, loser_conf = llm.value, llm.confidence, lex_value, lex.confidence
            reasoning = f"LLM chose '{llm_value}' ({llm.reasoning}) over lexical '{lex_value}' ({lex.reasoning})"
            evidence = (llm_ev,) + lex.evidence
        else:
            winner_value, winner_conf, loser_value, loser_conf = lex.value, lex.confidence, llm_value, llm.confidence
            reasoning = f"Lexical '{lex_value}' kept over LLM '{llm_value}' ({llm.reasoning}). {lex.reasoning}"
            evidence = lex.evidence + (llm_ev,)
        penalty = 0.25 if compatible else 0.5
        conf = round(clamp(winner_conf * (1 - penalty * loser_conf), 0.05, 0.97), 3)
        merged = Explained[typ](value=winner_value, confidence=conf, reasoning=reasoning + ".", evidence=evidence)
        winner_str = winner_value.value if isinstance(winner_value, StrEnum) else str(winner_value)
        amb = None if compatible else Ambiguity(
            field=dim, candidates=(winner_str, str(loser_value)),
            reasoning=f"Extractors disagree on {dim}: '{winner_str}' vs '{loser_value}'.",
            clarifying_question=f"Did you mean {winner_str} or {loser_value}?",
        )
        return merged, amb

    # ------------------------------------------------------------------ footage

    def reconcile_with_footage(self, intent: IntentAnalysis, clips: list[ClipIntelligence]) -> IntentAnalysis:
        """Revise weakly-supported genre/emotion using what the footage actually shows."""
        usable = [c for c in clips if c.usable]
        if not usable:
            return intent
        updates: dict = {}
        revisions = list(intent.revisions)
        resolved_missing: set[str] = set()

        genre_guess = self._footage_genre(usable)
        if genre_guess and (intent.genre.is_default or intent.genre.confidence < FOOTAGE_OVERRIDE_BELOW):
            genre, conf, reasoning = genre_guess
            if genre == intent.genre.value:
                new_conf = round(clamp(1 - (1 - intent.genre.confidence) * (1 - conf), 0, 0.97), 3)
                updates["genre"] = revalidate(intent.genre, **{
                    "confidence": new_conf, "reasoning": f"{intent.genre.reasoning} Footage agrees: {reasoning}",
                    "evidence": intent.genre.evidence + (Evidence(source="footage", detail=reasoning, weight=conf),),
                })
                revisions.append(f"genre '{genre}' confirmed by footage (confidence {intent.genre.confidence} → {new_conf})")
            elif conf > intent.genre.confidence:
                updates["genre"] = Explained[Genre](
                    value=genre, confidence=round(conf, 3),
                    reasoning=f"Footage shows {reasoning}; the prompt only weakly suggested '{intent.genre.value}'.",
                    evidence=(Evidence(source="footage", detail=reasoning, weight=conf),) + tuple(
                        e for e in intent.genre.evidence if e.source != "default"),
                )
                revisions.append(f"genre revised '{intent.genre.value}' → '{genre}' from footage ({reasoning})")
                resolved_missing.add(L.GENRE)

        if intent.emotion.is_default:
            emotion_guess = self._footage_emotion(usable)
            if emotion_guess:
                emotion, conf, reasoning = emotion_guess
                updates["emotion"] = Explained[Emotion](
                    value=emotion, confidence=round(conf, 3), reasoning=f"No emotion requested; footage reads as {reasoning}.",
                    evidence=(Evidence(source="footage", detail=reasoning, weight=conf),),
                )
                revisions.append(f"emotion inferred from footage: '{emotion}'")
                resolved_missing.add(L.EMOTION)

        if not updates:
            return intent
        merged = revalidate(intent, **updates)
        core = [merged.genre, merged.emotion, merged.pace, merged.target_platform]
        overall = sum(f.confidence for f in core) / 4 - 0.1 * sum(1 for c in merged.conflicts if c.severity == "hard") \
            - 0.04 * len(merged.ambiguities)
        return revalidate(merged, **{
            "revisions": tuple(revisions),
            "missing": tuple(m for m in merged.missing if m.field not in resolved_missing),
            "overall_confidence": round(clamp(overall), 3),
        })

    @staticmethod
    def _footage_genre(clips: list[ClipIntelligence]) -> tuple[Genre, float, str] | None:
        hits: dict[Genre, list[str]] = defaultdict(list)
        for c in clips:
            tokens = tokenize(" | ".join((*c.activities, *c.objects, c.scene_type, *c.visual_tags)))
            for genre, cues in FOOTAGE_GENRE_CUES.items():
                matched = phrases_in(tokens, cues)
                if matched:
                    hits[genre].append(f"{c.clip_id}:{matched[0]}")
        if not hits:
            return None
        genre, examples = max(hits.items(), key=lambda kv: len(kv[1]))
        coverage = len(examples) / len(clips)
        vision_share = sum(1 for c in clips if c.provenance.vision_model) / len(clips)
        conf = clamp(0.9 * coverage * vision_share)
        if conf < 0.2:
            return None
        return genre, conf, f"{len(examples)}/{len(clips)} clips with {genre.value} content ({', '.join(examples[:3])})"

    @staticmethod
    def _footage_emotion(clips: list[ClipIntelligence]) -> tuple[Emotion, float, str] | None:
        weights: Counter[Emotion] = Counter()
        for c in clips:
            if c.emotion and c.emotion != Emotion.NEUTRAL:
                weights[c.emotion] += c.emotion_confidence
        if not weights:
            return None
        emotion, w = weights.most_common(1)[0]
        share = w / sum(weights.values())
        mean_conf = w / sum(1 for c in clips if c.emotion == emotion)
        conf = clamp(0.7 * share * mean_conf)
        if conf < 0.2:
            return None
        return emotion, conf, f"predominantly '{emotion.value}' ({share:.0%} of emotional signal)"


