"""Narrative memory (in-memory + SQL parity, learned priors) and Layer 4 ContextPack assembly."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from memory.narrative_memory import (
    InMemoryNarrativeStore,
    InMemoryPreferenceStore,
    NarrativeLearner,
    SqlNarrativeStore,
    SqlPreferenceStore,
)
from services.context_composer import ContextComposer
from services.context_composer.preferences import apply_preferences, infer_preferences
from services.prompt_engine.lexical_extractor import LexicalIntentExtractor
from shared.contracts.context import ExplicitPreferences
from shared.contracts.memory import NarrativeMemoryRecord
from shared.contracts.vocab import CaptionStyle, Genre, Pace, Platform
from tests.conftest import gym_clips, metadata_for

PATTERNS = ["transformation", "fitness_reel", "travel"]


def rec(i: int, pattern: str, *, genre=Genre.FITNESS, user="u1", rating=8.0, engagement=8.0, pace=Pace.FAST, elements=()):
    return NarrativeMemoryRecord(record_id=f"r{i}", project_id=f"p{i}", user_id=user, project_type=genre, story_pattern=pattern,
                                 pace=pace, engagement_score=engagement, completion_rate=7.0, user_rating=rating,
                                 successful_elements=tuple(elements))


def test_success_index_weights():
    r = rec(1, "x", rating=10, engagement=10)
    assert r.success_index == pytest.approx((0.4 * 10 + 0.3 * 7 + 0.3 * 10) / 10)


@pytest.fixture(params=["memory", "sql"])
async def store(request):
    if request.param == "memory":
        yield InMemoryNarrativeStore()
        return
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    s = SqlNarrativeStore(engine)
    await s.create_schema()
    yield s
    await engine.dispose()


async def test_store_contract(store):
    for i in range(3):
        await store.record(rec(i, "transformation", elements=("caption:Nike", "hero_at_0.9")))
    await store.record(rec(3, "travel", genre=Genre.TRAVEL, rating=2, engagement=2))
    await store.record(rec(0, "transformation", rating=9.0, elements=("caption:Nike",)))  # upsert

    assert len(await store.for_user("u1")) == 4
    successful = await store.successful(Genre.FITNESS)
    assert [r.record_id for r in successful][0] == "r0" and all(r.project_type == Genre.FITNESS for r in successful)
    stats = {s.story_pattern: s for s in await store.pattern_stats(Genre.FITNESS)}
    assert stats["transformation"].sample_size == 3
    assert stats["transformation"].top_elements[0] == "caption:Nike"
    assert "travel" not in stats
    assert {s.story_pattern for s in await store.pattern_stats(None)} == {"transformation", "travel"}


async def test_sql_preference_store_roundtrip():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await SqlNarrativeStore(engine).create_schema()
    prefs = SqlPreferenceStore(engine)
    await prefs.put(ExplicitPreferences(user_id="u1", pace=Pace.SLOW))
    await prefs.put(ExplicitPreferences(user_id="u1", pace=Pace.FAST, blocked_patterns=("travel",)))
    got = await prefs.get("u1")
    assert got.pace == Pace.FAST and got.blocked_patterns == ("travel",)
    assert await prefs.get("nobody") is None
    await engine.dispose()


async def test_learner_smooths_and_falls_back():
    store = InMemoryNarrativeStore([rec(i, "transformation", rating=10, engagement=10) for i in range(3)]
                                   + [rec(10, "travel", genre=Genre.TRAVEL, rating=9, engagement=9)])
    priors = {p.story_pattern: p for p in await NarrativeLearner(store, smoothing_k=5).priors(Genre.FITNESS, PATTERNS)}
    t = priors["transformation"]
    assert 0.5 < t.success_mean < rec(0, "x", rating=10, engagement=10).success_index
    assert t.confidence == pytest.approx(3 / 8)
    assert priors["travel"].sample_size == 0 and 0 < priors["travel"].confidence < 0.5  # cross-genre only
    assert priors["fitness_reel"].confidence == 0 and priors["fitness_reel"].success_mean == 0.5


# ---------------------------------------------------------------- preferences

def test_preference_inference_from_history_and_explicit_override():
    history = [rec(i, "transformation", rating=9, pace=Pace.FAST, elements=("caption:Gymshark",)) for i in range(3)]
    history += [rec(9, "travel", rating=2), rec(8, "travel", rating=3)]
    p = infer_preferences("u1", None, history)
    assert p.pace.value == Pace.FAST and p.pace.confidence > 0
    assert p.caption_style.value == CaptionStyle.GYMSHARK
    assert p.preferred_patterns == ("transformation",)
    assert "travel" in p.disliked_patterns

    p2 = infer_preferences("u1", ExplicitPreferences(user_id="u1", pace=Pace.SLOW, blocked_patterns=("event",)), history)
    assert p2.pace.value == Pace.SLOW and p2.pace.confidence == 0.95
    assert "event" in p2.disliked_patterns


def test_apply_preferences_never_overrides_the_prompt():
    prefs = infer_preferences("u1", ExplicitPreferences(user_id="u1", pace=Pace.SLOW, platform=Platform.TIKTOK), [])
    stated = apply_preferences(LexicalIntentExtractor().extract("fast gym edit"), prefs)
    assert stated.pace.value == Pace.FAST
    assert stated.target_platform.value == Platform.TIKTOK and "target_platform" not in {m.field for m in stated.missing}
    unstated = apply_preferences(LexicalIntentExtractor().extract("gym edit"), prefs)
    assert unstated.pace.value == Pace.SLOW and unstated.revisions


# ---------------------------------------------------------------- composer

def _composer(memory=None, prefs=None, timeout=1.0):
    memory = memory or InMemoryNarrativeStore()
    return ContextComposer(memory=memory, preferences=prefs or InMemoryPreferenceStore(), learner=NarrativeLearner(memory),
                           pattern_ids=PATTERNS, source_timeout_s=timeout)


async def test_compose_builds_complete_pack():
    memory = InMemoryNarrativeStore([rec(i, "transformation") for i in range(3)])
    intent = LexicalIntentExtractor().extract("aggressive gym reel for tiktok")
    pack = await _composer(memory).compose(project_id="p", user_id="u1", intent=intent, clips=[metadata_for(c) for c in gym_clips()])
    assert pack.platform.platform == Platform.TIKTOK
    assert {p.story_pattern for p in pack.story_patterns} == set(PATTERNS)
    assert pack.prior_for("transformation").sample_size == 3
    assert len(pack.historical_edits) == 3 and pack.previous_successful_projects
    assert pack.style_presets and pack.style_presets[0].name == "hype"
    assert all(s.status == "ok" for s in pack.provenance)


class _SlowStore(InMemoryNarrativeStore):
    async def for_user(self, user_id, limit=50):
        await asyncio.sleep(5)
        return []


class _BrokenPrefs(InMemoryPreferenceStore):
    async def get(self, user_id):
        raise ConnectionError("db down")


async def test_compose_degrades_instead_of_failing():
    intent = LexicalIntentExtractor().extract("gym reel")
    pack = await _composer(_SlowStore(), _BrokenPrefs(), timeout=0.05).compose(
        project_id="p", user_id="u1", intent=intent, clips=[metadata_for(gym_clips()[0])])
    status = {s.source: s.status for s in pack.provenance}
    assert status["edit_history"] == "unavailable" and status["explicit_preferences"] == "unavailable"
    assert set(pack.degraded_sources) == {"edit_history", "explicit_preferences"}


async def test_compose_requires_clips():
    with pytest.raises(ValueError):
        await _composer().compose(project_id="p", user_id="u", intent=LexicalIntentExtractor().extract("x"), clips=[])


async def test_enrich_refreshes_priors_when_genre_changes():
    memory = InMemoryNarrativeStore([rec(i, "travel", genre=Genre.TRAVEL) for i in range(4)])
    composer = _composer(memory)
    intent = LexicalIntentExtractor().extract("make it cool")
    pack = await composer.compose(project_id="p", user_id="u9", intent=intent, clips=[metadata_for(gym_clips()[0])])
    assert pack.prior_for("travel").sample_size == 0
    travel_intent = LexicalIntentExtractor().extract("travel trip vacation")
    enriched = await composer.enrich_with_footage(pack, travel_intent, {})
    assert enriched.prior_for("travel").sample_size == 4
    assert pack.prior_for("travel").sample_size == 0, "ContextPack must be immutable"
