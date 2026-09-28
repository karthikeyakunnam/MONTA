"""
MONTA — Offline Memory Experiment
===================================
Paired comparison: every golden project is designed twice — once with a
narrative-memory store holding ``history`` (real exported records in
production), once with an empty store — and both plans are scored by the
independent Story Judge.

Reported:
* story-quality difference (judge overall), prompt-alignment difference,
  narrative (coherence) difference — mean paired delta with a bootstrap CI
* pattern flip rate — how often memory changed the chosen pattern
* per-project detail for the flips

Offline results say whether priors *move decisions toward better-judged
stories*; user satisfaction can only be measured online (``online.py``).
"""

import random
import statistics
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from datasets.schema import GoldenProject
from evaluation.golden import LayerStack, run_golden_project
from evaluation.story_judge import HeuristicStoryJudge
from memory.narrative_memory import InMemoryNarrativeStore, InMemoryPreferenceStore, NarrativeLearner
from orchestration.agents.story import DEFAULT_LIBRARY, StoryArchitect
from services.context_composer import ContextComposer
from services.prompt_engine import IntentEngine
from shared.contracts.memory import NarrativeMemoryRecord


class PairedDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric: str
    mean_delta: float
    ci_low: float
    ci_high: float
    n: int


class ProjectComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    pattern_without: str | None
    pattern_with: str | None
    overall_without: float | None
    overall_with: float | None


class OfflineMemoryReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    history_records: int
    projects: int
    flip_rate: float
    deltas: tuple[PairedDelta, ...]
    projects_detail: tuple[ProjectComparison, ...]
    verdict: str


def _stack(history: Sequence[NarrativeMemoryRecord]) -> LayerStack:
    memory = InMemoryNarrativeStore(list(history))
    composer = ContextComposer(memory=memory, preferences=InMemoryPreferenceStore(), learner=NarrativeLearner(memory),
                               pattern_ids=DEFAULT_LIBRARY.ids)
    return LayerStack(IntentEngine(), composer, StoryArchitect(), HeuristicStoryJudge())


def _paired(metric: str, a: list[float], b: list[float], rng: random.Random, n: int = 2000) -> PairedDelta:
    d = [x - y for x, y in zip(a, b)]
    if not d:
        return PairedDelta(metric=metric, mean_delta=0.0, ci_low=0.0, ci_high=0.0, n=0)
    boots = sorted(statistics.fmean(rng.choice(d) for _ in d) for _ in range(n))
    return PairedDelta(metric=metric, mean_delta=round(statistics.fmean(d), 4),
                       ci_low=round(boots[int(0.025 * n)], 4), ci_high=round(boots[int(0.975 * n) - 1], 4), n=len(d))


async def run_offline(projects: Sequence[GoldenProject], history: Sequence[NarrativeMemoryRecord], *,
                      user_id: str = "offline-user", seed: int = 0) -> OfflineMemoryReport:
    with_mem, without = _stack(history), _stack([])
    rows, detail = [], []
    for p in projects:
        a = await run_golden_project(p, with_mem, user_id=user_id)
        b = await run_golden_project(p, without, user_id=user_id)
        detail.append(ProjectComparison(project_id=p.project_id, pattern_without=b.story_pattern, pattern_with=a.story_pattern,
                                        overall_without=b.judgement.overall_score if b.judgement else None,
                                        overall_with=a.judgement.overall_score if a.judgement else None))
        if a.judgement and b.judgement:
            rows.append((a.judgement, b.judgement, a.story_pattern != b.story_pattern))
    rng = random.Random(seed)
    deltas = (
        _paired("story_quality", [r[0].overall_score for r in rows], [r[1].overall_score for r in rows], rng),
        _paired("prompt_alignment", [r[0].prompt_alignment_score for r in rows], [r[1].prompt_alignment_score for r in rows], rng),
        _paired("narrative_score", [r[0].coherence_score for r in rows], [r[1].coherence_score for r in rows], rng),
    )
    flips = sum(1 for r in rows if r[2]) / len(rows) if rows else 0.0
    q = deltas[0]
    if q.n == 0:
        verdict = "no comparable projects"
    elif q.ci_low > 0:
        verdict = f"memory improves judged story quality by {q.mean_delta:+.2f} (95% CI {q.ci_low:+.2f}..{q.ci_high:+.2f})"
    elif q.ci_high < 0:
        verdict = f"memory lowers judged story quality by {q.mean_delta:+.2f} (95% CI {q.ci_low:+.2f}..{q.ci_high:+.2f})"
    else:
        verdict = (f"no significant quality effect ({q.mean_delta:+.2f}, CI {q.ci_low:+.2f}..{q.ci_high:+.2f}); "
                   f"memory changed the pattern in {flips:.0%} of projects")
    return OfflineMemoryReport(history_records=len(history), projects=len(projects), flip_rate=round(flips, 4),
                               deltas=deltas, projects_detail=tuple(detail), verdict=verdict)
