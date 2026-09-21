"""
MONTA — Context Composer
===========================
Layer 4: Combines all context sources into a unified editing context.

Produces:
{
    "user_style": "cinematic",
    "target_platform": "instagram",
    "video_set": [...],
    "intent": {...}
}
"""


class ContextComposer:
    """Composes unified context from multiple sources."""

    async def compose(
        self,
        user_prompt: dict,
        uploaded_videos: list,
        previous_projects: list = None,
        user_preferences: dict = None,
        platform_rules: dict = None,
    ) -> dict:
        """
        Compose a unified context from all sources.
        
        Combines:
        - User Prompt (Layer 3 output)
        - Uploaded Videos (Layer 2 output)
        - Previous Projects (Layer 14 / DB)
        - User Preferences (Layer 14 / Qdrant)
        - Platform Rules (static config)
        """
        return {
            "user_style": user_preferences.get("style", "default") if user_preferences else "default",
            "target_platform": user_prompt.get("target", "instagram"),
            "video_set": uploaded_videos,
            "intent": user_prompt,
            "previous_projects": previous_projects or [],
            "preferences": user_preferences or {},
            "platform_rules": platform_rules or {},
        }
