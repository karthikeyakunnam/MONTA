"""
MONTA — Critic Evaluator (Layer 12)
=====================================
Multi-dimensional quality evaluator that scores edit quality across story structure,
pacing, audio/music sync, caption timing, and visual consistency.
Produces explainable scores, targeted feedback, and triggers bounded retries.
"""

import logging
from typing import Any, Optional

from orchestration.agents.critic.scoring import ScoringSystem

logger = logging.getLogger("monta.critic.evaluator")


class CriticEvaluator:
    """Multi-dimensional edit quality evaluator."""

    DIMENSIONS = [
        "story_quality",
        "pacing",
        "music_sync",
        "caption_timing",
        "visual_consistency",
    ]

    RETRY_THRESHOLD = 7.0
    MAX_RETRIES = 3

    def __init__(self, scoring: Optional[ScoringSystem] = None):
        self.scoring = scoring or ScoringSystem()

    def evaluate_story_quality(self, timeline_cuts: list[dict], story_plan: Optional[dict] = None) -> tuple[float, list[str]]:
        """Evaluate narrative progression, hero moment, and energy dynamics."""
        if not timeline_cuts:
            return 2.0, ["Timeline has no cuts."]

        feedback = []
        score = 8.0

        # 1. Hero moment check
        has_hero = any(cut.get("is_hero", False) for cut in timeline_cuts)
        if not has_hero:
            score -= 1.5
            feedback.append("No defining hero moment found in story.")

        # 2. Energy progression
        energies = [float(cut.get("energy", 5.0)) for cut in timeline_cuts]
        if energies:
            energy_range = max(energies) - min(energies)
            if energy_range < 2.0 and len(timeline_cuts) > 2:
                score -= 1.0
                feedback.append("Energy arc is flat; needs more contrast between acts.")
            elif energy_range >= 3.0:
                score += 1.0

        # 3. Minimum cuts check
        if len(timeline_cuts) < 2:
            score -= 2.0
            feedback.append("Story has too few cuts to establish narrative momentum.")

        return max(1.0, min(10.0, score)), feedback

    def evaluate_pacing(self, timeline_cuts: list[dict], target_pace: str = "medium") -> tuple[float, list[str]]:
        """Evaluate cut rhythm and duration against target pace."""
        if not timeline_cuts:
            return 2.0, ["Empty cut list for pacing check."]

        feedback = []
        score = 8.5

        durations = []
        for cut in timeline_cuts:
            start = cut.get("start", cut.get("timeline_start_ms", 0) / 1000.0)
            end = cut.get("end", cut.get("timeline_end_ms", 0) / 1000.0)
            dur = max(0.1, end - start)
            durations.append(dur)

        avg_dur = sum(durations) / len(durations)
        pace = target_pace.lower().strip()

        # Check against target pace expectations
        if pace == "fast" and avg_dur > 3.0:
            score -= 2.0
            feedback.append(f"Fast pace requested, but average cut length is {avg_dur:.1f}s (target: <2.5s).")
        elif pace == "slow" and avg_dur < 2.5:
            score -= 1.5
            feedback.append(f"Slow pace requested, but average cut length is {avg_dur:.1f}s (target: >3.5s).")
        elif pace in ("medium", "moderate"):
            if avg_dur > 6.0:
                score -= 1.5
                feedback.append(f"Cuts are lingering too long ({avg_dur:.1f}s average).")
            elif avg_dur < 1.0:
                score -= 1.0
                feedback.append(f"Cuts are overly frantic ({avg_dur:.1f}s average).")

        # Check for monotonous cut lengths
        if len(durations) >= 4:
            variance = sum((d - avg_dur) ** 2 for d in durations) / len(durations)
            if variance < 0.05:
                score -= 1.0
                feedback.append("Cut durations are completely uniform; vary pacing to create rhythm.")

        return max(1.0, min(10.0, score)), feedback

    def evaluate_music_sync(self, audio_tracks: list[dict], has_dialogue: bool = False) -> tuple[float, list[str]]:
        """Evaluate background audio presence, level, and speech ducking."""
        feedback = []
        score = 8.5

        if not audio_tracks:
            score -= 2.0
            feedback.append("No background music or audio soundtrack attached.")
            return max(1.0, score), feedback

        # Check ducking when dialogue is present
        bg_tracks = [t for t in audio_tracks if "bg" in str(t.get("track_id", "")).lower() or t.get("loop", False)]
        if has_dialogue and bg_tracks:
            ducked = any(t.get("ducking", False) for t in bg_tracks)
            if not ducked:
                score -= 1.5
                feedback.append("Dialogue present but background music ducking is disabled.")
            else:
                score += 0.5

        # Check volume range
        for t in bg_tracks:
            vol = float(t.get("volume", 1.0))
            if vol > 0.8 and has_dialogue:
                score -= 1.0
                feedback.append("Background music volume is too loud over speech.")

        return max(1.0, min(10.0, score)), feedback

    def evaluate_captions(self, captions: list[dict], requested_captions: bool = True) -> tuple[float, list[str]]:
        """Evaluate caption readability, length, and timing."""
        feedback = []
        score = 8.5

        if not requested_captions:
            return 9.0, []

        if not captions:
            # Captions requested but none provided
            score -= 1.5
            feedback.append("Captions requested but no caption segments generated.")
            return score, feedback

        # Check reading speed on caption segments
        for seg in captions:
            dur = max(0.2, seg.get("end", 0.0) - seg.get("start", 0.0))
            text = seg.get("text", "")
            chars_per_sec = len(text) / dur
            if chars_per_sec > 28.0:
                score -= 1.0
                feedback.append(f"Caption '{text[:20]}...' displayed too fast ({chars_per_sec:.0f} chars/sec).")
                break

        return max(1.0, min(10.0, score)), feedback

    def evaluate_visual_consistency(self, timeline_cuts: list[dict], color_grade: Optional[str] = None) -> tuple[float, list[str]]:
        """Evaluate color consistency and transition smoothness."""
        feedback = []
        score = 8.5

        if not timeline_cuts:
            return 2.0, ["No visual segments."]

        # Check color adjustment presence
        has_color = any(cut.get("color") is not None for cut in timeline_cuts)
        if color_grade and not has_color:
            score -= 1.0
            feedback.append(f"Color grade '{color_grade}' requested but segments lack color adjustments.")
        elif has_color:
            score += 0.5

        return max(1.0, min(10.0, score)), feedback

    async def evaluate(self, edit_result: dict[str, Any]) -> dict[str, Any]:
        """Comprehensive multi-dimensional evaluation of an edit."""
        timeline_cuts = edit_result.get("timeline", edit_result.get("video_segments", []))
        audio_tracks = edit_result.get("audio_segments", edit_result.get("audio_tracks", []))
        captions = edit_result.get("captions", [])
        has_dialogue = edit_result.get("has_dialogue", False)
        target_pace = edit_result.get("pace", "medium")
        color_grade = edit_result.get("color_grade")
        requested_captions = edit_result.get("requested_captions", len(captions) > 0)

        s_score, s_feed = self.evaluate_story_quality(timeline_cuts, edit_result.get("story_plan"))
        p_score, p_feed = self.evaluate_pacing(timeline_cuts, target_pace=target_pace)
        m_score, m_feed = self.evaluate_music_sync(audio_tracks, has_dialogue=has_dialogue)
        c_score, c_feed = self.evaluate_captions(captions, requested_captions=requested_captions)
        v_score, v_feed = self.evaluate_visual_consistency(timeline_cuts, color_grade=color_grade)

        scores = {
            "story_quality": s_score,
            "pacing": p_score,
            "music_sync": m_score,
            "caption_timing": c_score,
            "visual_consistency": v_score,
        }

        all_feedback = s_feed + p_feed + m_feed + c_feed + v_feed
        overall = self.scoring.calculate_weighted_score(scores)
        should_retry = overall < self.RETRY_THRESHOLD

        logger.info(
            "Critic evaluation: overall=%.2f (story=%.1f, pace=%.1f, music=%.1f, cap=%.1f, vis=%.1f) -> retry=%s",
            overall, s_score, p_score, m_score, c_score, v_score, should_retry,
        )

        return {
            "overall_score": overall,
            "scores": scores,
            "feedback": all_feedback,
            "should_retry": should_retry,
        }
