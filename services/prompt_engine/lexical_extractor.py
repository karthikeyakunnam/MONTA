"""
MONTA — Lexical Intent Extractor
==================================
Deterministic, span-grounded intent extraction. Always runs (cheap, no
network), provides the evidence trail, and is the full answer whenever the
LLM path is unavailable.

Pipeline: normalize → greedy longest-phrase cue matching → modifiers
(intensifier, postfix intensifier, negation, spelling-correction penalty) →
section attribution for arc language ("slow start", "huge ending") →
per-dimension evidence accumulation → calibrated confidence → ambiguity,
conflict and missing-information detection.
"""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from services.prompt_engine import lexicon as L
from services.prompt_engine.normalizer import BOUNDARY, NormalizedPrompt, normalize
from shared.contracts.explain import Evidence, Explained, clamp
from shared.contracts.intent import Ambiguity, ArcBeat, Conflict, IntentAnalysis, MissingInfo
from shared.contracts.vocab import CaptionStyle, ColorGrade, Emotion, Genre, MusicStyle, Pace, Platform
from shared.text import inflection_bases

SECTION_WINDOW = 3
INTENSIFIER_FACTOR = 1.3
CORRECTION_FACTOR = 0.85
NEGATION_FACTOR = -0.8
AMBIGUITY_RATIO = 0.7
CONFIDENCE_SCALE = 1.2

# Overall-weight of section-attributed evidence. The ending defines the takeaway emotion.
SECTION_OVERALL_WEIGHT = {
    L.EMOTION: {"start": 0.3, "middle": 0.3, "end": 1.0},
    L.PACE: {"start": 0.0, "middle": 0.0, "end": 0.0},
}
# Section pace used for the global pace only when nothing global was said.
SECTION_PACE_FALLBACK = (("end", 0.8), ("middle", 0.6), ("start", 0.5))

NEGATION_IMPLIES = {
    (L.PACE, "fast"): (L.PACE, "medium", 0.5),
    (L.PACE, "aggressive"): (L.PACE, "medium", 0.5),
    (L.PACE, "slow"): (L.PACE, "medium", 0.5),
}

# When the prompt names a mood but no pace, editors pace to the mood.
EMOTION_IMPLIED_PACE: dict[str, str] = {
    "calm": "slow", "romantic": "slow", "emotional": "slow", "nostalgic": "slow", "luxury": "slow",
    "energetic": "fast", "aggressive": "fast", "intense": "fast", "joyful": "fast",
}
IMPLIED_PACE_CONFIDENCE = 0.35
IMPLIED_PACE_MIN_EMOTION_CONFIDENCE = 0.4

CORE_DEFAULTS: dict[str, str] = {
    L.GENRE: Genre.CINEMATIC.value,
    L.EMOTION: Emotion.INSPIRING.value,
    L.PACE: Pace.MEDIUM.value,
    L.PLATFORM: Platform.INSTAGRAM.value,
}
DEFAULT_CONFIDENCE = {L.GENRE: 0.15, L.EMOTION: 0.15, L.PACE: 0.2, L.PLATFORM: 0.3}
DEFAULT_REASONING = {
    L.GENRE: "No genre cues in the prompt; defaulting to 'cinematic' until footage analysis can infer it.",
    L.EMOTION: "No emotional cues in the prompt; defaulting to 'inspiring', the broadest short-form tone.",
    L.PACE: "No pacing cues in the prompt; defaulting to 'medium'.",
    L.PLATFORM: "No platform mentioned; defaulting to Instagram (MONTA's most common destination).",
}

ENUMS: dict[str, type[StrEnum]] = {
    L.GENRE: Genre, L.EMOTION: Emotion, L.PACE: Pace, L.PLATFORM: Platform,
    L.COLOR: ColorGrade, L.CAPTION: CaptionStyle, L.MUSIC: MusicStyle,
}

DECADE_CONTEXT = {"vibe", "style", "look", "aesthetic", "feel", "music", "retro", "vintage", "throwback", "era"}
_NUM_UNIT = re.compile(r"^(\d{1,3}(?:\.\d+)?)(s|sec|secs|second|seconds|m|min|mins|minute|minutes)?$")


@dataclass
class _Match:
    start: int
    end: int
    phrase: str
    signals: list[L.Signal]


@dataclass
class LexicalResult:
    """Intermediate evidence exposed for the ensemble merger and for tests."""

    normalized: NormalizedPrompt
    scores: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))
    evidence: dict[str, dict[str, list[Evidence]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))
    global_scores: dict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))
    beats: list[tuple[str, str, str, float, str]] = field(default_factory=list)  # section, dim, value, weight, span
    duration: tuple[float, str] | None = None
    notes: list[str] = field(default_factory=list)


class LexicalIntentExtractor:
    name = "lexical"

    def extract_evidence(self, prompt: str) -> LexicalResult:
        norm = normalize(prompt)
        result = LexicalResult(normalized=norm)
        if norm.truncated:
            result.notes.append(f"prompt truncated to {len(norm.raw[:4000])} characters")
        tokens = norm.tokens
        clause = self._clause_ids(tokens)
        matches = self._match(tokens)
        matched_idx = {i for m in matches for i in range(m.start, m.end)}

        for m in matches:
            factor, mods = self._modifiers(m, tokens, clause, norm.corrected_indices, matched_idx)
            negated = factor < 0
            # References ("nike") describe the whole edit, never one section of it.
            section, section_idx = (None, None) if m.phrase in L.REFERENCES else self._section(m, tokens, clause)
            span = " ".join(tokens[m.start:m.end])
            for dim, value, base in m.signals:
                weight = base * factor
                if dim in SECTION_OVERALL_WEIGHT and section and not negated:
                    lo, hi = min(m.start, section_idx), max(m.end, section_idx + 1)
                    result.beats.append((section, dim, value, weight, " ".join(tokens[lo:hi])))
                    overall = weight * SECTION_OVERALL_WEIGHT[dim][section]
                else:
                    overall = weight
                    if not negated:
                        result.global_scores[dim][value] += weight
                if overall:
                    self._add(result, dim, value, overall, span, mods, section)
                if negated and (dim, value) in NEGATION_IMPLIES:
                    idim, ivalue, iw = NEGATION_IMPLIES[(dim, value)]
                    self._add(result, idim, ivalue, iw, span, [f"implied by negated '{value}'"], None)
                    result.global_scores[idim][ivalue] += iw

        if not any(v > 0 for v in result.global_scores[L.PACE].values()):
            for section, w in SECTION_PACE_FALLBACK:
                beat = self._best_beat(result, section, L.PACE)
                if beat:
                    value, weight, span = beat
                    self._add(result, L.PACE, value, weight * w, span, [f"from the {section} of the edit"], section)
                    break

        self._parse_duration(tokens, result)
        return result

    def extract(self, prompt: str) -> IntentAnalysis:
        return self.build(self.extract_evidence(prompt))

    # ------------------------------------------------------------------ matching

    @staticmethod
    def _clause_ids(tokens: list[str]) -> list[int]:
        ids, current = [], 0
        for t in tokens:
            if t == BOUNDARY:
                current += 1
            ids.append(current)
        return ids

    @staticmethod
    def _match(tokens: list[str]) -> list[_Match]:
        out, i = [], 0
        while i < len(tokens):
            if tokens[i] == BOUNDARY:
                i += 1
                continue
            for n in range(min(L.MAX_PHRASE_TOKENS, len(tokens) - i), 0, -1):
                window = tokens[i:i + n]
                if BOUNDARY in window:
                    continue
                phrase = " ".join(window)
                if phrase not in L.CUES:
                    # tolerate a regular inflection on the final token ("deadlifts", "weddings")
                    head = " ".join(window[:-1])
                    phrase = next((p for p in (f"{head} {b}".strip() for b in sorted(inflection_bases(window[-1])))
                                   if p in L.CUES), None)
                if phrase is not None:
                    out.append(_Match(i, i + n, phrase, L.CUES[phrase]))
                    i += n
                    break
            else:
                i += 1
        return out

    @staticmethod
    def _modifiers(m: _Match, tokens, clause, corrected: set[int], matched: set[int]) -> tuple[float, list[str]]:
        factor, mods = 1.0, []
        c = clause[m.start]
        before = [j for j in range(max(0, m.start - SECTION_WINDOW), m.start) if clause[j] == c and tokens[j] != BOUNDARY]
        if any(tokens[j] in L.INTENSIFIERS for j in before[-2:] if j not in matched):
            factor *= INTENSIFIER_FACTOR
            mods.append("intensified")
        if m.end < len(tokens) and tokens[m.end] in L.POSTFIX_INTENSIFIERS:
            factor *= INTENSIFIER_FACTOR
            mods.append("intensified")
        if any(i in corrected for i in range(m.start, m.end)):
            factor *= CORRECTION_FACTOR
            mods.append("spelling-corrected")
        if any(tokens[j] in L.NEGATORS for j in before if j not in matched):
            factor *= NEGATION_FACTOR
            mods.append("negated")
        return factor, mods

    @staticmethod
    def _section(m: _Match, tokens, clause) -> tuple[str | None, int | None]:
        """Nearest section word in the same clause within the window, with its token index."""
        c = clause[m.start]
        best: tuple[int, str, int] | None = None
        for j in range(max(0, m.start - SECTION_WINDOW), min(len(tokens), m.end + SECTION_WINDOW)):
            if m.start <= j < m.end or clause[j] != c:
                continue
            section = L.SECTION_WORDS.get(tokens[j])
            if section:
                dist = m.start - j if j < m.start else j - m.end + 1
                if best is None or dist < best[0]:
                    best = (dist, section, j)
        return (best[1], best[2]) if best else (None, None)

    @staticmethod
    def _add(result: LexicalResult, dim, value, weight, span, mods, section) -> None:
        result.scores[dim][value] += weight
        detail = f"'{span}' → {value} ({weight:+.2f}"
        if mods:
            detail += ", " + ", ".join(mods)
        if section:
            detail += f", {section} of edit"
        detail += ")"
        result.evidence[dim][value].append(Evidence(source="prompt", detail=detail, weight=round(weight, 3), span=span))

    @staticmethod
    def _best_beat(result: LexicalResult, section: str, dim: str) -> tuple[str, float, str] | None:
        cands = [(w, v, s) for sec, d, v, w, s in result.beats if sec == section and d == dim and w > 0]
        if not cands:
            return None
        w, v, s = max(cands)
        return v, w, s

    def _parse_duration(self, tokens: list[str], result: LexicalResult) -> None:
        for i, tok in enumerate(tokens):
            m = _NUM_UNIT.match(tok)
            if not m:
                continue
            number = float(m.group(1))
            unit = m.group(2)
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            prev = tokens[i - 1] if i > 0 else ""
            if unit == "s" and number in (50, 60, 70, 80, 90) and (nxt in DECADE_CONTEXT or prev == "the"):
                span = f"{tok} {nxt}".strip()
                self._add(result, L.COLOR, "vintage", 0.8, span, ["decade reference"], None)
                self._add(result, L.EMOTION, "nostalgic", 0.4, span, ["decade reference"], None)
                continue
            if unit is None:
                if nxt in ("seconds", "second", "s"):
                    unit, span = "s", f"{tok} {nxt}"
                elif nxt in ("minutes", "minute", "m"):
                    unit, span = "m", f"{tok} {nxt}"
                else:
                    continue
            else:
                span = tok
            seconds = number * 60 if unit.startswith("m") else number
            if 3 <= seconds <= 900:
                result.duration = (seconds, span)
                return
            result.notes.append(f"ignored implausible duration '{span}'")

    # ------------------------------------------------------------------ building

    def build(self, r: LexicalResult) -> IntentAnalysis:
        fields: dict[str, Explained | None] = {}
        ambiguities: list[Ambiguity] = []
        missing: list[MissingInfo] = []
        for dim, enum in ENUMS.items():
            f, amb = self.decide(r, dim, enum)
            if f is None and dim == L.PACE:
                emo = fields.get(L.EMOTION)
                implied = EMOTION_IMPLIED_PACE.get(emo.value.value) if emo is not None and not emo.is_default else None
                if implied and emo.confidence >= IMPLIED_PACE_MIN_EMOTION_CONFIDENCE:
                    f = Explained[Pace](
                        value=Pace(implied), confidence=IMPLIED_PACE_CONFIDENCE,
                        reasoning=f"No pacing stated; a {emo.value.value} mood is conventionally cut {implied}.",
                        evidence=(Evidence(source="prompt", detail=f"implied by emotion '{emo.value.value}'", weight=0.35),),
                    )
            if f is None and dim in CORE_DEFAULTS:
                f = Explained[enum](
                    value=enum(CORE_DEFAULTS[dim]), confidence=DEFAULT_CONFIDENCE[dim], reasoning=DEFAULT_REASONING[dim],
                    evidence=(Evidence(source="default", detail="policy default", weight=0.0),), is_default=True,
                )
                missing.append(MissingInfo(field=dim, default_used=CORE_DEFAULTS[dim], clarifying_question=L.CLARIFYING_QUESTIONS[dim]))
            fields[dim] = f
            if amb:
                ambiguities.append(amb)
        reference, ref_amb = self.decide(r, L.REFERENCE, None)
        if ref_amb:
            ambiguities.append(ref_amb)

        content = r.normalized.content_tokens
        if content and (len(content) <= 3 or all(t in L.VAGUE_WORDS or t in L.COMMON_WORDS for t in content)):
            ambiguities.append(Ambiguity(
                field="overall", candidates=(), reasoning="The request is too vague to infer a specific style.",
                clarifying_question="Describe the feeling or a reference you want, e.g. 'like a Nike ad' or 'calm travel vlog'.",
            ))

        conflicts = self._conflicts(r, fields)
        arc = self._arc(r)
        duration = None
        if r.duration:
            seconds, span = r.duration
            duration = Explained[float](
                value=seconds, confidence=0.9, reasoning=f"Explicit duration '{span}' in the prompt.",
                evidence=(Evidence(source="prompt", detail=f"'{span}'", span=span),),
            )

        core = [fields[d] for d in (L.GENRE, L.EMOTION, L.PACE, L.PLATFORM)]
        overall = sum(f.confidence for f in core) / len(core)
        overall -= 0.1 * sum(1 for c in conflicts if c.severity == "hard") + 0.04 * len(ambiguities)

        return IntentAnalysis(
            raw_prompt=r.normalized.raw,
            normalized_prompt=r.normalized.text,
            corrections=tuple(r.normalized.corrections),
            genre=fields[L.GENRE], emotion=fields[L.EMOTION], pace=fields[L.PACE], target_platform=fields[L.PLATFORM],
            color_grade=fields[L.COLOR], caption_style=fields[L.CAPTION], music_style=fields[L.MUSIC],
            reference_style=reference, target_duration_s=duration, pacing_arc=arc,
            ambiguities=tuple(ambiguities), conflicts=tuple(conflicts), missing=tuple(missing),
            overall_confidence=round(clamp(overall), 3),
            extractors=(f"{self.name}:{L.LEXICON_VERSION}",),
            degraded=tuple(r.notes),
        )

    @staticmethod
    def decide(r: LexicalResult, dim: str, enum: type[StrEnum] | None) -> tuple[Explained | None, Ambiguity | None]:
        """Choose the best-supported value of a dimension and calibrate its confidence."""
        scores = {v: s for v, s in r.scores.get(dim, {}).items() if s > 0}
        if not scores:
            return None, None
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_value, top = ranked[0]
        compatible = {v for group in L.COMPATIBLE_VALUES.get(dim, []) if top_value in group for v in group} - {top_value}
        total = sum(scores.values())
        compat_mass = sum(s for v, s in scores.items() if v in compatible)
        share = min(1.0, (top + 0.5 * compat_mass) / total)
        strength = 1 - math.exp(-top / CONFIDENCE_SCALE)
        confidence = round(clamp(share * strength, 0.05, 0.97), 3)

        ev = tuple(r.evidence[dim][top_value])
        reasons = "; ".join(e.detail for e in ev)
        reasoning = f"Chose '{top_value}' from {reasons}."
        ambiguity = None
        if len(ranked) > 1:
            runner, second = ranked[1]
            reasoning += f" Runner-up '{runner}' scored {second:.2f} vs {top:.2f}."
            if runner not in compatible and second >= 0.5 and second / top >= AMBIGUITY_RATIO:
                ambiguity = Ambiguity(
                    field=dim, candidates=(top_value, runner),
                    reasoning=f"'{top_value}' ({top:.2f}) and '{runner}' ({second:.2f}) are similarly supported.",
                    clarifying_question=f"Should it feel more {top_value} or more {runner}?",
                )
        value = enum(top_value) if enum else top_value
        typ = enum if enum else str
        return Explained[typ](value=value, confidence=confidence, reasoning=reasoning, evidence=ev), ambiguity

    @staticmethod
    def _arc(r: LexicalResult) -> tuple[ArcBeat, ...]:
        beats = []
        for section in ("start", "middle", "end"):
            pace = LexicalIntentExtractor._best_beat(r, section, L.PACE)
            emotion = LexicalIntentExtractor._best_beat(r, section, L.EMOTION)
            if not pace and not emotion:
                continue
            spans = sorted({b[2] for b in (pace, emotion) if b})
            parts = [f"{d} '{b[0]}'" for d, b in (("pace", pace), ("emotion", emotion)) if b]
            beats.append(ArcBeat(
                section=section,
                pace=Pace(pace[0]) if pace else None,
                emotion=Emotion(emotion[0]) if emotion else None,
                span=" / ".join(spans),
                reasoning=f"The prompt asks for {' and '.join(parts)} at the {section} ('{' / '.join(spans)}').",
            ))
        return tuple(beats)

    @staticmethod
    def _conflicts(r: LexicalResult, fields: dict[str, Explained | None]) -> list[Conflict]:
        out: list[Conflict] = []
        g = r.global_scores.get(L.PACE, {})
        slow = g.get("slow", 0.0)
        quick = max(g.get("fast", 0.0), g.get("aggressive", 0.0))
        if slow >= 0.8 and quick >= 0.8:
            chosen = fields[L.PACE].value if fields[L.PACE] else "medium"
            out.append(Conflict(
                fields=(L.PACE,), values=("slow", "fast"), severity="hard",
                reasoning="The prompt asks for both slow and fast pacing without saying where each applies.",
                resolution=f"Using '{chosen}' (stronger evidence). Say e.g. 'slow start, fast ending' to get both.",
            ))
        chosen = {d: f.value.value if isinstance(f.value, StrEnum) else f.value for d, f in fields.items() if f and f.confidence >= 0.3 and not f.is_default}
        for (d1, v1), (d2, v2), why in L.SOFT_CONFLICTS:
            if d1 == d2:
                s = r.scores.get(d1, {})
                if s.get(v1, 0) >= 0.8 and s.get(v2, 0) >= 0.8:
                    out.append(Conflict(fields=(d1,), values=(v1, v2), severity="hard", reasoning=why,
                                        resolution=f"Using '{chosen.get(d1, v1)}' (stronger evidence)."))
            elif chosen.get(d1) == v1 and chosen.get(d2) == v2:
                out.append(Conflict(fields=(d1, d2), values=(v1, v2), severity="soft", reasoning=why,
                                    resolution="Keeping both as requested; the Story Architect will bias toward the stronger instruction."))
        return out
