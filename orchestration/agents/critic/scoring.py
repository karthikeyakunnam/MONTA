"""
MONTA — Scoring System
========================
Weighted scoring for the Critic System.
"""


class ScoringSystem:
    """Weighted scoring across quality dimensions."""

    DEFAULT_WEIGHTS = {
        "story_quality": 0.25,
        "pacing": 0.20,
        "music_sync": 0.20,
        "caption_timing": 0.15,
        "visual_consistency": 0.20,
    }

    def calculate_weighted_score(self, scores: dict, weights: dict = None) -> float:
        """Calculate weighted average score."""
        w = weights or self.DEFAULT_WEIGHTS
        total = sum(scores.get(dim, 0) * w.get(dim, 0) for dim in w)
        return round(total, 2)

    def identify_weaknesses(self, scores: dict, threshold: float = 6.0) -> list:
        """Identify dimensions below threshold for targeted improvement."""
        return [dim for dim, score in scores.items() if score < threshold]
