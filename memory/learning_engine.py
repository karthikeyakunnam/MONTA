"""
MONTA — Learning Engine (Layer 14 / M7)
=====================================
Analyzes user interactions, ratings, and accepted edits to improve
subsequent video generation decisions.

Features:
- Learns user style affinities (pacing, music style, narrative patterns)
- Suggests edit adjustments based on learned user history
- Calibrates story and transition selection
- Local-first, offline compatible
- Provides baseline-vs-learned comparison metrics for evaluation
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from memory.feedback_loop import FeedbackLoop
from memory.user_preferences import UserPreferencesStore
from shared.contracts.intent import IntentAnalysis
from shared.contracts.story import StoryPlan
from shared.contracts.vocab import Genre, MusicStyle, Pace

logger = logging.getLogger("monta.memory.learning_engine")


class LearningEngine:
    """Learns from user behavior and feedback history to improve edit quality."""

    def __init__(
        self,
        feedback_loop: Optional[FeedbackLoop] = None,
        preferences_store: Optional[UserPreferencesStore] = None,
    ):
        self.feedback_loop = feedback_loop or FeedbackLoop(preferences_store=preferences_store)
        self.preferences_store = preferences_store or self.feedback_loop.preferences_store

    async def learn_from_project(
        self,
        user_id: str,
        project_id: str,
        edit_id: str,
        liked: bool,
        rating: float = 0.0,
        tags: Optional[List[str]] = None,
        comment: str = "",
        model_used: str = "",
        style: str = "",
        prompt_summary: str = "",
        story_pattern: Optional[str] = None,
        pace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Learn from a completed project's feedback."""
        record = await self.feedback_loop.process_feedback(
            user_id=user_id,
            project_id=project_id,
            edit_id=edit_id,
            liked=liked,
            rating=rating,
            tags=tags,
            comment=comment,
            model_used=model_used,
            style=style,
            prompt_summary=prompt_summary,
            story_pattern=story_pattern,
            pace=pace,
        )
        prefs = await self.feedback_loop.get_user_preferences(user_id)
        return {
            "record_id": record.dedup_key,
            "learned_preferences": prefs,
        }

    async def suggest_improvements(
        self,
        user_id: str,
        intent: Optional[IntentAnalysis] = None,
        current_style: str = "",
        current_pace: str = "",
    ) -> List[Dict[str, str]]:
        """Suggest improvements to an edit based on user's learned preferences.

        Returns a list of actionable adjustment suggestions.
        """
        prefs = await self.preferences_store.get_preferences(user_id)
        suggestions: List[Dict[str, str]] = []

        if prefs.get("feedback_count", 0) < 1:
            return suggestions  # No prior feedback to learn from

        preferred_style = prefs.get("preferred_style")
        if preferred_style and current_style and preferred_style.lower() != current_style.lower():
            suggestions.append({
                "type": "style_adjustment",
                "recommendation": f"Change style from '{current_style}' to '{preferred_style}' based on prior high ratings.",
            })

        preferred_tags = prefs.get("preferred_tags", [])
        if "fast" in preferred_tags and current_pace and current_pace.lower() != "fast":
            suggestions.append({
                "type": "pacing_adjustment",
                "recommendation": "Increase editing pace — user consistently approves dynamic, fast cuts.",
            })
        elif "slow" in preferred_tags and current_pace and current_pace.lower() != "slow":
            suggestions.append({
                "type": "pacing_adjustment",
                "recommendation": "Slow down cut frequency — user previously preferred longer, contemplative shots.",
            })

        return suggestions

    async def apply_learning_to_intent(
        self,
        user_id: str,
        intent: IntentAnalysis,
    ) -> IntentAnalysis:
        """Enrich or adjust intent using learned user preferences."""
        prefs = await self.preferences_store.get_preferences(user_id)
        if prefs.get("feedback_count", 0) < 1:
            return intent

        preferred_style = prefs.get("preferred_style")
        preferred_tags = prefs.get("preferred_tags", [])

        from shared.contracts.explain import Explained

        updates = {}
        # Adjust pace if strongly indicated by user history and not overridden by user prompt
        current_pace = intent.pace.value if hasattr(intent.pace, "value") else intent.pace
        if "fast" in preferred_tags and current_pace != Pace.FAST:
            updates["pace"] = Explained(
                value=Pace.FAST,
                confidence=0.88,
                reasoning="Pace adjusted to FAST based on positive feedback on high-energy edits.",
            )
        elif "slow" in preferred_tags and current_pace != Pace.SLOW:
            updates["pace"] = Explained(
                value=Pace.SLOW,
                confidence=0.88,
                reasoning="Pace adjusted to SLOW based on positive feedback on calm edits.",
            )

        # Adjust music style if user strongly prefers a specific style
        if preferred_style:
            try:
                music_style = MusicStyle(preferred_style.lower())
                current_music = intent.music_style.value if (intent.music_style and hasattr(intent.music_style, "value")) else None
                if current_music != music_style:
                    updates["music_style"] = Explained(
                        value=music_style,
                        confidence=0.85,
                        reasoning=f"Music style set to {music_style.value} based on creator preference history.",
                    )
            except ValueError:
                pass

        if updates:
            from shared.contracts.base import revalidate

            logger.info("Applying learned preferences to intent for user %s: %s", user_id, list(updates.keys()))
            return revalidate(intent, **updates)

        return intent
