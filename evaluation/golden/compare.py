"""
MONTA — Golden Comparison
===========================
``run_golden_project`` executes Layer 3 (intent) → footage reconciliation →
Layer 4 (ContextPack) → Layer 7 (story) → independent Story Judge on a golden
project's analyzed clips, then checks the result against the expectations:

=============  ==============================================================
check          passes when
=============  ==============================================================
pattern        story pattern is one of the acceptable patterns
genre / pace   Layer 3 (after reconciliation) matches the expected value
emotion        intent emotion equals or is affine to the expected emotion
arc_shape      timeline energy follows the expected shape (see ``arc_matches``)
start_pace     "slow start" requested → opening cuts ≥ 1.3× the median cut
end_emotion    closing 30% conveys the expected ending emotion
hero           hero clip is one of the acceptable hero shots
exclusions     no clip an editor ruled out appears
duration       within ±5% of the requested duration
valid          the plan passed its (pattern-specific) validation
judge          independent judge overall ≥ project's min_judge_score
=============  ==============================================================

Optionally records intent decisions + golden outcomes for calibration.
"""

import statistics
import time
from collections import defaultdict
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from datasets.schema import GoldenProject
from shared.calibration.calibrator import DecisionRecorder
from shared.contracts.base import revalidate
from shared.contracts.clip import TechnicalMetadata
from shared.contracts.evaluation import StoryJudgement
from shared.contracts.explain import Evidence, Explained
from shared.contracts.story import StoryPlan
from shared.contracts.vocab import EMOTION_AFFINITY, Emotion, Pace, Platform
from shared.observability.metrics import REGISTRY


class GoldenCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool | None  # None = not applicable to this project
    detail: str


class GoldenResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    category: str
    passed: bool
    checks: tuple[GoldenCheck, ...]
    story_pattern: str | None
    judgement: StoryJudgement | None
    runtime_s: float
    tokens: int
    error: str | None = None


class GoldenReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    projects: int
    pass_rate: float
    check_pass_rates: dict[str, float]
    category_pass_rates: dict[str, float]
    mean_judge: dict[str, float]
    results: tuple[GoldenResult, ...]


@dataclass
class LayerStack:
    """The Layer 3–7 components under test (a benchmark variant builds one of these)."""

    intent_engine: object
    composer: object
    architect: object
    judge: object
    recorder: DecisionRecorder | None = None


def _related(a: Emotion | None, b: Emotion) -> bool:
    return a is not None and (a == b or any({a, b} <= g for g in EMOTION_AFFINITY))


def _tokens_used() -> float:
    return sum(v for _, _, v in REGISTRY.get("monta_provider_tokens_total").samples())


def arc_matches(plan: StoryPlan, shape: str) -> tuple[bool, str]:
    e = [s.energy for s in plan.timeline]
    n = len(e)
    if n < 2:
        return True, "single cut: shape not assessable"
    peak_idx = max(range(n), key=lambda i: e[i])
    peak_pos = peak_idx / (n - 1)
    if shape == "build":
        body = e[: max(2, int(n * 0.85))]
        r = statistics.correlation(range(len(body)), body) if len(body) >= 3 and len(set(body)) > 1 else (1.0 if body[-1] >= body[0] else -1.0)
        ok = r > 0.3 and peak_pos >= 0.5
        return ok, f"energy/position r={r:+.2f}, peak at {peak_pos:.0%}"
    if shape == "build_release":
        ok = 0.3 <= peak_pos <= 0.92 and e[-1] <= e[peak_idx] - 1.0
        return ok, f"peak at {peak_pos:.0%}, ends {e[-1]:.1f} vs peak {e[peak_idx]:.1f}"
    if shape == "steady":
        sd = statistics.pstdev(e)
        return sd < 2.2, f"energy stdev {sd:.2f}"
    if shape == "hook_first":
        first_hero = any(s.is_hero for s in plan.timeline if s.start <= plan.total_duration_s * 0.2)
        ok = first_hero or e[0] >= statistics.median(e)
        return ok, f"hero in first 20% {first_hero}, opening energy {e[0]:.1f} vs median {statistics.median(e):.1f}"
    raise ValueError(f"unknown arc shape {shape}")


def _metadata(project: GoldenProject) -> list[TechnicalMetadata]:
    return [TechnicalMetadata(clip_id=c.clip_id, path=f"golden://{project.project_id}/{c.clip_id}", duration_s=c.duration_s,
                              fps=30.0, width=1080, height=1920, codec="h264", has_audio=True, file_size_bytes=1,
                              fingerprint=f"golden-{project.project_id}-{c.clip_id}") for c in project.clips]


def _with_project_platform(intent, platform: Platform):
    """The upload flow chooses the destination; it fills the platform only when the prompt didn't."""
    if not intent.target_platform.is_default:
        return intent
    field = Explained[Platform](value=platform, confidence=0.95, reasoning="Destination chosen in the project settings.",
                                evidence=(Evidence(source="preferences", detail="project destination"),))
    return revalidate(intent, target_platform=field, missing=tuple(m for m in intent.missing if m.field != "target_platform"))


async def run_golden_project(project: GoldenProject, stack: LayerStack, *, user_id: str = "golden-user") -> GoldenResult:
    started, tokens0 = time.perf_counter(), _tokens_used()
    clips = project.clip_intelligence()
    try:
        intent = await stack.intent_engine.analyze(project.prompt)
        decision_ids = await stack.recorder.record_intent(intent, project_id=project.project_id) if stack.recorder else {}
        intent = stack.intent_engine.reconcile_with_footage(intent, list(clips.values()))
        intent = _with_project_platform(intent, project.platform)
        pack = await stack.composer.compose(project_id=project.project_id, user_id=user_id, intent=intent, clips=_metadata(project))
        pack = await stack.composer.enrich_with_footage(pack, intent, clips)
        plan = await stack.architect.design(pack)
        judgement = await stack.judge.judge(plan, pack)
    except Exception as e:  # a crash is a failed golden project, reported — never hidden
        return GoldenResult(project_id=project.project_id, category=project.category, passed=False, checks=(),
                            story_pattern=None, judgement=None, runtime_s=round(time.perf_counter() - started, 4),
                            tokens=int(_tokens_used() - tokens0), error=f"{type(e).__name__}: {e}")

    x = project.expected
    checks = [
        GoldenCheck(name="pattern", passed=plan.story_pattern in x.story_patterns,
                    detail=f"{plan.story_pattern} (acceptable: {', '.join(x.story_patterns)})"),
        GoldenCheck(name="genre", passed=intent.genre.value == x.genre, detail=f"{intent.genre.value.value} vs {x.genre.value}"),
        GoldenCheck(name="pace", passed=intent.pace.value == x.pace, detail=f"{intent.pace.value.value} vs {x.pace.value}"),
        GoldenCheck(name="emotion", passed=_related(intent.emotion.value, x.emotion) if x.emotion else None,
                    detail=f"{intent.emotion.value.value} vs {x.emotion.value if x.emotion else '-'}"),
    ]
    ok, why = arc_matches(plan, x.emotional_arc.shape)
    checks.append(GoldenCheck(name="arc_shape", passed=ok, detail=f"{x.emotional_arc.shape}: {why}"))
    if x.emotional_arc.start_pace == Pace.SLOW:
        cuts = [s.duration for s in plan.timeline]
        opening = [s.duration for s in plan.timeline if s.start < plan.total_duration_s * 0.25] or cuts[:1]
        ratio = statistics.median(opening) / statistics.median(cuts)
        checks.append(GoldenCheck(name="start_pace", passed=ratio >= 1.3, detail=f"opening/median cut ratio {ratio:.2f}"))
    else:
        checks.append(GoldenCheck(name="start_pace", passed=None, detail="not requested"))
    if x.emotional_arc.end_emotion:
        tail = [s for s in plan.timeline if s.start >= plan.total_duration_s * 0.7] or list(plan.timeline[-1:])
        share = sum(1 for s in tail if _related(clips[s.clip_id].emotion, x.emotional_arc.end_emotion)) / len(tail)
        checks.append(GoldenCheck(name="end_emotion", passed=share >= 0.5, detail=f"{share:.0%} of closing cuts convey {x.emotional_arc.end_emotion.value}"))
    else:
        checks.append(GoldenCheck(name="end_emotion", passed=None, detail="not requested"))
    checks.append(GoldenCheck(name="hero", passed=plan.hero_clip_id in x.hero_clip_ids if x.hero_clip_ids else None,
                              detail=f"hero {plan.hero_clip_id} (acceptable: {', '.join(x.hero_clip_ids) or '-'})"))
    used = set(plan.selected_clip_ids)
    bad = sorted(used & set(x.must_exclude))
    checks.append(GoldenCheck(name="exclusions", passed=not bad if x.must_exclude else None,
                              detail=f"used excluded clips: {bad}" if bad else "no excluded clips used"))
    if x.duration_s:
        dev = abs(plan.total_duration_s - x.duration_s) / x.duration_s
        checks.append(GoldenCheck(name="duration", passed=dev <= 0.05, detail=f"{plan.total_duration_s:.2f}s vs {x.duration_s:.0f}s ({dev:.1%})"))
    else:
        checks.append(GoldenCheck(name="duration", passed=None, detail="no duration requested"))
    checks.append(GoldenCheck(name="valid", passed=plan.validation.passed, detail=plan.validation.summary))
    checks.append(GoldenCheck(name="judge", passed=judgement.overall_score >= x.min_judge_score,
                              detail=f"{judgement.overall_score:.2f} vs min {x.min_judge_score}"))

    if stack.recorder:
        truth = {"genre": x.genre.value, "pace": x.pace.value, **({"emotion": x.emotion.value} if x.emotion else {})}
        for field, value in truth.items():
            if field in decision_ids:
                predicted = str(getattr(getattr(intent, field).value, "value", getattr(intent, field).value))
                await stack.recorder.record_outcome(decision_ids[field], correct=predicted == value, source="golden",
                                                    observed_value=value)

    applicable = [c for c in checks if c.passed is not None]
    return GoldenResult(project_id=project.project_id, category=project.category, passed=all(c.passed for c in applicable),
                        checks=tuple(checks), story_pattern=plan.story_pattern, judgement=judgement,
                        runtime_s=round(time.perf_counter() - started, 4), tokens=int(_tokens_used() - tokens0))


async def run_golden(projects: list[GoldenProject], stack: LayerStack) -> GoldenReport:
    results = [await run_golden_project(p, stack) for p in projects]
    by_check: dict[str, list[bool]] = defaultdict(list)
    by_cat: dict[str, list[bool]] = defaultdict(list)
    judge: dict[str, list[float]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r.passed)
        for c in r.checks:
            if c.passed is not None:
                by_check[c.name].append(c.passed)
        if r.judgement:
            for dim in ("overall", "coherence", "emotion", "pacing", "hook", "ending", "prompt_alignment", "clip_relevance"):
                judge[dim].append(getattr(r.judgement, f"{dim}_score"))
    rate = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0  # noqa: E731
    return GoldenReport(
        projects=len(results), pass_rate=rate([r.passed for r in results]),
        check_pass_rates={k: rate(v) for k, v in sorted(by_check.items())},
        category_pass_rates={k: rate(v) for k, v in sorted(by_cat.items())},
        mean_judge={k: round(statistics.fmean(v), 3) for k, v in judge.items()},
        results=tuple(results),
    )
