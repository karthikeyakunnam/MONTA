"""
MONTA — Retry Logic (Layer 12)
================================
Handles the bounded automatic retry loop when critic scores are below threshold.
Identifies weak dimensions and generates targeted revision directives.
"""

from typing import Any


class RetryManager:
    """Manages the critic-retry loop and generates targeted revision strategies."""

    MAX_RETRIES = 3
    DEFAULT_THRESHOLD = 7.0
    STORY_RETRY_AREAS = frozenset({"story_quality", "pacing"})

    def should_retry(
        self,
        score: float,
        retry_count: int,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> bool:
        """Determine if the edit should be retried."""
        return score < threshold and retry_count < self.MAX_RETRIES

    def get_retry_strategy(
        self,
        feedback: list[str],
        scores: dict[str, float],
        threshold: float = DEFAULT_THRESHOLD,
    ) -> dict[str, Any]:
        """Determine what to focus on in the revision."""
        weak_areas = [dim for dim, s in scores.items() if s < threshold]
        strong_areas = [dim for dim, s in scores.items() if s >= threshold]

        directives = []
        for dim in weak_areas:
            if dim == "pacing":
                directives.append({
                    "action": "adjust_cut_durations",
                    "instruction": "Tighten cut durations and increase rhythmic variation between cuts.",
                })
            elif dim == "story_quality":
                directives.append({
                    "action": "strengthen_narrative_arc",
                    "instruction": "Ensure a clear hero climax shot is designated and maximize act energy delta.",
                })
            elif dim == "music_sync":
                directives.append({
                    "action": "enable_speech_ducking",
                    "instruction": "Ensure background audio ducking is enabled and adjust music volume balance.",
                })
            elif dim == "caption_timing":
                directives.append({
                    "action": "recalculate_caption_duration",
                    "instruction": "Extend caption segment display times to keep reading speed below 25 chars/sec.",
                })
            elif dim == "visual_consistency":
                directives.append({
                    "action": "apply_uniform_color_grade",
                    "instruction": "Apply consistent color grading and smooth transitions across all cuts.",
                })

        return {
            "focus_areas": weak_areas,
            "implemented_focus_areas": [dim for dim in weak_areas if dim in self.STORY_RETRY_AREAS],
            "deferred_focus_areas": [dim for dim in weak_areas if dim not in self.STORY_RETRY_AREAS],
            "skip_areas": strong_areas,
            "strategy": "targeted" if weak_areas else "pass",
            "directives": directives,
            "feedback_summary": "; ".join(feedback[:3]) if feedback else "Scores acceptable.",
        }
