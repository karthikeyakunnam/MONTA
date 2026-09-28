"""Narrative memory must justify itself: arm assignment, control-arm isolation, offline and online analysis."""

import random

from datasets.loader import load_golden
from evaluation.memory_experiment import MemoryExperiment, analyze_online, run_offline
from memory.narrative_memory import InMemoryNarrativeStore, InMemoryPreferenceStore, NarrativeLearner
from orchestration.agents.story import DEFAULT_LIBRARY
from services.context_composer import ContextComposer
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from shared.contracts.memory import NarrativeMemoryRecord
from shared.contracts.vocab import Genre
from tests.conftest import gym_clips, metadata_for


def rec(i, user, pattern="fitness_reel", rating=8.0, genre=Genre.FITNESS):
    return NarrativeMemoryRecord(record_id=f"r{i}", project_id=f"p{i}", user_id=user, project_type=genre,
                                 story_pattern=pattern, engagement_score=rating, completion_rate=rating, user_rating=rating)


def test_assignment_is_deterministic_and_proportional():
    exp = MemoryExperiment(control_share=0.2, salt="s1")
    arms = [exp.assign(f"user-{i}") for i in range(5000)]
    assert arms == [exp.assign(f"user-{i}") for i in range(5000)]
    share = arms.count("control") / len(arms)
    assert 0.17 < share < 0.23
    assert MemoryExperiment(control_share=0.2, salt="s2").assign("user-1") in ("memory", "control")


async def test_control_arm_gets_no_memory():
    memory = InMemoryNarrativeStore([rec(i, "u-ctrl") for i in range(5)] + [rec(10 + i, "other") for i in range(5)])
    exp = MemoryExperiment(control_share=1.0)
    composer = ContextComposer(memory=memory, preferences=InMemoryPreferenceStore(), learner=NarrativeLearner(memory),
                               pattern_ids=DEFAULT_LIBRARY.ids, experiment=exp)
    pack = await composer.compose(project_id="p", user_id="u-ctrl", intent=LexicalIntentExtractor().extract("gym"),
                                  clips=[metadata_for(c) for c in gym_clips()])
    assert pack.memory_arm == "control"
    assert not pack.historical_edits and not pack.story_patterns and not pack.previous_successful_projects
    assert all("control arm" in s.detail for s in pack.provenance if s.source != "explicit_preferences")


async def test_foreign_successes_are_anonymized():
    memory = InMemoryNarrativeStore([rec(i, "someone-else", rating=9.5) for i in range(3)])
    composer = ContextComposer(memory=memory, preferences=InMemoryPreferenceStore(), learner=NarrativeLearner(memory),
                               pattern_ids=DEFAULT_LIBRARY.ids)
    pack = await composer.compose(project_id="p", user_id="me", intent=LexicalIntentExtractor().extract("gym workout"),
                                  clips=[metadata_for(c) for c in gym_clips()])
    assert pack.previous_successful_projects
    assert all(r.user_id == "anonymous" and not r.project_id.startswith("p") for r in pack.previous_successful_projects)


def test_online_analysis_decisions():
    exp = MemoryExperiment(control_share=0.5, salt="online")
    rng = random.Random(3)
    users = [f"u{i}" for i in range(400)]
    improved = []
    for i, u in enumerate(users):
        base = 6.0 + (1.0 if exp.bucket(u) >= 0.5 else 0.0)  # memory arm rated one point higher
        improved.append(rec(i, u, rating=min(10, max(0, rng.gauss(base, 1.0)))))
    report = analyze_online(improved, lambda u: "control" if exp.bucket(u) < 0.5 else "memory")
    assert report.decision == "keep" and report.comparisons[0].difference > 0.5

    null = [rec(i, u, rating=min(10, max(0, rng.gauss(7, 1)))) for i, u in enumerate(users)]
    assert analyze_online(null, lambda u: "control" if exp.bucket(u) < 0.5 else "memory").decision == "collect"
    few = improved[:10]
    assert analyze_online(few, lambda u: "control" if exp.bucket(u) < 0.5 else "memory").decision == "collect"


async def test_offline_experiment_reports_paired_deltas_and_flips():
    projects = [p for p in load_golden() if p.category == "gym"]
    history = [rec(i, "offline-user", pattern="transformation", rating=10) for i in range(40)]
    report = await run_offline(projects, history)
    assert report.projects == len(projects) and report.history_records == 40
    assert {d.metric for d in report.deltas} == {"story_quality", "prompt_alignment", "narrative_score"}
    assert 0 <= report.flip_rate <= 1 and report.verdict
    assert any(d.pattern_with == "transformation" for d in report.projects_detail), "a strong prior must be able to move decisions"
