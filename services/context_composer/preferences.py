"""
MONTA — Preference Inference
==============================
Derives explainable ``UserPreferences`` from explicit settings plus rated
narrative history, and applies them to intent fields the prompt left open.

Precedence (highest first): what the creator says in this prompt → explicit
settings → preferences inferred from rated history → policy defaults.

History convention: ``successful_elements`` entries prefixed ``caption:``,
``music:`` or ``color:`` record the style used, e.g. ``"caption:Nike"``.
"""

from collections import Counter, defaultdict
from enum import StrEnum

from shared.contracts.base import revalidate
from shared.contracts.context import ExplicitPreferences, UserPreferences
from shared.contracts.explain import Evidence, Explained, clamp
from shared.contracts.intent import IntentAnalysis
from shared.contracts.memory import NarrativeMemoryRecord
from shared.contracts.vocab import CaptionStyle, ColorGrade, MusicStyle, Pace, Platform

EXPLICIT_CONFIDENCE = 0.95
LIKED_RATING = 7.0
MIN_SAMPLES = 2
APPLY_INFERRED_ABOVE = 0.5

_STYLE_PREFIXES: dict[str, type[StrEnum]] = {"caption": CaptionStyle, "music": MusicStyle, "color": ColorGrade}


def _explicit(value, enum, label: str) -> Explained:
    return Explained[enum](
        value=value, confidence=EXPLICIT_CONFIDENCE, reasoning=f"Set explicitly in your {label} settings.",
        evidence=(Evidence(source="preferences", detail=f"explicit {label}"),),
    )


def infer_preferences(user_id: str, explicit: ExplicitPreferences | None, history: list[NarrativeMemoryRecord]) -> UserPreferences:
    rated = list(history)
    fields: dict[str, Explained | None] = {"pace": None, "color_grade": None, "caption_style": None, "music_style": None, "platform": None}

    # Pace: net rating signal per pace; (rating - 5) / 5 in [-1, 1].
    pace_net: dict[Pace, float] = defaultdict(float)
    pace_n: Counter[Pace] = Counter()
    for r in rated:
        if r.pace:
            pace_net[r.pace] += (r.user_rating - 5) / 5
            pace_n[r.pace] += 1
    if pace_net:
        best, net = max(pace_net.items(), key=lambda kv: kv[1])
        if net >= 1.0 and pace_n[best] >= MIN_SAMPLES:
            conf = round(clamp(net / (pace_n[best] + 2), 0, 0.85), 3)
            fields["pace"] = Explained[Pace](
                value=best, confidence=conf,
                reasoning=f"You rated {pace_n[best]} {best.value}-paced edits positively (net signal {net:.1f}).",
                evidence=(Evidence(source="memory", detail=f"{pace_n[best]} rated {best.value} edits", weight=net),),
            )

    # Style elements from well-rated stories.
    style_counts: dict[str, Counter] = defaultdict(Counter)
    liked = [r for r in rated if r.user_rating >= LIKED_RATING]
    for r in liked:
        for element in r.successful_elements:
            prefix, _, value = element.partition(":")
            enum = _STYLE_PREFIXES.get(prefix)
            if enum and value in enum._value2member_map_:
                style_counts[prefix][value] += 1
    for prefix, field in (("caption", "caption_style"), ("music", "music_style"), ("color", "color_grade")):
        if style_counts[prefix]:
            value, n = style_counts[prefix].most_common(1)[0]
            if n >= MIN_SAMPLES:
                enum = _STYLE_PREFIXES[prefix]
                fields[field] = Explained[enum](
                    value=enum(value), confidence=round(clamp(n / (len(liked) + 1), 0, 0.85), 3),
                    reasoning=f"{n} of your {len(liked)} highly rated edits used {prefix} '{value}'.",
                    evidence=(Evidence(source="memory", detail=f"{n} liked edits with {prefix}:{value}", weight=n),),
                )

    # Patterns.
    by_pattern: dict[str, list[float]] = defaultdict(list)
    for r in rated:
        by_pattern[r.story_pattern].append(r.user_rating)
    preferred = tuple(sorted(p for p, rs in by_pattern.items() if len(rs) >= MIN_SAMPLES and sum(rs) / len(rs) >= 7.5))
    disliked = {p for p, rs in by_pattern.items() if len(rs) >= MIN_SAMPLES and sum(rs) / len(rs) <= 4.0}

    if explicit:
        disliked |= set(explicit.blocked_patterns)
        for attr, field, enum, label in (
            ("pace", "pace", Pace, "pace"), ("color_grade", "color_grade", ColorGrade, "color"),
            ("caption_style", "caption_style", CaptionStyle, "caption"), ("music_style", "music_style", MusicStyle, "music"),
            ("platform", "platform", Platform, "platform"),
        ):
            value = getattr(explicit, attr)
            if value is not None:
                fields[field] = _explicit(value, enum, label)

    return UserPreferences(
        user_id=user_id, **fields, preferred_patterns=tuple(p for p in preferred if p not in disliked),
        disliked_patterns=tuple(sorted(disliked)), sample_size=len(rated),
    )


def apply_preferences(intent: IntentAnalysis, prefs: UserPreferences) -> IntentAnalysis:
    """Fill intent fields the prompt left unspecified. Never overrides what the creator just said."""
    updates: dict = {}
    revisions = list(intent.revisions)
    resolved: set[str] = set()

    for field, pref_field in (("pace", "pace"), ("target_platform", "platform")):
        current: Explained = getattr(intent, field)
        pref: Explained | None = getattr(prefs, pref_field)
        if current.is_default and pref and pref.confidence >= APPLY_INFERRED_ABOVE:
            updates[field] = revalidate(pref, **{"reasoning": f"Not specified in the prompt. {pref.reasoning}"})
            revisions.append(f"{field} filled from preferences: '{pref.value}'")
            resolved.add(field)

    for field in ("color_grade", "caption_style", "music_style"):
        pref: Explained | None = getattr(prefs, field)
        if getattr(intent, field) is None and pref and pref.confidence >= APPLY_INFERRED_ABOVE:
            updates[field] = revalidate(pref, **{"reasoning": f"Not specified in the prompt. {pref.reasoning}"})
            revisions.append(f"{field} filled from preferences: '{pref.value}'")

    if not updates:
        return intent
    return revalidate(intent, **{
        **updates,
        "revisions": tuple(revisions),
        "missing": tuple(m for m in intent.missing if m.field not in resolved),
    })
