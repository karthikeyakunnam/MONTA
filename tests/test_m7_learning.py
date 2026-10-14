"""
MONTA — M7 Learning & Feedback Test Suite
=========================================
Tests:
- UserPreferencesStore: persistence, dedup, bounded records, corrupt file tolerance, privacy reset, GDPR export
- FeedbackLoop: multi-tier synchronization (UserPreferences + ExplicitPreferences + NarrativeMemory)
- LearningEngine: suggest improvements & apply learned preferences to intent
- Baseline vs. After Learning evaluation: verifies that positive feedback actively alters subsequent generation decisions
- Feedback API routes via TestClient
"""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.app.main import app
from memory.feedback_loop import FeedbackLoop
from memory.learning_engine import LearningEngine
from memory.narrative_memory import InMemoryNarrativeStore, InMemoryPreferenceStore
from memory.user_preferences import FeedbackRecord, UserPreferencesStore
from shared.contracts.explain import Explained
from shared.contracts.intent import IntentAnalysis
from shared.contracts.vocab import (
    CaptionStyle,
    ColorGrade,
    Emotion,
    Genre,
    MusicStyle,
    Pace,
    Platform,
)


# ===========================================================================
# 1. UserPreferencesStore Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_user_preferences_store_lifecycle(tmp_path: Path):
    store = UserPreferencesStore(storage_dir=str(tmp_path))
    user_id = "test_creator_1"

    # 1. Record feedback
    rec1 = await store.record_feedback(
        user_id=user_id,
        project_id="proj_1",
        edit_id="edit_1",
        liked=True,
        rating=9.5,
        tags=["fast", "cinematic"],
        style="dramatic",
        prompt_summary="Action sequence",
    )
    assert rec1.liked is True
    assert rec1.rating == 9.5

    # 2. Query aggregated preferences
    prefs = await store.get_preferences(user_id)
    assert prefs["feedback_count"] == 1
    assert prefs["like_ratio"] == 1.0
    assert prefs["preferred_style"] == "dramatic"
    assert "fast" in prefs["preferred_tags"]

    # 3. Retrieve liked examples
    liked = await store.get_liked_examples(user_id)
    assert len(liked) == 1
    assert liked[0]["style"] == "dramatic"

    # 4. Dedup test: updating same edit_id replaces old record
    await store.record_feedback(
        user_id=user_id,
        project_id="proj_1",
        edit_id="edit_1",
        liked=True,
        rating=10.0,
        tags=["fast", "neon"],
        style="cyberpunk",
    )
    prefs_after = await store.get_preferences(user_id)
    assert prefs_after["feedback_count"] == 1  # Still 1, not 2
    assert prefs_after["preferred_style"] == "cyberpunk"

    # 5. GDPR Export
    export = await store.export_user_data(user_id)
    assert export["user_id"] == user_id
    assert export["record_count"] == 1

    # 6. Privacy reset
    reset_ok = await store.reset_user(user_id)
    assert reset_ok is True
    prefs_reset = await store.get_preferences(user_id)
    assert prefs_reset["feedback_count"] == 0


@pytest.mark.asyncio
async def test_user_preferences_corrupt_file_handling(tmp_path: Path):
    store = UserPreferencesStore(storage_dir=str(tmp_path))
    user_id = "corrupt_user"

    # Write corrupt JSON to disk
    user_file = tmp_path / f"{user_id}.json"
    user_file.write_text("{corrupted json non-parseable", encoding="utf-8")

    # Should not crash; gracefully return empty preferences
    prefs = await store.get_preferences(user_id)
    assert prefs["feedback_count"] == 0

    # New feedback should safely overwrite corrupt file
    await store.record_feedback(
        user_id=user_id,
        project_id="proj_x",
        edit_id="edit_x",
        liked=True,
        rating=8.0,
    )
    prefs_repaired = await store.get_preferences(user_id)
    assert prefs_repaired["feedback_count"] == 1


# ===========================================================================
# 2. FeedbackLoop Coordination Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_feedback_loop_synchronization(tmp_path: Path):
    pref_store = UserPreferencesStore(storage_dir=str(tmp_path))
    explicit_store = InMemoryPreferenceStore()
    narrative_store = InMemoryNarrativeStore()

    loop = FeedbackLoop(
        preferences_store=pref_store,
        explicit_pref_store=explicit_store,
        narrative_store=narrative_store,
    )

    user_id = "sync_user"
    await loop.process_feedback(
        user_id=user_id,
        project_id="proj_100",
        edit_id="edit_v1",
        liked=True,
        rating=9.0,
        tags=["fast", "dynamic"],
        style="electronic",
        project_type="vlog",
        story_pattern="montage",
        pace="fast",
    )

    # 1. UserPreferencesStore updated
    prefs = await pref_store.get_preferences(user_id)
    assert prefs["feedback_count"] == 1

    # 2. ExplicitPreferenceStore updated (consumed by ContextComposer)
    exp = await explicit_store.get(user_id)
    assert exp is not None
    assert exp.pace == Pace.FAST

    # 3. NarrativeMemory updated (consumed by Bayesian priors)
    records = await narrative_store.for_user(user_id)
    assert len(records) == 1
    assert records[0].story_pattern == "montage"
    assert records[0].success_index >= 0.7


# ===========================================================================
# 3. LearningEngine & Baseline-vs-Learned Evaluation
# ===========================================================================

@pytest.mark.asyncio
async def test_learning_engine_suggest_improvements(tmp_path: Path):
    pref_store = UserPreferencesStore(storage_dir=str(tmp_path))
    engine = LearningEngine(preferences_store=pref_store)
    user_id = "learner_1"

    # Seed feedback: user loves "cinematic" and "fast" edits
    for i in range(3):
        await pref_store.record_feedback(
            user_id=user_id,
            project_id=f"p_{i}",
            edit_id=f"e_{i}",
            liked=True,
            rating=9.0,
            tags=["fast"],
            style="cinematic",
        )

    # Query suggestions for a slow, documentary cut
    suggestions = await engine.suggest_improvements(
        user_id=user_id,
        current_style="documentary",
        current_pace="slow",
    )
    assert len(suggestions) >= 2
    types = [s["type"] for s in suggestions]
    assert "style_adjustment" in types
    assert "pacing_adjustment" in types


@pytest.mark.asyncio
async def test_learning_engine_baseline_vs_after_evaluation(tmp_path: Path):
    """M7 Evaluation Gate:

    Demonstrates that before learning, intent is unchanged;
    After learning from user feedback, intent is adapted to user's
    demonstrated preferences without altering unrelated intent fields.
    """
    pref_store = UserPreferencesStore(storage_dir=str(tmp_path))
    engine = LearningEngine(preferences_store=pref_store)
    user_id = "eval_creator"

    # Baseline intent (unpersonalized)
    baseline_intent = IntentAnalysis(
        raw_prompt="My trip to Tokyo",
        normalized_prompt="my trip to tokyo",
        genre=Explained(value=Genre.TRAVEL, confidence=0.9, reasoning="Trip to Tokyo"),
        emotion=Explained(value=Emotion.INSPIRING, confidence=0.85, reasoning="Travel inspiration"),
        pace=Explained(value=Pace.MEDIUM, confidence=0.8, reasoning="Default pace"),
        target_platform=Explained(value=Platform.TIKTOK, confidence=0.9, reasoning="Short format"),
        music_style=Explained(value=MusicStyle.AMBIENT, confidence=0.8, reasoning="Ambient default"),
        color_grade=Explained(value=ColorGrade.NATURAL, confidence=0.85, reasoning="Natural look"),
        overall_confidence=0.85,
        extractors=("test_extractor",),
    )

    # 1. Before learning: intent remains exactly at baseline
    unchanged_intent = await engine.apply_learning_to_intent(user_id, baseline_intent)
    assert unchanged_intent.pace.value == Pace.MEDIUM
    assert unchanged_intent.music_style.value == MusicStyle.AMBIENT

    # 2. Training / Feedback phase: Creator rates 3 fast, upbeat edits very positively
    for i in range(3):
        await engine.learn_from_project(
            user_id=user_id,
            project_id=f"tokyo_v{i}",
            edit_id=f"cut_{i}",
            liked=True,
            rating=9.5,
            tags=["fast", "high-energy"],
            style="electronic",
        )

    # 3. After learning: Subsequent intent generation applies learned pace and music style
    learned_intent = await engine.apply_learning_to_intent(user_id, baseline_intent)
    assert learned_intent.pace.value == Pace.FAST  # Adapted based on feedback!
    assert learned_intent.music_style.value == MusicStyle.ELECTRONIC  # Adapted based on feedback!

    # 4. Unrelated fields remain unchanged (no corruption of prompt adherence)
    assert learned_intent.genre.value == Genre.TRAVEL
    assert learned_intent.target_platform.value == Platform.TIKTOK
    assert learned_intent.raw_prompt == "My trip to Tokyo"


# ===========================================================================
# 4. Feedback API Endpoints via TestClient
# ===========================================================================

def test_feedback_api_endpoints():
    client = TestClient(app)
    user_headers = {"X-MONTA-User": "api_test_user"}

    # 1. Submit feedback
    submission = {
        "project_id": "proj_api_1",
        "edit_id": "edit_api_1",
        "liked": True,
        "rating": 9.2,
        "tags": ["sharp", "fast"],
        "style": "cinematic",
        "comment": "Loved the fast cuts!",
    }
    resp = client.post("/api/v1/feedback/", json=submission, headers=user_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert "dedup_key" in data

    # 2. Get learned preferences
    resp_prefs = client.get("/api/v1/feedback/preferences", headers=user_headers)
    assert resp_prefs.status_code == 200
    pref_data = resp_prefs.json()
    assert pref_data["preferences"]["feedback_count"] == 1
    assert pref_data["preferences"]["preferred_style"] == "cinematic"

    # 3. Get liked examples
    resp_liked = client.get("/api/v1/feedback/liked", headers=user_headers)
    assert resp_liked.status_code == 200
    liked_data = resp_liked.json()
    assert len(liked_data["liked_examples"]) == 1

    # 4. Export GDPR data
    resp_export = client.get("/api/v1/feedback/export", headers=user_headers)
    assert resp_export.status_code == 200
    assert resp_export.json()["record_count"] == 1

    # 5. Reset data
    resp_reset = client.post("/api/v1/feedback/reset", headers=user_headers)
    assert resp_reset.status_code == 200
    assert resp_reset.json()["reset"] is True
