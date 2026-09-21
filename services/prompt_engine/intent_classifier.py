"""
MONTA — Intent Classifier
============================
Classifies parsed prompt elements into structured categories.
"""


class IntentClassifier:
    """Classifies editing intent into discrete categories."""

    GENRES = ["transformation", "vlog", "tutorial", "cinematic", "hype", "montage"]
    PACING = ["slow", "dynamic", "fast", "aggressive", "rhythmic"]
    COLORS = ["orange_teal", "bw", "vibrant", "dark", "natural", "vintage", "neon"]
    EMOTIONS = ["motivational", "calm", "aggressive", "sad", "hype", "dramatic"]

    async def classify(self, parsed_intent: dict) -> dict:
        """Validate and normalize classified intent."""
        # TODO: LLM-based classification with confidence scores
        return parsed_intent
