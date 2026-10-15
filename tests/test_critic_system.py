"""Tests for Layer 12: Critic Evaluator, Scoring, Retry Logic, and Critic Graph."""

import pytest

from orchestration.agents.critic.evaluator import CriticEvaluator
from orchestration.agents.critic.retry_logic import RetryManager
from orchestration.agents.critic.scoring import ScoringSystem
from orchestration.graphs.critic_graph import build_critic_graph


def test_scoring_system_weights():
    scoring = ScoringSystem()
    scores = {
        "story_quality": 8.0,
        "pacing": 8.0,
        "music_sync": 8.0,
        "caption_timing": 8.0,
        "visual_consistency": 8.0,
    }
    overall = scoring.calculate_weighted_score(scores)
    assert overall == 8.0

    weak = scoring.identify_weaknesses({"story_quality": 8.0, "pacing": 5.0}, threshold=6.0)
    assert weak == ["pacing"]


async def test_critic_evaluator_passes_good_edit():
    evaluator = CriticEvaluator()
    good_edit = {
        "timeline": [
            {"clip_id": "c1", "start": 0.0, "end": 2.5, "energy": 4.0, "color": {"brightness": 0.0}},
            {"clip_id": "c2", "start": 2.5, "end": 5.0, "energy": 8.5, "is_hero": True, "color": {"brightness": 0.0}},
        ],
        "audio_segments": [
            {"track_id": "bg_music", "volume": 0.35, "ducking": True, "loop": True},
        ],
        "captions": [
            {"start": 0.5, "end": 2.0, "text": "Hello world"},
        ],
        "has_dialogue": True,
        "pace": "medium",
        "color_grade": "cinematic",
    }

    result = await evaluator.evaluate(good_edit)
    assert result["overall_score"] >= 7.0
    assert result["should_retry"] is False
    assert "story_quality" in result["scores"]
    assert "pacing" in result["scores"]
    assert "music_sync" in result["scores"]


async def test_critic_evaluator_fails_poor_edit_and_triggers_retry():
    evaluator = CriticEvaluator()
    poor_edit = {
        # Only 1 cut, no hero, no audio, no captions
        "timeline": [
            {"clip_id": "c1", "start": 0.0, "end": 10.0, "energy": 3.0, "is_hero": False},
        ],
        "audio_segments": [],
        "captions": [],
        "has_dialogue": False,
        "pace": "fast",  # 10s cut for fast pace is completely mismatched
    }

    result = await evaluator.evaluate(poor_edit)
    assert result["overall_score"] < 7.0
    assert result["should_retry"] is True
    assert len(result["feedback"]) > 0


def test_retry_manager_bounds_retries():
    rm = RetryManager()
    # Below threshold on attempt 0, 1, 2 should retry
    assert rm.should_retry(score=5.5, retry_count=0) is True
    assert rm.should_retry(score=5.5, retry_count=1) is True
    assert rm.should_retry(score=5.5, retry_count=2) is True

    # Attempt 3 (MAX_RETRIES reached) must NOT retry (bounded loop)
    assert rm.should_retry(score=5.5, retry_count=3) is False

    # Meeting threshold should not retry
    assert rm.should_retry(score=7.5, retry_count=0) is False


def test_retry_manager_generates_targeted_strategy():
    rm = RetryManager()
    scores = {"story_quality": 4.5, "pacing": 5.0, "music_sync": 8.5}
    feedback = ["No hero moment", "Cuts lingering too long"]

    strategy = rm.get_retry_strategy(feedback, scores)
    assert "story_quality" in strategy["focus_areas"]
    assert "pacing" in strategy["focus_areas"]
    assert "music_sync" in strategy["skip_areas"]
    assert strategy["implemented_focus_areas"] == ["story_quality", "pacing"]
    assert strategy["deferred_focus_areas"] == []
    assert len(strategy["directives"]) == 2


def test_retry_manager_does_not_claim_unsupported_dimensions_are_applied():
    strategy = RetryManager().get_retry_strategy(
        feedback=["Music is too loud", "Captions are late"],
        scores={"music_sync": 4.0, "caption_timing": 5.0},
    )
    assert strategy["implemented_focus_areas"] == []
    assert strategy["deferred_focus_areas"] == ["music_sync", "caption_timing"]


async def test_critic_graph_execution():
    graph = build_critic_graph()
    app = graph.compile()

    state = {
        "timeline": [
            {"clip_id": "c1", "start": 0.0, "end": 2.0, "energy": 4.0},
            {"clip_id": "c2", "start": 2.0, "end": 5.0, "energy": 8.0, "is_hero": True},
        ],
        "audio_config": {
            "tracks": [{"track_id": "bg_01", "ducking": True}],
        },
        "captions": [],
        "intent": {"pace": "medium"},
        "retry_count": 0,
    }

    final_state = await app.ainvoke(state)

    assert "critic_score" in final_state
    assert final_state["critic_score"] > 0.0
    assert "critic_feedback" in final_state
    assert "should_retry" in final_state
