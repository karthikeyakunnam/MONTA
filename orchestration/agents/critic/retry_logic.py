"""
MONTA — Retry Logic
=====================
Handles the retry loop when critic scores are below threshold.
"""


class RetryManager:
    """Manages the critic-retry loop."""

    MAX_RETRIES = 3

    def should_retry(self, score: float, retry_count: int) -> bool:
        """Determine if the edit should be retried."""
        return score < 7.0 and retry_count < self.MAX_RETRIES

    def get_retry_strategy(self, feedback: list, scores: dict) -> dict:
        """Determine what to focus on in the retry."""
        # TODO: Analyze feedback to create targeted retry plan
        return {
            "focus_areas": [],
            "skip_areas": [],
            "strategy": "targeted",
        }
