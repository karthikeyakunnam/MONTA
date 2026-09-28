"""Layer 7 — pattern library, selection, adaptation, assignment, validation rules, repair, refinement, explainability."""

import json

import pytest

from orchestration.agents.story import DEFAULT_LIBRARY, StoryArchitect, StoryDesignError, StoryRefiner, TimelineValidator
from orchestration.agents.story.validator import ActSpec
from services.context_composer.platform_rules import PLATFORM_SPECS
from services.prompt_engine import IntentEngine
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from shared.contracts.context import ContextPack, UserPreferences
from shared.contracts.memory import PatternPrior
from shared.contracts.story import TimelineSegment, ValidationRule
from shared.contracts.vocab import Emotion, Genre, Pace, Platform, StoryRole
from shared.providers.errors import ProviderUnavailableError
from tests.conftest import ScriptedProvider, gym_clips, make_clip, metadata_for

NIKE = "make this feel like nike ad slow start then huge motivation ending use dark colors and aggressive cuts"


def make_pack(prompt=NIKE, clips=None, *, priors=(), prefs=None, platform=Platform.INSTAGRAM, reconcile=True) -> ContextPack:
    clips = clips if clips is not None else gym_clips()
    intent = LexicalIntentExtractor().extract(prompt)
    if reconcile:
        intent = IntentEngine().reconcile_with_footage(intent, clips)
    return ContextPack(
        project_id="p", user_id="u", user_prompt=prompt, intent=intent, user_preferences=prefs or UserPreferences(user_id="u"),
        story_patterns=tuple(priors), platform=PLATFORM_SPECS[platform],
        video_metadata=tuple(metadata_for(c) for c in clips), clip_intelligence={c.clip_id: c for c in clips},
    )


# ---------------------------------------------------------------- library

def test_library_has_required_patterns_and_invariants():
    assert set(DEFAULT_LIBRARY.ids) == {
        "transformation", "travel", "event", "product_launch", "wedding", "fitness_reel", "motivational_reel",
        "podcast_highlight", "tutorial", "cinematic_montage",
    }
    for p in DEFAULT_LIBRARY:
        assert abs(sum(a.share for a in p.acts) - 1) < 0.01
        assert len(p.pacing_curve) == len(p.acts)
        assert p.required_emotions and 0 <= p.hero_moment_position <= 1
        assert all(a.purpose for a in p.acts)


# ---------------------------------------------------------------- end-to-end design

async def test_flagship_story_is_valid_and_explained():
    clips = {c.clip_id: c for c in gym_clips()}
    plan = await StoryArchitect().design(make_pack())

    assert plan.story_pattern in {"motivational_reel", "transformation", "fitness_reel"}
    assert plan.validation.passed, plan.validation.summary
    assert plan.hero_clip_id == "c6"
    assert sum(s.is_hero for s in plan.timeline) == 1
    assert plan.timeline[0].start == 0
    assert all(abs(b.start - a.end) < 1e-6 for a, b in zip(plan.timeline, plan.timeline[1:]))
    assert all(s.source_out <= clips[s.clip_id].duration_s + 1e-6 for s in plan.timeline)

    # explainability for the Critic
    decisions = {d.clip_id: d for d in plan.reasoning.clip_decisions}
    assert set(decisions) == set(clips)
    assert not decisions["c7"].selected and "quality" in decisions["c7"].reasons[0]
    assert all(d.reasons for d in decisions.values())
    assert all(a.why_exists and a.purpose for a in plan.reasoning.acts)
    assert plan.reasoning.pattern_selection and plan.reasoning.pacing and plan.reasoning.emotion
    assert len(plan.reasoning.candidates) == len(DEFAULT_LIBRARY)
    for score in (plan.story_score, plan.emotion_score, plan.pacing_score):
        assert 0 <= score.value <= 10 and 0 <= score.confidence <= 1 and score.reasoning
    assert 0 < plan.story_confidence <= 1
    assert any("slow" in a for a in plan.reasoning.adaptations), "slow-start arc must adapt the first act"
    first_act = plan.reasoning.acts[0]
    assert first_act.cut_length_s > plan.reasoning.acts[2].cut_length_s


async def test_design_is_deterministic():
    a = await StoryArchitect().design(make_pack())
    b = await StoryArchitect().design(make_pack())
    assert a.act_assignments == b.act_assignments and [s.end for s in a.timeline] == [s.end for s in b.timeline]


@pytest.mark.parametrize("prompt,clips,expected", [
    ("our wedding day, emotional and romantic, vows", [
        make_clip("w1", 3, 8, Emotion.ROMANTIC, ((StoryRole.PREPARATION, .8),), activities=("bride getting ready",)),
        make_clip("w2", 4.5, 9, Emotion.EMOTIONAL, ((StoryRole.HERO, .9), (StoryRole.REACTION, .8)), activities=("vows",)),
        make_clip("w3", 7, 8, Emotion.JOYFUL, ((StoryRole.CELEBRATION, .9),), activities=("dancing",)),
        make_clip("w4", 4.5, 8, Emotion.NOSTALGIC, ((StoryRole.PAYOFF, .8),), activities=("couple walking away",)),
    ], "wedding"),
    ("travel vlog from my trip to bali, uplifting", [
        make_clip("t1", 4, 8, Emotion.CALM, ((StoryRole.ESTABLISHING, .9),), activities=("airplane window",)),
        make_clip("t2", 5, 8, Emotion.JOYFUL, ((StoryRole.B_ROLL, .8),), activities=("street food",)),
        make_clip("t3", 8, 9, Emotion.JOYFUL, ((StoryRole.PEAK, .9), (StoryRole.HERO, .7)), activities=("cliff jump",)),
        make_clip("t4", 3.5, 8, Emotion.NOSTALGIC, ((StoryRole.PAYOFF, .8),), activities=("sunset beach",)),
    ], "travel"),
])
async def test_pattern_selection_follows_intent_and_footage(prompt, clips, expected):
    plan = await StoryArchitect().design(make_pack(prompt, clips))
    assert plan.story_pattern == expected


async def test_memory_prior_can_flip_a_close_decision():
    base = await StoryArchitect().design(make_pack("gym video"))
    runner_up = base.reasoning.candidates[1].pattern_id
    strong = PatternPrior(story_pattern=runner_up, project_type=Genre.FITNESS, success_mean=0.95, confidence=0.9,
                          sample_size=50, reasoning="strong history")
    weak = PatternPrior(story_pattern=base.story_pattern, project_type=Genre.FITNESS, success_mean=0.2, confidence=0.9,
                        sample_size=50, reasoning="weak history")
    learned = await StoryArchitect().design(make_pack("gym video", priors=[strong, weak]))
    scores = {c.pattern_id: c for c in learned.reasoning.candidates}
    assert scores[runner_up].memory_prior > 0 > scores[base.story_pattern].memory_prior
    assert learned.story_pattern == runner_up
    assert "strong history" in learned.reasoning.pattern_selection


async def test_avoid_and_disliked_patterns_are_excluded():
    first = await StoryArchitect().design(make_pack())
    retry = await StoryArchitect().design(make_pack(), avoid_patterns=[first.story_pattern])
    assert retry.story_pattern != first.story_pattern
    disliked = UserPreferences(user_id="u", disliked_patterns=(first.story_pattern,))
    assert (await StoryArchitect().design(make_pack(prefs=disliked))).story_pattern != first.story_pattern


async def test_scarce_footage_merges_acts_without_empty_acts():
    clips = [make_clip("a", 4, 8, roles=((StoryRole.PROGRESS, .8),)), make_clip("b", 7, 9, roles=((StoryRole.HERO, .9),))]
    plan = await StoryArchitect().design(make_pack("gym motivation", clips))
    assert len(plan.act_assignments) == 2 and all(plan.act_assignments.values())
    assert plan.validation.passed
    assert any("dropped act" in a for a in plan.reasoning.adaptations)


async def test_single_clip_story():
    plan = await StoryArchitect().design(make_pack("gym", [make_clip("solo", 6, 8)]))
    assert len(plan.timeline) == 1 and plan.timeline[0].is_hero and plan.validation.passed


async def test_no_usable_clips_raises():
    with pytest.raises(StoryDesignError):
        await StoryArchitect().design(make_pack("gym", [make_clip("x", 5, usable=False)]))


async def test_low_quality_admitted_only_when_footage_is_scarce():
    clips = [make_clip("hq", 7, 9, roles=((StoryRole.HERO, .9),)), make_clip("lq", 4, 3.5)]
    plan = await StoryArchitect().design(make_pack("gym", clips))
    assert "lq" in plan.selected_clip_ids
    assert any("admitted lq" in a for a in plan.reasoning.adaptations)


async def test_target_duration_respected():
    clips = [make_clip(f"c{i}", 3 + (i % 15) * 0.4, 8, duration=10) for i in range(40)]
    plan = await StoryArchitect().design(make_pack("slow gym edit 15 seconds", clips, reconcile=False))
    assert plan.total_duration_s <= 15 * 1.2 + 1e-6
    unselected = [d for d in plan.reasoning.clip_decisions if not d.selected]
    assert unselected and all(any(k in d.reasons[0] for k in ("trimmed", "capacity", "weak fit")) for d in unselected)


# ---------------------------------------------------------------- validator rules

def seg(i, clip, start, end, act="a1", energy=5.0, hero=False, src_in=0.0):
    return TimelineSegment(index=i, clip_id=clip, act_id=act, start=start, end=end, source_in=src_in, source_out=src_in + (end - start),
                           energy=energy, is_hero=hero, purpose="p", reasoning="r")


CLIPS = {f"k{i}": make_clip(f"k{i}", 5, duration=4) for i in range(8)}
ACTS = [ActSpec("a1", "rise"), ActSpec("a2", "hold")]


def rules(timeline, acts=ACTS, clips=CLIPS, pace=Pace.MEDIUM, rejected=frozenset()):
    return {v.rule for v in TimelineValidator().validate(timeline, acts, clips, pace, rejected=rejected).violations}


def test_valid_timeline_passes():
    tl = [seg(0, "k0", 0, 2, energy=4), seg(1, "k1", 2, 4, energy=5, hero=True), seg(2, "k2", 4, 6, "a2", 6.5)]
    assert rules(tl) == set()


def test_each_rule_fires():
    assert ValidationRule.NO_GAPS in rules([seg(0, "k0", 0, 2), seg(1, "k1", 2.5, 4, "a2", hero=True)])
    assert ValidationRule.NO_GAPS in rules([seg(0, "k0", 1, 2, hero=True), seg(1, "k1", 2, 3, "a2")])
    assert ValidationRule.NO_OVERLAPS in rules([seg(0, "k0", 0, 2), seg(1, "k1", 1.5, 3, "a2", hero=True)])
    assert ValidationRule.NO_INVALID_CLIPS in rules([seg(0, "ghost", 0, 2, hero=True), seg(1, "k1", 2, 4, "a2")])
    assert ValidationRule.NO_INVALID_CLIPS in rules([seg(0, "k0", 0, 2, hero=True), seg(1, "k1", 2, 4, "a2")], rejected={"k1"})
    unusable = {**CLIPS, "bad": make_clip("bad", 5, usable=False)}
    assert ValidationRule.NO_INVALID_CLIPS in rules([seg(0, "bad", 0, 2, hero=True), seg(1, "k1", 2, 4, "a2")], clips=unusable)
    assert ValidationRule.SINGLE_HERO_MOMENT in rules([seg(0, "k0", 0, 2, hero=True), seg(1, "k1", 2, 4, "a2", hero=True)])
    assert ValidationRule.SINGLE_HERO_MOMENT in rules([seg(0, "k0", 0, 2), seg(1, "k1", 2, 4, "a2")])
    assert ValidationRule.NO_ABRUPT_ENERGY_SPIKES in rules([seg(0, "k0", 0, 2, energy=1), seg(1, "k1", 2, 4, "a2", energy=9, hero=True)])
    low = [seg(i, f"k{i}", i, i + 1, energy=2 + 0.1 * i) for i in range(4)] + [seg(4, "k4", 4, 5, "a2", 5, hero=True)]
    assert ValidationRule.MAX_LOW_ENERGY_RUN in rules(low)
    assert ValidationRule.ACT_EMOTIONAL_PROGRESSION in rules([seg(0, "k0", 0, 2, energy=8), seg(1, "k1", 2, 4, energy=5),
                                                              seg(2, "k2", 4, 6, "a2", 6, hero=True)])
    assert ValidationRule.ACT_EMOTIONAL_PROGRESSION in rules([seg(0, "k0", 0, 2, energy=5), seg(1, "k1", 2, 4, "a2", 5.2, hero=True)])
    assert ValidationRule.WITHIN_SOURCE_DURATION in rules([seg(0, "k0", 0, 3, src_in=2, hero=True), seg(1, "k1", 3, 5, "a2", 6)])
    assert ValidationRule.NO_EMPTY_ACTS in rules([seg(0, "k0", 0, 2, hero=True)])


def test_spike_threshold_depends_on_pace():
    tl = [seg(0, "k0", 0, 2, energy=3), seg(1, "k1", 2, 4, "a2", energy=8, hero=True)]
    assert ValidationRule.NO_ABRUPT_ENERGY_SPIKES in rules(tl, pace=Pace.MEDIUM)
    assert ValidationRule.NO_ABRUPT_ENERGY_SPIKES not in rules(tl, pace=Pace.AGGRESSIVE)


async def test_unfixable_spike_is_reported_not_hidden():
    R = StoryRole
    clips = [
        make_clip("low", 2.0, 8, Emotion.DRAMATIC, ((R.STRUGGLE, .9),)),
        make_clip("mid", 5.0, 7.0, Emotion.INTENSE, ((R.PREPARATION, .6),)),
        make_clip("high", 9.5, 8, Emotion.INTENSE, ((R.INTENSITY, .9), (R.PEAK, .8))),
        make_clip("hero", 8.5, 9.5, Emotion.INSPIRING, ((R.HERO, .9),)),
    ]
    plan = await StoryArchitect().design(make_pack("slow gym transformation", clips))
    assert not plan.validation.passed
    assert {v.rule for v in plan.validation.violations if v.severity == "error"} == {ValidationRule.NO_ABRUPT_ENERGY_SPIKES}
    assert plan.story_confidence < 0.6


def test_repair_bridges_a_spike_with_an_unused_clip():
    from orchestration.agents.story import assembly
    from orchestration.agents.story.selection import adapt_pattern

    R = StoryRole
    clips_list = [
        make_clip("low", 2.0, 8, Emotion.DRAMATIC, ((R.STRUGGLE, .9),)),
        make_clip("mid", 5.0, 7.0, Emotion.INTENSE, ((R.PREPARATION, .6),)),
        make_clip("high", 9.5, 8, Emotion.INTENSE, ((R.INTENSITY, .9), (R.PEAK, .8))),
        make_clip("hero", 8.5, 9.5, Emotion.INSPIRING, ((R.HERO, .9),)),
        make_clip("bridge", 7.4, 7.5, Emotion.INTENSE, ((R.PROGRESS, .7), (R.INTENSITY, .5))),
    ]
    clips = {c.clip_id: c for c in clips_list}
    pack = make_pack("slow gym transformation", clips_list)
    adapted = adapt_pattern(DEFAULT_LIBRARY.get("transformation"), pack, clips_list)
    draft = assembly.Assignment(
        acts={"struggle": ["low"], "grind": ["mid"], "breakthrough": ["high"], "reveal": ["hero"]}, hero="hero",
        fits={cid: (0.7, "fixture") for cid in ("low", "mid", "high", "hero")},
        rejected={"bridge": (0.6, ["fits 'grind' (0.60) but acts are at capacity for the target duration"])},
    )
    validator = TimelineValidator()
    before = validator.validate(assembly.build_timeline(adapted, draft, clips), assembly.act_specs(adapted), clips, Pace.SLOW,
                                rejected={"bridge"})
    assert ValidationRule.NO_ABRUPT_ENERGY_SPIKES in {v.rule for v in before.violations}

    fixed, timeline, report, log = assembly.repair(adapted, draft, clips, validator, Pace.SLOW, Emotion.MOTIVATIONAL)
    assert report.passed, report.summary
    assert "bridge" in fixed.selected and "bridge" not in fixed.rejected
    assert log and "added bridge" in log[0]


# ---------------------------------------------------------------- LLM refinement

def _refinement(plan, acts=None, hero=None, summary="tighter escalation"):
    return json.dumps({"act_assignments": acts or {k: list(v) for k, v in plan.act_assignments.items()},
                       "hero_clip_id": hero or plan.hero_clip_id, "reasoning": {"summary": summary, "changes": []}})


async def test_refiner_proposal_adopted_when_not_worse():
    base = await StoryArchitect().design(make_pack())
    refiner = StoryRefiner(ScriptedProvider([_refinement(base)], vision=False))
    plan = await StoryArchitect(refiner=refiner).design(make_pack())
    assert plan.reasoning.refinement.startswith("Adopted") and plan.validation.passed


async def test_refiner_proposal_rejected_when_it_breaks_rules():
    base = await StoryArchitect().design(make_pack())
    acts = {k: list(v) for k, v in base.act_assignments.items()}
    first, last = list(acts)[0], list(acts)[-1]
    acts[first], acts[last] = acts[last], acts[first]  # payoff first, struggle last: breaks progression
    hero_act = next(a for a, ids in acts.items() if base.hero_clip_id in ids)
    bad = _refinement(base, acts=acts, hero=base.hero_clip_id) if hero_act == last else _refinement(base, acts=acts)
    refiner = StoryRefiner(ScriptedProvider([bad, _refinement(base, acts=acts, hero=acts[last][0])], vision=False))
    plan = await StoryArchitect(refiner=refiner).design(make_pack())
    assert plan.reasoning.refinement.startswith("Rejected")
    assert plan.act_assignments == base.act_assignments


async def test_refiner_failure_keeps_deterministic_plan():
    base = await StoryArchitect().design(make_pack())
    refiner = StoryRefiner(ScriptedProvider([ProviderUnavailableError("down")], vision=False))
    plan = await StoryArchitect(refiner=refiner).design(make_pack())
    assert plan.act_assignments == base.act_assignments and "unavailable" in plan.reasoning.refinement
