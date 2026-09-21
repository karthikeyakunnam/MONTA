"""
MONTA — Critic Evaluator
==========================
Anthropic-style evaluator that scores edit quality.
"""


class CriticEvaluator:
    """Multi-dimensional edit quality evaluator."""

    DIMENSIONS = [
        "story_quality",
        "pacing",
        "music_sync",
        "caption_timing",
        "visual_consistency",
    ]

    RETRY_THRESHOLD = 7.0  # Score below this triggers retry
    MAX_RETRIES = 3

    def __init__(self, llm=None):
        self.llm = llm

    async def evaluate(self, edit_result: dict) -> dict:
        """
        Evaluate the complete edit across all dimensions.
        
        Returns:
            {
                "overall_score": 8.5,
                "scores": {"story_quality": 9.0, ...},
                "feedback": ["pacing could be tighter in act 2", ...],
                "should_retry": False
            }
        """
        # TODO: Multi-dimensional evaluation using LLM
        return {
            "overall_score": 0.0,
            "scores": {},
            "feedback": [],
            "should_retry": False,
        }
