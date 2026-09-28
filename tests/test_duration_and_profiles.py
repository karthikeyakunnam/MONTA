"""Critical bugs #6 (duration planning, ±5%) and #7 (pattern-specific validation)."""

import random

import pytest

from orchestration.agents.story import StoryArchitect, TimelineValidator
from orchestration.agents.story.assembly import waterfill
from orchestration.agents.story.patterns import DEFAULT_LIBRARY
from orchestration.agents.story.validator import ActSpec, ValidationConfig
from shared.contracts.story import ValidationRule
from shared.contracts.vocab import Emotion, Pace, StoryRole
from tests.conftest import gym_clips, make_clip
from tests.test_story import make_pack, seg

R = StoryRole


def _random_clips(n, dur, seed):
    rng = random.Random(seed)
    roles = list(R)
    return [make_clip(f"k{i}", round(rng.uniform(2, 9.5), 1), round(rng.uniform(5, 9.5), 1), rng.choice(list(Emotion)),
                      ((roles[i % len(roles)], .7),), duration=dur(rng)) for i in range(n)]


CASES = [
    ("gym reel 30 seconds", lambda: [make_clip(f"L{i}", 4 + 2 * i, 8, duration=180) for i in range(3)], 30),
    ("aggressive gym reel 20 seconds", gym_clips, 20),
    ("slow gym edit 15 seconds", lambda: _random_clips(40, lambda r: 3, 1), 15),
    ("cinematic travel film 60 seconds", lambda: _random_clips(25, lambda r: r.uniform(2, 12), 2), 60),
    ("podcast highlight 45 seconds", lambda: _random_clips(8, lambda r: r.uniform(20, 90), 3), 45),
    ("fast product promo 10 seconds", lambda: _random_clips(3, lambda r: r.uniform(4, 6), 4), 10),
]


@pytest.mark.parametrize("prompt,clips,requested", CASES)
async def test_duration_within_5_percent_when_footage_allows(prompt, clips, requested):
    plan = await StoryArchitect().design(make_pack(prompt, clips(), reconcile=False))
    assert plan.requested_duration_s == requested
    assert abs(plan.total_duration_s - requested) / requested <= 0.05
    assert ValidationRule.TARGET_DURATION not in {v.rule for v in plan.validation.violations}
    assert all(s.duration >= 0.45 for s in plan.timeline)


async def test_footage_limited_duration_is_reported_not_faked():
    clips = [make_clip(f"w{i}", 4 + i, 8, duration=5) for i in range(4)]
    plan = await StoryArchitect().design(make_pack("wedding video 90 seconds", clips, reconcile=False))
    assert plan.requested_duration_s == 90 and plan.target_duration_s < 20
    assert any("footage-limited" in a for a in plan.reasoning.adaptations)
    assert abs(plan.total_duration_s - plan.target_duration_s) / plan.target_duration_s <= 0.05


async def test_long_takes_are_split_into_rhythm_sized_non_overlapping_cuts():
    plan = await StoryArchitect().design(make_pack("aggressive gym reel 30 seconds",
                                                   [make_clip(f"L{i}", 4 + 2 * i, 8, duration=180) for i in range(3)],
                                                   reconcile=False))
    assert len(plan.timeline) >= 10
    by_clip = {}
    for s in plan.timeline:
        by_clip.setdefault(s.clip_id, []).append((s.source_in, s.source_out))
    for windows in by_clip.values():
        windows.sort()
        assert all(b[0] >= a[1] - 1e-6 for a, b in zip(windows, windows[1:])), "split windows must not reuse footage"
    assert ValidationRule.NO_SOURCE_OVERLAP not in {v.rule for v in plan.validation.violations}


def test_waterfill_hits_total_and_respects_bounds():
    lengths = waterfill([1.0, 1.0, 2.0], [0.5, 0.5, 0.5], [3.0, 10.0, 10.0], 12.0)
    assert sum(lengths) == pytest.approx(12.0) and lengths[0] <= 3.0
    assert waterfill([1, 1], [0.5, 0.5], [1, 1], 5.0) == [1, 1]          # infeasible: saturates at max
    assert waterfill([1, 1], [0.5, 0.5], [9, 9], 0.2) == [0.5, 0.5]      # over-full: saturates at min


# ---------------------------------------------------------------- pattern-specific validation

def test_profiles_differ_by_story_type():
    podcast, fitness = DEFAULT_LIBRARY.get("podcast_highlight").validation, DEFAULT_LIBRARY.get("fitness_reel").validation
    assert podcast.low_energy_threshold < fitness.low_energy_threshold
    assert podcast.max_low_energy_run > fitness.max_low_energy_run


async def test_podcast_on_realistic_energy_passes_its_own_profile():
    talk = [make_clip(f"t{i}", 2.0 + 0.3 * (i % 3), 8, Emotion.INSPIRING, ((R.TALKING_HEAD, .9),),
                      activities=("speaking into microphone",)) for i in range(8)]
    talk[3] = make_clip("t3", 3.0, 9, Emotion.INSPIRING, ((R.HERO, .9), (R.TALKING_HEAD, .9)), activities=("speaking into microphone",))
    plan = await StoryArchitect().design(make_pack("podcast clip from our episode", talk))
    assert plan.story_pattern == "podcast_highlight"
    assert plan.validation.profile == "podcast_highlight"
    assert plan.validation.passed, plan.validation.summary


def test_same_timeline_judged_by_pattern_profile():
    clips = {f"k{i}": make_clip(f"k{i}", 2.5, duration=4) for i in range(6)}
    tl = [seg(i, f"k{i}", i, i + 1, "a1", energy=2.5 + 0.1 * i, hero=(i == 5)) for i in range(6)]
    acts = [ActSpec("a1", "rise")]
    v = TimelineValidator()
    fitness = v.validate(tl, acts, clips, Pace.MEDIUM, config=v.config_for("fitness_reel"))
    podcast = v.validate(tl, acts, clips, Pace.MEDIUM, config=v.config_for("podcast_highlight"))
    assert ValidationRule.MAX_LOW_ENERGY_RUN in {x.rule for x in fitness.violations}
    assert ValidationRule.MAX_LOW_ENERGY_RUN not in {x.rule for x in podcast.violations}
    assert fitness.profile == "fitness_reel" and podcast.profile == "podcast_highlight"


def test_release_drop_into_fall_act_allowed_but_spike_up_is_not():
    clips = {f"k{i}": make_clip(f"k{i}", 5, duration=4) for i in range(3)}
    acts = [ActSpec("peak", "rise"), ActSpec("afterglow", "fall")]
    drop = [seg(0, "k0", 0, 2, "peak", energy=9.4, hero=True), seg(1, "k1", 2, 4, "afterglow", energy=3.6)]
    rise = [seg(0, "k0", 0, 2, "peak", energy=3.6), seg(1, "k1", 2, 4, "afterglow", energy=9.4, hero=True)]
    v = TimelineValidator()
    assert ValidationRule.NO_ABRUPT_ENERGY_SPIKES not in {x.rule for x in v.validate(drop, acts, clips, Pace.FAST).violations}
    assert ValidationRule.NO_ABRUPT_ENERGY_SPIKES in {x.rule for x in v.validate(rise, acts, clips, Pace.FAST).violations}


def test_low_energy_run_counts_shots_not_pieces():
    clips = {"a": make_clip("a", 3, duration=20), "b": make_clip("b", 6, duration=4)}
    tl = [seg(i, "a", i, i + 1, "a1", energy=3.0, src_in=float(i)) for i in range(5)] + [seg(5, "b", 5, 6, "a1", energy=6, hero=True)]
    report = TimelineValidator().validate(tl, [ActSpec("a1", "rise")], clips, Pace.MEDIUM, config=ValidationConfig())
    assert ValidationRule.MAX_LOW_ENERGY_RUN not in {x.rule for x in report.violations}
    assert ValidationRule.NO_JUMP_CUTS in {x.rule for x in report.violations}
    assert report.passed, "jump cuts are warnings"
