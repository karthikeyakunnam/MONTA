"""
MONTA — Critic Graph (Layer 12)
=================================
Anthropic-style evaluator loop.
Checks story quality, pacing, music sync, caption timing, visual consistency,
aggregates scores, and manages the bounded revision loop.
"""

from langgraph.graph import StateGraph, END
from orchestration.agents.critic.evaluator import CriticEvaluator
from orchestration.agents.critic.retry_logic import RetryManager
from orchestration.agents.critic.scoring import ScoringSystem
from orchestration.state.graph_state import MontaState

_evaluator = CriticEvaluator()
_scoring = ScoringSystem()
_retry_manager = RetryManager()


def build_critic_graph() -> StateGraph:
    """Build the Critic System subgraph."""
    graph = StateGraph(MontaState)

    graph.add_node("evaluate_story", evaluate_story_node)
    graph.add_node("evaluate_pacing", evaluate_pacing_node)
    graph.add_node("evaluate_music_sync", evaluate_music_node)
    graph.add_node("evaluate_captions", evaluate_captions_node)
    graph.add_node("evaluate_visual", evaluate_visual_node)
    graph.add_node("aggregate_scores", aggregate_scores_node)

    graph.set_entry_point("evaluate_story")
    graph.add_edge("evaluate_story", "evaluate_pacing")
    graph.add_edge("evaluate_pacing", "evaluate_music_sync")
    graph.add_edge("evaluate_music_sync", "evaluate_captions")
    graph.add_edge("evaluate_captions", "evaluate_visual")
    graph.add_edge("evaluate_visual", "aggregate_scores")
    graph.add_edge("aggregate_scores", END)

    return graph


async def evaluate_story_node(state: MontaState) -> dict:
    """Evaluate story quality and narrative coherence."""
    timeline = state.get("timeline") or []
    score, feedback = _evaluator.evaluate_story_quality(timeline)
    fb_dict = dict(state.get("critic_feedback") or {})
    fb_dict["story"] = feedback
    return {
        "story_score": score,
        "critic_feedback": fb_dict,
    }


async def evaluate_pacing_node(state: MontaState) -> dict:
    """Evaluate pacing matches the intended mood."""
    timeline = state.get("timeline") or []
    intent = state.get("intent") or {}
    pace = intent.get("pace", "medium") if isinstance(intent, dict) else getattr(intent, "pace", "medium")
    score, feedback = _evaluator.evaluate_pacing(timeline, target_pace=str(pace))
    fb_dict = dict(state.get("critic_feedback") or {})
    fb_dict["pacing"] = feedback
    return {
        "pacing_score": score,
        "critic_feedback": fb_dict,
    }


async def evaluate_music_node(state: MontaState) -> dict:
    """Evaluate music sync with visual cuts."""
    audio_cfg = state.get("audio_config") or {}
    audio_tracks = audio_cfg.get("tracks", []) if isinstance(audio_cfg, dict) else []
    has_dialogue = bool(state.get("has_dialogue", False))
    score, feedback = _evaluator.evaluate_music_sync(audio_tracks, has_dialogue=has_dialogue)
    fb_dict = dict(state.get("critic_feedback") or {})
    fb_dict["music"] = feedback
    return {
        "music_sync_score": score,
        "critic_feedback": fb_dict,
    }


async def evaluate_captions_node(state: MontaState) -> dict:
    """Evaluate caption timing and readability."""
    captions = state.get("captions") or []
    score, feedback = _evaluator.evaluate_captions(captions, requested_captions=bool(captions))
    fb_dict = dict(state.get("critic_feedback") or {})
    fb_dict["captions"] = feedback
    return {
        "caption_score": score,
        "critic_feedback": fb_dict,
    }


async def evaluate_visual_node(state: MontaState) -> dict:
    """Evaluate visual consistency (color grading, transitions)."""
    timeline = state.get("timeline") or []
    intent = state.get("intent") or {}
    color = intent.get("color") if isinstance(intent, dict) else getattr(intent, "color_grade", None)
    score, feedback = _evaluator.evaluate_visual_consistency(timeline, color_grade=str(color) if color else None)
    fb_dict = dict(state.get("critic_feedback") or {})
    fb_dict["visual"] = feedback
    return {
        "visual_score": score,
        "critic_feedback": fb_dict,
    }


async def aggregate_scores_node(state: MontaState) -> dict:
    """Aggregate all scores into final critic verdict and evaluate retry."""
    scores = {
        "story_quality": state.get("story_score", 0.0),
        "pacing": state.get("pacing_score", 0.0),
        "music_sync": state.get("music_sync_score", 0.0),
        "caption_timing": state.get("caption_score", 0.0),
        "visual_consistency": state.get("visual_score", 0.0),
    }

    critic_score = _scoring.calculate_weighted_score(scores)
    retry_count = state.get("retry_count", 0)
    should_retry = _retry_manager.should_retry(critic_score, retry_count)

    fb_dict = dict(state.get("critic_feedback") or {})
    all_feedback_lines = [line for sublist in fb_dict.values() if isinstance(sublist, list) for line in sublist]

    retry_strategy = _retry_manager.get_retry_strategy(all_feedback_lines, scores)
    fb_dict["retry_strategy"] = retry_strategy

    new_retry_count = retry_count + 1 if should_retry else retry_count

    return {
        "critic_score": critic_score,
        "critic_feedback": fb_dict,
        "should_retry": should_retry,
        "retry_count": new_retry_count,
    }
