"""
MONTA — Feedback Loop (Layer 14 / M7)
======================================
Coordinates user feedback across the system:
1. Persists granular user feedback into local-first UserPreferencesStore
2. Synchronizes feedback into PreferenceStore (for ContextComposer consumption)
3. Registers completed project outcomes into NarrativeMemoryStore (for Bayesian prior updates)
4. Provides GDPR-compliant privacy reset and data export
"""

import logging
from typing import Any, Dict, List, Optional

from memory.narrative_memory import (
    InMemoryNarrativeStore,
    InMemoryPreferenceStore,
    NarrativeMemoryStore,
    PreferenceStore,
)
from memory.user_preferences import FeedbackRecord, UserPreferencesStore
from shared.contracts.context import ExplicitPreferences
from shared.contracts.memory import NarrativeMemoryRecord
from shared.contracts.vocab import (
    CaptionStyle,
    ColorGrade,
    Emotion,
    Genre,
    MusicStyle,
    Pace,
    Platform,
)

logger = logging.getLogger("monta.memory.feedback_loop")


class FeedbackLoop:
    """Processes user feedback and updates preference models and priors."""

    def __init__(
        self,
        preferences_store: Optional[UserPreferencesStore] = None,
        explicit_pref_store: Optional[PreferenceStore] = None,
        narrative_store: Optional[NarrativeMemoryStore] = None,
    ):
        self.preferences_store = preferences_store or UserPreferencesStore()
        self.explicit_pref_store = explicit_pref_store or InMemoryPreferenceStore()
        self.narrative_store = narrative_store or InMemoryNarrativeStore()

    async def process_feedback(
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
        project_type: Optional[str] = None,
        story_pattern: Optional[str] = None,
        pace: Optional[str] = None,
        emotion: Optional[str] = None,
    ) -> FeedbackRecord:
        """Process user feedback and update both local preferences and narrative memory.

        Args:
            user_id: Unique user identifier
            project_id: Identifier of the video project
            edit_id: Specific edit or render version
            liked: True if accepted/liked, False if rejected/disliked
            rating: Rating out of 10.0 (or 0-5 scaled)
            tags: Descriptive tags (e.g. 'cinematic', 'too-fast', 'great-music')
            comment: Optional user review or change request
            model_used: LLM model used for the edit
            style: Video style (e.g. 'dramatic', 'energetic')
            prompt_summary: High-level prompt intent
            project_type: Genre (e.g. 'vlog', 'hype', 'narrative')
            story_pattern: Pattern name (e.g. 'three_act', 'montage')
            pace: Video pace (e.g. 'fast', 'moderate', 'slow')
            emotion: Video emotion (e.g. 'joy', 'suspense')
        """
        # 1. Store in local-first UserPreferencesStore
        record = await self.preferences_store.record_feedback(
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
        )

        # 2. Update explicit preferences if feedback strongly indicates style/pace
        try:
            current_prefs = await self.explicit_pref_store.get(user_id)
            current_pace = current_prefs.pace if current_prefs else None
            current_color = current_prefs.color_grade if current_prefs else None
            current_music = current_prefs.music_style if current_prefs else None

            # Infer pace updates from tags or explicit pace argument
            if pace:
                try:
                    current_pace = Pace(pace.lower())
                except ValueError:
                    pass
            elif tags:
                if any(t in tags for t in ["fast", "quick", "dynamic", "punchy"]):
                    current_pace = Pace.FAST
                elif any(t in tags for t in ["slow", "calm", "relaxing"]):
                    current_pace = Pace.SLOW

            # Infer music updates from style/tags
            if style:
                try:
                    current_music = MusicStyle(style.lower())
                except ValueError:
                    pass

            updated_prefs = ExplicitPreferences(
                user_id=user_id,
                pace=current_pace,
                color_grade=current_color,
                music_style=current_music,
            )
            await self.explicit_pref_store.put(updated_prefs)
        except Exception as e:
            logger.warning("Could not sync feedback to explicit preference store: %s", e)

        # 3. Update narrative memory outcome if story pattern is provided
        if story_pattern and project_type:
            try:
                norm_type = project_type.lower().strip()
                genre_aliases = {
                    "vlog": Genre.LIFESTYLE,
                    "action": Genre.SPORTS,
                    "promo": Genre.PRODUCT,
                    "doc": Genre.DOCUMENTARY,
                }
                genre = genre_aliases.get(norm_type) or Genre(norm_type)
                mem_record = NarrativeMemoryRecord(
                    record_id=f"fb_{user_id}_{project_id}_{edit_id}",
                    project_id=project_id,
                    user_id=user_id,
                    project_type=genre,
                    story_pattern=story_pattern,
                    pace=Pace(pace.lower()) if pace else None,
                    emotion=Emotion(emotion.lower()) if emotion else None,
                    engagement_score=rating if rating > 0 else (8.0 if liked else 2.0),
                    completion_rate=9.0 if liked else 3.0,
                    user_rating=rating if rating > 0 else (9.0 if liked else 2.0),
                    successful_elements=tuple(tags or []),
                )
                await self.narrative_store.record(mem_record)
            except Exception as e:
                logger.warning("Could not record narrative memory from feedback: %s", e)

        logger.info(
            "Processed feedback for user=%s project=%s edit=%s liked=%s rating=%.1f",
            user_id, project_id, edit_id, liked, rating,
        )
        return record

    async def get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        """Retrieve aggregated user preferences."""
        return await self.preferences_store.get_preferences(user_id)

    async def get_liked_examples(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve liked examples for prompt context enrichment."""
        return await self.preferences_store.get_liked_examples(user_id, limit=limit)

    async def reset_user(self, user_id: str) -> bool:
        """Erase all feedback and personalization for a user (GDPR Right to be Forgotten)."""
        return await self.preferences_store.reset_user(user_id)

    async def export_user_data(self, user_id: str) -> Dict[str, Any]:
        """Export all recorded feedback for a user."""
        return await self.preferences_store.export_user_data(user_id)
